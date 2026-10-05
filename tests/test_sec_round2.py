"""SEC 第二轮：认证、并发写锁、存量身份与注销边界的数据库回归。"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

import db
import main
from db import connect
from test_app import client, register
from test_account_security import (
    PASSWORD, NEW_PASSWORD, NOW, add_session, insert_user, login,
    registration_payload, reset_token,
)


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, request):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    if "client" in request.fixturenames:
        monkeypatch.setenv("AVATAR_DIR", str(request.getfixturevalue("tmp_path") / "avatars"))


def account_state(user_id):
    with connect() as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        sessions = [tuple(row) for row in conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY token_hash", (user_id,)
        )]
        resets = [tuple(row) for row in conn.execute(
            "SELECT * FROM password_resets WHERE user_id = ? ORDER BY token_hash", (user_id,)
        )]
    return user, sessions, resets


def sensitive_request(client, surface, password=PASSWORD, email="changed@example.com"):
    if surface == "email":
        return client.put("/api/me/email", json={"email": email, "password": password})
    if surface == "delete":
        return client.post("/api/me/delete-account", json={"password": password})
    return client.post("/api/me/password", json={
        "current_password": password, "new_password": NEW_PASSWORD,
    })


def test_a_shared_guard_counts_failures_before_hash_without_database(monkeypatch):
    """数据库 I/O 被替换；直接验证实际限流与哈希调用顺序，不创建临时目录。"""
    main.reset_rate_limits()
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = {"password_hash": "stored-hash"}

    @contextmanager
    def account_lookup(**kwargs):
        yield conn

    monkeypatch.setattr(main, "connect", account_lookup)
    matches = Mock(return_value=False)
    monkeypatch.setattr(main, "password_matches", matches)
    request = SimpleNamespace(client=SimpleNamespace(host="sec-guard-unit-ip"))
    try:
        for _ in range(5):
            with pytest.raises(HTTPException) as failure:
                main.verify_current_password(42, "wrong-password", request)
            assert failure.value.status_code == 400
        with pytest.raises(HTTPException) as blocked:
            main.verify_current_password(42, "correct-password", request)
        assert blocked.value.status_code == 429
        assert matches.call_count == 5
        assert conn.execute.call_count == 5
        matches.return_value = True
        assert main.verify_current_password(43, "correct-password", request) == "stored-hash"
        assert "sec-password-user:43" not in main._rate_buckets
    finally:
        main.reset_rate_limits()


@pytest.mark.parametrize("valid_session", [False, True], ids=["revoked", "active"])
def test_d_revoke_route_rechecks_session_before_writes_without_database(monkeypatch, valid_session):
    """模拟会话在认证依赖通过后失效，写边界必须再次检查。"""
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = {"exists": 1} if valid_session else None
    conn.execute.return_value.rowcount = 2

    @contextmanager
    def transaction(*, write=False):
        assert write
        yield conn

    monkeypatch.setattr(main, "connect", transaction)
    monkeypatch.setattr(main, "recheck_account", lambda conn, uid: {"id": uid})
    request = SimpleNamespace(cookies={"session": "inflight-token"})
    if valid_session:
        assert main.revoke_sessions(request, {"id": 42}) == {"ok": True, "revoked": 2}
    else:
        with pytest.raises(HTTPException) as revoked:
            main.revoke_sessions(request, {"id": 42})
        assert revoked.value.status_code == 401
        assert conn.execute.call_count == 1


@pytest.mark.parametrize("surface", ["email", "password", "delete"])
def test_a_shared_failure_limit_checks_before_password_hash(client, monkeypatch, surface):
    user = register(client)
    before = account_state(user["id"])
    original = main.password_matches
    calls = []

    def counted(password, stored):
        calls.append(password)
        return original(password, stored)

    monkeypatch.setattr(main, "password_matches", counted)
    # 三个入口共享一个用户的失败预算。
    for entry in ["email", "password", "delete", "email", "password"]:
        assert sensitive_request(client, entry, "incorrect-password").status_code == 400
    assert len(calls) == 5
    response = sensitive_request(client, surface)
    assert response.status_code == 429
    assert response.json()["detail"] == "尝试次数过多，请 15 分钟后再试"
    assert len(calls) == 5, "超限请求不能执行 PBKDF2"
    assert account_state(user["id"]) == before


def test_a_success_clears_only_user_failures_and_window_expires(client, monkeypatch):
    user = register(client)
    now = main.time.time()
    monkeypatch.setattr(main.time, "time", lambda: now)
    for _ in range(4):
        assert sensitive_request(client, "delete", "incorrect-password").status_code == 400
    assert sensitive_request(client, "email").status_code == 200
    for _ in range(5):
        assert sensitive_request(client, "password", "incorrect-password").status_code == 400
    assert sensitive_request(client, "email").status_code == 429
    now += 15 * 60 + 1
    assert sensitive_request(client, "email").status_code == 200
    assert account_state(user["id"])[0]["deleted_at"] is None


def test_a_ip_budget_counts_successes_across_users_before_hash(client, monkeypatch):
    user = register(client)
    original = main.password_matches
    calls = []

    def counted(password, stored):
        calls.append(password)
        return original(password, stored)

    monkeypatch.setattr(main, "password_matches", counted)
    for _ in range(19):
        assert sensitive_request(client, "email").status_code == 200
    client.post("/api/auth/logout")
    other = register(client, "bob")
    assert sensitive_request(client, "email", email="bob-changed@example.com").status_code == 200
    before_user = account_state(user["id"])
    before_other = account_state(other["id"])
    before_calls = len(calls)
    response = sensitive_request(client, "password")
    assert response.status_code == 429
    assert len(calls) == before_calls
    assert sensitive_request(client, "delete").status_code == 429
    assert len(calls) == before_calls
    assert account_state(user["id"]) == before_user
    assert account_state(other["id"]) == before_other
    monkeypatch.setattr(main, "client_ip", lambda request: "different-test-ip")
    assert sensitive_request(client, "email", email="bob-changed@example.com").status_code == 200


@pytest.mark.parametrize("failure", ["password", "duplicate"])
def test_b_email_failure_preserves_reset_tokens_and_account(client, failure):
    user = register(client)
    reset_token(user["id"], "old-email-token")
    reset_token(user["id"], "second-old-email-token")
    if failure == "duplicate":
        with connect(write=True) as conn:
            other_id = insert_user(conn, "other")
            conn.execute("UPDATE users SET email='occupied@example.com' WHERE id=?", (other_id,))
    before = account_state(user["id"])
    response = client.put("/api/me/email", json={
        "email": "occupied@example.com" if failure == "duplicate" else "changed@example.com",
        "password": PASSWORD if failure == "duplicate" else "wrong-password",
    })
    assert response.status_code == (409 if failure == "duplicate" else 400)
    assert account_state(user["id"]) == before


def test_b_email_success_invalidates_all_old_tokens_only_for_that_user(client):
    user = register(client)
    reset_token(user["id"], "old-email-token")
    reset_token(user["id"], "second-old-email-token")
    with connect(write=True) as conn:
        other_id = insert_user(conn, "other")
    reset_token(other_id, "unrelated-reset-token")
    other_before = account_state(other_id)
    assert sensitive_request(client, "email").status_code == 200
    after = account_state(user["id"])
    assert after[0]["email"] == "changed@example.com"
    assert after[2] == []
    assert account_state(other_id) == other_before
    response = client.post("/api/auth/reset-password", json={
        "token": "old-email-token", "password": NEW_PASSWORD,
    })
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_c_password_rotation_invalidates_old_token_and_keeps_other_user(client):
    user = register(client)
    old_token = client.cookies.get("session")
    with connect(write=True) as conn:
        add_session(conn, user["id"], "other-device")
        other_id = insert_user(conn, "other")
        add_session(conn, other_id, "unrelated-device")
        add_session(conn, other_id, "unrelated-expired-device")
        conn.execute("UPDATE sessions SET expires_at=1 WHERE token_hash=?", (
            main.token_hash("unrelated-expired-device"),
        ))
    unrelated = account_state(other_id)
    assert sensitive_request(client, "password").status_code == 200
    new_token = client.cookies.get("session")
    assert new_token and new_token != old_token
    assert client.get("/api/me").json()["id"] == user["id"]
    assert client.get("/api/me", headers={"Cookie": f"session={old_token}"}).status_code == 401
    assert client.get("/api/me", headers={"Cookie": "session=other-device"}).status_code == 401
    assert account_state(other_id) == unrelated
    assert {row[0] for row in account_state(user["id"])[1]} == {main.token_hash(new_token)}
    assert main.password_matches(NEW_PASSWORD, account_state(user["id"])[0]["password_hash"])


@pytest.mark.parametrize("surface", ["password", "delete", "revoke"])
@pytest.mark.parametrize("replacement", [False, True], ids=["removed", "different-owner"])
def test_d_revoked_inflight_session_is_rechecked_inside_write_transaction(
    client, monkeypatch, surface, replacement
):
    user = register(client)
    request_token = client.cookies.get("session")
    with connect(write=True) as conn:
        add_session(conn, user["id"], "surviving-device")
        other_id = insert_user(conn, "other")
    before_user = account_state(user["id"])[0]
    original = main.connect
    interleaved = False

    @contextmanager
    def revoke_before_write(*args, **kwargs):
        nonlocal interleaved
        if kwargs.get("write") and not interleaved:
            interleaved = True
            with original(write=True) as conn:
                conn.execute("DELETE FROM sessions WHERE token_hash=?", (main.token_hash(request_token),))
                if replacement:
                    add_session(conn, other_id, request_token)
        with original(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(main, "connect", revoke_before_write)
    if surface == "revoke":
        response = client.post("/api/me/sessions/revoke-others")
    else:
        response = sensitive_request(client, surface)
    assert interleaved
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert account_state(user["id"])[0] == before_user
    with connect() as conn:
        assert conn.execute("SELECT user_id FROM sessions WHERE token_hash=?", (
            main.token_hash("surviving-device"),
        )).fetchone()[0] == user["id"]


def test_d_two_devices_cannot_revoke_each_other_after_first_revoke(client):
    user = register(client)
    with connect(write=True) as conn:
        add_session(conn, user["id"], "second-device")
    response = client.post("/api/me/sessions/revoke-others")
    assert response.status_code == 200 and response.json()["revoked"] == 1
    assert client.post("/api/me/sessions/revoke-others", headers={
        "Cookie": "session=second-device",
    }).status_code == 401
    assert client.get("/api/me").status_code == 200
    assert len(account_state(user["id"])[1]) == 1


@pytest.mark.parametrize("character", ["\u200b", "\u202e", "\x01"], ids=["zero-width", "direction", "control"])
@pytest.mark.parametrize("surface", ["register", "rename"])
def test_e_rejects_invisible_characters_without_changing_accounts(client, character, surface):
    if surface == "register":
        response = client.post("/api/auth/register", json=registration_payload("al" + character + "ice"))
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    else:
        user = register(client)
        before = account_state(user["id"])
        response = client.put("/api/me/username", json={"username": "al" + character + "ice"})
        assert account_state(user["id"]) == before
    assert response.status_code == 400
    assert response.json()["detail"] == "用户名不能包含不可见字符"


def test_e_invisible_reserved_name_cannot_bypass_and_legacy_exact_login_survives(client):
    response = client.post("/api/auth/register", json=registration_payload("ad\u200bmin"))
    assert response.status_code == 400
    assert response.json()["detail"] == "用户名不能包含不可见字符"
    with connect(write=True) as conn:
        user_id = insert_user(conn, "legacy\u200bname")
    assert login(client, "legacy\u200bname").status_code == 200
    assert client.get("/api/me").json()["id"] == user_id


@pytest.mark.parametrize("character", ["\x00", "\t", "\n", "\u200b", "\u200f", "\u2060", "\ufeff", "\u202a", "\u2069"])
def test_e_normalizer_rejects_controls_even_before_trimming(character):
    with pytest.raises(ValueError, match="用户名不能包含不可见字符"):
        db.normalize_username(character + "alice")
    assert db.normalize_username(" ＡＬＩＣＥ ") == "alice"


def test_i_login_input_preserves_legacy_exact_name_before_lookup():
    assert main.Credentials(username="legacy\t", password=PASSWORD).username == "legacy\t"


@pytest.mark.parametrize("token_kind", ["missing", "empty", "expired", "at-expiry"])
def test_f_invalid_reset_tokens_have_machine_code_and_do_not_change_account(client, monkeypatch, token_kind):
    user = register(client)
    if token_kind in ("expired", "at-expiry"):
        reset_token(user["id"], "expired-token")
        now = int(main.time.time())
        monkeypatch.setattr(main.time, "time", lambda: now)
        with connect(write=True) as conn:
            conn.execute("UPDATE password_resets SET expires_at=?", (now if token_kind == "at-expiry" else 1,))
    before = account_state(user["id"])
    response = client.post("/api/auth/reset-password", json={
        "token": "expired-token" if token_kind in ("expired", "at-expiry") else "" if token_kind == "empty" else "missing-token",
        "password": "short",
    })
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"
    assert response.json()["detail"] == "重置链接无效或已过期，请重新申请"
    assert account_state(user["id"]) == before


@pytest.mark.parametrize("password", ["", "short"], ids=["empty-password", "short-password"])
def test_f_valid_token_weak_password_can_retry_same_link(client, password):
    user = register(client)
    token = reset_token(user["id"])
    before = account_state(user["id"])
    response = client.post("/api/auth/reset-password", json={"token": token, "password": password})
    assert response.status_code == 400
    assert response.json()["code"] == "weak_password"
    assert response.json()["detail"] == "密码至少 8 位"
    assert account_state(user["id"]) == before
    assert client.post("/api/auth/reset-password", json={"token": token, "password": NEW_PASSWORD}).status_code == 200
    assert account_state(user["id"])[1:] == ([], [])
    assert main.password_matches(NEW_PASSWORD, account_state(user["id"])[0]["password_hash"])


def test_k_avatar_unlink_failure_is_logged_and_deleted_avatar_stays_private(client, monkeypatch, caplog):
    user = register(client)
    avatar = main.avatar_path(user["id"])
    avatar.write_bytes(b"stand-in-avatar")
    assert client.get(f"/api/users/{user['id']}/avatar").status_code == 200
    original = Path.unlink

    def failing_unlink(path, *args, **kwargs):
        if path == avatar:
            raise PermissionError("SEC simulated avatar deletion denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    response = sensitive_request(client, "delete")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert avatar.exists()
    assert account_state(user["id"])[0]["deleted_at"]
    assert account_state(user["id"])[1] == []
    assert "SEC simulated avatar deletion denied" in caplog.text
    register(client, "other")
    assert client.get(f"/api/users/{user['id']}/avatar").status_code == 404


@pytest.mark.parametrize("occupied", [0, 1, 3])
def test_l_anonymous_name_chooses_first_available_suffix(client, occupied):
    user = register(client)
    prefix = f"已注销用户 #{user['id']}"
    names = [prefix] + [f"{prefix}-{n}" for n in range(2, occupied + 1)]
    with connect(write=True) as conn:
        for name in names[:occupied]:
            insert_user(conn, name)
    response = sensitive_request(client, "delete")
    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    expected = prefix if occupied == 0 else f"{prefix}-{occupied + 1}"
    assert account_state(user["id"])[0]["username"] == expected
    assert account_state(user["id"])[0]["deleted_at"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == occupied + 1


@pytest.mark.parametrize("name", ["alice", "ａｌｉｃｅ", "legacy\t"], ids=["ascii", "fullwidth", "trailing-control"])
def test_i_exact_login_wins_over_normalized_collision(client, name):
    with connect(write=True) as conn:
        first_id = insert_user(conn, "alice")
        second_id = insert_user(conn, "ａｌｉｃｅ", password=NEW_PASSWORD)
        legacy_id = insert_user(conn, "legacy\t")
        insert_user(conn, "legacy", password=NEW_PASSWORD)
    expected = {"alice": first_id, "ａｌｉｃｅ": second_id, "legacy\t": legacy_id}[name]
    password = NEW_PASSWORD if name == "ａｌｉｃｅ" else PASSWORD
    assert login(client, name, password).status_code == 200
    assert client.get("/api/me").json()["id"] == expected
    assert login(client, "ＡＬＩＣＥ", PASSWORD).status_code == 401
    assert client.get("/api/me").json()["id"] == expected


def test_b_email_write_failure_rolls_back_email_and_reset_tokens(client):
    user = register(client)
    reset_token(user["id"])
    before = account_state(user["id"])
    with connect(write=True) as conn:
        conn.execute(
            "CREATE TRIGGER sec_fail_reset_delete BEFORE DELETE ON password_resets "
            "BEGIN SELECT RAISE(ABORT, 'SEC simulated reset deletion failure'); END"
        )
    assert sensitive_request(client, "email").status_code == 409
    assert account_state(user["id"]) == before


def test_k_avatar_flag_ignores_leftover_files_for_deleted_and_missing_users(client):
    user = register(client)
    main.avatar_path(user["id"]).write_bytes(b"avatar")
    assert main.sec_has_avatar(user["id"]) is True
    assert client.get("/api/me").json()["has_avatar"] is True
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET deleted_at=? WHERE id=?", (NOW, user["id"]))
    assert main.sec_has_avatar(user["id"]) is False
    assert main.sec_has_avatar(999) is False


def test_h_existing_admin_survives_application_startup_and_config_changes(client, monkeypatch):
    from fastapi.testclient import TestClient

    user = register(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (user["id"],))
    before = account_state(user["id"])[0]
    monkeypatch.setenv("ADMIN_USERNAME", "differentoperator")
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as restarted:
        assert login(restarted).status_code == 200
        assert restarted.get("/api/me").json()["is_admin"] is True
    assert account_state(user["id"])[0] == before
