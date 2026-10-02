"""SQL/API regressions for shared-member activity and timestamp boundaries."""
from datetime import date
import sqlite3

import pytest
from fastapi.testclient import TestClient

import group_levels
import main
from db import connect
from group_levels import points_by_user
from test_app import register
from test_groups import create_group


@pytest.fixture
def activity_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 9, 19))
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def insert_activity(user_id, created_at, review_times):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, 'group activity', '算法', 'Python', 'pass', 'test', ?)",
            (user_id, created_at),
        ).lastrowid
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) "
            "VALUES (?, 'group activity', '2099-01-01')", (problem_id,),
        ).lastrowid
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, 4, ?, '2099-01-01')",
            [(mistake_id, at) for at in review_times],
        )
    return problem_id, mistake_id


def set_activity_membership(group_id, user_id, joined_at, timezone_name="Asia/Shanghai"):
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (timezone_name, user_id))
        conn.execute(
            "UPDATE study_group_members SET joined_at = ? WHERE group_id = ? AND user_id = ?",
            (joined_at, group_id, user_id),
        )


def assert_api_points(activity_client, group_id, expected):
    response = activity_client.get(f"/api/groups/{group_id}")
    assert response.status_code == 200
    observed = response.json()
    assert observed["points"] == expected
    assert observed["level"]["points"] == expected
    assert observed["members"][0]["points"] == expected
    listed = activity_client.get("/api/groups")
    assert listed.status_code == 200
    groups = {group["id"]: group for group in listed.json()["groups"]}
    assert groups[group_id]["level"]["points"] == expected


class StreamingActivityCursor:
    def __init__(self, cursor, connection):
        self.cursor = cursor
        self.connection = connection
        self.time_key = next(
            column[0] for column in cursor.description
            if column[0] in {"reviewed_at", "created_at"}
        )

    def __iter__(self):
        for row in self.cursor:
            self.connection.read_rows[self.time_key] += 1
            yield row

    def fetchall(self):
        pytest.fail("Group activity must be streamed, never loaded with fetchall()")


class CountingActivityConnection:
    def __init__(self, connection):
        self.connection = connection
        self.queries = []
        self.read_rows = {"reviewed_at": 0, "created_at": 0}

    def execute(self, query, parameters=()):
        self.queries.append(query)
        return StreamingActivityCursor(self.connection.execute(query, parameters), self)


