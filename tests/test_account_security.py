"""账号安全、注销脱敏、管理员身份和公开法律页的后端回归。"""
from datetime import datetime, timezone
import io
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import ai
import cleanup_trial_accounts
import mailer
import main
import send_reminders
from db import connect
from legal import TERMS_VERSION
from test_app import client, new_problem, register


PASSWORD = "a-test-password-123"
NEW_PASSWORD = "a-new-password-456"
NOW = "2026-10-03T08:00:00+00:00"


@pytest.fixture(autouse=True)
def isolate_account_configuration(monkeypatch, request):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("LEGAL_OPERATOR_NAME", raising=False)
    monkeypatch.delenv("LEGAL_CONTACT_EMAIL", raising=False)
    if "client" in request.fixturenames:
        # API 夹具已有系统临时目录；纯函数测试不申请临时目录。
        monkeypatch.setenv("AVATAR_DIR", str(request.getfixturevalue("tmp_path") / "avatars"))

    def unexpected_external_call(*args, **kwargs):
        pytest.fail("账号测试不得调用真实 AI 或邮件服务")

    monkeypatch.setattr(ai, "generate", unexpected_external_call)
    monkeypatch.setattr(mailer, "send_email", unexpected_external_call)


@pytest.fixture
def isolated_avatar_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))


def registration_payload(username="securityuser", **overrides):
    payload = {
        "username": username, "password": PASSWORD,
        "email": "mailidentity@example.com", "timezone": "Asia/Shanghai",
        "invite_code": "test-invite", "accept_terms": True,
    }
    payload.update(overrides)
    return payload


def login(client, username="alice", password=PASSWORD):
    return client.post("/api/auth/login", json={
        "username": username, "password": password,
    })


def insert_user(conn, username, *, password=PASSWORD, deleted_at=None):
    return conn.execute(
        "INSERT INTO users(username, password_hash, timezone, created_at, "
        "deleted_at) VALUES (?, ?, 'Asia/Shanghai', ?, ?)",
        (username, main.password_hash(password), NOW, deleted_at),
    ).lastrowid


def add_session(conn, user_id, token):
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (main.token_hash(token), user_id, int(main.time.time()) + 3600),
    )


def reset_token(user_id, token="account-security-reset-token"):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at) "
            "VALUES (?, ?, ?)",
            (main.token_hash(token), user_id, int(main.time.time()) + 3600),
        )
    return token


@pytest.mark.parametrize("stored", [
    "unused", "", "not-hex:abcd", "00:zz", "00:00:00", None,
])
def test_password_matches_rejects_malformed_stored_hash(stored):
    assert main.password_matches(PASSWORD, stored) is False


def test_password_matches_accepts_valid_hash_and_rejects_placeholder():
    assert main.password_matches(PASSWORD, main.password_hash(PASSWORD)) is True
    assert main.password_matches(PASSWORD, "00" * 16 + ":" + "00" * 32) is False


def test_new_password_length_boundaries_and_common_list_size():
    password = "abcdefghijk-" * 10 + "12345678"
    assert len(password) == 128
    assert main.check_new_password(password) is None
    with pytest.raises(HTTPException) as error:
        main.check_new_password(password + "x")
    assert error.value.status_code == 400
    assert error.value.detail == "密码最多 128 位"
    assert 80 <= len(main.COMMON_PASSWORDS) <= 150


@pytest.mark.parametrize("surface", ["register", "reset", "change"])
@pytest.mark.parametrize("password,detail", [
    ("short", "密码至少 8 位"),
    ("12345678", "这个密码太常见，请换一个"),
    ("xxxxxxxx", "这个密码太常见，请换一个"),
    ("SecurityUser", "密码不能和用户名或邮箱相同"),
    ("MailIdentity", "密码不能和用户名或邮箱相同"),
])
def test_new_password_policy_applies_to_all_three_surfaces(
    client, surface, password, detail
):
    if surface == "register":
        response = client.post("/api/auth/register", json=registration_payload(
            password=password,
        ))
    else:
        user = register(client, "securityuser", email="mailidentity@example.com")
        token = reset_token(user["id"])
        if surface == "reset":
            response = client.post("/api/auth/reset-password", json={
                "token": token, "password": password,
            })
        else:
            response = client.post("/api/me/password", json={
                "current_password": PASSWORD, "new_password": password,
            })
        assert client.get("/api/me").status_code == 200
        with connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM password_resets WHERE user_id = ?",
                (user["id"],),
            ).fetchone()[0] == 1
    assert response.status_code == 400
    assert response.json()["detail"] == detail


