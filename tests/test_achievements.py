from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import ai
import main
from db import connect
from test_app import client, mock_generated_practice, new_problem, register


ENDPOINT = "/api/achievements"
TODAY = date(2026, 9, 19)
FAMILIES = {
    "streak": ("current_streak_days", [3, 7, 30, 100], "天"),
    "mistakes": ("mistake_count", [1, 10, 50, 100], "条"),
    "zones": ("recorded_zone_count", [3, 5], "个分区"),
    "practice": ("generated_practice_count", [2, 20, 100], "道"),
    "analysis": ("has_weakness_analysis", [1], "次"),
}
EMPTY_METRICS = {
    "current_streak_days": 0,
    "mistake_count": 0,
    "recorded_zone_count": 0,
    "generated_practice_count": 0,
    "has_weakness_analysis": False,
}


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("徽章读取不能调用 AI；生成场景必须显式 mock")

    monkeypatch.setattr(ai, "generate", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)


def payload(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    return response.json()


def badges_by_key(data):
    return {badge["key"]: badge for badge in data["achievements"]}


def locked_message(category, remaining, target):
    if category == "streak":
        return f"再连续复习 {remaining} 天，达到 {target} 天打卡目标"
    if category == "mistakes":
        return f"再记录 {remaining} 条易错点"
    if category == "zones":
        return f"再到 {remaining} 个不同分区记录题目"
    if category == "practice":
        return f"再成功生成 {remaining} 道练习题"
    return "前往薄弱点分析，完成首次分析"


def assert_family(data, category, current):
    metric, targets, unit = FAMILIES[category]
    badges = badges_by_key(data)
    for target in targets:
        badge = badges[f"{category}_{target}"]
        remaining = max(0, target - current)
        assert badge["category"] == category
        assert badge["unlocked"] is (current >= target)
        assert badge["progress"] == {
            "metric": metric,
            "current": current,
            "target": target,
            "remaining": remaining,
            "unit": unit,
            "message": (
                "已达成" if current >= target
                else locked_message(category, remaining, target)
            ),
        }


def seed_problem(user_id, mistake_count=1, zone="算法"):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            """
            INSERT INTO problems(
                user_id, title, zone, language, code, thinking, created_at
            ) VALUES (?, '二分边界', ?, 'Python', '', '', ?)
            """,
            (user_id, zone, main.utc_now()),
        ).lastrowid
        ids = [
            conn.execute(
                "INSERT INTO mistakes(problem_id, description, due_date) "
                "VALUES (?, '遗漏边界条件', ?)",
                (problem_id, TODAY.isoformat()),
            ).lastrowid
            for _ in range(mistake_count)
        ]
    return ids


def seed_reviews(mistake_id, timestamps):
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, ?, ?, ?)",
            [
                (mistake_id, index % 6, timestamp, TODAY.isoformat())
                for index, timestamp in enumerate(timestamps)
            ],
        )


def seed_streak(mistake_id, count, last_day=TODAY):
    seed_reviews(mistake_id, [
        datetime.combine(
            last_day - timedelta(days=offset), datetime.min.time(), timezone.utc
        ).isoformat()
        for offset in range(count)
    ])


def seed_practice(mistake_id, count):
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO variants(mistake_id, description, model, created_at) "
            "VALUES (?, '练习题', 'mock-model', ?)",
            [(mistake_id, "2026-09-19T00:00:00+00:00")] * count,
        )


def seed_analysis(user_id, content="{}"):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO weakness_insights(user_id, content, created_at) "
            "VALUES (?, ?, ?)",
            (user_id, content, main.utc_now()),
        )


def test_achievements_require_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_new_user_gets_every_badge_and_clear_next_steps(client):
    register(client)
    data = payload(client)
    assert set(data) == {"metrics", "achievements"}
    assert data["metrics"] == EMPTY_METRICS
    assert data["metrics"]["has_weakness_analysis"] is False
    expected_keys = {
        f"{category}_{target}"
        for category, (_, targets, _) in FAMILIES.items()
        for target in targets
    }
    assert set(badges_by_key(data)) == expected_keys
    assert len(data["achievements"]) == len(expected_keys)
    for badge in data["achievements"]:
        assert set(badge) == {
            "key", "category", "name", "description", "unlocked", "progress"
        }
        assert badge["name"].strip()
        assert badge["description"].strip()
    for category in FAMILIES:
        assert_family(data, category, 0)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("target", [3, 7, 30, 100])
def test_streak_thresholds_progress_and_lower_tiers(client, target, offset):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id)[0]
    count = target + offset
    seed_streak(mistake_id, count)

    data = payload(client)
    assert data["metrics"]["current_streak_days"] == count
    assert_family(data, "streak", count)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("target", [1, 10, 50, 100])
def test_mistake_thresholds_count_points_not_problems(client, target, offset):
    user_id = register(client)["id"]
    count = target + offset
    seed_problem(user_id, mistake_count=count)

    data = payload(client)
    assert data["metrics"]["mistake_count"] == count
    assert_family(data, "mistakes", count)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("target", [3, 5])
def test_zone_thresholds_use_distinct_recorded_problem_zones(client, target, offset):
    user_id = register(client)["id"]
    count = target + offset
    for zone in main.PROBLEM_ZONES[:count]:
        seed_problem(user_id, mistake_count=0, zone=zone)
    seed_problem(user_id, mistake_count=5, zone=main.PROBLEM_ZONES[0])

    data = payload(client)
    assert data["metrics"]["recorded_zone_count"] == count
    assert_family(data, "zones", count)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("target", [2, 20, 100])
