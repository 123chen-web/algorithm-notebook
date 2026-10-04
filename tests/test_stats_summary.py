"""GET /api/stats/summary：总览「趋势」区的数据口径（按用户本地日、SQL 分组、纯统计）。"""

import random
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import ai
import main
import stats_summary
from db import connect
from test_app import client, register  # noqa: F401  (client 是 pytest 夹具)


TODAY = date(2026, 9, 19)  # 星期六；与 test_app.client 冻结的用户本地日期一致（用户时区 Asia/Shanghai）。
URL = "/api/stats/summary"


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("趋势统计是纯统计，不能调用 AI")

    monkeypatch.setattr(ai, "generate", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)


def user_id(client):
    return client.get("/api/me").json()["id"]


def seed_mistakes(owner_id, count=1, due_date=None):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '二分边界', '算法', 'Python', '', '', ?)",
            (owner_id, "2026-01-01T00:00:00+00:00"),
        ).lastrowid
        return [
            conn.execute(
                "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, '遗漏边界条件', ?)",
                (problem_id, due_date or TODAY.isoformat()),
            ).lastrowid
            for _ in range(count)
        ]


def seed_reviews(mistake_id, *items):
    """items: 时间戳字符串，或 (时间戳, 评分)。只写 reviews 的老列（旧数据没有新日志列）。"""
    rows = []
    for item in items:
        stamp, quality = item if isinstance(item, tuple) else (item, 4)
        rows.append((mistake_id, quality, stamp, TODAY.isoformat()))
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)",
            rows,
        )


def at(day, hour=4, minute=0, second=0):
    """某个 UTC 时刻；默认 04:00 UTC = 上海 12:00，稳稳落在当天。"""
    return datetime(day.year, day.month, day.day, hour, minute, second,
                    tzinfo=timezone.utc).isoformat()


def summary(client, **params):
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def set_timezone(owner_id, name):
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (name, owner_id))


def daily(data):
    return {row["date"]: row["count"] for row in data["daily_reviews"]}


@pytest.fixture
def me(client):
    register(client)
    return user_id(client)


# ---------------------------------------------------------------- 接口与参数
def test_requires_login(client):
    assert client.get(URL).status_code == 401


def test_default_days_is_30_and_shape(client, me):
    data = summary(client)
    assert data["days"] == 30
    assert data["timezone"] == "Asia/Shanghai"
    assert data["today"] == TODAY.isoformat()
    assert set(data) == {
        "timezone", "today", "days", "due", "streak_days", "reviews",
        "retention", "forecast", "daily_reviews",
    }
    assert data["due"] == {"today": 0, "overdue": 0}
    assert data["streak_days"] == 0
    assert data["reviews"] == {"current": 0, "previous": 0}
    empty = {"reviews": 0, "passed": 0, "rate": None}
    assert data["retention"]["current"] == empty
    assert data["retention"]["previous"] == empty
    assert len(data["retention"]["weekly"]) == 8
    assert all(week["rate"] is None and week["reviews"] == 0 for week in data["retention"]["weekly"])
    assert len(data["forecast"]) == 14
    assert all(item["due"] == 0 for item in data["forecast"])
    assert len(data["daily_reviews"]) == 30


@pytest.mark.parametrize("days", [7, 30, 90])
def test_accepted_ranges_set_the_period_length(client, me, days):
    data = summary(client, days=days)
    assert data["days"] == days
    assert len(data["daily_reviews"]) == days
    assert data["daily_reviews"][-1]["date"] == TODAY.isoformat()
    assert data["daily_reviews"][0]["date"] == (TODAY - timedelta(days=days - 1)).isoformat()


@pytest.mark.parametrize("value", ["0", "1", "14", "31", "365", "-7", "abc", "", "7.5"])
def test_invalid_ranges_are_rejected(client, me, value):
    assert client.get(URL, params={"days": value}).status_code == 422


def test_response_has_no_private_fields(client, me):
    seed_reviews(seed_mistakes(me)[0], at(TODAY))
    text = client.get(URL).text
    assert "alice" not in text and "example.com" not in text and "@" not in text


def test_trial_account_gets_its_own_numbers(client):
    created = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert created.status_code == 201
    owner = user_id(client)
    mistake = seed_mistakes(owner, due_date=(TODAY - timedelta(days=2)).isoformat())[0]
    seed_reviews(mistake, at(TODAY))
    data = summary(client)
    assert data["due"] == {"today": 1, "overdue": 1}
    assert data["reviews"]["current"] == 1