@pytest.mark.parametrize("password", [
    "123456789", "1234567890", "password", "password1", "password123",
    "qwertyui", "qwerty123", "iloveyou", "abc12345", "11111111",
    "00000000", "1q2w3e4r", "admin123", "letmein1", "welcome1",
])
def test_required_common_passwords_are_rejected(client, password):
    response = client.post("/api/auth/register", json=registration_payload(
        password=password,
    ))
    assert response.status_code == 400
    expected = "密码至少 8 位" if len(password) < 8 else "这个密码太常见，请换一个"
    assert response.json()["detail"] == expected


@pytest.mark.parametrize("password", ["abc123", "abcdefg"])
def test_login_still_accepts_legacy_six_and_seven_character_passwords(client, password):
    with connect(write=True) as conn:
        user_id = insert_user(conn, "legacyuser", password=password)
    response = login(client, "legacyuser", password)
    assert response.status_code == 200
    assert client.get("/api/me").json()["id"] == user_id


def test_nfkc_registration_and_rename_share_normalized_login(client):
    response = client.post("/api/auth/register", json=registration_payload(
        username=" ＡＬＩＣＥ ", email="alice@example.com",
    ))
    assert response.status_code == 201
    user_id = response.json()["id"]
    assert response.json()["username"] == "alice"
    client.post("/api/auth/logout")
    assert login(client, "alice").status_code == 200
    assert client.get("/api/me").json()["id"] == user_id
    renamed = client.put("/api/me/username", json={"username": " ＢＯＢ２ "})
    assert renamed.status_code == 200
    assert renamed.json()["username"] == "bob2"
    client.post("/api/auth/logout")
    assert login(client, "bob2").status_code == 200
    assert client.get("/api/me").json()["id"] == user_id


def test_login_falls_back_to_legacy_fullwidth_username(client):
    with connect(write=True) as conn:
        user_id = insert_user(conn, "ｌｅｇａｃｙ")
    response = login(client, "ＬＥＧＡＣＹ")
    assert response.status_code == 200
    assert client.get("/api/me").json()["id"] == user_id


def test_login_legacy_nfkc_expansion_can_exceed_new_username_limit(client):
    username = "㍿" * 9
    with connect(write=True) as conn:
        user_id = insert_user(conn, username)
    assert login(client, username).status_code == 200
    assert client.get("/api/me").json()["id"] == user_id


@pytest.mark.parametrize("username", [
    "admin", "administrator", "root", "system", "support", "official",
    "moderator", "staff", "官方", "管理员", "客服", "系统", "站长",
    "版主", "欧叶", "欧叶oy", "算法错题本", "已注销用户 #1",
    "已注销用户任意后缀", " ＡＤＭＩＮ ", "欧叶ＯＹ",
])
@pytest.mark.parametrize("surface", ["register", "rename"])
def test_reserved_username_rejected_on_registration_and_rename(client, username, surface):
    if surface == "register":
        response = client.post("/api/auth/register", json=registration_payload(username))
    else:
        register(client)
        response = client.put("/api/me/username", json={"username": username})
        assert client.get("/api/me").json()["username"] == "alice"
    assert response.status_code == 400
    assert response.json()["detail"] == "这个用户名已被保留"


def test_existing_reserved_username_can_still_log_in(client):
    with connect(write=True) as conn:
        user_id = insert_user(conn, "admin")
    assert login(client, "admin").status_code == 200
    assert client.get("/api/me").json()["id"] == user_id


