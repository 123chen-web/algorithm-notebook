"""Group levels: pure scoring rules, API contracts and membership boundaries."""
from datetime import datetime, timezone
import sqlite3

import pytest

import main
from db import connect
from group_levels import (
    CHECKIN_BONUS,
    LEVELS,
    RECORD_DAILY_CAP,
    RECORD_POINTS,
    REVIEW_DAILY_CAP,
    REVIEW_POINTS,
    level_summary,
    points_by_user,
)
from test_app import client, register
from test_groups import create_group, login


EXPECTED_LEVELS = [
    {"number": 1, "name": "初稿", "min_points": 0},
    {"number": 2, "name": "批注", "min_points": 120},
    {"number": 3, "name": "圈点", "min_points": 360},
    {"number": 4, "name": "朱批", "min_points": 800},
    {"number": 5, "name": "精批", "min_points": 1500},
    {"number": 6, "name": "优等", "min_points": 2600},
    {"number": 7, "name": "金榜", "min_points": 4200},
    {"number": 8, "name": "满分", "min_points": 6500},
]
EXPECTED_RULES = [
    {"key": "review", "label": "复习评分", "points": 1, "daily_cap": 10},
    {"key": "checkin", "label": "每日打卡", "points": 5, "daily_cap": 5},
    {"key": "record", "label": "新增题目记录", "points": 3, "daily_cap": 9},
]


def member(user_id=1, timezone_name="Asia/Shanghai", joined_at="2026-09-19T00:00:00+00:00"):
    return {"id": user_id, "timezone": timezone_name, "joined_at": joined_at}


def review(user_id=1, at="2026-09-19T01:00:00+00:00"):
    return {"user_id": user_id, "reviewed_at": at}


def problem(user_id=1, at="2026-09-19T01:00:00+00:00"):
    return {"user_id": user_id, "created_at": at}


def test_level_table_and_rule_constants_are_exact():
    assert list(LEVELS) == EXPECTED_LEVELS
    assert (REVIEW_POINTS, REVIEW_DAILY_CAP, CHECKIN_BONUS) == (1, 10, 5)
    assert (RECORD_POINTS, RECORD_DAILY_CAP) == (3, 9)


@pytest.mark.parametrize(
    "points,number",
    [(0, 1), (119, 1), (120, 2), (359, 2), (360, 3), (799, 3),
     (800, 4), (1499, 4), (1500, 5), (2599, 5), (2600, 6),
     (4199, 6), (4200, 7), (6499, 7), (6500, 8)],
)
def test_level_summary_every_threshold_boundary(points, number):
    summary = level_summary(points)
    current = EXPECTED_LEVELS[number - 1]
    following = EXPECTED_LEVELS[number] if number < 8 else None
    assert set(summary) == {
        "number", "name", "points", "floor", "next_name", "next_points",
        "points_to_next", "progress",
    }
    assert summary["number"] == number
    assert summary["name"] == current["name"]
    assert summary["points"] == points
    assert summary["floor"] == current["min_points"]
    if following:
        assert summary["next_name"] == following["name"]
        assert summary["next_points"] == following["min_points"]
        assert summary["points_to_next"] == following["min_points"] - points
        expected = ((points - current["min_points"])
                    / (following["min_points"] - current["min_points"]))
        assert summary["progress"] == pytest.approx(expected)
    else:
        assert summary["next_name"] is None
        assert summary["next_points"] is None
        assert summary["points_to_next"] is None
        assert summary["progress"] == 1.0


@pytest.mark.parametrize("points", [6500, 6501, 100000])
def test_level_summary_full_level_always_has_complete_progress(points):
    assert level_summary(points) == {
        "number": 8, "name": "满分", "points": points, "floor": 6500,
        "next_name": None, "next_points": None, "points_to_next": None,
        "progress": 1.0,
    }


@pytest.mark.parametrize("points", [60, 240, 580, 1150, 2050, 3400, 5350])
def test_level_summary_progress_is_within_current_interval(points):
    assert level_summary(points)["progress"] == 0.5


