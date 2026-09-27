"""GET /api/insights/growth：按分区统计的纯数据成长趋势，不调用 AI、不扣配额。"""
from datetime import date, timedelta

import pytest

from db import connect
from test_app import client, register

TODAY = date(2026, 9, 19)  # 跟 test_app.py 的 client fixture 冻结的"今天"保持一致。


def add_mistake_on(client, zone, days_ago):
    # 不用 test_app.new_problem()：它固定带 2 条 mistakes，会让这里的计数翻倍；
    # 这些测试需要精确控制"这个分区恰好新增了 1 条易错点"。
    response = client.post(
        "/api/problems",
        json={
            "title": "题目",
            "zone": zone,
            "language": "Python" if zone in ("算法", "前端", "后端", "数据库", "系统设计") else "",
            "code": "pass",
            "thinking": "思路",
            "mistakes": ["易错点"],
        },
    )
    assert response.status_code == 201
    mistake_id = response.json()["mistake_ids"][0]
    created_at = (TODAY - timedelta(days=days_ago)).isoformat() + "T00:00:00+00:00"
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "SELECT problem_id FROM mistakes WHERE id = ?", (mistake_id,)
        ).fetchone()["problem_id"]
        conn.execute(
            "UPDATE problems SET created_at = ? WHERE id = ?", (created_at, problem_id)
        )
    return mistake_id


def zones_by_name(response):
    return {item["zone"]: item for item in response.json()["zones"]}


def test_empty_history_returns_empty_list_without_error(client):
    register(client)
    response = client.get("/api/insights/growth")
    assert response.status_code == 200
    assert response.json() == {"zones": []}


def test_counts_and_days_since_last_mistake_per_zone(client):
    register(client)
    add_mistake_on(client, "算法", days_ago=0)
    add_mistake_on(client, "算法", days_ago=0)
    add_mistake_on(client, "算法", days_ago=14)
    add_mistake_on(client, "高等数学", days_ago=49)

    zones = zones_by_name(client.get("/api/insights/growth"))
    assert set(zones) == {"算法", "高等数学"}

    algorithm = zones["算法"]
    assert algorithm["total_mistakes"] == 3
    assert algorithm["days_since_last_mistake"] == 0
    assert algorithm["recent_30_days"] == 3
    assert algorithm["prior_30_days"] == 0
    assert algorithm["quiet_streak"] is False

    math = zones["高等数学"]
    assert math["total_mistakes"] == 1
    assert math["days_since_last_mistake"] == 49
    assert math["recent_30_days"] == 0
    assert math["prior_30_days"] == 1
    assert math["quiet_streak"] is False


@pytest.mark.parametrize(
    "days_ago,expected_recent,expected_prior",
    [
        (0, 1, 0),
        (29, 1, 0),
        (30, 0, 1),
        (59, 0, 1),
        (60, 0, 0),
    ],
)
def test_thirty_day_window_boundaries(client, days_ago, expected_recent, expected_prior):
    register(client)
    add_mistake_on(client, "算法", days_ago=days_ago)

    zone = zones_by_name(client.get("/api/insights/growth"))["算法"]
    assert zone["recent_30_days"] == expected_recent
    assert zone["prior_30_days"] == expected_prior
    assert zone["days_since_last_mistake"] == days_ago


def test_quiet_streak_requires_at_least_sixty_days_with_nothing_recent(client):
    register(client)
    add_mistake_on(client, "算法", days_ago=59)
    assert zones_by_name(client.get("/api/insights/growth"))["算法"]["quiet_streak"] is False

    add_mistake_on(client, "前端", days_ago=60)
    assert zones_by_name(client.get("/api/insights/growth"))["前端"]["quiet_streak"] is True

    add_mistake_on(client, "后端", days_ago=400)
    assert zones_by_name(client.get("/api/insights/growth"))["后端"]["quiet_streak"] is True


def test_quiet_streak_is_false_when_the_most_recent_mistake_in_zone_is_recent(client):
    # 该分区历史上有很老的记录，但最近又出现了新的——不能因为"总量老"就误判安静。
    register(client)
    add_mistake_on(client, "算法", days_ago=400)
    add_mistake_on(client, "算法", days_ago=5)

    zone = zones_by_name(client.get("/api/insights/growth"))["算法"]
    assert zone["total_mistakes"] == 2
    assert zone["days_since_last_mistake"] == 5
    assert zone["quiet_streak"] is False