def test_public_registration_rejects_configured_admin_name_and_creates_only_regular_users(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", " ＡＬＩＣＥ ")
    denied = client.post("/api/auth/register", json=registration_payload("alice", email="alice@example.com"))
    assert denied.status_code == 400
    assert denied.json()["detail"] == "这个用户名已被保留"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    user = register(client, "bob")
    assert client.get("/api/me").json()["is_admin"] is False
    with connect() as conn:
        assert conn.execute("SELECT is_admin FROM users WHERE id = ?", (user["id"],)).fetchone()[0] == 0


def test_startup_does_not_grant_existing_configured_name(client, monkeypatch):
    with connect(write=True) as conn:
        user_id = insert_user(conn, "oldoperator")
    monkeypatch.setenv("ADMIN_USERNAME", " ＯＬＤＯＰＥＲＡＴＯＲ ")
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as restarted:
        assert login(restarted, "oldoperator").status_code == 200
        assert restarted.get("/api/me").json()["is_admin"] is False
    with connect() as conn:
        row = conn.execute("SELECT is_admin, terms_accepted_at FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row["is_admin"] == 0
    assert row["terms_accepted_at"] is None


def test_startup_grants_neither_normalized_nor_fullwidth_configured_name(client, monkeypatch):
    with connect(write=True) as conn:
        fullwidth_id = insert_user(conn, "ａｌｉｃｅ")
        exact_id = insert_user(conn, "alice")
    monkeypatch.setenv("ADMIN_USERNAME", " ＡＬＩＣＥ ")
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}):
        pass
    with connect() as conn:
        roles = {row["id"]: row["is_admin"] for row in conn.execute("SELECT id,is_admin FROM users")}
    assert roles == {fullwidth_id: 0, exact_id: 0}


def test_deleted_admin_does_not_trigger_public_registration_promotion(client, monkeypatch):
    with connect(write=True) as conn:
        old_id = insert_user(conn, "deletedoperator", deleted_at=NOW)
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (old_id,))
    monkeypatch.setenv("ADMIN_USERNAME", "operator")
    register(client)
    assert client.get("/api/me").json()["is_admin"] is False


def test_admin_can_rename_to_configured_admin_name(client, monkeypatch):
    user = register(client, "operator")
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    monkeypatch.setenv("ADMIN_USERNAME", "nextoperator")
    renamed = client.put("/api/me/username", json={"username": "ＮＥＸＴＯＰＥＲＡＴＯＲ"})
    assert renamed.status_code == 200
    assert renamed.json()["username"] == "nextoperator"
    assert client.get("/api/me").json()["is_admin"] is True


def test_change_password_revokes_other_sessions_and_rotates_current(client, monkeypatch):
    user = register(client)
    current_token = client.cookies.get("session")
    reset_token(user["id"])
    with connect(write=True) as conn:
        add_session(conn, user["id"], "other-device")
        add_session(conn, user["id"], "another-device")
        other_id = insert_user(conn, "otheruser")
        add_session(conn, other_id, "unrelated-device")
    original_hash = main.password_hash

    def hash_without_write_lock(password):
        with connect(write=True) as conn:
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
        return original_hash(password)

    monkeypatch.setattr(main, "password_hash", hash_without_write_lock)
    changed = client.post("/api/me/password", json={
        "current_password": PASSWORD, "new_password": NEW_PASSWORD,
    })
    assert changed.status_code == 200
    assert changed.json() == {"ok": True, "revoked_sessions": 2}
    assert client.get("/api/me").status_code == 200
    new_token = client.cookies.get("session")
    assert new_token and new_token != current_token
    with connect() as conn:
        sessions = {row[0] for row in conn.execute("SELECT token_hash FROM sessions")}
        assert sessions == {main.token_hash(new_token), main.token_hash("unrelated-device")}
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0
    assert client.get("/api/me", headers={"Cookie": "session=other-device"}).status_code == 401
    assert client.get("/api/me", headers={"Cookie": f"session={current_token}"}).status_code == 401
    client.post("/api/auth/logout")
    assert login(client).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize("current,new,detail", [
    ("wrong-password", NEW_PASSWORD, "当前密码不正确"),
    (PASSWORD, PASSWORD, "新密码不能和当前密码相同"),
])
def test_change_password_errors_are_400_and_keep_session(client, current, new, detail):
    user = register(client)
    token = client.cookies.get("session")
    reset_token(user["id"])
    with connect(write=True) as conn:
        add_session(conn, user["id"], "unchanged-other-device")
    before = database_snapshot()
    response = client.post("/api/me/password", json={
        "current_password": current, "new_password": new,
    })
    assert response.status_code == 400
    assert response.json()["detail"] == detail
    assert client.get("/api/me").status_code == 200
    assert client.cookies.get("session") == token
    assert "set-cookie" not in response.headers
    assert database_snapshot() == before


