"""管理后台运营指标 GET /api/admin/metrics：口径、周期边界、权限和查询次数。"""
from datetime import datetime, timedelta, timezone

import pytest

import main
from admin_metrics import compute_metrics
from db import connect
from test_app import client, register

# 2026-09-28 是周一。days=7 时：本周期 09-22 … 09-28，上一周期 09-15 … 09-21。
NOW = "2026-09-28T12:00:00+00:00"
OLD = "2026-08-01T10:00:00+00:00"
CUR_FIRST = "2026-09-22T00:00:00+00:00"
PREV_LAST = "2026-09-21T23:59:59+00:00"
PREV_FIRST = "2026-09-15T00:00:00+00:00"
BEFORE_PREV = "2026-09-14T23:59:59+00:00"


def at(day, hour=10):
    return f"2026-09-{day:02d}T{hour:02d}:00:00+00:00"


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(main, "utc_now", lambda: NOW)
    monkeypatch.setenv("ADMIN_USERNAME", "alice")
    user = register(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", (OLD, user["id"]))
    return user["id"]


def add_user(conn, name, created_at=OLD, trial=False, deleted=False):
    return conn.execute(
        "INSERT INTO users(username, password_hash, timezone, created_at, is_trial, deleted_at) "
        "VALUES (?, 'unused', 'Asia/Taipei', ?, ?, ?)",
        (name, created_at, int(trial), NOW if deleted else None),
    ).lastrowid


def add_problem(conn, user_id, created_at):
    return conn.execute(
        "INSERT INTO problems(user_id, title, language, code, thinking, created_at, zone) "
        "VALUES (?, 't', 'Python', '', '', ?, '算法')", (user_id, created_at),
    ).lastrowid


def add_review(conn, user_id, reviewed_at, problem_id=None):
    problem_id = problem_id or add_problem(conn, user_id, OLD)
    mistake = conn.execute(
        "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, 'm', '2026-09-28')",
        (problem_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
        "VALUES (?, 4, ?, '2026-09-29')", (mistake, reviewed_at),
    )


def add_post(conn, user_id, created_at, deleted=False):
    return conn.execute(
        "INSERT INTO posts(user_id, title, body, created_at, deleted_at) VALUES (?, 't', 'b', ?, ?)",
        (user_id, created_at, NOW if deleted else None),
    ).lastrowid


def add_comment(conn, user_id, created_at, post_id=None, deleted=False):
    post_id = post_id or add_post(conn, user_id, OLD)
    conn.execute(
        "INSERT INTO post_comments(post_id, user_id, body, created_at, deleted_at) "
        "VALUES (?, ?, 'c', ?, ?)", (post_id, user_id, created_at, NOW if deleted else None),
    )


def add_ai(conn, feature, created_at, ok=1, prompt=None, completion=None, user_id=None):
    conn.execute(
        "INSERT INTO ai_calls(user_id, feature, model, prompt_tokens, completion_tokens, ok, "
        "error, duration_ms, created_at) VALUES (?, ?, 'm', ?, ?, ?, '', 5, ?)",
        (user_id, feature, prompt, completion, ok, created_at),
    )


def plan_id(conn):
    row = conn.execute("SELECT id FROM plans LIMIT 1").fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, created_at) "
        "VALUES ('p', 30, 5, 100, ?)", (OLD,),
    ).lastrowid


def add_code(conn, n, created_at=OLD, redeemed_by=None, redeemed_at=None, revoked_at=None,
             expires_at=None, hint="abcd"):
    conn.execute(
        "INSERT INTO redeem_codes(code_hash, code_hint, plan_id, period_days, created_at, "
        "expires_at, redeemed_by, redeemed_at, revoked_at) VALUES (?, ?, ?, 30, ?, ?, ?, ?, ?)",
        (f"hash{n}", hint, plan_id(conn), created_at, expires_at, redeemed_by, redeemed_at,
         revoked_at),
    )


def metrics(client, days=None):
    response = client.get("/api/admin/metrics" + (f"?days={days}" if days else ""))
    assert response.status_code == 200, response.text
    return response.json()


# ---- 鉴权与参数 ----------------------------------------------------------

