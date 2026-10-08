"""学习小组新功能：每周小目标 / 今日动态 / 共享题单 / 留言板。"""
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


# ---------- 功能 3：小组共享题单 ----------

def recommend(client, group_id, title="二分查找", **kwargs):
    payload = {"title": title, "zone": "算法", **kwargs}
    return client.post(f"/api/groups/{group_id}/shared-problems", json=payload)


def test_recommend_and_list(client):
    register(client, "alice")
    group = create_group(client)
    response = recommend(client, group["id"], source_url="https://example.com/p1", note="经典")
    assert response.status_code == 201
    item = response.json()
    assert item["title"] == "二分查找"
    assert item["note"] == "经典"
    assert item["is_mine"] is True
    assert item["can_delete"] is True
    assert item["collected"] is False
    assert item["recommender"]["username"] == "alice"

    listed = client.get(f"/api/groups/{group['id']}/shared-problems").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == item["id"]


def test_recommend_duplicate_url_409(client):
    register(client, "alice")
    group = create_group(client)
    assert recommend(client, group["id"], source_url="https://example.com/p1").status_code == 201
    response = recommend(
        client, group["id"], title="另一个名字", source_url="https://example.com/p1/"
    )
    assert response.status_code == 409


def test_recommend_duplicate_title_409(client):
    register(client, "alice")
    group = create_group(client)
    assert recommend(client, group["id"], title="二分查找").status_code == 201
    response = recommend(client, group["id"], title="  二分查找  ")
    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "", "zone": "算法"},
        {"title": "x" * 201, "zone": "算法"},
        {"title": "t", "zone": "不存在的分区"},
        {"title": "t", "zone": "算法", "note": "y" * 61},
        {"title": "t", "zone": "算法", "source_url": "ftp://x"},
    ],
)
def test_recommend_validation(client, payload):
    register(client, "alice")
    group = create_group(client)
    assert recommend(client, group["id"], **payload).status_code == 422


def test_recommend_daily_limit(client):
    register(client, "alice")
    group = create_group(client)
    for i in range(5):
        assert recommend(client, group["id"], title=f"题{i}").status_code == 201
    assert recommend(client, group["id"], title="题5").status_code == 403


def test_recommend_group_limit(client, monkeypatch):
    register(client, "alice")
    group = create_group(client)
    monkeypatch.setattr(main, "GROUP_SHARED_MAX_PER_GROUP", 1)
    assert recommend(client, group["id"], title="题0").status_code == 201
    assert recommend(client, group["id"], title="题1").status_code == 403


def test_recommend_non_member_404(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "mallory")
    assert recommend(client, group["id"]).status_code == 404


def test_collect_creates_problem(client):
    register(client, "alice")
    group = create_group(client)
    shared_id = recommend(client, group["id"], note="必做").json()["id"]
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    response = client.post(
        f"/api/groups/{group['id']}/shared-problems/{shared_id}/collect", json={}
    )
    assert response.status_code == 200
    problem_id = response.json()["problem_id"]
    with connect() as conn:
        problem = conn.execute(
            "SELECT title, thinking FROM problems WHERE id = ?", (problem_id,)
        ).fetchone()
    assert problem["title"] == "二分查找"
    assert "来自小组" in problem["thinking"] and "一起刷题" in problem["thinking"]
    # 再次收录：已收录，不重复创建。
    again = client.post(
        f"/api/groups/{group['id']}/shared-problems/{shared_id}/collect", json={}
    )
    assert again.json() == {"collected": True, "problem_id": problem_id}
    listed = client.get(f"/api/groups/{group['id']}/shared-problems").json()
    assert listed["items"][0]["collected"] is True


def test_delete_shared_problem_permissions(client):
    register(client, "alice")
    group = create_group(client)
    shared_id = recommend(client, group["id"]).json()["id"]
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    # 普通成员不能删别人的推荐。
    assert client.delete(
        f"/api/groups/{group['id']}/shared-problems/{shared_id}"
    ).status_code == 403
    # 组长可以删。
    login(client, "alice")
    assert client.delete(
        f"/api/groups/{group['id']}/shared-problems/{shared_id}"
    ).status_code == 200
    assert client.get(f"/api/groups/{group['id']}/shared-problems").json()["total"] == 0