@pytest.mark.parametrize("path,payload", [
    ("/api/me/password", {"current_password": "wrong-password", "new_password": NEW_PASSWORD}),
    ("/api/me/delete-account", {"password": "wrong-password"}),
])
def test_sensitive_account_actions_rate_limit_by_user(client, path, payload):
    register(client)
    for _ in range(5):
        assert client.post(path, json=payload).status_code == 400
    blocked = client.post(path, json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "尝试次数过多，请 15 分钟后再试"
    # 相同 IP 的另一个账号拥有独立额度。
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.post(path, json=payload).status_code == 400


@pytest.mark.parametrize("path,payload,detail", [
    ("/api/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}, None),
    ("/api/me/delete-account", {"password": PASSWORD}, "体验账号到期会自动清理"),
])
def test_trial_cannot_change_password_or_delete_account(client, path, payload, detail):
    assert client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).status_code == 201
    response = client.post(path, json=payload)
    assert response.status_code == 403
    if detail:
        assert response.json()["detail"] == detail


@pytest.mark.parametrize("trial", [False, True])
def test_revoke_other_devices_preserves_current_and_unrelated_sessions(client, trial):
    if trial:
        user = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).json()
    else:
        user = register(client)
    token = client.cookies.get("session")
    with connect(write=True) as conn:
        add_session(conn, user["id"], "revoked-device")
        unrelated = insert_user(conn, "otheruser")
        add_session(conn, unrelated, "unrelated-device")
    response = client.post("/api/me/sessions/revoke-others", json={})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "revoked": 1}
    assert client.get("/api/me").status_code == 200
    assert client.cookies.get("session") == token
    with connect() as conn:
        assert {row[0] for row in conn.execute("SELECT token_hash FROM sessions")} == {
            main.token_hash(token), main.token_hash("unrelated-device"),
        }
    assert client.post("/api/me/sessions/revoke-others", json={}).json()["revoked"] == 0


