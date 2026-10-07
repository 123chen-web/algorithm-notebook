"""每日推荐题：选题逻辑 + 接口。

today 由 client 夹具固定在 2026-09-19（Asia/Shanghai）。
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import cf_problems
import main
import recommend
from db import connect
from test_app import client, register


TODAY = date(2026, 9, 19)
PASSWORD = "a-test-password-123"


def at(day, hour=4):
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).isoformat()


def me(client):
    return client.get("/api/me").json()


def seed(owner, title="题", tags=(), created_days_ago=10, reviews=(), zone="算法"):
    """造一条易错点并贴标签。reviews = [(几天前, 质量, 间隔天数), ...]。"""
    created = TODAY - timedelta(days=created_days_ago)
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, ?, ?, 'Python', '', '', ?)",
            (owner, title, zone, at(created)),
        ).lastrowid
        if reviews:
            last_ago, _quality, interval = min(reviews, key=lambda item: item[0])
            due = TODAY - timedelta(days=last_ago) + timedelta(days=interval)
        else:
            due = created
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
            (problem_id, f"{title}的错因", due.isoformat()),
        ).lastrowid
        for days_ago, quality, interval in reviews:
            reviewed = TODAY - timedelta(days=days_ago)
            conn.execute(
                "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
                "VALUES (?, ?, ?, ?)",
                (mistake_id, quality, at(reviewed),
                 (reviewed + timedelta(days=interval)).isoformat()),
            )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for tag in tags:
            conn.execute(
                "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) "
                "VALUES (?, ?, ?, ?)",
                (mistake_id, owner, tag, now),
            )
    return mistake_id


def cf_problem(contest_id, index, rating, tags, name=None):
    return {
        "contestId": contest_id, "index": index,
        "name": name or f"Problem {contest_id}{index}",
        "rating": rating, "tags": tags,
    }


def write_cache(problems):
    cf_problems.save_cache(problems)


def get(client):
    response = client.get("/api/recommend")
    assert response.status_code == 200, response.text
    return response.json()


def test_requires_login(client):
    assert client.get("/api/recommend").status_code == 401
    assert client.post("/api/recommend/4/A", json={"state": "done"}).status_code == 401


def test_no_cache_gives_hint_not_500(client):
    register(client)
    data = get(client)
    assert data == {"items": [], "hint": "推荐题库还没准备好"}


def test_corrupt_cache_file_gives_hint(client):
    register(client)
    cache = cf_problems.cache_path()
    cache.write_text("{坏掉的 json", encoding="utf-8")
    data = get(client)
    assert data == {"items": [], "hint": "推荐题库还没准备好"}


def test_no_mistakes_gives_guidance_hint(client):
    register(client)
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    data = get(client)
    assert data == {"items": [], "hint": "先记几条错题，我才知道推荐什么"}


def test_unmapped_tags_are_ignored(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("不存在的标签",), created_days_ago=10)
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    data = get(client)
    assert data == {"items": [], "hint": "先记几条错题，我才知道推荐什么"}


def test_mapping_and_reason_use_user_tags(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, title="BFS题", tags=("BFS",), created_days_ago=10)
    for index in range(4):
        seed(owner, title=f"贪心题{index}", tags=("贪心",), created_days_ago=10)
    write_cache([
        cf_problem(4, "A", 800, ["graphs", "dfs and similar"], name="图题一"),
        cf_problem(4, "B", 900, ["graphs"], name="图题二"),
        cf_problem(5, "A", 1000, ["greedy"], name="贪心题"),
        cf_problem(6, "A", 800, ["math"], name="数学题"),
    ])
    data = get(client)
    assert data["hint"] == ""
    # 弱点分：greedy=4，graphs=1，dfs and similar=1 → 前 2 是 greedy、dfs and similar。
    by_id = {item["id"]: item for item in data["items"]}
    assert set(by_id) == {"5A", "4A"}
    assert by_id["5A"]["reason"] == "你在贪心上有4条未掌握的错题"
    assert by_id["4A"]["reason"] == "你在BFS上有1条未掌握的错题"
    assert by_id["4A"]["url"] == "https://codeforces.com/problemset/problem/4/A"
    assert by_id["4A"]["rating"] == 800
    assert by_id["4A"]["tags"] == ["graphs", "dfs and similar"]


def test_overlapping_chinese_tags_count_each_mistake_once(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS", "DFS"))
    with connect() as conn:
        scores = recommend.weakness_scores(conn, owner, "Asia/Shanghai", TODAY)
    assert scores["graphs"]["count"] == 1
    assert scores["dfs and similar"]["count"] == 1
    write_cache([cf_problem(4, "A", 800, ["graphs", "dfs and similar"])])
    assert get(client)["items"][0]["reason"] == "你在BFS上有1条未掌握的错题"


def test_recommendation_reason_uses_the_named_labels_actual_count(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",))
    seed(owner, tags=("BFS",))
    seed(owner, tags=("DFS",))
    with connect() as conn:
        scores = recommend.weakness_scores(conn, owner, "Asia/Shanghai", TODAY)
    assert scores["graphs"]["count"] == 3
    write_cache([cf_problem(4, "A", 800, ["graphs", "dfs and similar"])])
    assert get(client)["items"][0]["reason"] == "你在BFS上有2条未掌握的错题"


def test_default_band_excludes_hard_problems(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",), created_days_ago=10)
    write_cache([
        cf_problem(4, "A", 800, ["graphs"]),
        cf_problem(4, "B", 1300, ["graphs"]),
        cf_problem(4, "C", 900, ["graphs"]),
    ])
    data = get(client)
    ids = [item["id"] for item in data["items"]]
    assert "4B" not in ids
    assert set(ids) == {"4A", "4C"}


def test_problems_without_rating_are_never_picked(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",), created_days_ago=10)
    write_cache([
        {"contestId": 4, "index": "A", "name": "无分题",
         "rating": None, "tags": ["graphs"]},
        cf_problem(4, "B", 800, ["graphs"]),
    ])
    data = get(client)
    assert [item["id"] for item in data["items"]] == ["4B"]


def test_skilled_band_raises_ceiling_to_1400(client):
    register(client)
    owner = me(client)["id"]
    # 12 条已掌握的错题（近期复习过，保持率高）+ 2 条未掌握的 BFS 错题。
    for index in range(12):
        seed(owner, title=f"熟题{index}", created_days_ago=2,
             reviews=[(1, 5, 14)])
    for index in range(2):
        seed(owner, title=f"BFS题{index}", tags=("BFS",), created_days_ago=10)
    write_cache([
        cf_problem(4, "A", 800, ["graphs"]),
        cf_problem(4, "B", 1300, ["graphs"]),
        cf_problem(4, "C", 1500, ["graphs"]),
    ])
    data = get(client)
    ids = [item["id"] for item in data["items"]]
    assert "4B" in ids, "高掌握度用户难度上限应提到 1400"
    assert "4C" not in ids, "1400 以上仍然不选"


def test_band_unit_cases(client):
    register(client)
    owner = me(client)["id"]
    with connect() as conn:
        assert recommend.difficulty_band(conn, owner, "Asia/Shanghai", TODAY) == (800, 1200)
    for index in range(9):
        seed(owner, title=f"熟题{index}", created_days_ago=2, reviews=[(1, 5, 14)])
    with connect() as conn:
        # 只有 9 次复习记录，不够 10 次。
        assert recommend.difficulty_band(conn, owner, "Asia/Shanghai", TODAY) == (800, 1200)


def test_old_reviews_do_not_raise_the_difficulty_ceiling(client):
    register(client)
    owner = me(client)["id"]
    for index in range(10):
        seed(owner, title=f"Old {index}", created_days_ago=90, reviews=[(40, 5, 1000)])
    with connect() as conn:
        assert recommend.difficulty_band(conn, owner, "Asia/Shanghai", TODAY) == (800, 1200)


def test_recent_reviewed_mistakes_determine_recent_mastery(client):
    register(client)
    owner = me(client)["id"]
    for index in range(10):
        seed(owner, title=f"Recent {index}", created_days_ago=2, reviews=[(1, 5, 14)])
    for index in range(20):
        seed(owner, title=f"Never reviewed {index}", created_days_ago=90)
    with connect() as conn:
        assert recommend.difficulty_band(conn, owner, "Asia/Shanghai", TODAY) == (800, 1400)


def test_recent_review_window_uses_the_users_local_day(client):
    register(client)
    owner = me(client)["id"]
    # UTC Aug 20 evening is Aug 21 locally, within the last 30 local days.
    for index in range(10):
        mistake = seed(owner, title=f"Boundary {index}", created_days_ago=90)
        with connect(write=True) as conn:
            conn.execute("INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 5, ?, ?)",
                (mistake, "2026-08-20T20:00:00+00:00", "2029-01-01"))
    with connect() as conn:
        assert recommend.difficulty_band(conn, owner, "Asia/Shanghai", TODAY) == (800, 1400)
        assert recommend.difficulty_band(conn, owner, "UTC", TODAY) == (800, 1200)


def test_future_reviews_do_not_count_toward_ten_completed_reviews(client):
    register(client)
    owner = me(client)["id"]
    for index in range(9):
        seed(owner, title=f"Recent {index}", created_days_ago=2, reviews=[(1, 5, 14)])
    seed(owner, title="Future", created_days_ago=2, reviews=[(-1, 5, 14)])
    with connect() as conn:
        assert recommend.difficulty_band(conn, owner, "Asia/Shanghai", TODAY) == (800, 1200)


def test_excludes_previously_recommended(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",), created_days_ago=10)
    write_cache([
        cf_problem(4, "A", 800, ["graphs"]),
        cf_problem(4, "B", 800, ["graphs"]),
        cf_problem(4, "C", 800, ["graphs"]),
        cf_problem(4, "D", 800, ["graphs"]),
    ])
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO problem_recommendations(user_id, contest_id, idx, recommended_at, state)"
            " VALUES (?, 4, 'A', '2026-09-18T00:00:00+00:00', 'done')",
            (owner,),
        )
    data = get(client)
    ids = [item["id"] for item in data["items"]]
    assert "4A" not in ids
    assert set(ids) == {"4B", "4C", "4D"}


def test_same_day_requests_return_the_same_items(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",), created_days_ago=10)
    write_cache([
        cf_problem(4, "A", 800, ["graphs"]),
        cf_problem(4, "B", 800, ["graphs"]),
        cf_problem(4, "C", 800, ["graphs"]),
        cf_problem(4, "D", 800, ["graphs"]),
    ])
    first = get(client)
    second = get(client)
    assert first["items"] == second["items"]
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM problem_recommendations WHERE user_id = ?", (owner,)
        ).fetchone()[0]
    assert count == 3, "同一天重复请求不应重复落库"


def test_same_day_multitag_order_is_stable(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",))
    for _ in range(4):
        seed(owner, tags=("贪心",))
    write_cache([
        cf_problem(4, "A", 800, ["graphs", "dfs and similar"]),
        cf_problem(5, "A", 800, ["greedy"]),
        cf_problem(5, "B", 800, ["greedy"]),
    ])
    first = get(client)
    assert [item["id"] for item in first["items"]] == ["5A", "4A", "5B"]
    assert get(client) == first


def test_same_day_recommendations_survive_mastering_all_weak_points(client):
    register(client)
    owner = me(client)["id"]
    mistake = seed(owner, tags=("BFS",))
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    first = get(client)
    with connect(write=True) as conn:
        conn.execute("INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 5, ?, ?)",
            (mistake, at(TODAY), (TODAY + timedelta(days=14)).isoformat()))
    second = get(client)
    assert [item["id"] for item in second["items"]] == [item["id"] for item in first["items"]]
    assert second["items"][0]["name"] == first["items"][0]["name"]
    assert second["hint"] == ""


def test_same_day_cache_refresh_keeps_saved_problem_metadata(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",))
    original = [cf_problem(4, index, 800, ["graphs"], name=f"Original {index}")
                for index in ("A", "B", "C")]
    write_cache(original)
    first = get(client)
    write_cache([cf_problem(1, "A", 800, ["graphs"])] + original)
    assert get(client) == first


def test_same_day_recommendations_survive_tag_changes(client):
    register(client)
    owner = me(client)["id"]
    mistake = seed(owner, tags=("BFS",))
    write_cache([cf_problem(4, "A", 800, ["graphs"]), cf_problem(5, "A", 800, ["dp"])])
    first = get(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE mistake_tags SET tag = 'DP' WHERE mistake_id = ? AND user_id = ?", (mistake, owner))
    second = get(client)
    assert [item["id"] for item in second["items"]] == [item["id"] for item in first["items"]]
    assert second["items"][0]["name"] == first["items"][0]["name"]


def test_post_state_change_and_validation(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",), created_days_ago=10)
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    item = get(client)["items"][0]
    assert item["state"] == "new"
    response = client.post("/api/recommend/4/A", json={"state": "done"})
    assert response.status_code == 200
    assert get(client)["items"][0]["state"] == "done"
    response = client.post("/api/recommend/4/A", json={"state": "weird"})
    assert response.status_code == 422
    response = client.post("/api/recommend/4/A", json={})
    assert response.status_code == 422


def test_post_cannot_touch_others_records_or_missing(client):
    register(client)
    alice_id = me(client)["id"]
    with connect(write=True) as conn:
        other_id = conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at)"
            " VALUES ('bob', 'x', 'Asia/Shanghai', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        ).lastrowid
        conn.execute(
            "INSERT INTO problem_recommendations(user_id, contest_id, idx, recommended_at)"
            " VALUES (?, 99, 'Z', '2026-09-19T00:00:00+00:00')",
            (other_id,),
        )
    # 别人的记录：404 掩盖。
    assert client.post("/api/recommend/99/Z", json={"state": "done"}).status_code == 404
    # 根本不存在的记录：同样 404。
    assert client.post("/api/recommend/1/A", json={"state": "done"}).status_code == 404
    with connect() as conn:
        state = conn.execute(
            "SELECT state FROM problem_recommendations WHERE user_id = ?", (other_id,)
        ).fetchone()[0]
    assert state == "new", "别人的记录不能被改动"
    assert alice_id != other_id


def test_trial_account_can_view(client):
    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    data = get(client)
    assert data["hint"] == "先记几条错题，我才知道推荐什么"


def test_delete_account_removes_recommendations(client):
    user = register(client)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO problem_recommendations(user_id, contest_id, idx, recommended_at)"
            " VALUES (?, 4, 'A', '2026-09-19T00:00:00+00:00')",
            (user["id"],),
        )
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM problem_recommendations").fetchone()[0]
    assert count == 0


def test_get_is_rate_limited(client):
    register(client)
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    for _ in range(30):
        assert client.get("/api/recommend").status_code == 200
    assert client.get("/api/recommend").status_code == 429