def test_requires_login(client):
    assert client.get("/api/admin/metrics").status_code == 401


def test_non_admin_is_forbidden(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "someone-else")
    register(client)
    assert client.get("/api/admin/metrics").status_code == 403


def test_trial_account_is_forbidden(client):
    assert client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).status_code == 201
    assert client.get("/api/admin/metrics").status_code == 403


@pytest.mark.parametrize("value", ["0", "1", "8", "14", "90", "abc", "-7", "7.5", ""])
def test_invalid_days_is_422(client, admin, value):
    assert client.get(f"/api/admin/metrics?days={value}").status_code == 422


def test_days_defaults_to_seven_and_accepts_thirty(client, admin):
    default = metrics(client)
    assert default["days"] == 7 and len(default["daily"]) == 7
    thirty = metrics(client, 30)
    assert thirty["days"] == 30 and len(thirty["daily"]) == 30
    assert metrics(client, 7)["days"] == 7


# ---- 空数据与结构 ----------------------------------------------------------

def test_empty_database_is_all_zero_with_filled_days(client, admin):
    result = metrics(client)
    assert result["generated_at"] == NOW
    for key in ("new_users", "active_users", "records", "reviews", "posts", "comments"):
        assert result[key] == {"current": 0, "previous": 0}, key
    assert result["ai"] == {
        "calls": {"current": 0, "previous": 0}, "failed": {"current": 0, "previous": 0},
        "tokens": {"prompt": 0, "completion": 0}, "by_feature": [],
    }
    assert result["pending_reports"] == 0
    assert result["redeem"] == {"created": 0, "redeemed": 0, "unused": 0}
    assert result["funnel"] == {
        "registered": 0, "first_record": 0, "first_review": 0, "returned_next_day": 0,
    }
    assert [d["date"] for d in result["daily"]] == [f"2026-09-{n}" for n in range(22, 29)]
    assert all(d["new_users"] == d["active_users"] == d["reviews"] == 0 for d in result["daily"])


def test_daily_dates_cross_month_boundary_for_thirty_days(client, admin):
    daily = metrics(client, 30)["daily"]
    assert daily[0]["date"] == "2026-08-30" and daily[-1]["date"] == "2026-09-28"
    assert len({d["date"] for d in daily}) == 30


def test_response_has_no_private_fields(client, admin):
    with connect(write=True) as conn:
        add_user(conn, "bob", at(25))
    body = client.get("/api/admin/metrics").text
    assert "@example.com" not in body and "bob" not in body and "alice" not in body
    assert set(metrics(client)) == {
        "days", "generated_at", "new_users", "active_users", "records", "reviews", "posts",
        "comments", "ai", "pending_reports", "redeem", "funnel", "daily",
    }


# ---- 周期边界（UTC 日界与上一周期） --------------------------------------------

def test_period_boundaries_for_new_users(client, admin):
    with connect(write=True) as conn:
        add_user(conn, "today-end", "2026-09-28T23:59:59+00:00")      # 本周期
        add_user(conn, "cur-first", CUR_FIRST)                        # 本周期第一秒
        add_user(conn, "prev-last", PREV_LAST)                        # 上一周期最后一秒
        add_user(conn, "prev-first", PREV_FIRST)                      # 上一周期第一秒
        add_user(conn, "before", BEFORE_PREV)                         # 都不算
        add_user(conn, "tomorrow", "2026-09-29T00:00:00+00:00")       # 未来，都不算
    result = metrics(client)
    assert result["new_users"] == {"current": 2, "previous": 2}
    by_date = {d["date"]: d["new_users"] for d in result["daily"]}
    assert by_date["2026-09-22"] == 1 and by_date["2026-09-28"] == 1
    assert sum(by_date.values()) == 2


def test_period_boundaries_follow_the_clock_not_the_user_timezone(client, admin, monkeypatch):
    # UTC 00:30：用户在 +8 时区已是下午，但后台只按 UTC 日切分。
    monkeypatch.setattr(main, "utc_now", lambda: "2026-09-29T00:30:00+00:00")
    with connect(write=True) as conn:
        add_user(conn, "late", "2026-09-28T23:59:00+00:00")
        add_user(conn, "old-edge", "2026-09-22T00:00:00+00:00")  # 此时已是上一周期最后一天
    result = metrics(client)
    assert result["daily"][0]["date"] == "2026-09-23" and result["daily"][-1]["date"] == "2026-09-29"
    assert result["new_users"] == {"current": 1, "previous": 1}