def test_other_users_and_deleted_accounts_are_not_mixed_in(client, me, monkeypatch):
    mine = seed_mistakes(me, due_date=TODAY.isoformat())[0]
    seed_reviews(mine, at(TODAY))
    client.post("/api/auth/logout")
    register(client, "bob")
    bob = user_id(client)
    theirs = seed_mistakes(bob, count=3, due_date=(TODAY - timedelta(days=1)).isoformat())
    for mistake in theirs:
        seed_reviews(mistake, at(TODAY - timedelta(days=3)), at(TODAY - timedelta(days=2)))
    bob_view = summary(client)
    assert bob_view["due"] == {"today": 3, "overdue": 3}
    assert bob_view["reviews"]["current"] == 6

    with connect(write=True) as conn:  # bob 注销
        conn.execute("UPDATE users SET deleted_at = '2026-09-18T00:00:00+00:00' WHERE id = ?", (bob,))
    client.post("/api/auth/logout")
    login = client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"})
    assert login.status_code == 200
    alice_view = summary(client)
    assert alice_view["due"] == {"today": 1, "overdue": 0}
    assert alice_view["reviews"] == {"current": 1, "previous": 0}
    assert alice_view["retention"]["current"]["reviews"] == 0
    assert sum(item["due"] for item in alice_view["forecast"]) == 1


# ---------------------------------------------------------------- 用户时区
def test_review_just_after_local_midnight_counts_for_the_new_day(client, me):
    mistake = seed_mistakes(me)[0]
    # 上海 UTC+8：09-18 15:59:59Z = 23:59:59（18 日）；16:00:00Z = 00:00:00（19 日）。
    seed_reviews(mistake, "2026-09-18T15:59:59+00:00", "2026-09-18T16:00:00+00:00")
    counts = daily(summary(client, days=7))
    assert counts["2026-09-18"] == 1
    assert counts["2026-09-19"] == 1


def test_negative_offset_timezone_moves_the_day_back(client, me):
    set_timezone(me, "America/Los_Angeles")  # 9 月是 UTC-7
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, "2026-09-19T06:59:59+00:00", "2026-09-19T07:00:00+00:00")
    counts = daily(summary(client, days=7))
    assert counts["2026-09-18"] == 1  # 23:59:59 PDT
    assert counts["2026-09-19"] == 1  # 00:00:00 PDT


def test_timestamps_with_other_offsets_or_fractions_are_read_as_instants(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(
        mistake,
        "2026-09-19T00:30:00+08:00",       # 上海 19 日
        "2026-09-18T23:30:00.250000+08:00",  # 上海 18 日
        "2026-09-18T16:30:00Z",            # 上海 19 日
    )
    counts = daily(summary(client, days=7))
    assert (counts["2026-09-18"], counts["2026-09-19"]) == (1, 2)


def test_text_date_may_differ_from_the_local_day_at_the_window_edges(client, me):
    """窗口两端：文本里的日期与本地日相差一天以上的写法也不能被粗筛漏掉。"""
    mistake = seed_mistakes(me)[0]
    seed_reviews(
        mistake,
        "2026-03-23T23:00:00-12:00",   # = 03-24 11:00Z = 上海 03-24 19:00：90 天窗口（上一周期）的第一天
        "2026-09-20T05:00:00+14:00",   # = 09-19 15:00Z = 上海 09-19 23:00：今天
    )
    data = summary(client, days=90)
    assert data["reviews"] == {"current": 1, "previous": 1}
    assert daily(data)["2026-09-19"] == 1


@pytest.mark.parametrize("zone_name", [
    "Asia/Shanghai", "America/New_York", "Europe/London", "Australia/Lord_Howe",
    "Pacific/Kiritimati", "Asia/Kolkata", "America/Sao_Paulo", "Asia/Beirut", "Pacific/Pago_Pago",
])
def test_day_buckets_match_the_python_reference_across_dst(client, me, monkeypatch, zone_name):
    """随机时间戳（含每个夏令时跳变点两侧）：SQL 分桶必须与 astimezone().date() 完全一致。"""
    set_timezone(me, zone_name)
    zone = ZoneInfo(zone_name)
    today = date(2026, 11, 8)  # 窗口覆盖美、欧、澳多个夏令时切换日
    monkeypatch.setattr(main, "today_for", lambda user: today)
    rng = random.Random(zone_name)
    first = today - timedelta(days=89)
    start = int(datetime(first.year, first.month, first.day, tzinfo=timezone.utc).timestamp()) - 86400
    end = int(datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp()) + 2 * 86400
    stamps = {rng.randrange(start, end) for _ in range(1500)}
    for day_offset in range(0, 90):  # 每个本地午夜附近都放几条
        local_midnight = datetime(first.year, first.month, first.day) + timedelta(days=day_offset)
        base = int(local_midnight.replace(tzinfo=zone).timestamp())
        stamps.update({base - 1, base, base + 1, base - 3600, base + 3600})
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, *[datetime.fromtimestamp(s, timezone.utc).isoformat() for s in sorted(stamps)])
    expected = Counter(datetime.fromtimestamp(s, timezone.utc).astimezone(zone).date() for s in stamps)
    data = summary(client, days=90)
    assert daily(data) == {
        (first + timedelta(days=offset)).isoformat(): expected.get(first + timedelta(days=offset), 0)
        for offset in range(90)
    }