def test_results_are_isolated_between_users(client):
    register(client, "alice")
    add_mistake_on(client, "算法", days_ago=0)
    client.post("/api/auth/logout")

    register(client, "bob")
    add_mistake_on(client, "前端", days_ago=0)

    bob_zones = zones_by_name(client.get("/api/insights/growth"))
    assert set(bob_zones) == {"前端"}

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    )
    alice_zones = zones_by_name(client.get("/api/insights/growth"))
    assert set(alice_zones) == {"算法"}


def test_requires_authentication(client):
    response = client.get("/api/insights/growth")
    assert response.status_code == 401


def create_background_user_with_mistakes(username, zone, mistake_count):
    # 直接插库造"别的用户"：这些用户只用来让全站聚合统计有数据，不需要
    # 真的注册登录，密码哈希是占位符，不会被拿来验证任何东西。
    created_at = TODAY.isoformat() + "T00:00:00+00:00"
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES (?, 'unused', 'Asia/Shanghai', ?)",
            (username, created_at),
        )
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, ?, ?, 'Python', 'pass', '思路', ?)",
            (user_id, f"{username} 的题目", zone, created_at),
        ).lastrowid
        conn.executemany(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
            [(problem_id, f"易错点 {i}", TODAY.isoformat()) for i in range(mistake_count)],
        )


def test_community_ratio_hidden_below_minimum_cohort(client):
    register(client)
    add_mistake_on(client, "算法", days_ago=0)
    # 加上当前用户自己一共只有 3 个用户在这个分区有记录，低于最小样本阈值 5。
    create_background_user_with_mistakes("bg_user_1", "算法", mistake_count=5)
    create_background_user_with_mistakes("bg_user_2", "算法", mistake_count=1)

    zone = zones_by_name(client.get("/api/insights/growth"))["算法"]
    assert zone["community_sample_size"] == 3
    assert zone["community_struggling_ratio"] is None


def test_community_ratio_computed_at_minimum_cohort(client):
    register(client)
    add_mistake_on(client, "算法", days_ago=0)  # 当前用户只有 1 条，够不上"反复出错"。
    # 连同当前用户，这个分区恰好 5 个用户有记录：3 个达到 struggling 阈值(>=3 条)。
    create_background_user_with_mistakes("bg_user_1", "算法", mistake_count=3)
    create_background_user_with_mistakes("bg_user_2", "算法", mistake_count=4)
    create_background_user_with_mistakes("bg_user_3", "算法", mistake_count=3)
    create_background_user_with_mistakes("bg_user_4", "算法", mistake_count=1)

    zone = zones_by_name(client.get("/api/insights/growth"))["算法"]
    assert zone["community_sample_size"] == 5
    assert zone["community_struggling_ratio"] == 0.6


def test_community_stats_are_per_zone_and_do_not_leak_into_unrelated_zones(client):
    register(client)
    add_mistake_on(client, "前端", days_ago=0)
    for i in range(5):
        create_background_user_with_mistakes(f"bg_algo_{i}", "算法", mistake_count=3)

    zone = zones_by_name(client.get("/api/insights/growth"))["前端"]
    # 当前用户是这个分区唯一有记录的用户，不该被"算法"分区的样本量污染。
    assert zone["community_sample_size"] == 1
    assert zone["community_struggling_ratio"] is None


def test_community_response_never_exposes_other_users_identifying_data(client):
    register(client, "alice")
    add_mistake_on(client, "算法", days_ago=0)
    create_background_user_with_mistakes("bob_secret_username", "算法", mistake_count=4)
    create_background_user_with_mistakes("carol_secret_username", "算法", mistake_count=1)
    for i in range(3):
        create_background_user_with_mistakes(f"filler_user_{i}", "算法", mistake_count=1)

    response = client.get("/api/insights/growth")
    body = response.json()
    raw_text = response.text

    # 明确的白名单：每个 zone 条目只能出现这些字段，不能夹带用户名或内容。
    allowed_keys = {
        "zone", "total_mistakes", "days_since_last_mistake", "recent_30_days",
        "prior_30_days", "quiet_streak", "community_sample_size",
        "community_struggling_ratio",
    }
    for zone in body["zones"]:
        assert set(zone) == allowed_keys

    for leaked in ("bob_secret_username", "carol_secret_username", "filler_user_"):
        assert leaked not in raw_text


def test_own_growth_fields_unaffected_by_community_addition(client):
    register(client)
    add_mistake_on(client, "算法", days_ago=0)
    add_mistake_on(client, "算法", days_ago=14)

    zone = zones_by_name(client.get("/api/insights/growth"))["算法"]
    assert zone["total_mistakes"] == 2
    assert zone["days_since_last_mistake"] == 0
    assert zone["recent_30_days"] == 2
    assert zone["prior_30_days"] == 0