def seed_account_data(user_id, other_id):
    with connect(write=True) as conn:
        add_session(conn, user_id, "delete-other-device")
        conn.execute("INSERT INTO password_resets VALUES (?, ?, ?)", (
            main.token_hash("delete-reset"), user_id, int(main.time.time()) + 3600,
        ))
        problem_id = conn.execute(
            "INSERT INTO problems(user_id,title,language,code,thinking,created_at) "
            "VALUES (?, '注销测试题', 'Python', 'pass', '思路', ?)", (user_id, NOW),
        ).lastrowid
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id,description,due_date) VALUES (?, '错因', '2026-10-03')",
            (problem_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO reviews(mistake_id,quality,reviewed_at,next_due_date) "
            "VALUES (?, 4, ?, '2026-10-04')", (mistake_id, NOW),
        )
        conn.execute(
            "INSERT INTO variants(mistake_id,description,model,created_at) VALUES (?, '变体', 'mock', ?)",
            (mistake_id, NOW),
        )
        conn.execute("INSERT INTO mistake_tags VALUES (?, ?, '边界', ?)", (mistake_id, user_id, NOW))
        conn.execute("INSERT INTO weakness_insights VALUES (?, '分析', ?)", (user_id, NOW))
        conn.execute("INSERT INTO mistake_clusters VALUES (?, '聚类', ?)", (user_id, NOW))
        conn.execute("INSERT INTO ai_usage VALUES (?, '2026-10-03', 1)", (user_id,))
        call_id = conn.execute(
            "INSERT INTO ai_calls(user_id,feature,model,ok,duration_ms,created_at) "
            "VALUES (?, 'practice', 'mock', 1, 12, ?)", (user_id, NOW),
        ).lastrowid
        for reporter, owner in [(user_id, other_id), (other_id, user_id)]:
            conn.execute(
                "INSERT INTO avatar_reports(reporter_user_id,avatar_owner_id,created_at) VALUES (?, ?, ?)",
                (reporter, owner, NOW),
            )
        own_group_id = conn.execute(
            "INSERT INTO study_groups(name,invite_code,created_by,created_at) "
            "VALUES ('仅自己小组', 'DELOWN01', ?, ?)", (user_id, NOW),
        ).lastrowid
        other_group_id = conn.execute(
            "INSERT INTO study_groups(name,invite_code,created_by,created_at) "
            "VALUES ('他人小组', 'DELOTHER', ?, ?)", (other_id, NOW),
        ).lastrowid
        conn.executemany("INSERT INTO study_group_members VALUES (?, ?, ?)", [
            (own_group_id, user_id, NOW), (other_group_id, user_id, NOW),
            (other_group_id, other_id, NOW),
        ])
        post_id = conn.execute(
            "INSERT INTO posts(user_id,title,body,created_at) VALUES (?, '保留帖子', '保留正文', ?)",
            (user_id, NOW),
        ).lastrowid
        comment_id = conn.execute(
            "INSERT INTO post_comments(post_id,user_id,body,created_at) VALUES (?, ?, '保留评论', ?)",
            (post_id, user_id, NOW),
        ).lastrowid
        report_id = conn.execute(
            "INSERT INTO reports(reporter_user_id,post_id,reason,created_at) VALUES (?, ?, '保留举报', ?)",
            (user_id, post_id, NOW),
        ).lastrowid
        plan_id = conn.execute(
            "INSERT INTO plans(name,period_days,ai_daily_limit,price_cents,created_at) "
            "VALUES ('保留套餐', 30, 10, 990, ?)", (NOW,),
        ).lastrowid
        conn.execute(
            "INSERT INTO orders(id,user_id,plan_id,amount_cents,channel,status,created_at,paid_at) "
            "VALUES ('retained-order', ?, ?, 990, 'alipay', 'paid', ?, ?)",
            (user_id, plan_id, NOW, NOW),
        )
        conn.execute(
            "UPDATE users SET avatar_version = 7, last_reminder_sent = '2026-10-03', "
            "plan_id = ?, plan_expires_at = '2099-01-01T00:00:00+00:00' WHERE id = ?",
            (plan_id, user_id),
        )
    return {
        "problem_id": problem_id, "mistake_id": mistake_id, "call_id": call_id,
        "own_group_id": own_group_id, "other_group_id": other_group_id,
        "post_id": post_id, "comment_id": comment_id, "report_id": report_id,
        "plan_id": plan_id,
    }


def database_snapshot():
    with connect() as conn:
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
        )]
        return {table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in tables}