# ---------------------------------------------------------------- 本周期 / 上一周期
@pytest.mark.parametrize("days", [7, 30, 90])
def test_period_boundaries(client, me, days):
    mistake = seed_mistakes(me)[0]
    current_first = TODAY - timedelta(days=days - 1)
    previous_last = current_first - timedelta(days=1)
    previous_first = previous_last - timedelta(days=days - 1)
    seed_reviews(
        mistake,
        at(TODAY),                                   # 本周期最后一天
        at(current_first),                           # 本周期第一天
        at(current_first), at(previous_last),        # 上一周期最后一天
        at(previous_first),                          # 上一周期第一天
        at(previous_first - timedelta(days=1)),      # 更早：两个周期都不算
        at(TODAY + timedelta(days=1)),               # 未来：不算
    )
    data = summary(client, days=days)
    assert data["reviews"] == {"current": 3, "previous": 2}
    counts = daily(data)
    assert counts[TODAY.isoformat()] == 1
    assert counts[current_first.isoformat()] == 2
    assert sum(counts.values()) == 3


def test_empty_account_has_zero_periods(client, me):
    data = summary(client, days=90)
    assert data["reviews"] == {"current": 0, "previous": 0}
    assert sum(item["count"] for item in data["daily_reviews"]) == 0


# ---------------------------------------------------------------- 真实保持率
def test_first_review_of_a_mistake_is_not_counted(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY - timedelta(days=5)), 5), (at(TODAY - timedelta(days=2)), 4))
    data = summary(client)
    assert data["reviews"]["current"] == 2
    assert data["retention"]["current"] == {"reviews": 1, "passed": 1, "rate": 1.0}


def test_a_mistake_with_only_one_review_gives_no_rate(client, me):
    seed_reviews(seed_mistakes(me)[0], (at(TODAY), 5))
    assert summary(client)["retention"]["current"] == {"reviews": 0, "passed": 0, "rate": None}


def test_quality_three_passes_and_two_fails(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(
        mistake, (at(TODAY - timedelta(days=9)), 0),
        (at(TODAY - timedelta(days=8)), 3), (at(TODAY - timedelta(days=7)), 2),
        (at(TODAY - timedelta(days=6)), 5), (at(TODAY - timedelta(days=5)), 0),
    )
    # 非首次 4 条：3 和 5 通过，2 和 0 未通过。
    assert summary(client)["retention"]["current"] == {"reviews": 4, "passed": 2, "rate": 0.5}


def test_rate_is_rounded_to_three_places(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY - timedelta(days=9)), 5))
    seed_reviews(mistake, *[(at(TODAY - timedelta(days=8 - index)), 4 if index < 2 else 1) for index in range(3)])
    assert summary(client)["retention"]["current"] == {"reviews": 3, "passed": 2, "rate": 0.667}


def test_earlier_review_outside_the_period_still_makes_the_next_one_a_repeat(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY - timedelta(days=200)), 4), (at(TODAY - timedelta(days=1)), 4))
    data = summary(client, days=7)
    assert data["reviews"]["current"] == 1
    assert data["retention"]["current"] == {"reviews": 1, "passed": 1, "rate": 1.0}


def test_same_second_reviews_count_the_later_row_as_the_repeat(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY), 1), (at(TODAY), 5))  # 同一秒：先插入的是首次
    assert summary(client)["retention"]["current"] == {"reviews": 1, "passed": 1, "rate": 1.0}


def test_first_review_is_decided_by_time_not_row_order(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY - timedelta(days=1)), 5))   # 先插入但时间更晚
    seed_reviews(mistake, (at(TODAY - timedelta(days=4)), 0))   # 后插入但时间更早 → 它才是首次
    assert summary(client)["retention"]["current"] == {"reviews": 1, "passed": 1, "rate": 1.0}