def test_previous_period_for_thirty_days(client, admin):
    with connect(write=True) as conn:
        add_user(conn, "cur", "2026-08-30T00:00:00+00:00")
        add_user(conn, "prev-last", "2026-08-29T23:59:59+00:00")
        add_user(conn, "prev-first", "2026-07-31T00:00:00+00:00")
        add_user(conn, "before", "2026-07-30T23:59:59+00:00")
    # 管理员自己注册于 OLD（08-01），落在 30 天的上一周期里，所以 previous 是 2 + 1。
    assert metrics(client, 30)["new_users"] == {"current": 1, "previous": 3}


def test_records_reviews_posts_comments_split_by_period(client, admin):
    with connect(write=True) as conn:
        user = add_user(conn, "u1")
        add_problem(conn, user, at(28))
        add_problem(conn, user, PREV_LAST)
        add_problem(conn, user, BEFORE_PREV)
        add_review(conn, user, at(24))
        add_review(conn, user, at(24, 11))
        add_review(conn, user, at(16))
        add_post(conn, user, at(23))
        add_post(conn, user, at(20))
        add_comment(conn, user, at(25))
        add_comment(conn, user, at(17))
    result = metrics(client)
    assert result["records"] == {"current": 1, "previous": 1}
    assert result["reviews"] == {"current": 2, "previous": 1}
    assert result["posts"] == {"current": 1, "previous": 1}
    assert result["comments"] == {"current": 1, "previous": 1}
    daily = {d["date"]: d for d in result["daily"]}
    assert daily["2026-09-24"]["reviews"] == 2 and daily["2026-09-23"]["reviews"] == 0


# ---- 账号排除 --------------------------------------------------------------

def test_trial_and_deleted_accounts_are_excluded_everywhere(client, admin):
    with connect(write=True) as conn:
        real = add_user(conn, "real", at(25))
        trial = add_user(conn, "trial", at(25), trial=True)
        gone = add_user(conn, "gone", at(25), deleted=True)
        for user in (real, trial, gone):
            add_problem(conn, user, at(26))
            add_review(conn, user, at(26))
            add_post(conn, user, at(26))
            add_comment(conn, user, at(26))
    result = metrics(client)
    assert result["new_users"]["current"] == 1
    assert result["active_users"]["current"] == 1
    # real 的 add_review 额外建了 1 条旧题目（OLD），不在周期内：本周期记录只有各 1 条。
    assert result["records"]["current"] == 1
    assert result["reviews"]["current"] == 1
    assert result["posts"]["current"] == 1
    assert result["comments"]["current"] == 1
    assert result["funnel"]["registered"] == 1


def test_soft_deleted_forum_content_is_not_counted(client, admin):
    with connect(write=True) as conn:
        user = add_user(conn, "spammer")
        add_post(conn, user, at(26), deleted=True)
        add_comment(conn, user, at(26), deleted=True)
    result = metrics(client)
    assert result["posts"]["current"] == 0 and result["comments"]["current"] == 0
    assert result["active_users"]["current"] == 0


# ---- 活跃用户 --------------------------------------------------------------

def test_active_users_are_deduplicated_across_actions_and_days(client, admin):
    with connect(write=True) as conn:
        a, b, c, d = (add_user(conn, name) for name in "abcd")
        # a：同一周期内多种行为、多天，只算一个人
        add_problem(conn, a, at(23))
        add_review(conn, a, at(24))
        add_post(conn, a, at(25))
        add_comment(conn, a, at(26))
        # b：只发评论；c：只复习；d：只在上一周期活跃
        add_comment(conn, b, at(27))
        add_review(conn, c, at(28))
        add_review(conn, d, at(17))
        add_problem(conn, d, at(18))
    result = metrics(client)
    assert result["active_users"] == {"current": 3, "previous": 1}
    daily = {x["date"]: x["active_users"] for x in result["daily"]}
    assert daily["2026-09-23"] == 1 and daily["2026-09-24"] == 1
    assert daily["2026-09-22"] == 0