def test_delete_account_removes_learning_data_and_preserves_anonymous_records(
    client, isolated_avatar_dir, monkeypatch
):
    user = register(client)
    token = client.cookies.get("session")
    with connect(write=True) as conn:
        other_id = insert_user(conn, "otheruser")
    seeded = seed_account_data(user["id"], other_id)
    avatar = main.avatar_path(user["id"])
    avatar.write_bytes(b"stand-in avatar")
    monkeypatch.setattr(main, "utc_now", lambda: NOW)
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "set-cookie" not in response.headers
    assert client.cookies.get("session") == token
    assert not avatar.exists()
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        assert row["username"] == f"已注销用户 #{user['id']}"
        assert row["email"] is None
        assert row["deleted_at"] == NOW
        assert row["avatar_version"] == 0
        assert row["last_reminder_sent"] is None
        assert row["is_admin"] == 0
        assert row["plan_id"] == seeded["plan_id"]
        assert row["plan_expires_at"] == "2099-01-01T00:00:00+00:00"
        assert main.password_matches(PASSWORD, row["password_hash"]) is False
        for table in ["sessions", "password_resets", "problems", "mistakes", "reviews",
                      "variants", "mistake_tags", "weakness_insights", "mistake_clusters",
                      "ai_usage", "avatar_reports"]:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
        call = conn.execute("SELECT * FROM ai_calls WHERE id = ?", (seeded["call_id"],)).fetchone()
        assert call["user_id"] is None
        assert call["model"] == "mock" and call["duration_ms"] == 12
        assert conn.execute("SELECT id FROM study_groups WHERE id = ?", (seeded["own_group_id"],)).fetchone() is None
        assert conn.execute("SELECT COUNT(*) FROM study_groups").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM study_group_members WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT user_id FROM orders WHERE id = 'retained-order'").fetchone()[0] == user["id"]
        for table in ["posts", "post_comments", "reports"]:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert login(client).status_code == 401
    assert login(client, f"已注销用户 #{user['id']}").status_code == 401
    assert client.get("/api/me", headers={"Cookie": f"session={token}"}).status_code == 401
    assert login(client, "otheruser").status_code == 200
    post = client.get(f"/api/posts/{seeded['post_id']}").json()
    assert post["username"] == f"已注销用户 #{user['id']}"
    assert post["comments"][0]["username"] == f"已注销用户 #{user['id']}"
    group = client.get(f"/api/groups/{seeded['other_group_id']}").json()
    assert [member["id"] for member in group["members"]] == [other_id]
    assert all(entry["display_name"] != f"用户 #{user['id']}" for entry in client.get("/api/leaderboard").json()["entries"])
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (other_id,))
    dashboard = client.get("/api/admin/dashboard").json()
    assert dashboard["users"]["total"] == 1
    assert dashboard["subscriptions"]["active"] == 0
    assert dashboard["subscriptions"]["paid_orders"] == 1
    reports = client.get("/api/admin/reports").json()["reports"]
    assert reports[0]["reporter_username"] == f"已注销用户 #{user['id']}"


def test_delete_rejects_wrong_password_without_changing_any_data(client):
    user = register(client)
    new_problem(client)
    before = database_snapshot()
    response = client.post("/api/me/delete-account", json={"password": "wrong-password"})
    assert response.status_code == 400
    assert response.json()["detail"] == "当前密码不正确"
    assert database_snapshot() == before
    assert client.get("/api/me").json()["id"] == user["id"]


def test_admin_account_cannot_self_delete(client, monkeypatch):
    user = register(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["detail"] == "管理员账号不能自助注销，请先用 admin_tool.py 撤销管理员身份"
    assert client.get("/api/me").json()["is_admin"] is True


def test_creator_with_other_members_cannot_delete_and_nothing_is_changed(
    client, isolated_avatar_dir
):
    user = register(client)
    with connect(write=True) as conn:
        other_id = insert_user(conn, "otheruser")
    seeded = seed_account_data(user["id"], other_id)
    with connect(write=True) as conn:
        conn.execute("INSERT INTO study_group_members VALUES (?, ?, ?)", (
            seeded["own_group_id"], other_id, NOW,
        ))
    avatar = main.avatar_path(user["id"])
    avatar.write_bytes(b"stand-in avatar")
    before = database_snapshot()
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 409
    assert "仅自己小组" in response.json()["detail"]
    assert database_snapshot() == before
    assert avatar.exists()
    assert client.get("/api/me").status_code == 200


def test_delete_transaction_rolls_back_on_midway_database_failure(
    client, isolated_avatar_dir
):
    user = register(client)
    with connect(write=True) as conn:
        other_id = insert_user(conn, "otheruser")
    seed_account_data(user["id"], other_id)
    avatar = main.avatar_path(user["id"])
    avatar.write_bytes(b"stand-in avatar")
    with connect(write=True) as conn:
        conn.execute(
            "CREATE TRIGGER fail_account_anonymization BEFORE UPDATE OF deleted_at ON users "
            "WHEN NEW.deleted_at IS NOT NULL BEGIN SELECT RAISE(ABORT, '模拟注销中途失败'); END"
        )
    before = database_snapshot()
    with pytest.raises(sqlite3.IntegrityError, match="模拟注销中途失败"):
        client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert database_snapshot() == before
    assert avatar.exists()
    assert client.get("/api/me").status_code == 200


def test_deletion_without_avatar_file_succeeds(client, isolated_avatar_dir):
    register(client)
    assert client.post("/api/me/delete-account", json={"password": PASSWORD}).status_code == 200


def test_deleted_user_is_rejected_even_if_legacy_session_was_left_behind(client):
    user = register(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET deleted_at = ? WHERE id = ?", (NOW, user["id"]))
    assert client.get("/api/me").status_code == 401
    response = login(client)
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码不正确"


def test_deleted_user_cannot_issue_or_consume_password_reset_token(client):
    user = register(client)
    token = reset_token(user["id"])
    with connect(write=True) as conn:
        before_hash = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()[0]
        conn.execute("UPDATE users SET deleted_at = ? WHERE id = ?", (NOW, user["id"]))
    # 即使模拟遗留邮箱和 token，找回密码也不发送邮件，重置也不能复活账号。
    assert client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}).json() == {"ok": True}
    response = client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD})
    assert response.status_code == 400
    with connect() as conn:
        assert conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()[0] == before_hash