def test_shared_recommender_left_shows_departed(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    recommend(client, group["id"])
    client.post(f"/api/groups/{group['id']}/leave")
    login(client, "alice")
    item = client.get(f"/api/groups/{group['id']}/shared-problems").json()["items"][0]
    assert item["recommender"]["username"] == "已离开的成员"


def test_shared_pagination(client):
    register(client, "alice")
    group = create_group(client)
    for i in range(5):
        assert recommend(client, group["id"], title=f"题{i}").status_code == 201
    page1 = client.get(
        f"/api/groups/{group['id']}/shared-problems?limit=2&offset=0"
    ).json()
    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    page2 = client.get(
        f"/api/groups/{group['id']}/shared-problems?limit=2&offset=2"
    ).json()
    assert len(page2["items"]) == 2
    assert {i["id"] for i in page1["items"]}.isdisjoint({i["id"] for i in page2["items"]})


# ---------- 功能 4：小组留言板 ----------

def post_message(client, group_id, body="你好"):
    return client.post(f"/api/groups/{group_id}/messages", json={"body": body})


def test_post_and_list_messages(client):
    register(client, "alice")
    group = create_group(client)
    response = post_message(client, group["id"], "加油")
    assert response.status_code == 201
    msg = response.json()
    assert msg["body"] == "加油"
    assert msg["author"]["username"] == "alice"
    listed = client.get(f"/api/groups/{group['id']}/messages").json()
    assert len(listed["messages"]) == 1
    assert listed["messages"][0]["id"] == msg["id"]


def test_message_validation(client):
    register(client, "alice")
    group = create_group(client)
    assert post_message(client, group["id"], "x" * 301).status_code == 422
    assert post_message(client, group["id"], "   ").status_code == 400


def test_message_minute_rate_limit(client):
    register(client, "alice")
    group = create_group(client)
    for i in range(6):
        assert post_message(client, group["id"], f"msg{i}").status_code == 201
    assert post_message(client, group["id"], "msg6").status_code == 429


def test_message_duplicate_rejected(client):
    register(client, "alice")
    group = create_group(client)
    assert post_message(client, group["id"], "重复").status_code == 201
    assert post_message(client, group["id"], "重复").status_code == 429


def test_message_daily_limit(client, monkeypatch):
    register(client, "alice")
    group = create_group(client)
    monkeypatch.setattr(main, "GROUP_MESSAGE_MAX_PER_DAY", 2)
    assert post_message(client, group["id"], "a").status_code == 201
    assert post_message(client, group["id"], "b").status_code == 201
    assert post_message(client, group["id"], "c").status_code == 429


def test_delete_message_permissions(client):
    register(client, "alice")
    group = create_group(client)
    msg_id = post_message(client, group["id"], "hello").json()["id"]
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    # 普通成员不能删别人的。
    assert client.delete(
        f"/api/groups/{group['id']}/messages/{msg_id}"
    ).status_code == 403
    # 组长可以删。
    login(client, "alice")
    assert client.delete(
        f"/api/groups/{group['id']}/messages/{msg_id}"
    ).status_code == 200
    assert client.get(f"/api/groups/{group['id']}/messages").json()["messages"] == []


def test_author_deletes_own_message(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "bob")
    join_with_code(client, group["invite_code"])
    msg_id = post_message(client, group["id"], "mine").json()["id"]
    assert client.delete(
        f"/api/groups/{group['id']}/messages/{msg_id}"
    ).status_code == 200


def test_message_pagination_before_id(client):
    register(client, "alice")
    group = create_group(client)
    main.reset_rate_limits()
    ids = []
    for i in range(5):
        main.reset_rate_limits()
        ids.append(post_message(client, group["id"], f"m{i}").json()["id"])
    page1 = client.get(f"/api/groups/{group['id']}/messages?limit=2").json()
    assert [m["id"] for m in page1["messages"]] == ids[::-1][:2]
    page2 = client.get(
        f"/api/groups/{group['id']}/messages?limit=2&before_id={ids[-2]}"
    ).json()
    assert [m["id"] for m in page2["messages"]] == ids[::-1][2:4]


def test_message_500_cap(client, monkeypatch):
    register(client, "alice")
    group = create_group(client)
    monkeypatch.setattr(main, "GROUP_MESSAGE_LIST_MAX", 3)
    for i in range(5):
        main.reset_rate_limits()
        assert post_message(client, group["id"], f"m{i}").status_code == 201
    listed = client.get(f"/api/groups/{group['id']}/messages?limit=100").json()
    assert len(listed["messages"]) == 3


def test_message_non_member_404(client):
    register(client, "alice")
    group = create_group(client)
    register(client, "mallory")
    assert post_message(client, group["id"], "hi").status_code == 404
    assert client.get(f"/api/groups/{group['id']}/messages").status_code == 404


# ---------- 账号注销 ----------

def test_account_deletion_cleans_group_extras(client):
    # bob 建组，alice 加入后推荐并留言；alice 注销后小组仍在，
    # 其推荐被清理、留言匿名化保留。
    register(client, "bob")
    group = create_group(client, "小组")
    register(client, "alice")
    join_with_code(client, group["invite_code"])
    recommend(client, group["id"])
    post_message(client, group["id"], "再见")
    response = client.post(
        "/api/me/delete-account", json={"password": "a-test-password-123"}
    )
    assert response.status_code == 200
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM group_shared_problems"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM group_problem_collections"
        ).fetchone()[0] == 0
        # 留言保留但匿名化展示。
        messages = conn.execute("SELECT * FROM group_messages").fetchall()
        assert len(messages) == 1
        user = conn.execute(
            "SELECT username FROM users WHERE id = ?", (messages[0]["user_id"],)
        ).fetchone()
        assert user["username"].startswith("已注销用户")
    # 组长仍能看到留言，作者显示为"已离开的成员"（匿名化，不暴露原身份）。
    login(client, "bob")
    listed = client.get(f"/api/groups/{group['id']}/messages").json()
    assert listed["messages"][0]["author"]["username"] == "已离开的成员"


# ---------- 迁移 ----------

def test_migrations_40_to_43_upgrade(tmp_path, monkeypatch):
    import db as db_module

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "old.db"))
    full = db_module.MIGRATIONS
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
    monkeypatch.setattr(db_module, "SCHEMA_VERSION", 43)
    init_db()
    with connect() as conn:
        assert schema_version(conn) == 43
        assert conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0] == 1
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        assert "show_group_today" in columns
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"group_weekly_goals", "group_shared_problems",
                "group_problem_collections", "group_messages"} <= tables
    # 重复启动幂等。
    init_db()
    with connect() as conn:
        assert schema_version(conn) == 43
