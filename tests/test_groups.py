"""邀请码学习小组：成员权限、名额、各自时区的打卡及群体信号。"""
from datetime import datetime, timezone

import pytest

import main
from db import connect
from scheduler import today_in_timezone
from test_app import client, new_problem, register
from test_growth_insights import (
    add_mistake_on,
    create_background_user_with_mistakes,
)
from test_leaderboard import insert_review


def create_group(client, name="一起刷题"):
    response = client.post("/api/groups", json={"name": name})
    assert response.status_code == 201
    return response.json()


def login(client, username="alice"):
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "a-test-password-123"},
    )
    assert response.status_code == 200


def add_background_member(group_id, username, mistake_count, zone="算法"):
    create_background_user_with_mistakes(username, zone, mistake_count)
    with connect(write=True) as conn:
        user_id = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) "
            "VALUES (?, ?, '2026-09-19T00:00:00+00:00')",
            (group_id, user_id),
        )
    return user_id


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/groups", {"name": "一起刷题"}),
        ("GET", "/api/groups", None),
        ("POST", "/api/groups/join", {"invite_code": "ABCDEFGH"}),
        ("GET", "/api/groups/1", None),
        ("POST", "/api/groups/1/leave", None),
        ("DELETE", "/api/groups/1", None),
    ],
)
def test_group_endpoints_require_authentication(client, method, path, body):
    assert client.request(method, path, json=body).status_code == 401


def test_creator_automatically_joins_and_receives_group_detail(client):
    alice = register(client)
    assert client.get("/api/groups").json() == {"groups": []}

    group = create_group(client)
    assert group["name"] == "一起刷题"
    assert group["is_creator"] is True
    assert group["created_at"]
    assert group["members"] == [{"username": "alice", "current_streak_days": 0}]
    assert group["weakness_by_zone"] == {}
    code = group["invite_code"]
    assert len(code) == 8
    assert code.isascii() and code.isalnum() and code == code.upper()
    assert not set(code).intersection("01OIL")
    assert client.get(f"/api/groups/{group['id']}").json() == group
    with connect() as conn:
        members = conn.execute(
            "SELECT user_id, joined_at FROM study_group_members WHERE group_id = ?",
            (group["id"],),
        ).fetchall()
    assert len(members) == 1
    assert members[0]["user_id"] == alice["id"]
    assert members[0]["joined_at"]


@pytest.mark.parametrize("name", ["", "   ", "长" * 41])
def test_group_name_rejects_empty_or_overlong_values(client, name):
    register(client)
    assert client.post("/api/groups", json={"name": name}).status_code == 422
    assert client.get("/api/groups").json() == {"groups": []}


def test_group_name_accepts_length_boundaries(client):
    register(client)
    assert create_group(client, "组")["name"] == "组"
    assert create_group(client, "组" * 40)["name"] == "组" * 40


def test_invite_code_collision_retries_without_partial_group(client, monkeypatch):
    register(client)
    codes = iter(("ABCDEFGH", "ABCDEFGH", "HJKLMNPQ"))
    monkeypatch.setattr(main, "generate_group_invite_code", lambda: next(codes))
    first = create_group(client, "第一组")
    second = create_group(client, "第二组")
    assert first["invite_code"] == "ABCDEFGH"
    assert second["invite_code"] == "HJKLMNPQ"
    assert first["id"] != second["id"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM study_group_members"
        ).fetchone()[0] == 2


def test_join_accepts_case_insensitive_code_and_rejects_invalid_or_duplicate(client):
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")

    assert client.post(
        "/api/groups/join", json={"invite_code": "00000000"}
    ).status_code == 404
    response = client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"].lower()}
    )
    assert response.status_code == 200
    joined = response.json()
    assert joined["id"] == group["id"]
    assert joined["invite_code"] == group["invite_code"]
    assert joined["is_creator"] is False
    assert joined["members"] == [
        {"username": "alice", "current_streak_days": 0},
        {"username": "bob", "current_streak_days": 0},
    ]
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 409


def test_group_list_contains_only_own_groups_and_no_private_details(client):
    register(client)
    shared = create_group(client, "共同小组")
    create_group(client, "只有 alice 的小组")
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.get("/api/groups").json() == {"groups": []}
    assert client.post(
        "/api/groups/join", json={"invite_code": shared["invite_code"]}
    ).status_code == 200
    own = create_group(client, "bob 的小组")

    listed = {group["id"]: group for group in client.get("/api/groups").json()["groups"]}
    assert set(listed) == {shared["id"], own["id"]}
    assert listed[shared["id"]] == {
        "id": shared["id"], "name": "共同小组", "member_count": 2,
        "is_creator": False, "created_at": shared["created_at"],
    }
    assert listed[own["id"]] == {
        "id": own["id"], "name": "bob 的小组", "member_count": 1,
        "is_creator": True, "created_at": own["created_at"],
    }