@pytest.mark.parametrize(
    "review_count,record_count,expected",
    [(0, 0, 0), (0, 1, 3), (0, 3, 9), (0, 7, 9),
     (1, 0, 6), (2, 0, 7), (10, 0, 15), (30, 0, 15),
     (1, 1, 9), (30, 7, 24)],
)
def test_points_by_user_caps_reviews_records_and_one_daily_checkin(
    review_count, record_count, expected,
):
    assert points_by_user(
        [member()], [review() for _ in range(review_count)],
        [problem() for _ in range(record_count)],
    ) == {1: expected}


def test_points_by_user_daily_caps_and_checkin_reset_per_day_and_member():
    reviews = [review(1, f"2026-09-{day}T01:00:00+00:00")
               for day in (19, 20) for _ in range(20)]
    records = [problem(1, f"2026-09-{day}T01:00:00+00:00")
               for day in (19, 20) for _ in range(5)]
    reviews.extend([review(2), review(2)])
    records.extend([problem(2), problem(2)])
    assert points_by_user([member(1), member(2), member(3)], reviews, records) == {
        1: 48, 2: 13, 3: 0,
    }


def test_points_by_user_excludes_before_joining_and_includes_exact_instant():
    before = "2026-09-18T23:59:59.999999+00:00"
    boundary = "2026-09-19T00:00:00+00:00"
    assert points_by_user([member()], [review(at=before)], [problem(at=before)]) == {1: 0}
    assert points_by_user(
        [member()], [review(at=boundary)], [problem(at=boundary)],
    ) == {1: 9}
    # Offset forms denote the same inclusive instant, rather than text order.
    assert points_by_user(
        [member(joined_at="2026-09-19T08:00:00+08:00")],
        [review(at=boundary)], [problem(at=boundary)],
    ) == {1: 9}


def test_points_by_user_utc_2330_is_next_local_day_in_shanghai():
    members = [member(1, "Asia/Shanghai", "2026-09-18T00:00:00+00:00"),
               member(2, "UTC", "2026-09-18T00:00:00+00:00"),
               member(3, "America/Los_Angeles", "2026-09-18T00:00:00+00:00")]
    reviews = [review(user_id, at)
               for user_id in (1, 2, 3)
               for at in ("2026-09-18T15:30:00+00:00", "2026-09-18T23:30:00+00:00")
               for _ in range(8)]
    records = [problem(user_id, at)
               for user_id in (1, 2, 3)
               for at in ("2026-09-18T15:30:00+00:00", "2026-09-18T23:30:00+00:00")
               for _ in range(2)]
    # Shanghai gets two local days: (8 + 5 + 6) twice. Others share one capped day.
    assert points_by_user(members, reviews, records) == {1: 38, 2: 24, 3: 24}


def test_points_by_user_counts_only_current_members_and_handles_empty_group():
    reviews = [review(1), review(2), review(99)]
    records = [problem(1), problem(2), problem(99)]
    assert points_by_user([member(1), member(3)], reviews, records) == {1: 9, 3: 0}
    assert points_by_user([], reviews, records) == {}


def test_points_by_user_accepts_utc_datetime_values_and_legacy_naive_timestamps():
    joined = datetime(2026, 9, 19, tzinfo=timezone.utc)
    assert points_by_user(
        [member(joined_at=joined)],
        [review(at=joined)], [problem(at="2026-09-19T00:00:00")],
    ) == {1: 9}


@pytest.fixture
def group_client(client, tmp_path, monkeypatch):
    # Do not inspect/create actual project avatars, or configure any AI service.
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return client


def insert_problem(user_id, created_at, review_times=()):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '计分题目', '算法', 'Python', 'pass', '思路', ?)",
            (user_id, created_at),
        ).lastrowid
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) "
            "VALUES (?, '易错点', '2099-01-01')", (problem_id,),
        ).lastrowid
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, ?, ?, '2099-01-01')",
            [(mistake_id, index % 6, at) for index, at in enumerate(review_times)],
        )
    return mistake_id


def insert_member(group_id, username, joined_at, timezone_name="Asia/Shanghai"):
    with connect(write=True) as conn:
        user_id = conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES (?, 'unused', ?, ?)",
            (username, timezone_name, joined_at),
        ).lastrowid
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user_id, joined_at),
        )
    return user_id