def test_each_mistake_has_its_own_first_review(client, me):
    first, second = seed_mistakes(me, count=2)
    seed_reviews(first, (at(TODAY - timedelta(days=3)), 4), (at(TODAY - timedelta(days=2)), 4))
    seed_reviews(second, (at(TODAY - timedelta(days=3)), 1), (at(TODAY - timedelta(days=2)), 1))
    assert summary(client)["retention"]["current"] == {"reviews": 2, "passed": 1, "rate": 0.5}


def test_previous_period_retention_and_empty_denominator(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(
        mistake,
        (at(TODAY - timedelta(days=45)), 4), (at(TODAY - timedelta(days=40)), 4),
        (at(TODAY - timedelta(days=39)), 1),
    )
    data = summary(client, days=30)
    assert data["retention"]["current"] == {"reviews": 0, "passed": 0, "rate": None}
    assert data["retention"]["previous"] == {"reviews": 2, "passed": 1, "rate": 0.5}


def test_retention_works_without_the_newer_log_columns(client, me):
    """旧数据库的 reviews 没有 elapsed_days 等日志列；统计只读老列，仍然正确。"""
    with connect(write=True) as conn:
        for column in ("elapsed_days", "scheduled_days", "ease_before", "repetitions_before"):
            try:
                conn.execute(f"ALTER TABLE reviews DROP COLUMN {column}")
            except Exception:  # 这个 SQLite 不支持 DROP COLUMN：列本来就不存在或无法删，仍用老列验证
                pass
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY - timedelta(days=3)), 4), (at(TODAY - timedelta(days=2)), 2))
    assert summary(client)["retention"]["current"] == {"reviews": 1, "passed": 0, "rate": 0.0}


# ---------------------------------------------------------------- 每周保持率
def test_weekly_retention_groups_by_local_monday_weeks(client, me):
    mistake = seed_mistakes(me)[0]
    # 今天 2026-09-19 是周六，本周一是 09-14；上周一 09-07。
    # 周日 09-13 23:59:59 上海 = 15:59:59Z；周一 09-14 00:00:00 上海 = 16:00:00Z（前一天）。
    seed_reviews(
        mistake,
        ("2026-09-01T04:00:00+00:00", 5),                       # 首次，不计
        ("2026-09-13T15:59:59+00:00", 5),                       # 周日 → 09-07 那一周，通过
        ("2026-09-13T16:00:00+00:00", 1),                       # 周一 00:00 → 本周，未通过
        ("2026-09-19T04:00:00+00:00", 4),                       # 本周，通过
    )
    weekly = summary(client)["retention"]["weekly"]
    assert [week["week_start"] for week in weekly] == [
        "2026-07-27", "2026-08-03", "2026-08-10", "2026-08-17",
        "2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14",
    ]
    assert weekly[-2] == {"week_start": "2026-09-07", "reviews": 1, "passed": 1, "rate": 1.0}
    assert weekly[-1] == {"week_start": "2026-09-14", "reviews": 2, "passed": 1, "rate": 0.5}
    # 首次复习所在的 08-31 周没有可计的复习：rate 为 null，而不是 0。
    assert weekly[5] == {"week_start": "2026-08-31", "reviews": 0, "passed": 0, "rate": None}


def test_weekly_retention_is_independent_of_the_days_range(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, (at(TODAY - timedelta(days=40)), 4), (at(TODAY - timedelta(days=38)), 4))
    assert summary(client, days=7)["retention"]["weekly"] == summary(client, days=90)["retention"]["weekly"]
    assert sum(week["reviews"] for week in summary(client, days=7)["retention"]["weekly"]) == 1


# ---------------------------------------------------------------- 预测与到期
def test_forecast_today_includes_everything_overdue(client, me):
    day = lambda offset: (TODAY + timedelta(days=offset)).isoformat()  # noqa: E731
    for offset, count in [(-30, 2), (-1, 1), (0, 3), (1, 4), (13, 2), (14, 5), (40, 7)]:
        seed_mistakes(me, count=count, due_date=day(offset))
    data = summary(client)
    assert data["due"] == {"today": 6, "overdue": 3}
    assert [item["date"] for item in data["forecast"]] == [day(offset) for offset in range(14)]
    due = {item["date"]: item["due"] for item in data["forecast"]}
    assert due[day(0)] == 6
    assert due[day(1)] == 4
    assert due[day(13)] == 2
    assert due[day(2)] == 0
    assert sum(due.values()) == 12  # 14 天之外的（+14、+40）不在预测里


