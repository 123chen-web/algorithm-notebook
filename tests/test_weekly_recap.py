"""本周学习战报：按本地日期统计连续两周的现有数据，不调用 AI。"""

from datetime import date, timedelta

import pytest

import ai
import main
from db import connect
from learning_stats import current_streak
from test_app import client, register


ENDPOINT = "/api/insights/weekly-recap"
TODAY = date(2026, 9, 19)  # 跟 test_app.client 冻结的用户本地日期一致。
EMPTY_COUNTS = {
    "mistakes_recorded": 0,
    "reviews_completed": 0,
    "practice_generated": 0,
    "active_days": 0,
}
EMPTY_RECAP = {
    "week_start": "2026-09-13",
    "week_end": "2026-09-19",
    **EMPTY_COUNTS,
    "zones_touched": 0,
    "current_streak_days": 0,
    "previous_week": EMPTY_COUNTS,
}


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("学习战报是纯统计，不能调用 AI")

    monkeypatch.setattr(ai, "generate", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)
    monkeypatch.setattr(ai, "recognize_photo", unexpected_call)


def timestamp(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat() + "T00:00:00+00:00"


def seed_problem(user_id, created_at, zone="算法", mistake_count=1):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '二分边界', ?, 'Python', '', '', ?)",
            (user_id, zone, created_at),
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
                (mistake_id, index % 6, value, TODAY.isoformat())
                for index, value in enumerate(timestamps)
            ],
        )


def seed_practice(mistake_id, timestamps):
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO variants(mistake_id, description, model, created_at) "
            "VALUES (?, '练习题', 'mock-model', ?)",
            [(mistake_id, value) for value in timestamps],
        )


def seed_activity(user_id, created_at, zone="算法"):
    mistake_id = seed_problem(user_id, created_at, zone)[0]
    seed_reviews(mistake_id, [created_at])
    seed_practice(mistake_id, [created_at])
    return mistake_id


def payload(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    return response.json()


def test_weekly_recap_requires_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_new_user_gets_zero_counts_and_both_window_dates(client):
    register(client)
    assert payload(client) == EMPTY_RECAP


@pytest.mark.parametrize("days_ago,current,previous", [
    (-1, 0, 0),
    (0, 1, 0),
    (6, 1, 0),
    (7, 0, 1),
    (13, 0, 1),
    (14, 0, 0),
])
def test_windows_include_both_ends_without_overlap_or_future_data(
    client, days_ago, current, previous,
):
    user_id = register(client)["id"]
    seed_activity(user_id, timestamp(days_ago))

    data = payload(client)
    assert data["week_start"] == "2026-09-13"
    assert data["week_end"] == "2026-09-19"
    for key in EMPTY_COUNTS:
        assert data[key] == current
        assert data["previous_week"][key] == previous
    assert data["zones_touched"] == current


def test_active_days_deduplicates_reviews_across_mistakes_and_counts_all_qualities(client):
    user_id = register(client)["id"]
    first, second = seed_problem(user_id, timestamp(30), mistake_count=2)
    seed_reviews(first, [timestamp(0)] * 6 + [timestamp(1), timestamp(7), timestamp(8)])
    seed_reviews(second, [timestamp(0), timestamp(7), timestamp(7)])

    data = payload(client)
    assert data["reviews_completed"] == 8
    assert data["active_days"] == 2
    assert data["previous_week"]["reviews_completed"] == 4
    assert data["previous_week"]["active_days"] == 2
    assert data["mistakes_recorded"] == 0


def test_zones_union_recording_and_reviews_without_counting_practice_only_zones(client):
    user_id = register(client)["id"]
    both = seed_problem(user_id, timestamp(0), zone="算法", mistake_count=2)[0]
    seed_reviews(both, [timestamp(0)] * 3)
    seed_problem(user_id, timestamp(1), zone="前端")  # 只有新增易错点。
    reviewed = seed_problem(user_id, timestamp(30), zone="后端")[0]
    seed_reviews(reviewed, [timestamp(2)])  # 老题本周只复习。
    practiced = seed_problem(user_id, timestamp(30), zone="数据库")[0]
    seed_practice(practiced, [timestamp(0)])
    seed_problem(user_id, timestamp(0), zone="系统设计", mistake_count=0)
    seed_activity(user_id, timestamp(7), zone="高等数学")

    data = payload(client)
    assert data["mistakes_recorded"] == 3
    assert data["reviews_completed"] == 4
    assert data["practice_generated"] == 1
    assert data["zones_touched"] == 3


def test_mistakes_use_parent_problem_created_at_and_count_points_not_problems(client):
    user_id = register(client)["id"]
    old = seed_problem(user_id, timestamp(14), mistake_count=5)
    seed_problem(user_id, timestamp(0), mistake_count=3)
    seed_problem(user_id, timestamp(7), mistake_count=2)
    with connect(write=True) as conn:
        # due_date 和 last_reviewed_at 都不是记录时间；mistakes 没有 created_at。
        assert "created_at" not in {
            row["name"] for row in conn.execute("PRAGMA table_info(mistakes)")
        }
        conn.executemany(
            "UPDATE mistakes SET due_date = ?, last_reviewed_at = ? WHERE id = ?",
            [(TODAY.isoformat(), timestamp(0), mistake_id) for mistake_id in old],
        )

    data = payload(client)
    assert data["mistakes_recorded"] == 3
    assert data["previous_week"]["mistakes_recorded"] == 2
    assert data["reviews_completed"] == 0


@pytest.mark.parametrize("last_day_offset,expected_streak", [(0, 12), (1, 12), (2, 0)])
def test_streak_reuses_global_days_with_yesterday_grace(client, last_day_offset, expected_streak):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id, timestamp(30))[0]
    offsets = range(last_day_offset, last_day_offset + 12)
    review_dates = {TODAY - timedelta(days=offset) for offset in offsets}
    seed_reviews(mistake_id, [timestamp(offset) for offset in offsets])

    data = payload(client)
    assert data["current_streak_days"] == current_streak(review_dates, TODAY) == expected_streak
    assert data["active_days"] == 7 - last_day_offset


