"""针对 GET /api/leaderboard 连续打卡排行榜的测试。

client fixture 把 main.today_for 整体 mock 成固定返回 2026-09-19（见
test_app.py），所以这里的"今天"锚点是确定的；但 reviews.reviewed_at 到
当地日期的换算走的是真实的 today_in_timezone/zoneinfo，不受这个 mock
影响，可以用来验证跨时区边界是否算对。
"""
import main
from db import connect
from test_app import client, new_problem, register


def insert_review(mistake_id, reviewed_at, quality=4):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, ?, ?, '2099-01-01')",
            (mistake_id, quality, reviewed_at),
        )


def test_streak_counts_consecutive_local_days_ending_today(client):
    register(client)
    mistake_id = new_problem(client)[0]
    for day in ("2026-09-17", "2026-09-18", "2026-09-19"):
        insert_review(mistake_id, f"{day}T04:00:00+00:00")

    assert client.get("/api/leaderboard").json()["me"] == {
        "streak_days": 3, "rank": 1, "is_trial": False,
    }


def test_streak_still_counts_when_today_not_reviewed_yet(client):
    register(client)
    mistake_id = new_problem(client)[0]
    for day in ("2026-09-17", "2026-09-18"):
        insert_review(mistake_id, f"{day}T04:00:00+00:00")

    # 昨天有打卡、今天还没打卡：不能因为今天没过完就直接清零。
    assert client.get("/api/leaderboard").json()["me"] == {
        "streak_days": 2, "rank": 1, "is_trial": False,
    }


def test_streak_resets_after_a_missed_day(client):
    register(client)
    mistake_id = new_problem(client)[0]
    insert_review(mistake_id, "2026-09-16T04:00:00+00:00")

    body = client.get("/api/leaderboard").json()
    assert body["me"] == {"streak_days": 0, "rank": None, "is_trial": False}
    assert body["entries"] == []


def test_same_local_day_reviews_do_not_double_count(client):
    register(client)
    mistake_id = new_problem(client)[0]
    insert_review(mistake_id, "2026-09-19T01:00:00+00:00")
    insert_review(mistake_id, "2026-09-19T09:00:00+00:00", quality=5)

    assert client.get("/api/leaderboard").json()["me"] == {
        "streak_days": 1, "rank": 1, "is_trial": False,
    }


def test_streak_uses_users_own_timezone_not_raw_utc_calendar_date(client):
    user = register(client, "lauser")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE id = ?",
            ("America/Los_Angeles", user["id"]),
        )
    mistake_id = new_problem(client)[0]
    # 两条记录的 UTC 日期都是 09-19，但洛杉矶（UTC-7）本地日期分别是
    # 09-18 和 09-19；如果实现漏掉时区转换、直接按 UTC 日期分组，两条会
    # 被误判成同一天，得到 streak=1 而不是正确的 2。
    insert_review(mistake_id, "2026-09-19T01:00:00+00:00")
    insert_review(mistake_id, "2026-09-19T20:00:00+00:00")

    assert client.get("/api/leaderboard").json()["me"] == {
        "streak_days": 2, "rank": 1, "is_trial": False,
    }


def test_trial_account_streak_hidden_from_entries_but_visible_to_self(client):
    normal = register(client, "regular")
    mistake_id = new_problem(client)[0]
    insert_review(mistake_id, "2026-09-19T04:00:00+00:00")
    client.post("/api/auth/logout")

    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    trial_id = client.get("/api/me").json()["id"]
    trial_mistake_id = new_problem(client)[0]
    for day in ("2026-09-17", "2026-09-18", "2026-09-19"):
        insert_review(trial_mistake_id, f"{day}T04:00:00+00:00")

    body = client.get("/api/leaderboard").json()
    # 体验账号自己的连续天数（3 天，比正式账号的 1 天更长）仍然可见……
    assert body["me"] == {"streak_days": 3, "rank": None, "is_trial": True}
    # ……但不会挤进公开排行榜，正式账号的 1 天顶到第一名。
    assert body["entries"] == [
        {"rank": 1, "display_name": f"用户 #{normal['id']}", "streak_days": 1}
    ]
    assert all(
        entry["display_name"] != f"用户 #{trial_id}" for entry in body["entries"]
    )


def test_zero_streak_user_excluded_but_sees_own_zero(client):
    register(client)
    new_problem(client)  # 从不提交复习

    body = client.get("/api/leaderboard").json()
    assert body["me"] == {"streak_days": 0, "rank": None, "is_trial": False}
    assert body["entries"] == []


def test_entries_use_anonymized_display_name_not_real_username(client):
    alice = register(client, "alice")
    mistake_id = new_problem(client)[0]
    insert_review(mistake_id, "2026-09-19T04:00:00+00:00")
    client.post("/api/auth/logout")
    register(client, "bob")

    entries = client.get("/api/leaderboard").json()["entries"]
    assert entries == [
        {"rank": 1, "display_name": f"用户 #{alice['id']}", "streak_days": 1}
    ]
    assert "alice" not in entries[0]["display_name"]


def test_ranking_orders_by_streak_desc_then_user_id_asc_on_ties(client):
    first = register(client, "first")
    first_mistake = new_problem(client)[0]
    insert_review(first_mistake, "2026-09-19T04:00:00+00:00")
    insert_review(first_mistake, "2026-09-18T04:00:00+00:00")
    client.post("/api/auth/logout")

    second = register(client, "second")
    second_mistake = new_problem(client)[0]
    insert_review(second_mistake, "2026-09-19T04:00:00+00:00")
    client.post("/api/auth/logout")

    third = register(client, "third")
    third_mistake = new_problem(client)[0]
    insert_review(third_mistake, "2026-09-19T04:00:00+00:00")

    entries = client.get("/api/leaderboard").json()["entries"]
    assert entries == [
        {"rank": 1, "display_name": f"用户 #{first['id']}", "streak_days": 2},
        {"rank": 2, "display_name": f"用户 #{second['id']}", "streak_days": 1},
        {"rank": 3, "display_name": f"用户 #{third['id']}", "streak_days": 1},
    ]


def test_leaderboard_truncates_and_still_reports_own_rank_beyond_cutoff(
    client, monkeypatch
):
    monkeypatch.setattr(main, "LEADERBOARD_SIZE", 2)
    ids = []
    for index, name in enumerate(("usera", "userb", "userc")):
        user = register(client, name)
        ids.append(user["id"])
        mistake_id = new_problem(client)[0]
        insert_review(mistake_id, "2026-09-19T04:00:00+00:00")
        if index < 2:
            client.post("/api/auth/logout")

    # 仍然是 "userc" 的登录态：自己排第 3，但截断到 2 条的公开列表里看不到自己。
    body = client.get("/api/leaderboard").json()
    # leaderboard_size 要跟着被 monkeypatch 改小的常量走，不能是写死的默认值，
    # 前端就是靠这个字段判断"未进入前 N 名"，不能让它跟真实截断阈值脱节。
    assert body["leaderboard_size"] == 2
    assert [entry["display_name"] for entry in body["entries"]] == [
        f"用户 #{ids[0]}", f"用户 #{ids[1]}",
    ]
    assert body["me"] == {"streak_days": 1, "rank": 3, "is_trial": False}