def test_avatar_upload_rechecks_account_after_image_processing(
    client, isolated_avatar_dir, monkeypatch
):
    user = register(client)
    content = io.BytesIO()
    Image.new("RGB", (8, 8)).save(content, format="JPEG")
    original_resize = main.resize_avatar_to_square_jpeg

    def resize_during_account_deletion(image):
        jpeg = original_resize(image)
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET deleted_at = ?, avatar_version = 0 WHERE id = ?", (NOW, user["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        return jpeg

    monkeypatch.setattr(main, "resize_avatar_to_square_jpeg", resize_during_account_deletion)
    response = client.post("/api/me/avatar", files={
        "file": ("avatar.jpg", content.getvalue(), "image/jpeg"),
    })
    assert response.status_code == 401
    assert not main.avatar_path(user["id"]).exists()
    with connect() as conn:
        assert conn.execute("SELECT avatar_version FROM users WHERE id = ?", (user["id"],)).fetchone()[0] == 0


def test_reminders_and_trial_cleanup_skip_deleted_rows(client, monkeypatch, capsys):
    user = register(client)
    new_problem(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET deleted_at = ?, is_trial = 1, created_at = '2000-01-01' WHERE id = ?", (NOW, user["id"]))
    monkeypatch.setattr(send_reminders, "today_in_timezone", lambda tz: datetime(2026, 10, 3).date())
    assert send_reminders.main(["--dry-run"]) == 0
    assert "alice" not in capsys.readouterr().out
    assert cleanup_trial_accounts.main([]) == 0
    assert "alice" not in capsys.readouterr().out
    with connect() as conn:
        assert conn.execute("SELECT deleted_at FROM users WHERE id = ?", (user["id"],)).fetchone()[0] == NOW


def test_visible_user_lists_filter_deleted_rows_with_leftover_membership_and_reviews(client):
    user = register(client)
    with connect(write=True) as conn:
        deleted_id = insert_user(conn, "deletedlearner", deleted_at=NOW)
        group_id = conn.execute(
            "INSERT INTO study_groups(name,invite_code,created_by,created_at) "
            "VALUES ('过滤组', 'FILTER01', ?, ?)", (user["id"], NOW),
        ).lastrowid
        conn.executemany("INSERT INTO study_group_members VALUES (?, ?, ?)", [
            (group_id, user["id"], NOW), (group_id, deleted_id, NOW),
        ])
        problem_id = conn.execute(
            "INSERT INTO problems(user_id,title,language,code,thinking,created_at) "
            "VALUES (?, '遗留题目', 'Python', '', '', ?)", (deleted_id, NOW),
        ).lastrowid
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id,description,due_date) VALUES (?, '遗留错因', '2026-09-19')",
            (problem_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO reviews(mistake_id,quality,reviewed_at,next_due_date) "
            "VALUES (?, 4, '2026-09-19T04:00:00+00:00', '2026-09-20')", (mistake_id,),
        )
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
    assert client.get("/api/leaderboard").json()["entries"] == []
    group = client.get(f"/api/groups/{group_id}").json()
    assert [member["id"] for member in group["members"]] == [user["id"]]
    groups = client.get("/api/groups").json()["groups"]
    assert groups[0]["member_count"] == 1
    assert client.get("/api/admin/dashboard").json()["users"]["total"] == 1