def test_join_rejects_full_group_without_adding_membership(client, monkeypatch):
    monkeypatch.setattr(main, "GROUP_MAX_MEMBERS", 1)
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 403
    assert client.get("/api/groups").json() == {"groups": []}


def test_personal_group_limit_applies_to_creating_and_joining(client, monkeypatch):
    monkeypatch.setattr(main, "GROUP_MAX_PER_USER", 1)
    register(client)
    invited = create_group(client, "alice 的小组")
    client.post("/api/auth/logout")
    register(client, "bob")
    own = create_group(client, "bob 的小组")
    assert client.post("/api/groups", json={"name": "超过名额"}).status_code == 403
    assert client.post(
        "/api/groups/join", json={"invite_code": invited["invite_code"]}
    ).status_code == 403
    assert len(client.get("/api/groups").json()["groups"]) == 1
    assert client.post(f"/api/groups/{own['id']}/leave").status_code == 200
    assert client.post(
        "/api/groups/join", json={"invite_code": invited["invite_code"]}
    ).status_code == 200
    assert client.post("/api/groups", json={"name": "仍然超额"}).status_code == 403


def test_join_rate_limit_counts_invalid_codes_and_is_shared_by_client_ip(client, monkeypatch):
    monkeypatch.setattr(main, "GROUP_JOIN_LIMIT", 2)
    register(client)
    group = create_group(client)
    for _ in range(main.GROUP_JOIN_LIMIT):
        assert client.post(
            "/api/groups/join", json={"invite_code": "00000000"}
        ).status_code == 404

    # 同一个 IP 换一个登录账号，仍不能绕开邀请码枚举的次数限制。
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 429
    assert client.get("/api/groups").json() == {"groups": []}


def test_non_member_cannot_probe_detail_leave_or_delete(client):
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    for group_id in (group["id"], group["id"] + 1000):
        assert client.get(f"/api/groups/{group_id}").status_code == 404
        assert client.post(f"/api/groups/{group_id}/leave").status_code == 404
        assert client.delete(f"/api/groups/{group_id}").status_code == 404


def test_members_show_real_usernames_and_streaks_use_each_members_timezone(client):
    register(client)
    alice_mistake = new_problem(client)[0]
    # 上海本地都是 09-19，重复打卡只能计作一天。
    insert_review(alice_mistake, "2026-09-18T20:00:00+00:00")
    insert_review(alice_mistake, "2026-09-19T01:00:00+00:00")
    group = create_group(client)
    client.post("/api/auth/logout")
    bob = register(client, "bob")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET timezone = 'America/Los_Angeles' WHERE id = ?",
            (bob["id"],),
        )
    bob_mistake = new_problem(client)[0]
    # UTC 都是 09-19，洛杉矶本地却分别是 09-18、09-19，应该计两天。
    insert_review(bob_mistake, "2026-09-19T01:00:00+00:00")
    insert_review(bob_mistake, "2026-09-19T20:00:00+00:00")
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 200

    login(client)
    members = client.get(f"/api/groups/{group['id']}").json()["members"]
    assert members == [
        {"username": "bob", "current_streak_days": 2},
        {"username": "alice", "current_streak_days": 1},
    ]


def test_each_member_uses_own_local_today_even_when_requester_has_next_day(client, monkeypatch):
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    bob = register(client, "bob")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET timezone = 'America/Los_Angeles' WHERE id = ?",
            (bob["id"],),
        )
    mistake_id = new_problem(client)[0]
    insert_review(mistake_id, "2026-09-17T01:00:00+00:00")  # LA 09-16
    insert_review(mistake_id, "2026-09-18T01:00:00+00:00")  # LA 09-17
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 200
    frozen_now = datetime(2026, 9, 19, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        main, "today_for",
        lambda user: today_in_timezone(user["timezone"], frozen_now),
    )
    login(client)
    # alice 已是 09-19，bob 仍是 09-18；bob 昨天有打卡，连续两天不能清零。
    assert client.get(f"/api/groups/{group['id']}").json()["members"] == [
        {"username": "bob", "current_streak_days": 2},
        {"username": "alice", "current_streak_days": 0},
    ]


def test_equal_streak_members_sort_by_username_instead_of_user_id(client):
    register(client, "bob")
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client)
    response = client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    )
    assert response.status_code == 200
    assert response.json()["members"] == [
        {"username": "alice", "current_streak_days": 0},
        {"username": "bob", "current_streak_days": 0},
    ]


def test_member_can_leave_and_disappears_from_member_list(client):
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 200
    assert client.post(f"/api/groups/{group['id']}/leave").json() == {"ok": True}
    assert client.get("/api/groups").json() == {"groups": []}
    assert client.get(f"/api/groups/{group['id']}").status_code == 404
    login(client)
    assert client.get(f"/api/groups/{group['id']}").json()["members"] == [
        {"username": "alice", "current_streak_days": 0},
    ]


