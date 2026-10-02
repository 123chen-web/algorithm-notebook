"""总览与热力图接口：纯统计，按用户本地日分桶，不调用 AI、不占额度。"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import ai
import main
from activity import activity_summary
from db import connect
from test_app import client, new_problem, register


TODAY = date(2026, 9, 19)  # 星期六；跟 test_app.client 冻结的用户本地日期一致。
THIS_MONDAY = date(2026, 9, 14)
ACTIVITY = "/api/stats/activity"
OVERVIEW = "/api/overview"


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch, tmp_path):
    def unexpected_call(*args, **kwargs):
        pytest.fail("总览与热力图是纯统计，不能调用 AI")

    monkeypatch.setattr(ai, "generate", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)
    monkeypatch.setattr(ai, "recognize_photo", unexpected_call)
    # 小组预览会读头像文件，放进测试专用的临时目录。
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))


def user_id(client):
    return client.get("/api/me").json()["id"]


def seed_problem(owner_id, created_at, zone="算法", mistake_count=1, due_date=None):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '二分边界', ?, 'Python', '', '', ?)",
            (owner_id, zone, created_at),
        ).lastrowid
        return [
            conn.execute(
                "INSERT INTO mistakes(problem_id, description, due_date) "
                "VALUES (?, '遗漏边界条件', ?)",
                (problem_id, due_date or TODAY.isoformat()),
            ).lastrowid
            for _ in range(mistake_count)
        ]


def seed_reviews(mistake_id, timestamps):
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, 3, ?, ?)",
            [(mistake_id, value, TODAY.isoformat()) for value in timestamps],
        )


def at(day, hour=4, minute=0, second=0):
    """某个 UTC 时刻；默认 04:00 UTC = 上海 12:00，稳稳落在当天。"""
    return datetime(day.year, day.month, day.day, hour, minute, second,
                    tzinfo=timezone.utc).isoformat()


def activity(client, **params):
    response = client.get(ACTIVITY, params=params)
    assert response.status_code == 200
    return response.json()


def overview(client):
    response = client.get(OVERVIEW)
    assert response.status_code == 200
    return response.json()


def day_map(data):
    return {item["date"]: (item["reviews"], item["records"]) for item in data["days"]}


# ---------------------------------------------------------------- /api/stats/activity


@pytest.mark.parametrize("path", [ACTIVITY, OVERVIEW])
def test_requires_login(client, path):
    assert client.get(path).status_code == 401


def test_calendar_week_helper_starts_on_monday():
    assert TODAY.weekday() == 5  # 周六
    assert THIS_MONDAY.weekday() == 0
    assert activity_summary({}, {}, TODAY, 1)["from"] == THIS_MONDAY.isoformat()


def test_empty_account_gets_an_empty_heatmap_with_a_26_week_window(client):
    register(client)
    data = activity(client)
    assert data == {
        "today": "2026-09-19",
        "from": "2026-03-23",
        "weeks": 26,
        "days": [],
        "totals": {"reviews": 0, "records": 0, "active_days": 0},
        "streak_days": 0,
    }


def test_days_are_bucketed_by_the_users_local_date(client):
    register(client)
    owner = user_id(client)
    mistakes = seed_problem(owner, at(TODAY), mistake_count=2)
    seed_reviews(mistakes[0], [
        at(TODAY),
        at(TODAY, 1),
        at(TODAY - timedelta(days=2)),
    ])
    data = activity(client)
    assert day_map(data) == {
        "2026-09-17": (1, 0),
        "2026-09-19": (2, 1),  # 两条易错点同属一道题，只算一条新增记录
    }
    assert data["totals"] == {"reviews": 3, "records": 1, "active_days": 2}
    assert [item["date"] for item in data["days"]] == sorted(day_map(data))


def test_local_midnight_decides_the_day_not_utc(client):
    register(client)
    owner = user_id(client)
    mistakes = seed_problem(owner, at(TODAY, 20))  # 上海次日 04:00 → 已过今天，不计入
    seed_reviews(mistakes[0], [
        at(TODAY - timedelta(days=1), 15, 59, 59),  # 上海 9-18 23:59:59
        at(TODAY - timedelta(days=1), 16, 0, 0),    # 上海 9-19 00:00:00
        at(TODAY, 15, 59, 59),                      # 上海 9-19 23:59:59
        at(TODAY, 16, 0, 0),                        # 上海 9-20 00:00:00 → 未来，不计入
    ])
    data = activity(client)
    assert day_map(data) == {"2026-09-18": (1, 0), "2026-09-19": (2, 0)}
    assert data["totals"]["reviews"] == 3


def test_weeks_parameter_limits_the_window_to_whole_monday_weeks(client):
    register(client)
    owner = user_id(client)
    before = seed_problem(owner, at(THIS_MONDAY - timedelta(days=1)))
    seed_reviews(before[0], [at(THIS_MONDAY - timedelta(days=1)), at(THIS_MONDAY)])
    one_week = activity(client, weeks=1)
    assert one_week["from"] == "2026-09-14"
    assert one_week["weeks"] == 1
    assert day_map(one_week) == {"2026-09-14": (1, 0)}
    two_weeks = activity(client, weeks=2)
    assert two_weeks["from"] == "2026-09-07"
    assert day_map(two_weeks) == {"2026-09-13": (1, 1), "2026-09-14": (1, 0)}
    assert activity(client, weeks=52)["from"] == "2025-09-22"


@pytest.mark.parametrize("weeks", ["0", "53", "-1", "abc", "1.5"])
def test_weeks_parameter_is_validated(client, weeks):
    register(client)
    assert client.get(ACTIVITY, params={"weeks": weeks}).status_code == 422


def test_records_only_count_problems_that_have_a_mistake(client):
    register(client)
    owner = user_id(client)
    seed_problem(owner, at(TODAY), mistake_count=0)
    seed_problem(owner, at(TODAY), mistake_count=1)
    seed_problem(owner, at(TODAY), mistake_count=3)
    assert day_map(activity(client)) == {"2026-09-19": (0, 2)}


def test_streak_matches_the_weekly_recap_and_keeps_a_grace_day(client):
    register(client)
    owner = user_id(client)
    mistake = seed_problem(owner, at(TODAY - timedelta(days=9)))[0]
    seed_reviews(mistake, [at(TODAY - timedelta(days=n)) for n in (1, 2, 3, 5)])
    data = activity(client)
    assert data["streak_days"] == 3  # 今天还没复习，昨天起连续 3 天，不清零
    assert client.get("/api/insights/weekly-recap").json()["current_streak_days"] == 3
    seed_reviews(mistake, [at(TODAY)])
    assert activity(client)["streak_days"] == 4


def test_a_gap_of_two_days_resets_the_streak(client):
    register(client)
    owner = user_id(client)
    mistake = seed_problem(owner, at(TODAY - timedelta(days=9)))[0]
    seed_reviews(mistake, [at(TODAY - timedelta(days=2)), at(TODAY - timedelta(days=3))])
    assert activity(client)["streak_days"] == 0


def test_other_users_activity_is_never_included(client):
    register(client, "alice")
    alice = user_id(client)
    seed_reviews(seed_problem(alice, at(TODAY))[0], [at(TODAY)])
    client.post("/api/auth/logout")
    register(client, "bob")
    assert activity(client)["days"] == []
    assert activity(client)["totals"]["active_days"] == 0
    assert overview(client)["week"] == {"reviews": 0, "prev_reviews": 0, "records": 0}


def test_activity_endpoint_does_not_write_or_spend_quota(client):
    register(client)
    owner = user_id(client)
    seed_problem(owner, at(TODAY))
    with connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0]
    activity(client)
    activity(client, weeks=3)
    overview(client)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0] == before
    assert client.get("/api/me").json()["ai_daily_used"] == 0


# ---------------------------------------------------------------- /api/overview


EMPTY_OVERVIEW = {
    "today": "2026-09-19",
    "due_count": 0,
    "overdue_count": 0,
    "total_mistakes": 0,
    "streak_days": 0,
    "last7": [False] * 7,
    "zones": [],
    "week": {"reviews": 0, "prev_reviews": 0, "records": 0},
    "due_preview": [],
    "group_count": 0,
    "groups_preview": [],
    "weakness": {"status": "insufficient_data", "mistake_count": 0, "minimum_mistakes": 5, "top": None},
    "hot_post": None,
}


def test_new_account_gets_a_complete_but_empty_overview(client):
    register(client)
    assert overview(client) == EMPTY_OVERVIEW


def test_trial_account_can_read_the_overview(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    assert overview(client) == EMPTY_OVERVIEW
    assert activity(client)["days"] == []


def test_counts_and_zone_breakdown_follow_the_review_schedule(client):
    register(client)
    new_problem(client, "算法")
    new_problem(client, "高等数学")
    new_problem(client, "算法")
    owner = user_id(client)
    seed_problem(owner, at(TODAY), "前端", due_date="2026-09-25")  # 还没到期
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET due_date = '2026-09-16' WHERE id = "
            "(SELECT MIN(id) FROM mistakes)"
        )
    data = overview(client)
    assert data["total_mistakes"] == 7
    assert data["due_count"] == 6
    assert data["overdue_count"] == 1
    assert data["zones"] == [
        {"zone": "算法", "total": 4, "due": 4},
        {"zone": "高等数学", "total": 2, "due": 2},
        {"zone": "前端", "total": 1, "due": 0},
    ]
    due_items = client.get("/api/mistakes", params={"due_only": "true"}).json()["items"]
    assert data["due_count"] == len(due_items)


def test_due_preview_lists_the_five_most_overdue_with_overdue_days(client):
    register(client)
    owner = user_id(client)
    ids = []
    for days_late in (0, 4, 1, 9, 2, 3, 0):
        due = (TODAY - timedelta(days=days_late)).isoformat()
        ids.append(seed_problem(owner, at(TODAY), due_date=due)[0])
    seed_problem(owner, at(TODAY), due_date="2026-09-20")  # 明天才到期，不在预览里
    data = overview(client)
    assert data["due_count"] == 7
    assert [item["overdue_days"] for item in data["due_preview"]] == [9, 4, 3, 2, 1]
    assert [item["id"] for item in data["due_preview"]] == [ids[3], ids[1], ids[5], ids[4], ids[2]]
    first = data["due_preview"][0]
    assert set(first) == {
        "id", "problem_id", "title", "zone", "description",
        "due_date", "overdue_days", "repetitions",
    }
    assert first["due_date"] == "2026-09-10"
    assert first["title"] == "二分边界"
    assert first["description"] == "遗漏边界条件"
    assert first["repetitions"] == 0


def test_last7_marks_review_days_by_local_date_oldest_first(client):
    register(client)
    owner = user_id(client)
    mistake = seed_problem(owner, at(TODAY - timedelta(days=20)))[0]
    seed_reviews(mistake, [
        at(date(2026, 9, 12), 15, 59, 59),  # 上海 9-12 23:59 → 窗口之外（7 天是 9-13 起）
        at(date(2026, 9, 12), 16, 0, 0),    # 上海 9-13 00:00 → 窗口第一天
        at(date(2026, 9, 15), 4),
        at(date(2026, 9, 19), 4),
    ])
    data = overview(client)
    assert data["last7"] == [True, False, True, False, False, False, True]
    assert data["streak_days"] == 1
    assert data["week"] == {"reviews": 3, "prev_reviews": 1, "records": 0}


def test_week_block_matches_the_weekly_recap_endpoint(client):
    register(client)
    owner = user_id(client)
    mistakes = seed_problem(owner, at(TODAY - timedelta(days=1)), mistake_count=2)
    seed_problem(owner, at(TODAY - timedelta(days=10)))
    seed_reviews(mistakes[0], [at(TODAY), at(TODAY - timedelta(days=9)), at(TODAY - timedelta(days=8))])
    recap = client.get("/api/insights/weekly-recap").json()
    assert overview(client)["week"] == {
        "reviews": recap["reviews_completed"],
        "prev_reviews": recap["previous_week"]["reviews_completed"],
        "records": recap["mistakes_recorded"],
    }
    assert overview(client)["week"] == {"reviews": 1, "prev_reviews": 2, "records": 2}


def test_other_users_data_never_leaks_into_the_overview(client):
    register(client, "alice")
    new_problem(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    assert overview(client) == EMPTY_OVERVIEW


# -- 小组预览


def create_group(client, name):
    response = client.post("/api/groups", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def test_groups_preview_is_limited_to_three_and_sorted_by_level_points(client):
    register(client)
    ids = [create_group(client, f"小组{index}") for index in range(4)]
    data = overview(client)
    assert data["group_count"] == 4
    assert len(data["groups_preview"]) == 3
    # 积分都是 0 时按小组 id 从小到大；确定顺序，刷新不会乱跳。
    assert [item["id"] for item in data["groups_preview"]] == ids[:3]
    first = data["groups_preview"][0]
    assert set(first) == {"id", "name", "member_count", "member_limit", "level"}
    assert first["member_count"] == 1
    assert first["member_limit"] == main.GROUP_MAX_MEMBERS
    assert first["level"]["points"] == 0


def test_groups_with_more_points_come_first_and_ties_follow_the_id(client, monkeypatch):
    register(client)

    def fake_group(group_id, points):
        return {
            "id": group_id, "name": f"组{group_id}", "member_count": 2,
            "member_limit": 10, "is_creator": False, "created_at": "x",
            "level": {"level": 1, "points": points}, "members_preview": [],
        }

    monkeypatch.setattr(main, "list_groups", lambda user: {"groups": [
        fake_group(4, 10), fake_group(3, 40), fake_group(9, 40), fake_group(1, 5),
    ]})
    data = overview(client)
    assert data["group_count"] == 4
    assert [item["id"] for item in data["groups_preview"]] == [3, 9, 4]
    assert data["groups_preview"][0] == {
        "id": 3, "name": "组3", "member_count": 2, "member_limit": 10,
        "level": {"level": 1, "points": 40},
    }


# -- 薄弱点


def seed_insight(owner_id, patterns):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO weakness_insights(user_id, content, created_at) VALUES (?, ?, ?)",
            (owner_id, json.dumps({"summary": "总结", "patterns": patterns}), main.utc_now()),
        )


def test_weakness_summary_progresses_with_the_users_history(client):
    register(client)
    owner = user_id(client)
    seed_problem(owner, at(TODAY), mistake_count=4)
    assert overview(client)["weakness"] == {
        "status": "insufficient_data", "mistake_count": 4, "minimum_mistakes": 5, "top": None,
    }
    seed_problem(owner, at(TODAY), mistake_count=1)
    assert overview(client)["weakness"] == {
        "status": "not_analyzed", "mistake_count": 5, "minimum_mistakes": 5, "top": None,
    }
    seed_insight(owner, [])
    assert overview(client)["weakness"] == {
        "status": "ready", "mistake_count": 5, "minimum_mistakes": 5, "top": None,
    }


def test_weakness_summary_exposes_only_the_top_pattern(client):
    register(client)
    owner = user_id(client)
    seed_problem(owner, at(TODAY), mistake_count=5)
    seed_insight(owner, [
        {"title": "区间不变量与循环边界脱节", "explanation": "长说明不应出现在总览里",
         "action": "x", "confidence": "较明确", "evidence": []},
        {"title": "第二个规律", "explanation": "e", "action": "x",
         "confidence": "待验证", "evidence": []},
    ])
    weakness = overview(client)["weakness"]
    assert weakness["top"] == {"title": "区间不变量与循环边界脱节", "confidence": "较明确"}
    assert "长说明" not in json.dumps(weakness, ensure_ascii=False)


# -- 讨论区热帖


def make_post(owner_id, title, created_at=None, deleted=False):
    with connect(write=True) as conn:
        return conn.execute(
            "INSERT INTO posts(user_id, title, body, created_at, deleted_at) "
            "VALUES (?, ?, '正文', ?, ?)",
            (owner_id, title, created_at or main.utc_now(),
             main.utc_now() if deleted else None),
        ).lastrowid


def make_comments(post_id, owner_id, count, deleted=0):
    with connect(write=True) as conn:
        for index in range(count):
            conn.execute(
                "INSERT INTO post_comments(post_id, user_id, body, created_at, deleted_at) "
                "VALUES (?, ?, '评论', ?, ?)",
                (post_id, owner_id, main.utc_now(),
                 main.utc_now() if index < deleted else None),
            )


def test_hot_post_is_the_most_commented_recent_post_and_needs_a_comment(client):
    register(client)
    owner = user_id(client)
    quiet = make_post(owner, "没人评论")
    assert overview(client)["hot_post"] is None
    busy = make_post(owner, "热闹的帖子")
    make_comments(busy, owner, 3)
    make_comments(quiet, owner, 1)
    data = overview(client)["hot_post"]
    assert data["id"] == busy
    assert data["title"] == "热闹的帖子"
    assert data["comment_count"] == 3
    assert data["username"] == "alice"
    assert set(data) == {"id", "title", "comment_count", "username", "created_at"}


def test_hot_post_ties_go_to_the_newer_post_and_ignore_deleted_content(client):
    register(client)
    owner = user_id(client)
    older = make_post(owner, "较早", created_at="2026-09-10T00:00:00+00:00")
    newer = make_post(owner, "较新", created_at="2026-09-11T00:00:00+00:00")
    gone = make_post(owner, "已删除的帖子", deleted=True)
    make_comments(older, owner, 2)
    make_comments(newer, owner, 2)
    make_comments(gone, owner, 9)
    # 帖子都不到 30 天内时用真实当前时间判断，这里的固定日期必然更早，改用相对现在的时间。
    recent = datetime.now(timezone.utc)
    with connect(write=True) as conn:
        conn.execute("UPDATE posts SET created_at = ? WHERE id = ?",
                     ((recent - timedelta(days=2)).isoformat(timespec="seconds"), older))
        conn.execute("UPDATE posts SET created_at = ? WHERE id = ?",
                     ((recent - timedelta(days=1)).isoformat(timespec="seconds"), newer))
    assert overview(client)["hot_post"]["id"] == newer
    # 被删除的评论不算数：较新的帖子 2 条里删掉 1 条，就输给较早的那条。
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = "
            "(SELECT MIN(id) FROM post_comments WHERE post_id = ?)",
            (main.utc_now(), newer),
        )
    assert overview(client)["hot_post"]["id"] == older


def test_hot_post_ignores_posts_older_than_thirty_days(client):
    register(client)
    owner = user_id(client)
    old = make_post(owner, "上个月的帖子",
                    created_at=(datetime.now(timezone.utc) - timedelta(days=31))
                    .isoformat(timespec="seconds"))
    make_comments(old, owner, 5)
    assert overview(client)["hot_post"] is None


def test_created_on_filter_uses_the_users_local_dates_on_both_sides_of_midnight(client):
    register(client)
    owner = user_id(client)
    before = seed_problem(owner, "2026-09-18T15:59:59+00:00")[0]  # 上海 9-18 23:59:59
    first = seed_problem(owner, "2026-09-18T16:00:00+00:00")[0]   # 上海 9-19 00:00:00
    last = seed_problem(owner, "2026-09-19T15:59:59+00:00")[0]    # 上海 9-19 23:59:59
    after = seed_problem(owner, "2026-09-19T16:00:00+00:00")[0]   # 上海 9-20 00:00:00

    def ids(day):
        response = client.get("/api/mistakes", params={"due_only": "false", "created_on": day})
        assert response.status_code == 200
        return {item["id"] for item in response.json()["items"]}

    assert ids("2026-09-19") == {first, last}
    assert ids("2026-09-18") == {before}
    assert ids("2026-09-20") == {after}
    assert ids("2026-09-21") == set()
    for bad in ("2026-13-40", "yesterday", "2026/09/19"):
        assert client.get("/api/mistakes", params={"created_on": bad}).status_code == 422