def test_email_change_requires_current_password_and_preserves_session(client):
    register(client)
    assert client.put("/api/me/email", json={"email": "new@example.com"}).status_code == 422
    bad = client.put("/api/me/email", json={"email": "new@example.com", "password": "wrong-password"})
    assert bad.status_code == 400
    assert bad.json()["detail"] == "当前密码不正确"
    assert client.get("/api/me").json()["email"] == "alice@example.com"
    good = client.put("/api/me/email", json={"email": "new@example.com", "password": PASSWORD})
    assert good.status_code == 200
    assert good.json()["email"] == "new@example.com"


@pytest.mark.parametrize("accepted", [None, False])
def test_registration_requires_explicit_terms_acceptance(client, accepted):
    payload = registration_payload()
    if accepted is None:
        payload.pop("accept_terms")
    else:
        payload["accept_terms"] = accepted
    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "请先阅读并同意服务条款和隐私政策"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_terms_acceptance_records_version_and_utc_time_but_trial_does_not(client):
    user = register(client)
    with connect() as conn:
        row = conn.execute("SELECT terms_accepted_at,terms_version FROM users WHERE id = ?", (user["id"],)).fetchone()
    assert row["terms_version"] == TERMS_VERSION == "2026-10-03"
    assert datetime.fromisoformat(row["terms_accepted_at"]).utcoffset() == timezone.utc.utcoffset(None)
    client.post("/api/auth/logout")
    trial = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert trial.status_code == 201
    with connect() as conn:
        row = conn.execute("SELECT terms_accepted_at,terms_version FROM users WHERE id = ?", (trial.json()["id"],)).fetchone()
    assert row["terms_accepted_at"] is None
    assert row["terms_version"] is None


@pytest.mark.parametrize("path,title,keywords", [
    ("/terms", "服务条款", ["账号与安全", "AI", "付费与退款"]),
    ("/privacy", "隐私政策", ["收集", "第三方", "保存", "权利", "14"]),
])
def test_legal_pages_are_public_secure_and_escape_operator_fields(client, monkeypatch, path, title, keywords):
    operator = "<script>operator</script>"
    contact = '<script>contact</script>"&'
    monkeypatch.setenv("LEGAL_OPERATOR_NAME", operator)
    monkeypatch.setenv("LEGAL_CONTACT_EMAIL", contact)
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text.lower()
    assert 'lang="zh-CN"' in response.text
    assert f"<title>{title} · 欧叶OY</title>" in response.text
    assert 'href="/"' in response.text
    assert TERMS_VERSION in response.text
    assert "/static/style.css?v=" in response.text
    assert "/static/legal.css?v=1" in response.text
    assert all(word in response.text for word in keywords)
    assert "&lt;script&gt;operator&lt;/script&gt;" in response.text
    assert "&lt;script&gt;contact&lt;/script&gt;&quot;&amp;" in response.text
    assert "<script>" not in response.text
    assert "<style" not in response.text
    assert "style=" not in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "style-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" not in response.headers.get("cache-control", "")
    assert "etag" in response.headers or "max-age=" in response.headers.get("cache-control", "")
    cache_control = response.headers.get("cache-control", "").lower()
    assert "immutable" not in cache_control
    for directive in cache_control.split(","):
        if directive.strip().startswith(("max-age=", "s-maxage=")):
            assert int(directive.strip().split("=", 1)[1]) <= 3600
    assert "set-cookie" not in response.headers


def test_legal_pages_show_fallback_operator_and_contact(client):
    for path in ["/terms", "/privacy"]:
        page = client.get(path).text
        assert "本站管理员" in page
        assert "请通过站内渠道联系管理员" in page


@pytest.mark.parametrize("path,payload", [
    ("/api/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
    ("/api/me/sessions/revoke-others", {}),
    ("/api/me/delete-account", {"password": PASSWORD}),
])
def test_new_account_actions_require_login(client, path, payload):
    assert client.post(path, json=payload).status_code == 401
