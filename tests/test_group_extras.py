"""学习小组新功能：每周小目标 / 今日动态。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import ai
import main
from db import connect, init_db, schema_version
from test_app import register, new_problem
from test_leaderboard import insert_review


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    main.reset_rate_limits()

    def unexpected_ai(*args, **kwargs):
        pytest.fail("Group extras tests must not call AI")

    monkeypatch.setattr(ai, "generate", unexpected_ai)
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


@pytest.fixture(autouse=True)
def isolate_group_avatars(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))


def create_group(client, name="一起刷题"):
    response = client.post("/api/groups", json={"name": name})
    assert response.status_code == 201
    return response.json()


def login(client, username):
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "a-test-password-123"},
    )
    assert response.status_code == 200


def join_with_code(client, invite_code):
    response = client.post("/api/groups/join", json={"invite_code": invite_code})
    assert response.status_code == 200
    return response.json()


def review_now(client, count):
    """当前用户：新建 1 道题（带易错点）并插入 count 条当前时间的复习记录。"""
    mistake_ids = new_problem(client)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for _ in range(count):
        insert_review(mistake_ids[0], now)


# ---------- 功能 1：每周小目标 ----------

def test_weekly_goal_set_and_detail(client):
    register(client, "alice")
    group = create_group(client)
    response = client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 50},
    )
    assert response.status_code == 200
    goal = response.json()["weekly_goal"]
    assert goal["goal_type"] == "review"
    assert goal["target"] == 50
    assert goal["total"] == 0
    assert goal["progress"] == 0
    assert goal["week_start"] == main.group_week_start()

    detail = client.get(f"/api/groups/{group['id']}").json()
    assert detail["weekly_goal"]["target"] == 50


def test_weekly_goal_unset_shows_null(client):
    register(client, "alice")
    group = create_group(client)
    assert client.get(f"/api/groups/{group['id']}").json()["weekly_goal"] is None


def test_weekly_goal_only_creator(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    response = client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 50},
    )
    assert response.status_code == 403


def test_weekly_goal_non_member_404(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "mallory")
    response = client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 50},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("target", [4, 501, 0, -1])
def test_weekly_goal_target_bounds(client, target):
    register(client, "alice")
    group = create_group(client)
    response = client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": target},
    )
    assert response.status_code == 422


def test_weekly_goal_type_validation(client):
    register(client, "alice")
    group = create_group(client)
    response = client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "sleep", "target": 50},
    )
    assert response.status_code == 422


def test_weekly_goal_progress_counts_reviews(client):
    register(client, "alice")
    group = create_group(client)
    review_now(client, 7)
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 20},
    )
    goal = client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]
    assert goal["total"] == 7
    assert goal["progress"] == 35


def test_weekly_goal_daily_cap(client):
    register(client, "alice")
    group = create_group(client)
    review_now(client, 25)
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 100},
    )
    goal = client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]
    assert goal["total"] == 20  # 每人每天封顶 20


def test_weekly_goal_record_type_counts_problems(client):
    register(client, "alice")
    group = create_group(client)
    new_problem(client)
    new_problem(client)
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "record", "target": 10},
    )
    goal = client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]
    assert goal["total"] == 2
    assert goal["progress"] == 20


def test_weekly_goal_ignores_pre_join_activity(client):
    register(client, "alice")
    # 加组前 1 小时复习：不应计入。
    mistake_ids = new_problem(client)
    before = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    for _ in range(5):
        insert_review(mistake_ids[0], before)
    group = create_group(client)
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 50},
    )
    goal = client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]
    assert goal["total"] == 0


def test_weekly_goal_update_keeps_progress(client):
    register(client, "alice")
    group = create_group(client)
    review_now(client, 8)
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 10},
    )
    assert client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]["progress"] == 80
    # 改目标：进度由数据实时得出，不会丢失。
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 16},
    )
    goal = client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]
    assert goal["total"] == 8
    assert goal["progress"] == 50


def test_weekly_goal_counts_member_activity(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    review_now(client, 6)
    login(client, "alice")
    client.put(
        f"/api/groups/{group['id']}/weekly-goal",
        json={"goal_type": "review", "target": 10},
    )
    goal = client.get(f"/api/groups/{group['id']}").json()["weekly_goal"]
    assert goal["total"] == 6


# ---------- 功能 2：组内今日动态 ----------

def test_today_feed(client):
    register(client, "alice")
    group = create_group(client)
    review_now(client, 3)
    response = client.get(f"/api/groups/{group['id']}/today")
    assert response.status_code == 200
    today = response.json()["today"]
    assert len(today) == 1
    entry = today[0]
    assert entry["username"] == "alice"
    assert entry["visible"] is True
    assert entry["reviews_today"] == 3
    assert entry["goal_met"] is True
    # 不暴露私密字段。
    assert "email" not in entry


def test_today_goal_not_met(client):
    register(client, "alice")
    group = create_group(client)
    entry = client.get(f"/api/groups/{group['id']}/today").json()["today"][0]
    assert entry["reviews_today"] == 0
    assert entry["goal_met"] is False


def test_today_privacy_off(client):
    register(client, "alice")
    group = create_group(client)
    review_now(client, 2)
    response = client.put("/api/me/group-today", json={"show": False})
    assert response.status_code == 200
    assert response.json() == {"show": False}
    entry = client.get(f"/api/groups/{group['id']}/today").json()["today"][0]
    assert entry["visible"] is False
    assert "reviews_today" not in entry
    # 再打开。
    client.put("/api/me/group-today", json={"show": True})
    entry = client.get(f"/api/groups/{group['id']}/today").json()["today"][0]
    assert entry["visible"] is True
    assert entry["reviews_today"] == 2


def test_today_non_member_404(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "mallory")
    assert client.get(f"/api/groups/{group['id']}/today").status_code == 404


# ---------- 迁移 ----------

def test_migrations_40_to_41_upgrade(tmp_path, monkeypatch):
    import db as db_module

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "old.db"))
    full = db_module.MIGRATIONS
    assert [version for version, _, _ in full if 40 <= version < 50] == [40, 41]
    monkeypatch.setattr(db_module, "MIGRATIONS", [m for m in full if m[0] <= 22])
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 22)
    init_db()
    with connect() as conn:
        assert schema_version(conn) == 22
        # 旧库里建一组数据。
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES ('u1', 'x', 'Asia/Shanghai', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO study_groups(name, invite_code, created_by, created_at) "
            "VALUES ('g', 'ABCDEFGH', 1, ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) "
            "VALUES (1, 1, ?)",
            (now,),
        )
    # 恢复完整迁移并升级。
    monkeypatch.setattr(db_module, "MIGRATIONS", full)
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 41)
    init_db()
    with connect() as conn:
        assert schema_version(conn) == 41
        assert conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0] == 1
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        assert "show_group_today" in columns
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {name for name in tables if name.startswith("group_")} == {"group_weekly_goals"}
    # 重复启动幂等。
    init_db()
    with connect() as conn:
        assert schema_version(conn) == 41