def test_daily_active_users_counts_each_user_once_per_day(client, admin):
    with connect(write=True) as conn:
        a, b = add_user(conn, "a"), add_user(conn, "b")
        for hour in (8, 9, 10):
            add_problem(conn, a, at(24, hour))
        add_review(conn, b, at(24))
        add_problem(conn, a, at(25))
    daily = {x["date"]: x["active_users"] for x in metrics(client)["daily"]}
    assert daily["2026-09-24"] == 2 and daily["2026-09-25"] == 1


# ---- 漏斗 ------------------------------------------------------------------

def funnel(client):
    return metrics(client)["funnel"]


def test_funnel_counts_only_current_period_registrations(client, admin):
    with connect(write=True) as conn:
        add_user(conn, "cur", at(25))
        add_user(conn, "prev", PREV_LAST)
    assert funnel(client)["registered"] == 1


def test_funnel_first_record_requires_a_problem_after_registration(client, admin):
    with connect(write=True) as conn:
        yes = add_user(conn, "yes", at(25, 9))
        before = add_user(conn, "before", at(25, 9))
        none = add_user(conn, "none", at(25, 9))
        add_problem(conn, yes, at(25, 9))          # 注册同一时刻也算“注册后”
        add_problem(conn, before, at(24, 9))       # 注册前创建的不算
    assert funnel(client) == {
        "registered": 3, "first_record": 1, "first_review": 0, "returned_next_day": 0,
    }


def test_funnel_first_review_requires_a_review_after_registration(client, admin):
    with connect(write=True) as conn:
        yes = add_user(conn, "yes", at(25, 9))
        early = add_user(conn, "early", at(25, 9))
        record_only = add_user(conn, "record-only", at(25, 9))
        add_review(conn, yes, at(25, 10))
        add_review(conn, early, at(25, 8))          # 注册前的复习不算
        add_problem(conn, record_only, at(25, 10))
    result = funnel(client)
    assert result["first_review"] == 1
    assert result["first_record"] == 1  # record_only；yes/early 的题目建于 OLD（注册前）


def test_funnel_returned_next_day_needs_a_strictly_later_utc_day(client, admin):
    with connect(write=True) as conn:
        same_day = add_user(conn, "same-day", at(25, 1))
        late_night = add_user(conn, "late-night", "2026-09-25T23:59:00+00:00")
        next_day_review = add_user(conn, "next-review", at(25, 10))
        next_day_post = add_user(conn, "next-post", at(25, 10))
        next_day_comment = add_user(conn, "next-comment", at(25, 10))
        next_day_record = add_user(conn, "next-record", at(25, 10))
        later = add_user(conn, "later", at(23, 10))
        add_problem(conn, same_day, at(25, 23))                     # 当天又回来：不算
        add_review(conn, same_day, at(25, 22))
        add_post(conn, same_day, at(25, 21))
        add_comment(conn, same_day, at(25, 20))
        add_problem(conn, late_night, "2026-09-26T00:00:30+00:00")  # 跨 UTC 午夜：算
        add_review(conn, next_day_review, at(26, 3))
        add_post(conn, next_day_post, at(26, 3))
        add_comment(conn, next_day_comment, at(26, 3))
        add_problem(conn, next_day_record, at(26, 3))
        add_problem(conn, later, at(28, 1))                         # 隔了好几天：也算
    result = funnel(client)
    assert result["registered"] == 7
    assert result["returned_next_day"] == 6


def test_funnel_returned_ignores_soft_deleted_content(client, admin):
    with connect(write=True) as conn:
        user = add_user(conn, "u", at(25))
        add_post(conn, user, at(26), deleted=True)
        add_comment(conn, user, at(26), deleted=True)
    assert funnel(client)["returned_next_day"] == 0


