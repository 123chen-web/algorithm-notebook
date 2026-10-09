"""登录 / 注册 / 改密 / 忘记密码的边界补充测试（只新增，不改业务代码）。"""
import sqlite3

import pytest

import main
from db import connect
from test_app import client, register


PASSWORD = "a-test-password-123"


def registration_payload(username="extrawuser", **overrides):
    payload = {
        "username": username,
        "password": PASSWORD,
        "email": f"{username}@example.com",
        "timezone": "Asia/Shanghai",
        "invite_code": "test-invite",
        "accept_terms": True,
    }
    payload.update(overrides)
    return payload


def login(client, username="alice", password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


# ---------------- 注册边界 ----------------

def test_register_without_invite_code_configured_returns_503(client, monkeypatch):
    monkeypatch.delenv("INVITE_CODE", raising=False)
    response = client.post("/api/auth/register", json=registration_payload("noinvite"))
    assert response.status_code == 503
    assert "邀请码" in response.json()["detail"]


def test_register_with_placeholder_invite_code_returns_503(client, monkeypatch):
    monkeypatch.setenv("INVITE_CODE", "change-me")
    response = client.post("/api/auth/register", json=registration_payload("placeholder"))
    assert response.status_code == 503


def test_register_wrong_invite_code_is_forbidden(client):
    response = client.post(
        "/api/auth/register",
        json=registration_payload("wrongcode", invite_code="nope"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "邀请码不正确"


def test_register_must_accept_terms(client):
    response = client.post(
        "/api/auth/register",
        json=registration_payload("terms", accept_terms=False),
    )
    assert response.status_code == 400
    assert "条款" in response.json()["detail"]


def test_register_duplicate_username_with_different_email_is_409(client):
    register(client, "dupeuser", email="first@example.com")
    response = client.post(
        "/api/auth/register",
        json=registration_payload("dupeuser", email="second@example.com"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "用户名已被使用"


def test_register_duplicate_email_is_409(client):
    register(client, "firstuser", email="same@example.com")
    response = client.post(
        "/api/auth/register",
        json=registration_payload("seconduser", email="same@example.com"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "这个邮箱已经被使用"


def test_register_reserved_username_is_rejected(client):
    response = client.post(
        "/api/auth/register",
        json=registration_payload("admin"),
    )
    assert response.status_code == 400
    assert "保留" in response.json()["detail"]


def test_register_email_is_normalized_to_lowercase(client):
    response = client.post(
        "/api/auth/register",
        json=registration_payload("casemail", email="CasEmail@Example.COM"),
    )
    assert response.status_code == 201
    with connect() as conn:
        row = conn.execute(
            "SELECT email FROM users WHERE username = ?", ("casemail",)
        ).fetchone()
    assert row["email"] == "casemail@example.com"


# ---------------- 登录边界 ----------------

def test_login_wrong_password_returns_401(client):
    register(client, "logintarget")
    response = login(client, "logintarget", "wrong-password-999")
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码不正确"


def test_login_nonexistent_user_returns_401(client):
    response = login(client, "ghost-user-404")
    assert response.status_code == 401


def test_login_unknown_user_does_not_set_cookie(client):
    response = login(client, "ghost-user-404")
    assert "session" not in response.cookies


def test_login_success_sets_session_and_me_works(client):
    register(client, "oklogin")
    response = login(client, "oklogin")
    assert response.status_code == 200
    assert client.get("/api/me").status_code == 200


def test_banned_user_cannot_login(client):
    register(client, "banneduser")
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned = 1 WHERE username = ?", ("banneduser",))
    response = login(client, "banneduser")
    assert response.status_code == 403
    assert "封禁" in response.json()["detail"]


def test_login_rate_limits_after_repeated_failures(client):
    register(client, "ratelogin")
    # LOGIN_LIMIT = 10 / window；第 11 次进入限流（不限用户名，按 IP）。
    for _ in range(10):
        login(client, "ratelogin", "bad-password-000")
    blocked = login(client, "ratelogin", "bad-password-000")
    assert blocked.status_code == 429


def test_logout_invalidates_session(client):
    register(client, "logoutuser")
    assert client.get("/api/me").status_code == 200
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    assert client.get("/api/me").status_code == 401


# ---------------- 忘记 / 重置密码 ----------------

def test_forgot_password_always_returns_ok_even_for_unknown_email(client, monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_password_reset_email",
                        lambda email, username, token: sent.append((email, token)))
    response = client.post(
        "/api/auth/forgot-password", json={"email": "nobody-here@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == []


def test_forgot_password_creates_reset_token_for_real_user(client, monkeypatch):
    sent = []
    monkeypatch.setattr(main, "send_password_reset_email",
                        lambda email, username, token: sent.append((email, token)))
    register(client, "forgotuser", email="forgot@example.com")
    # register() 已建立会话；先登出再走忘记密码流程。
    client.post("/api/auth/logout")
    response = client.post(
        "/api/auth/forgot-password", json={"email": "forgot@example.com"}
    )
    assert response.status_code == 200
    assert len(sent) == 1
    token = sent[0][1]
    reset = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "brand-new-pass-777"}
    )
    assert reset.status_code == 200
    # 旧密码失效、新密码可登录。
    assert login(client, "forgotuser", PASSWORD).status_code == 401
    assert login(client, "forgotuser", "brand-new-pass-777").status_code == 200


def test_reset_password_with_garbage_token_returns_invalid_token(client):
    response = client.post(
        "/api/auth/reset-password",
        json={"token": "not-a-real-token", "password": "another-pass-123"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_reset_password_rejects_weak_password_with_weak_password_code(client):
    # 造一个真实有效 token，但新密码太常见。
    user = register(client, "weakreset", email="weak@example.com")
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (main.token_hash("weak-reset-token"), user["id"],
             int(main.time.time()) + 3600),
        )
    response = client.post(
        "/api/auth/reset-password",
        json={"token": "weak-reset-token", "password": "12345678"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "weak_password"


def test_reset_password_invalidates_existing_sessions(client):
    user = register(client, "sessionreset", email="session@example.com")
    # 当前 client 持有 session；再发一个新 token 重置密码。
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (main.token_hash("session-reset-token"), user["id"],
             int(main.time.time()) + 3600),
        )
    response = client.post(
        "/api/auth/reset-password",
        json={"token": "session-reset-token", "password": "reset-pass-888"},
    )
    assert response.status_code == 200
    # 重置后旧会话立即失效。
    assert client.get("/api/me").status_code == 401


# ---------------- 改密码 ----------------

def test_change_password_requires_current_password(client):
    register(client, "changepw")
    response = client.post(
        "/api/me/password",
        json={"current_password": "wrong-current-000", "new_password": "new-pass-1234"},
    )
    assert response.status_code == 400
    assert "当前密码" in response.json()["detail"]


def test_change_password_rejects_same_new_password(client):
    register(client, "samepw")
    response = client.post(
        "/api/me/password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
    )
    assert response.status_code == 400
    assert "相同" in response.json()["detail"]


def test_change_password_revokes_other_sessions(client):
    register(client, "multisession", email="multi@example.com")
    # 用第二个 TestClient 模拟另一台设备的会话。
    from fastapi.testclient import TestClient
    import main as main_module
    other = TestClient(main_module.app, headers={"X-CSRF-Protection": "1"})
    other.post("/api/auth/login", json={"username": "multisession", "password": PASSWORD})
    assert other.get("/api/me").status_code == 200

    response = client.post(
        "/api/me/password",
        json={"current_password": PASSWORD, "new_password": "rotated-pass-456"},
    )
    assert response.status_code == 200
    assert response.json()["revoked_sessions"] >= 1
    # 另一台设备的旧会话已失效。
    assert other.get("/api/me").status_code == 401
    # 改密后自己的新会话仍然有效。
    assert client.get("/api/me").status_code == 200
    # 新密码可以登录。
    fresh = TestClient(main_module.app, headers={"X-CSRF-Protection": "1"})
    assert fresh.post(
        "/api/auth/login",
        json={"username": "multisession", "password": "rotated-pass-456"},
    ).status_code == 200


# ---------------- 体验账号 ----------------

def test_trial_account_creation_returns_trial_user(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    assert response.json()["username"].startswith("trial_")
    me = client.get("/api/me").json()
    assert me["is_trial"] is True


def test_trial_account_falls_back_to_503_when_usernames_collide(client, monkeypatch):
    # 预先把 trial_deadbeefcafe 占掉，再让用户名生成器永远返回同一个合法 hex，
    # 这样 5 次尝试全部撞唯一索引，走到兜底 503。
    fixed_hex = "deadbeefcafe"
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at, is_trial) "
            "VALUES (?, ?, 'Asia/Shanghai', ?, 1)",
            (f"trial_{fixed_hex}", main.password_hash("x"), main.utc_now()),
        )
    real_token_hex = main.secrets.token_hex

    def fake_token_hex(n):
        # password_hash 也会调 token_hex 取 salt，只拦截建号用的 6 字节调用。
        if n == 6:
            return fixed_hex
        return real_token_hex(n)

    monkeypatch.setattr(main.secrets, "token_hex", fake_token_hex)
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 503