def set_joined_at(group_id, user_id, at):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE study_group_members SET joined_at = ? WHERE group_id = ? AND user_id = ?",
            (at, group_id, user_id),
        )


def detail(group_client, group_id):
    response = group_client.get(f"/api/groups/{group_id}")
    assert response.status_code == 200
    return response.json()


def test_group_levels_endpoint_requires_login_and_returns_complete_contract(group_client):
    response = group_client.get("/api/group-levels")
    assert response.status_code == 401
    register(group_client)
    response = group_client.get("/api/group-levels")
    assert response.status_code == 200
    assert response.json() == {
        "member_limit": 10, "levels": EXPECTED_LEVELS, "rules": EXPECTED_RULES,
    }


def test_group_detail_and_list_include_points_creators_and_real_avatar_presence(group_client):
    alice = register(group_client)
    group = create_group(group_client)
    bob_id = insert_member(group["id"], "bob", "2026-09-19T01:00:00+00:00", "UTC")
    set_joined_at(group["id"], alice["id"], "2026-09-19T00:00:00+00:00")
    # Review an old problem; score every quality, cap reviews at 10, and add one checkin.
    insert_problem(alice["id"], "2026-09-18T23:59:59+00:00", [
        "2026-09-18T23:59:59+00:00",
        *["2026-09-19T00:00:00+00:00"] * 15,
    ])
    for _ in range(4):
        insert_problem(alice["id"], "2026-09-19T00:00:00+00:00")
    insert_problem(bob_id, "2026-09-18T00:00:00+00:00", ["2026-09-19T04:00:00+00:00"])
    # Incremented cache versions without an actual avatar must stay false.
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET avatar_version = 2 WHERE id = ?", (alice["id"],))
        conn.execute("UPDATE users SET avatar_version = 9 WHERE id = ?", (bob_id,))
    main.avatar_path(alice["id"]).write_bytes(b"avatar-exists")
    observed = detail(group_client, group["id"])
    assert observed["member_limit"] == 10
    assert observed["points"] == 30
    assert observed["level"] == level_summary(30)
    assert observed["members"] == [
        {"id": alice["id"], "username": "alice", "current_streak_days": 1,
         "points": 24, "is_creator": True, "avatar_version": 2, "has_avatar": True},
        {"id": bob_id, "username": "bob", "current_streak_days": 1,
         "points": 6, "is_creator": False, "avatar_version": 9, "has_avatar": False},
    ]
    listed = group_client.get("/api/groups").json()["groups"][0]
    assert listed["member_limit"] == 10
    assert listed["level"] == observed["level"]
    assert listed["members_preview"] == [
        {"id": alice["id"], "username": "alice", "avatar_version": 2, "has_avatar": True},
        {"id": bob_id, "username": "bob", "avatar_version": 9, "has_avatar": False},
    ]
    assert "invite_code" not in listed
    assert "weakness_by_zone" not in listed
    main.avatar_path(alice["id"]).unlink()
    assert detail(group_client, group["id"])["members"][0]["has_avatar"] is False
    assert group_client.get("/api/groups").json()["groups"][0]["members_preview"][0] == {
        "id": alice["id"], "username": "alice", "avatar_version": 2, "has_avatar": False,
    }


def test_trial_member_activity_counts_towards_growth(group_client):
    register(group_client)
    group = create_group(group_client)
    group_client.post("/api/auth/logout")
    assert group_client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).status_code == 201
    trial = group_client.get("/api/me").json()
    assert group_client.post("/api/groups/join", json={"invite_code": group["invite_code"]}).status_code == 200
    set_joined_at(group["id"], trial["id"], "2026-09-19T00:00:00+00:00")
    insert_problem(trial["id"], "2026-09-19T00:00:00+00:00", ["2026-09-19T04:00:00+00:00"])
    observed = detail(group_client, group["id"])
    assert observed["points"] == 9
    assert observed["level"]["points"] == 9
    trial_member = next(item for item in observed["members"] if item["id"] == trial["id"])
    assert trial_member["points"] == 9
    assert trial_member["is_creator"] is False
    assert trial_member["current_streak_days"] == 1