def test_funnel_full_path(client, admin):
    with connect(write=True) as conn:
        a = add_user(conn, "a", at(23, 8))
        b = add_user(conn, "b", at(23, 8))
        c = add_user(conn, "c", at(23, 8))
        add_user(conn, "d", at(23, 8))
        pa = add_problem(conn, a, at(23, 9))
        add_review(conn, a, at(23, 10), problem_id=pa)
        add_review(conn, a, at(24, 10), problem_id=pa)
        add_problem(conn, b, at(23, 9))
        add_problem(conn, c, at(23, 9))
        add_comment(conn, c, at(27, 9))
    assert funnel(client) == {
        "registered": 4, "first_record": 3, "first_review": 1, "returned_next_day": 2,
    }


# ---- AI ------------------------------------------------------------------

def test_ai_counts_failures_and_tokens_with_missing_usage(client, admin):
    with connect(write=True) as conn:
        add_ai(conn, "variant", at(25), prompt=1000, completion=500)
        add_ai(conn, "variant", at(26), ok=0, prompt=None, completion=None)  # 缺失令牌按 0
        add_ai(conn, "variant", at(27), prompt=200, completion=None)
        add_ai(conn, "photo", at(28), prompt=50, completion=25)
        add_ai(conn, "photo", PREV_LAST, ok=0, prompt=999, completion=999)    # 上一周期
        add_ai(conn, "weakness", at(16))                                      # 上一周期
        add_ai(conn, "variant", BEFORE_PREV, prompt=999)                      # 都不算
    ai = metrics(client)["ai"]
    assert ai["calls"] == {"current": 4, "previous": 2}
    assert ai["failed"] == {"current": 1, "previous": 1}
    assert ai["tokens"] == {"prompt": 1250, "completion": 525}
    assert ai["by_feature"] == [
        {"feature": "variant", "calls": 3, "failed": 1, "tokens": 1700},
        {"feature": "photo", "calls": 1, "failed": 0, "tokens": 75},
    ]


def test_ai_by_feature_is_sorted_and_skips_features_without_current_calls(client, admin):
    with connect(write=True) as conn:
        add_ai(conn, "b-feature", at(25))
        add_ai(conn, "a-feature", at(25))
        add_ai(conn, "c-feature", at(25))
        add_ai(conn, "c-feature", at(26))
        add_ai(conn, "old-only", at(16))
    assert [f["feature"] for f in metrics(client)["ai"]["by_feature"]] == [
        "c-feature", "a-feature", "b-feature",
    ]


def test_ai_calls_are_counted_regardless_of_account_kind(client, admin):
    # 注销账号的 ai_calls.user_id 会被置空；体验账号同样花服务商的钱。
    with connect(write=True) as conn:
        trial = add_user(conn, "trial", OLD, trial=True)
        add_ai(conn, "variant", at(25), user_id=None, prompt=10)
        add_ai(conn, "variant", at(25), user_id=trial, prompt=10)
    assert metrics(client)["ai"]["calls"]["current"] == 2


# ---- 举报与兑换码 -----------------------------------------------------------

def test_pending_reports_match_the_admin_queue(client, admin):
    with connect(write=True) as conn:
        a, b = add_user(conn, "a"), add_user(conn, "b")
        gone = add_user(conn, "gone", deleted=True)
        post = add_post(conn, a, at(25))
        conn.execute("INSERT INTO reports(reporter_user_id, post_id, created_at) VALUES (?, ?, ?)",
                     (b, post, at(26)))
        conn.execute("INSERT INTO reports(reporter_user_id, post_id, created_at, resolved_at) "
                     "VALUES (?, ?, ?, ?)", (a, post, at(26), at(27)))
        conn.execute("INSERT INTO avatar_reports(reporter_user_id, avatar_owner_id, created_at) "
                     "VALUES (?, ?, ?)", (a, b, at(26)))
        conn.execute("INSERT INTO avatar_reports(reporter_user_id, avatar_owner_id, created_at) "
                     "VALUES (?, ?, ?)", (b, gone, at(26)))
        conn.execute("INSERT INTO avatar_reports(reporter_user_id, avatar_owner_id, created_at, "
                     "resolved_at) VALUES (?, ?, ?, ?)", (b, a, at(26), at(27)))
    assert metrics(client)["pending_reports"] == 2
    assert len(client.get("/api/admin/reports").json()["reports"]) == 2