def test_practice_thresholds_count_persisted_questions(client, target, offset):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id)[0]
    count = target + offset
    seed_practice(mistake_id, count)

    data = payload(client)
    assert data["metrics"]["generated_practice_count"] == count
    assert_family(data, "practice", count)


@pytest.mark.parametrize("content", [None, "{}", "不是 JSON，也不判断分析质量"])
def test_analysis_badge_only_requires_an_existing_record(client, content):
    user_id = register(client)["id"]
    if content is not None:
        seed_analysis(user_id, content)

    data = payload(client)
    assert data["metrics"]["has_weakness_analysis"] is (content is not None)
    assert_family(data, "analysis", int(content is not None))


def test_metrics_and_badges_are_isolated_between_users(client):
    alice_id = register(client, "alice")["id"]
    alice_mistake = seed_problem(alice_id, mistake_count=2)[0]
    seed_streak(alice_mistake, 2)
    seed_practice(alice_mistake, 2)

    bob_id = register(client, "bob")["id"]
    bob_mistake = seed_problem(bob_id, mistake_count=100)[0]
    for zone in main.PROBLEM_ZONES[1:5]:
        seed_problem(bob_id, mistake_count=0, zone=zone)
    seed_streak(bob_mistake, 100)
    seed_practice(bob_mistake, 100)
    seed_analysis(bob_id)
    assert all(badge["unlocked"] for badge in payload(client)["achievements"])

    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "a-test-password-123",
    }).status_code == 200
    data = payload(client)
    assert data["metrics"] == {
        "current_streak_days": 2,
        "mistake_count": 2,
        "recorded_zone_count": 1,
        "generated_practice_count": 2,
        "has_weakness_analysis": False,
    }
    for category, current in {
        "streak": 2, "mistakes": 2, "zones": 1, "practice": 2, "analysis": 0,
    }.items():
        assert_family(data, category, current)


def test_failed_ai_attempt_does_not_count_as_generated_practice(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]

    def fail_generation(item):
        raise HTTPException(502, "模拟生成失败")

    monkeypatch.setattr(ai, "generate", fail_generation)
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 502
    with connect() as conn:
        assert conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()[0] == 1
    assert_family(payload(client), "practice", 0)

    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 201
    data = payload(client)
    assert data["metrics"]["generated_practice_count"] == 2
    assert_family(data, "practice", 2)


def test_successful_generations_in_same_second_are_not_deduplicated(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(main, "utc_now", lambda: "2026-09-19T00:00:00+00:00")
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    for _ in range(2):
        response = client.post(f"/api/mistakes/{mistake_id}/variants")
        assert response.status_code == 201
        assert len(response.json()["variants"]) == 2
    data = payload(client)
    assert data["metrics"]["generated_practice_count"] == 4
    assert_family(data, "practice", 4)


@pytest.mark.parametrize("tz,timestamps", [
    ("Asia/Shanghai", [
        "2026-09-17T15:30:00+00:00", "2026-09-17T16:30:00+00:00",
        "2026-09-18T16:30:00+00:00", "2026-09-18T17:30:00+00:00",
    ]),
    ("America/Los_Angeles", [
        "2026-09-18T06:30:00+00:00", "2026-09-18T08:30:00+00:00",
        "2026-09-19T08:30:00+00:00", "2026-09-19T09:30:00+00:00",
    ]),
])
def test_streak_uses_local_dates_all_qualities_and_deduplicates_days(client, tz, timestamps):
    user_id = register(client)["id"]
    first, second = new_problem(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (tz, user_id))
    seed_reviews(first, timestamps)
    seed_reviews(second, timestamps)

    data = payload(client)
    assert data["metrics"]["current_streak_days"] == 3
    assert_family(data, "streak", 3)


def test_streak_keeps_yesterday_grace_then_relocks_after_a_gap(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id)[0]
    seed_streak(mistake_id, 7)
    assert_family(payload(client), "streak", 7)

    monkeypatch.setattr(main, "today_for", lambda user: TODAY + timedelta(days=1))
    assert_family(payload(client), "streak", 7)

    monkeypatch.setattr(main, "today_for", lambda user: TODAY + timedelta(days=2))
    data = payload(client)
    assert data["metrics"]["current_streak_days"] == 0
    assert_family(data, "streak", 0)


def test_achievements_recompute_after_source_data_is_deleted(client):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id, mistake_count=10)[0]
    seed_streak(mistake_id, 7)
    seed_practice(mistake_id, 20)
    seed_analysis(user_id)
    before = payload(client)
    for key in ("streak_7", "mistakes_10", "practice_20", "analysis_1"):
        assert badges_by_key(before)[key]["unlocked"] is True

    with connect(write=True) as conn:
        conn.execute("DELETE FROM problems WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM weakness_insights WHERE user_id = ?", (user_id,))
    after = payload(client)
    assert after["metrics"] == EMPTY_METRICS
    for category in FAMILIES:
        assert_family(after, category, 0)


def test_achievements_are_read_only_and_ignore_unrelated_ai_usage(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id)[0]
    seed_streak(mistake_id, 3)
    seed_analysis(user_id)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 100)",
            (user_id, TODAY.isoformat()),
        )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with connect() as conn:
        before = list(conn.iterdump())

    first = payload(client)
    assert payload(client) == first
    assert first["metrics"]["generated_practice_count"] == 0
    assert first["metrics"]["has_weakness_analysis"] is True
    with connect() as conn:
        assert list(conn.iterdump()) == before