def test_list_preview_keeps_earliest_five_members_in_join_order(group_client):
    alice = register(group_client)
    group = create_group(group_client)
    set_joined_at(group["id"], alice["id"], "2026-09-19T01:00:00+00:00")
    # Insertion order and username sorting deliberately disagree with joining order.
    newest = insert_member(group["id"], "aaa-newest", "2026-09-19T07:00:00+00:00")
    first = insert_member(group["id"], "zzz-first", "2026-09-19T00:00:00+00:00")
    other_ids = [insert_member(group["id"], f"member-{hour}", f"2026-09-19T0{hour}:00:00+00:00")
                 for hour in (2, 3, 4, 5)]
    listed = group_client.get("/api/groups").json()["groups"][0]
    assert listed["member_count"] == 7
    assert [item["id"] for item in listed["members_preview"]] == [first, alice["id"], *other_ids[:3]]
    assert newest not in {item["id"] for item in listed["members_preview"]}
    for item in listed["members_preview"]:
        assert set(item) == {"id", "username", "avatar_version", "has_avatar"}


def test_multiple_groups_use_each_membership_joining_boundary(group_client):
    alice = register(group_client)
    first = create_group(group_client, "第一组")
    second = create_group(group_client, "第二组")
    set_joined_at(first["id"], alice["id"], "2026-09-19T00:00:00+00:00")
    set_joined_at(second["id"], alice["id"], "2026-09-20T00:00:00+00:00")
    insert_problem(alice["id"], "2026-09-19T00:00:00+00:00", ["2026-09-19T04:00:00+00:00"])
    insert_problem(alice["id"], "2026-09-20T00:00:00+00:00", ["2026-09-20T04:00:00+00:00"])
    assert detail(group_client, first["id"])["points"] == 18
    assert detail(group_client, second["id"])["points"] == 9
    listed = {item["id"]: item for item in group_client.get("/api/groups").json()["groups"]}
    assert listed[first["id"]]["level"]["points"] == 18
    assert listed[second["id"]]["level"]["points"] == 9


@pytest.mark.parametrize("action", ["leave", "remove"])
def test_departure_removes_contribution_and_rejoin_starts_new_window(group_client, action):
    register(group_client)
    group = create_group(group_client)
    group_client.post("/api/auth/logout")
    bob = register(group_client, "bob")
    assert group_client.post("/api/groups/join", json={"invite_code": group["invite_code"]}).status_code == 200
    set_joined_at(group["id"], bob["id"], "2026-09-19T00:00:00+00:00")
    insert_problem(bob["id"], "2026-09-19T00:00:00+00:00", ["2026-09-19T04:00:00+00:00"])
    assert detail(group_client, group["id"])["points"] == 9
    if action == "remove":
        login(group_client)
        response = group_client.delete(f"/api/groups/{group['id']}/members/{bob['id']}")
    else:
        response = group_client.post(f"/api/groups/{group['id']}/leave")
    assert response.status_code == 200
    login(group_client)
    remaining = detail(group_client, group["id"])
    assert remaining["points"] == 0
    assert remaining["level"]["points"] == 0
    assert group_client.get("/api/groups").json()["groups"][0]["level"]["points"] == 0
    login(group_client, "bob")
    assert group_client.get(f"/api/groups/{group['id']}").status_code == 404
    assert group_client.post("/api/groups/join", json={"invite_code": group["invite_code"]}).status_code == 200
    assert detail(group_client, group["id"])["points"] == 0


def test_eleventh_member_rejected_with_actual_ten_member_limit_and_no_relationship(group_client):
    register(group_client)
    group = create_group(group_client)
    for index in range(8):
        insert_member(group["id"], f"existing-{index}", "2026-09-19T00:00:00+00:00")
    group_client.post("/api/auth/logout")
    tenth = register(group_client, "tenth")
    response = group_client.post("/api/groups/join", json={"invite_code": group["invite_code"]})
    assert response.status_code == 200
    assert len(response.json()["members"]) == 10
    group_client.post("/api/auth/logout")
    eleventh = register(group_client, "eleventh")
    response = group_client.post("/api/groups/join", json={"invite_code": group["invite_code"]})
    assert response.status_code == 403
    assert response.json() == {"detail": "小组已达到 10 人上限"}
    assert group_client.get("/api/groups").json() == {"groups": []}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_group_members WHERE group_id = ?", (group["id"],)).fetchone()[0] == 10
        assert conn.execute("SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?", (group["id"], eleventh["id"])).fetchone() is None
        assert conn.execute("SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?", (group["id"], tenth["id"])).fetchone() is not None


