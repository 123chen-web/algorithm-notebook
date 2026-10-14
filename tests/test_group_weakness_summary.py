"""教练/组长视角的成员薄弱点汇总：权限、人数阈值、退出开关、聚合口径、隐私。"""
import json
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

import ai
import main
from core import config
from core import ratelimit
from core import time
from db import connect
from test_app import new_problem, register


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setattr(time, "today_for", lambda user: date(2026, 9, 19))
    ratelimit.reset_rate_limits()

    def unexpected_ai(*args, **kwargs):
        pytest.fail("Summary tests must not call AI")

    monkeypatch.setattr(ai, "generate", unexpected_ai)
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def login(client, username):
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "a-test-password-123"},
    )
    assert response.status_code == 200


def make_group(client, leader="leader"):
    register(client, username=leader)
    response = client.post("/api/groups", json={"name": "集训队"})
    assert response.status_code == 201
    return response.json()


def join_as(client, username, invite_code):
    register(client, username=username)
    response = client.post("/api/groups/join", json={"invite_code": invite_code})
    assert response.status_code == 200
    return response.json()


def tag_mistake(mistake_id, user_id, tag):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) VALUES (?,?,?,?)",
            (mistake_id, user_id, tag, datetime.now(timezone.utc).isoformat()),
        )


def user_id_of(username):
    with connect() as conn:
        return conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()[0]


def test_non_member_gets_404(client):
    group = make_group(client)
    register(client, username="outsider")
    response = client.get(f"/api/groups/{group['id']}/weakness-summary")
    assert response.status_code == 404


def test_member_non_leader_gets_403(client):
    group = make_group(client)
    join_as(client, "member", group["invite_code"])
    response = client.get(f"/api/groups/{group['id']}/weakness-summary")
    assert response.status_code == 403


def test_leader_gets_summary(client):
    group = make_group(client)
    join_as(client, "m1", group["invite_code"])
    join_as(client, "m2", group["invite_code"])
    login(client, "leader")
    response = client.get(f"/api/groups/{group['id']}/weakness-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["has_summary"] is True
    assert data["eligible_members"] == 3


def test_below_threshold_shows_no_summary(client):
    group = make_group(client)
    join_as(client, "m1", group["invite_code"])
    login(client, "leader")
    response = client.get(f"/api/groups/{group['id']}/weakness-summary")
    assert response.status_code == 200
    data = response.json()
    assert data["has_summary"] is False
    assert data["eligible_members"] == 2
    # 人数不足时不返回任何汇总字段。
    for key in ("cause_groups", "causes", "verdicts", "top_patterns", "roadmap"):
        assert key not in data
    assert "reason" in data


def test_opt_out_excludes_member_and_counts(client):
    group = make_group(client)
    join_as(client, "m1", group["invite_code"])
    join_as(client, "m2", group["invite_code"])
    join_as(client, "m3", group["invite_code"])
    # m3 退出汇总：4 人 → 3 个合格成员，仍可显示。
    login(client, "m3")
    response = client.post(
        f"/api/groups/{group['id']}/summary-preference",
        json={"include_in_summary": False},
    )
    assert response.status_code == 200
    assert response.json()["include_in_summary"] is False
    login(client, "leader")
    data = client.get(f"/api/groups/{group['id']}/weakness-summary").json()
    assert data["has_summary"] is True
    assert data["eligible_members"] == 3
    # 再退出一人 → 只剩 2 个合格成员，不再显示。
    login(client, "m2")
    client.post(
        f"/api/groups/{group['id']}/summary-preference",
        json={"include_in_summary": False},
    )
    login(client, "leader")
    data = client.get(f"/api/groups/{group['id']}/weakness-summary").json()
    assert data["has_summary"] is False
    assert data["eligible_members"] == 2


def test_opt_out_default_is_participating(client):
    group = make_group(client)
    assert group["include_in_summary"] is True


def test_aggregation_counts_match_hand_computed(client):
    group = make_group(client)
    join_as(client, "m1", group["invite_code"])
    join_as(client, "m2", group["invite_code"])
    # leader: 2 条易错点，打 boundary + impl 标签，判题 WA。
    login(client, "leader")
    leader_id = user_id_of("leader")
    response = client.post(
        "/api/problems",
        json={
            "title": "题A", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["错1", "错2"],
            "verdict": "WA",
        },
    )
    mistake_ids = response.json()["mistake_ids"]
    tag_mistake(mistake_ids[0], leader_id, "boundary")
    tag_mistake(mistake_ids[1], leader_id, "impl")
    # m1: 1 条易错点，打 boundary 标签，判题 TLE。
    login(client, "m1")
    m1_id = user_id_of("m1")
    response = client.post(
        "/api/problems",
        json={
            "title": "题B", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["错3"],
            "verdict": "TLE",
        },
    )
    tag_mistake(response.json()["mistake_ids"][0], m1_id, "boundary")
    # m2: 不打标签、不填判题，只凑人数。
    login(client, "m2")
    client.post(
        "/api/problems",
        json={
            "title": "题C", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["错4"],
        },
    )
    login(client, "leader")
    data = client.get(f"/api/groups/{group['id']}/weakness-summary").json()
    assert data["has_summary"] is True
    # 错因大类：implementation 3 条（boundary×2 + impl×1）。
    impl = next(g for g in data["cause_groups"] if g["code"] == "implementation")
    assert impl["count"] == 3
    boundary = next(c for c in data["causes"] if c["code"] == "boundary")
    assert boundary["count"] == 2
    assert boundary["label"] == "边界/特例"
    # 判题：WA 1，TLE 1。
    verdict_map = {v["code"]: v["count"] for v in data["verdicts"]}
    assert verdict_map == {"WA": 1, "TLE": 1}


def test_response_contains_no_personal_fields(client):
    group = make_group(client)
    join_as(client, "m1", group["invite_code"])
    join_as(client, "m2", group["invite_code"])
    login(client, "leader")
    leader_id = user_id_of("leader")
    response = client.post(
        "/api/problems",
        json={
            "title": "秘密题名XYZ", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["秘密错因描述ABC"],
            "verdict": "WA",
        },
    )
    tag_mistake(response.json()["mistake_ids"][0], leader_id, "boundary")
    data = client.get(f"/api/groups/{group['id']}/weakness-summary").json()
    dumped = json.dumps(data, ensure_ascii=False)
    for secret in ("leader", "m1", "m2", "秘密题名XYZ", "秘密错因描述ABC"):
        assert secret not in dumped


def test_deleted_account_no_longer_counted(client):
    group = make_group(client)
    join_as(client, "m1", group["invite_code"])
    join_as(client, "m2", group["invite_code"])
    login(client, "leader")
    assert client.get(f"/api/groups/{group['id']}/weakness-summary").json()[
        "eligible_members"
    ] == 3
    # 注销 m2：合格成员剩 2 人，不再显示汇总。
    login(client, "m2")
    response = client.post("/api/me/delete-account", json={"password": "a-test-password-123"})
    assert response.status_code in (200, 204)
    login(client, "leader")
    data = client.get(f"/api/groups/{group['id']}/weakness-summary").json()
    assert data["eligible_members"] == 2
    assert data["has_summary"] is False


def test_migration_84_idempotent_and_adds_column(tmp_path, monkeypatch):
    import db
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "mig84.db"))
    db.init_db()
    with connect() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(study_group_members)")}
        assert "include_in_summary" in cols
    db.init_db()  # 再跑一次：幂等
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