@pytest.mark.parametrize("tz,timestamps", [
    ("Asia/Shanghai", [
        "2026-09-05T15:59:59+00:00",  # 本地 9 月 5 日：上周之前。
        "2026-09-05T16:00:00+00:00",  # 本地 9 月 6 日：上周首日。
        "2026-09-12T15:59:59+00:00",  # 本地 9 月 12 日：上周末日。
        "2026-09-12T16:00:00+00:00",  # 本地 9 月 13 日：本周首日。
        "2026-09-19T15:59:59+00:00",  # 本地 9 月 19 日：今天。
        "2026-09-19T16:00:00+00:00",  # 本地 9 月 20 日：未来。
    ]),
    ("America/Los_Angeles", [
        "2026-09-06T06:59:59+00:00",
        "2026-09-06T07:00:00+00:00",
        "2026-09-13T06:59:59+00:00",
        "2026-09-13T07:00:00+00:00",
        "2026-09-20T06:59:59+00:00",
        "2026-09-20T07:00:00+00:00",
    ]),
])
def test_all_timestamp_sources_use_user_local_dates(client, tz, timestamps):
    user_id = register(client)["id"]
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (tz, user_id))
    for value in timestamps:
        seed_activity(user_id, value)

    data = payload(client)
    for key in EMPTY_COUNTS:
        assert data[key] == 2
        assert data["previous_week"][key] == 2
    assert data["zones_touched"] == 1
    assert data["current_streak_days"] == 1


def test_generated_practice_counts_variant_rows_with_their_own_timestamps(client):
    user_id = register(client)["id"]
    mistake_id = seed_problem(user_id, timestamp(30))[0]
    seed_practice(mistake_id, [timestamp(0)] * 4 + [timestamp(7)] * 2)

    data = payload(client)
    assert data["practice_generated"] == 4
    assert data["previous_week"]["practice_generated"] == 2
    assert data["mistakes_recorded"] == data["active_days"] == data["zones_touched"] == 0


def test_both_weeks_and_global_streak_are_isolated_between_users(client):
    alice_id = register(client, "alice")["id"]
    seed_activity(alice_id, timestamp(0))
    seed_activity(alice_id, timestamp(7))
    alice_data = payload(client)

    bob_id = register(client, "bob")["id"]
    bob_mistake = seed_problem(bob_id, timestamp(0), zone="前端", mistake_count=10)[0]
    seed_problem(bob_id, timestamp(7), zone="后端", mistake_count=5)
    seed_reviews(bob_mistake, [timestamp(offset) for offset in range(14)])
    seed_practice(bob_mistake, [timestamp(0)] * 4 + [timestamp(7)] * 3)
    assert payload(client)["current_streak_days"] == 14

    assert client.post("/api/auth/login", json={
        "username": "alice", "password": "a-test-password-123",
    }).status_code == 200
    assert payload(client) == alice_data


def test_recap_is_read_only_and_ignores_ai_usage(client, monkeypatch):
    user_id = register(client)["id"]
    seed_activity(user_id, timestamp(0))
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 100)",
            (user_id, TODAY.isoformat()),
        )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with connect() as conn:
        before = list(conn.iterdump())

    first = payload(client)
    assert first["practice_generated"] == 1
    assert payload(client) == first
    with connect() as conn:
        assert list(conn.iterdump()) == before


def test_deleted_source_records_disappear_from_recap(client):
    user_id = register(client)["id"]
    seed_activity(user_id, timestamp(0))
    seed_activity(user_id, timestamp(7))
    assert payload(client)["mistakes_recorded"] == 1

    with connect(write=True) as conn:
        conn.execute("DELETE FROM problems WHERE user_id = ?", (user_id,))
    assert payload(client) == EMPTY_RECAP


def test_endpoint_reads_all_metrics_from_one_explicit_snapshot(client, monkeypatch):
    user_id = register(client)["id"]
    seed_problem(user_id, timestamp(30))
    original_recap = main.weekly_recap
    inserted = False

    def recap_with_concurrent_insert(conn, *args):
        assert conn.in_transaction  # 非 write 的 connect() 不会自动开启事务。

        class InsertAfterFirstRead:
            def execute(self, sql, parameters=()):
                nonlocal inserted
                cursor = conn.execute(sql, parameters)
                if not inserted and sql.lstrip().upper().startswith("SELECT"):
                    inserted = True
                    # 首条 SELECT 已建立快照；另一连接提交三种新数据。
                    seed_activity(user_id, timestamp(0))
                return cursor

            def __getattr__(self, name):
                return getattr(conn, name)

        return original_recap(InsertAfterFirstRead(), *args)

    monkeypatch.setattr(main, "weekly_recap", recap_with_concurrent_insert)
    assert payload(client) == EMPTY_RECAP
    assert inserted
    after = payload(client)
    for key in EMPTY_COUNTS:
        assert after[key] == 1
    assert after["zones_touched"] == after["current_streak_days"] == 1