def test_legacy_twelve_member_group_stays_accessible_and_cannot_accept_new_members(group_client):
    register(group_client)
    group = create_group(group_client)
    for index in range(11):
        insert_member(group["id"], f"legacy-{index}", "2026-09-19T00:00:00+00:00")
    existing = detail(group_client, group["id"])
    assert len(existing["members"]) == 12
    assert existing["member_limit"] == 10
    assert existing["level"] == level_summary(0)
    listed = group_client.get("/api/groups").json()["groups"][0]
    assert listed["member_count"] == 12
    assert listed["member_limit"] == 10
    assert len(listed["members_preview"]) == 5
    group_client.post("/api/auth/logout")
    newcomer = register(group_client, "newcomer")
    response = group_client.post("/api/groups/join", json={"invite_code": group["invite_code"]})
    assert response.status_code == 403
    assert response.json() == {"detail": "小组已达到 10 人上限"}
    assert group_client.get(f"/api/groups/{group['id']}").status_code == 404
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_group_members WHERE group_id = ?", (group["id"],)).fetchone()[0] == 12
        assert conn.execute("SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?", (group["id"], newcomer["id"])).fetchone() is None
    login(group_client)
    assert detail(group_client, group["id"]) == existing


def test_non_member_cannot_read_level_or_member_contributions(group_client):
    register(group_client)
    group = create_group(group_client)
    group_client.post("/api/auth/logout")
    register(group_client, "outsider")
    insert_problem(group_client.get("/api/me").json()["id"], "2026-09-19T00:00:00+00:00", ["2026-09-19T04:00:00+00:00"])
    response = group_client.get(f"/api/groups/{group['id']}")
    assert response.status_code == 404
    assert response.json() == {"detail": "小组不存在"}
    assert group_client.get("/api/groups").json() == {"groups": []}
    login(group_client)
    assert detail(group_client, group["id"])["points"] == 0

def test_group_points_queries_batch_groups_and_keep_membership_windows():
    # This SQL regression uses memory only; it never creates a temp directory.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            CREATE TABLE users(id INTEGER, username TEXT, timezone TEXT, avatar_version INTEGER);
            CREATE TABLE study_group_members(group_id INTEGER, user_id INTEGER, joined_at TEXT);
            CREATE TABLE problems(id INTEGER, user_id INTEGER, created_at TEXT);
            CREATE TABLE mistakes(id INTEGER, problem_id INTEGER);
            CREATE TABLE reviews(mistake_id INTEGER, reviewed_at TEXT);
            INSERT INTO users VALUES (1, 'alice', 'Asia/Shanghai', 0), (2, 'bob', 'UTC', 0);
            INSERT INTO study_group_members VALUES
                (1, 1, '2026-09-19T00:00:00+00:00'),
                (1, 2, '2026-09-19T00:00:00+00:00'),
                (2, 1, '2026-09-20T00:00:00+00:00');
            INSERT INTO problems VALUES
                (1, 1, '2026-09-18T00:00:00+00:00'),
                (2, 1, '2026-09-20T00:00:00+00:00'),
                (3, 99, '2026-09-19T00:00:00+00:00');
            INSERT INTO mistakes VALUES (1, 1), (2, 2), (3, 3);
            INSERT INTO reviews VALUES
                (1, '2026-09-19T04:00:00+00:00'),
                (1, '2026-09-20T04:00:00+00:00'),
                (3, '2026-09-19T04:00:00+00:00');
        """)
        queries = []
        conn.set_trace_callback(queries.append)
        members = main.group_members_by_id(conn, (1, 2))
        points = main.group_points_by_id(conn, members)
        assert points == {1: {1: 15, 2: 0}, 2: {1: 9}}
        selects = [query for query in queries if query.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 3  # one members query, one reviews query, one records query.
        assert main.group_points_by_id(conn, {}) == {}
        assert len(queries) == 3
    finally:
        conn.close()