def test_creator_can_leave_but_cannot_delete_after_leaving_and_last_member_removes_group(client):
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 200
    login(client)
    assert client.post(f"/api/groups/{group['id']}/leave").json() == {"ok": True}
    assert client.delete(f"/api/groups/{group['id']}").status_code == 404
    login(client, "bob")
    detail = client.get(f"/api/groups/{group['id']}").json()
    assert detail["members"] == [{"username": "bob", "current_streak_days": 0}]
    assert detail["is_creator"] is False
    assert client.post(f"/api/groups/{group['id']}/leave").json() == {"ok": True}
    with connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM study_groups WHERE id = ?", (group["id"],)
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ?", (group["id"],)
        ).fetchone() is None
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 404


def test_only_creator_can_dissolve_and_memberships_are_cascade_deleted(client):
    register(client)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 200
    assert client.delete(f"/api/groups/{group['id']}").status_code == 403
    assert client.get(f"/api/groups/{group['id']}").status_code == 200
    login(client)
    assert client.delete(f"/api/groups/{group['id']}").json() == {"ok": True}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM study_group_members"
        ).fetchone()[0] == 0
    login(client, "bob")
    assert client.get("/api/groups").json() == {"groups": []}
    assert client.get(f"/api/groups/{group['id']}").status_code == 404
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 404


def test_trial_account_can_create_a_group_and_is_a_normal_member(client):
    assert client.post(
        "/api/auth/trial", json={"timezone": "Asia/Shanghai"}
    ).status_code == 201
    trial = client.get("/api/me").json()
    mistake_id = new_problem(client)[0]
    insert_review(mistake_id, "2026-09-19T04:00:00+00:00")
    group = create_group(client)
    assert group["is_creator"] is True
    assert group["members"] == [
        {"username": trial["username"], "current_streak_days": 1},
    ]
    assert group["weakness_by_zone"] == {}


def test_group_weakness_excludes_trial_data_and_outsiders_but_keeps_trial_streak(client):
    assert main.GROUP_WEAKNESS_MIN_COHORT == 3
    register(client)
    add_mistake_on(client, "算法", days_ago=0)
    group = create_group(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    for _ in range(3):
        add_mistake_on(client, "算法", days_ago=0)
    assert client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    ).status_code == 200

    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/trial", json={"timezone": "Asia/Shanghai"}
    ).status_code == 201
    trial = client.get("/api/me").json()
    trial_mistake = new_problem(client)[0]
    add_mistake_on(client, "算法", days_ago=0)  # 体验账号也满足 >= 3 条的分子门槛。
    for day in ("2026-09-17", "2026-09-18", "2026-09-19"):
        insert_review(trial_mistake, f"{day}T04:00:00+00:00")
    response = client.post(
        "/api/groups/join", json={"invite_code": group["invite_code"]}
    )
    assert response.status_code == 200
    below_threshold = response.json()
    assert len(below_threshold["members"]) == 3
    assert below_threshold["members"][0] == {
        "username": trial["username"], "current_streak_days": 3,
    }
    assert below_threshold["weakness_by_zone"]["算法"] == {
        "sample_size": 2, "struggling_ratio": None,
    }

    add_background_member(group["id"], "carol", mistake_count=4)
    add_background_member(group["id"], "dave", mistake_count=1, zone="前端")
    create_background_user_with_mistakes("outsider_algo", "算法", mistake_count=5)
    create_background_user_with_mistakes("outsider_backend", "后端", mistake_count=5)
    detail = client.get(f"/api/groups/{group['id']}").json()
    assert len(detail["members"]) == 5
    assert detail["members"][0] == {
        "username": trial["username"], "current_streak_days": 3,
    }
    assert detail["weakness_by_zone"] == {
        "算法": {"sample_size": 3, "struggling_ratio": 0.6667},
        "前端": {"sample_size": 1, "struggling_ratio": None},
    }


def test_empty_user_filter_is_not_the_whole_community_and_default_wrapper_is_unchanged(client):
    alice = register(client)
    add_mistake_on(client, "算法", days_ago=0)
    for username in ("bob", "carol", "dave", "eve"):
        create_background_user_with_mistakes(username, "算法", mistake_count=3)
    with connect() as conn:
        assert main.weakness_by_zone(conn, user_ids=set(), min_cohort=3) == {}
        assert main.weakness_by_zone(conn, user_ids={alice["id"]}, min_cohort=1) == {
            "算法": {"sample_size": 1, "struggling_ratio": 0.0},
        }
        expected = {"算法": {"sample_size": 5, "struggling_ratio": 0.8}}
        assert main.weakness_by_zone(conn) == expected
        assert main.community_weakness_by_zone(conn) == expected