def test_shared_member_activity_streams_once_and_keeps_each_groups_daily_window(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    boundaries = {
        1: "2026-09-19T00:00:00+00:00",
        2: "2026-09-19T04:00:00+00:00",
        3: "2026-09-20T00:00:00+00:00",
    }
    review_times = [
        "2026-09-18T23:59:59.999999+00:00",
        *["2026-09-19T00:00:00+00:00"] * 12,
        *["2026-09-19T04:00:00+00:00"] * 2,
        "2026-09-19T16:00:00+00:00",
        "2026-09-20T00:00:00+00:00",
    ]
    problem_times = [
        "2026-09-18T00:00:00+00:00",
        *["2026-09-19T00:00:00+00:00"] * 4,
        "2026-09-19T04:00:00+00:00",
        "2026-09-19T16:00:00+00:00",
        "2026-09-20T00:00:00+00:00",
    ]
    try:
        conn.executescript("""
            CREATE TABLE users(id INTEGER, username TEXT, timezone TEXT, avatar_version INTEGER);
            CREATE TABLE study_group_members(group_id INTEGER, user_id INTEGER, joined_at TEXT);
            CREATE TABLE problems(id INTEGER, user_id INTEGER, created_at TEXT);
            CREATE TABLE mistakes(id INTEGER, problem_id INTEGER);
            CREATE TABLE reviews(mistake_id INTEGER, reviewed_at TEXT);
            INSERT INTO users VALUES (1, 'alice', 'Asia/Shanghai', 0);
            INSERT INTO mistakes VALUES (1, 1), (99, 99);
            INSERT INTO problems VALUES (99, 99, '2026-09-19T00:00:00+00:00');
            INSERT INTO reviews VALUES (99, '2026-09-19T00:00:00+00:00');
        """)
        conn.executemany(
            "INSERT INTO study_group_members VALUES (?, 1, ?)",
            list(boundaries.items()),
        )
        conn.executemany(
            "INSERT INTO problems VALUES (?, 1, ?)",
            list(enumerate(problem_times, 1)),
        )
        conn.executemany(
            "INSERT INTO reviews VALUES (1, ?)", [(at,) for at in review_times],
        )
        members = main.group_members_by_id(conn, tuple(boundaries))
        reviews = [{"user_id": 1, "reviewed_at": at} for at in review_times]
        problems = [{"user_id": 1, "created_at": at} for at in problem_times]
        expected = {
            group_id: points_by_user(rows, reviews, problems)
            for group_id, rows in members.items()
        }
        assert expected == {1: {1: 37}, 2: {1: 23}, 3: {1: 9}}
        calls = {"timestamp": 0, "local_day": 0}
        original_timestamp = group_levels._timestamp
        original_local_day = group_levels.today_in_timezone

        def counted_timestamp(value):
            calls["timestamp"] += 1
            return original_timestamp(value)

        def counted_local_day(timezone_name, occurred_at):
            calls["local_day"] += 1
            return original_local_day(timezone_name, occurred_at)

        monkeypatch.setattr(group_levels, "_timestamp", counted_timestamp)
        monkeypatch.setattr(group_levels, "today_in_timezone", counted_local_day)
        counting = CountingActivityConnection(conn)
        assert main.group_points_by_id(counting, members) == expected
        assert len(counting.queries) == 2
        assert counting.read_rows == {
            "reviewed_at": len(review_times), "created_at": len(problem_times),
        }
        activity_count = len(review_times) + len(problem_times)
        membership_count = sum(len(rows) for rows in members.values())
        assert calls == {
            "timestamp": membership_count + activity_count,
            "local_day": activity_count,
        }
    finally:
        conn.close()


@pytest.mark.parametrize(
    "joined_at,occurred_at",
    [
        ("2026-09-19T08:00:00+08:00", "2026-09-19T00:00:00+00:00"),
        ("2026-09-19T00:00:00.000000+00:00", "2026-09-19T00:00:00+00:00"),
    ],
    ids=["different-offset", "different-precision"],
)
def test_api_includes_equal_instants_with_different_iso_representations(
    activity_client, joined_at, occurred_at,
):
    alice = register(activity_client)
    group = create_group(activity_client)
    set_activity_membership(group["id"], alice["id"], joined_at)
    insert_activity(alice["id"], occurred_at, [occurred_at])
    assert_api_points(activity_client, group["id"], 9)


def test_api_dst_repeated_hour_counts_reviews_in_one_local_day(activity_client):
    alice = register(activity_client)
    group = create_group(activity_client)
    set_activity_membership(
        group["id"], alice["id"], "2026-11-01T00:00:00+00:00", "America/Los_Angeles",
    )
    insert_activity(alice["id"], "2026-10-31T00:00:00+00:00", [
        "2026-11-01T08:30:00+00:00", "2026-11-01T09:30:00+00:00",
    ])
    assert_api_points(activity_client, group["id"], 7)


def test_api_deleting_mistake_then_problem_removes_their_current_points(activity_client):
    alice = register(activity_client)
    group = create_group(activity_client)
    boundary = "2026-09-19T00:00:00+00:00"
    set_activity_membership(group["id"], alice["id"], boundary)
    problem_id, mistake_id = insert_activity(alice["id"], boundary, [boundary])
    assert_api_points(activity_client, group["id"], 9)
    response = activity_client.delete(f"/api/mistakes/{mistake_id}")
    assert response.status_code == 200
    assert_api_points(activity_client, group["id"], 3)
    response = activity_client.delete(f"/api/problems/{problem_id}")
    assert response.status_code == 200
    assert_api_points(activity_client, group["id"], 0)