def test_overdue_counts_only_past_due_dates(client, me):
    seed_mistakes(me, count=2, due_date=TODAY.isoformat())
    assert summary(client)["due"] == {"today": 2, "overdue": 0}
    seed_mistakes(me, count=1, due_date=(TODAY - timedelta(days=1)).isoformat())
    assert summary(client)["due"] == {"today": 3, "overdue": 1}


def test_due_uses_the_users_local_today(client, me, monkeypatch):
    seed_mistakes(me, count=2, due_date="2026-09-20")
    assert summary(client)["due"]["today"] == 0
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 9, 20))
    data = summary(client)
    assert data["due"] == {"today": 2, "overdue": 0}
    assert data["forecast"][0] == {"date": "2026-09-20", "due": 2}


def test_all_due_queries_go_through_the_central_conditions():
    assert "m.due_date <= :today" in stats_summary.due_condition("m")
    assert "m.due_date < :today" in stats_summary.overdue_condition("m")
    assert stats_summary.review_pool("m") in stats_summary.due_condition("m")
    assert stats_summary.review_pool("m") in stats_summary.overdue_condition("m")


# ---------------------------------------------------------------- 每日复习与连续打卡
def test_daily_reviews_include_zero_days_in_order(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, at(TODAY), at(TODAY), at(TODAY - timedelta(days=3)))
    data = summary(client, days=7)
    assert [row["count"] for row in data["daily_reviews"]] == [0, 0, 0, 1, 0, 0, 2]
    assert [row["date"] for row in data["daily_reviews"]] == [
        (TODAY - timedelta(days=6 - offset)).isoformat() for offset in range(7)
    ]


def test_streak_follows_the_existing_rule(client, me):
    mistake = seed_mistakes(me)[0]
    assert summary(client)["streak_days"] == 0
    seed_reviews(mistake, *[at(TODAY - timedelta(days=offset)) for offset in (1, 2, 3, 5)])
    assert summary(client)["streak_days"] == 3  # 今天还没复习，昨天复习了 → 不断
    seed_reviews(mistake, at(TODAY))
    assert summary(client)["streak_days"] == 4


def test_streak_longer_than_any_range_is_counted_in_full(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, *[at(TODAY - timedelta(days=offset)) for offset in range(0, 800)])
    seed_reviews(mistake, at(TODAY - timedelta(days=801)))  # 断了一天，再往前的不算
    assert summary(client, days=7)["streak_days"] == 800


def test_streak_matches_the_overview_endpoint(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, *[at(TODAY - timedelta(days=offset)) for offset in (0, 1, 2, 4, 5)])
    assert summary(client)["streak_days"] == client.get("/api/overview").json()["streak_days"] == 3


# ---------------------------------------------------------------- 规模：查询次数与耗时
def count_statements(owner, mistakes):
    statements = []
    with connect() as conn:
        conn.set_trace_callback(statements.append)
        conn.execute("BEGIN")
        stats_summary.summary(conn, owner, "Asia/Shanghai", TODAY, 30)
    return len([text for text in statements if text.lstrip().upper().startswith("SELECT")])


def test_query_count_does_not_grow_with_the_number_of_reviews(client, me):
    mistake = seed_mistakes(me)[0]
    seed_reviews(mistake, *[at(TODAY - timedelta(days=offset % 40)) for offset in range(10)])
    few = count_statements(me, mistake)
    seed_reviews(mistake, *[at(TODAY - timedelta(days=offset % 40), hour=offset % 24) for offset in range(3000)])
    assert count_statements(me, mistake) == few
    assert few <= 5


def test_sixty_thousand_reviews_answer_quickly(client, me):
    mistakes = seed_mistakes(me, count=600)
    rng = random.Random(7)
    rows = []
    for index in range(60_000):
        day = TODAY - timedelta(days=rng.randrange(0, 400))
        rows.append((mistakes[index % 600], rng.randrange(0, 6),
                     at(day, hour=rng.randrange(24), minute=rng.randrange(60)), TODAY.isoformat()))
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)", rows
        )
    started = time.perf_counter()
    data = summary(client, days=90)
    elapsed = time.perf_counter() - started
    print(f"summary over 60k reviews: {elapsed * 1000:.0f} ms")
    assert data["reviews"]["current"] > 0 and data["retention"]["current"]["rate"] is not None
    assert elapsed < 1.5, f"too slow: {elapsed:.2f}s"