def test_redeem_counts(client, admin):
    with connect(write=True) as conn:
        user = add_user(conn, "u")
        add_code(conn, 1, created_at=at(25))                                     # 本周期生成 + 未使用
        add_code(conn, 2, created_at=at(26), redeemed_by=user, redeemed_at=at(27))
        add_code(conn, 3, created_at=OLD, redeemed_by=user, redeemed_at=at(28))  # 本周期兑换
        add_code(conn, 4, created_at=OLD, redeemed_by=user, redeemed_at=at(16))  # 上周期兑换
        add_code(conn, 5, created_at=at(25), revoked_at=at(26))                  # 已撤销
        add_code(conn, 6, created_at=at(25), expires_at="2026-09-27T00:00:00+00:00")  # 已过期
        add_code(conn, 7, created_at=OLD, expires_at="2026-09-29T00:00:00+00:00")     # 未过期
        add_code(conn, 8, created_at=at(25), redeemed_by=user, redeemed_at=at(25),
                 hint="----")                                                    # 直接开通审计记录
    assert metrics(client)["redeem"] == {"created": 4, "redeemed": 2, "unused": 2}


# ---- 查询次数与只读 -----------------------------------------------------------

def count_statements(days, size):
    from db import connect as real_connect
    from contextlib import contextmanager

    statements = []

    @contextmanager
    def counting(*args, **kwargs):
        with real_connect(*args, **kwargs) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    with counting() as conn:
        for index in range(size):
            user = add_user(conn, f"bulk{size}-{index}", at(25))
            add_problem(conn, user, at(26))
            add_review(conn, user, at(27))
            add_post(conn, user, at(26))
            add_comment(conn, user, at(26))
            add_ai(conn, "variant", at(26), prompt=1)
        statements.clear()
        compute_metrics(conn, days, datetime(2026, 9, 28, 12, tzinfo=timezone.utc))
    return len(statements)


def test_query_count_does_not_grow_with_data(client, admin):
    small = count_statements(7, 2)
    large = count_statements(7, 40)
    assert small == large
    assert small <= 16


def test_metrics_do_not_modify_the_database(client, admin):
    with connect(write=True) as conn:
        user = add_user(conn, "u", at(25))
        add_problem(conn, user, at(26))
        add_ai(conn, "variant", at(26))
    tables = ("users", "problems", "reviews", "posts", "post_comments", "ai_calls",
              "redeem_codes", "reports", "sessions")

    def snapshot():
        with connect() as conn:
            return [conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables]

    before = [[tuple(r) for r in rows] for rows in snapshot()]
    metrics(client)
    metrics(client, 30)
    assert [[tuple(r) for r in rows] for rows in snapshot()] == before


def test_compute_metrics_rejects_unsupported_days():
    with pytest.raises(ValueError):
        compute_metrics(None, 14, datetime.now(timezone.utc))


def test_now_with_other_offset_is_normalised_to_utc(client, admin):
    with connect(write=True) as conn:
        add_user(conn, "edge", "2026-09-28T23:00:00+00:00")
        # 本地时间已是 09-29 06:00（+7），但 UTC 仍是 09-28 23:30。
        result = compute_metrics(conn, 7, datetime(2026, 9, 29, 6, 30,
                                                   tzinfo=timezone(timedelta(hours=7))))
    assert result["daily"][-1]["date"] == "2026-09-28"
    assert result["new_users"]["current"] == 1


def test_rows_dated_after_today_are_excluded_everywhere(client, admin):
    tomorrow = "2026-09-29T00:00:00+00:00"
    with connect(write=True) as conn:
        user = add_user(conn, "future", tomorrow)
        add_problem(conn, user, tomorrow)
        add_review(conn, user, tomorrow)
        add_post(conn, user, tomorrow)
        add_ai(conn, "variant", tomorrow, ok=0, prompt=500)
        add_code(conn, 1, created_at=tomorrow, redeemed_by=user, redeemed_at=tomorrow)
    result = metrics(client)
    assert result["new_users"]["current"] == 0
    assert result["active_users"] == {"current": 0, "previous": 0}
    assert result["ai"]["calls"]["current"] == 0 and result["ai"]["tokens"]["prompt"] == 0
    assert result["redeem"] == {"created": 0, "redeemed": 0, "unused": 0}
    assert result["funnel"]["registered"] == 0
