import hashlib
import time

import pytest
from fastapi.testclient import TestClient

import mailer
import main
from db import connect

PASSWORD = "a-test-password-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: messages.append((to, subject, body))
    )
    return messages


def register(client, username="alice", email=None):
    response = client.post("/api/auth/register", json={
        "username": username, "password": PASSWORD, "accept_terms": True,
        "invite_code": "test-invite", "email": email or f"{username}@example.com",
        "timezone": "Asia/Shanghai",
    })
    assert response.status_code == 201
    return response.json()


def request_change(client, email="new@example.com"):
    return client.put("/api/me/email", json={"email": email, "password": PASSWORD})


def token_from(sent):
    body = next(body for to, _, body in sent if to == "new@example.com")
    return body.split("email_token=")[1].split()[0]


def current_email(client):
    return client.get("/api/me").json()["email"]


def test_request_keeps_email_and_mails_both_addresses(client, sent):
    register(client)
    response = request_change(client)
    assert response.status_code == 200 and response.json()["pending"] is True
    assert current_email(client) == "alice@example.com"
    assert {to for to, _, _ in sent} == {"new@example.com", "alice@example.com"}
    token = token_from(sent)
    with connect() as conn:
        row = conn.execute("SELECT * FROM email_changes").fetchone()
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(tuple(row))


def test_confirm_applies_change_once(client, sent):
    register(client)
    request_change(client)
    token = token_from(sent)
    done = client.post("/api/me/email/confirm", json={"token": token})
    assert done.status_code == 200 and done.json()["email"] == "new@example.com"
    assert current_email(client) == "new@example.com"
    reuse = client.post("/api/me/email/confirm", json={"token": token})
    assert reuse.status_code == 400
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_changes").fetchone()[0] == 0


def test_confirm_requires_csrf_header(client, sent):
    register(client)
    request_change(client)
    response = client.post(
        "/api/me/email/confirm", json={"token": token_from(sent)},
        headers={"X-CSRF-Protection": "0"},
    )
    assert response.status_code == 403


def test_expired_token_is_rejected(client, sent):
    register(client)
    request_change(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE email_changes SET expires_at = ?", (int(time.time()) - 1,))
    response = client.post("/api/me/email/confirm", json={"token": token_from(sent)})
    assert response.status_code == 400
    assert current_email(client) == "alice@example.com"


def test_newer_request_replaces_older_token(client, sent):
    register(client)
    request_change(client)
    first = token_from(sent)
    sent.clear()
    request_change(client)
    assert client.post("/api/me/email/confirm", json={"token": first}).status_code == 400
    assert client.post("/api/me/email/confirm", json={"token": token_from(sent)}).status_code == 200


def test_email_taken_at_request_or_confirm_time(client, sent):
    register(client)
    client.post("/api/auth/logout")
    register(client, "bob", "taken@example.com")
    assert request_change(client, "alice@example.com").status_code == 409
    assert request_change(client).status_code == 200
    token = token_from(sent)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET email = 'new@example.com' WHERE username = 'alice'")
    response = client.post("/api/me/email/confirm", json={"token": token})
    assert response.status_code == 409
    assert current_email(client) == "taken@example.com"


def test_wrong_password_creates_no_pending_change(client, sent):
    register(client)
    response = client.put("/api/me/email", json={"email": "new@example.com", "password": "nope-nope-nope"})
    assert response.status_code == 400
    assert sent == []
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_changes").fetchone()[0] == 0


def test_old_address_missing_skips_notice(client, sent):
    register(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET email = NULL")
    assert request_change(client).status_code == 200
    assert [to for to, _, _ in sent] == ["new@example.com"]


def test_forgot_password_per_email_limit_keeps_response_and_old_token(client, sent, monkeypatch):
    # IP 级限流放宽，单独验证按邮箱的限流。
    monkeypatch.setattr(main, "FORGOT_PASSWORD_LIMIT", 100)
    register(client)
    client.post("/api/auth/logout")
    results = []
    for _ in range(3):
        results.append(client.post("/api/auth/forgot-password", json={"email": "alice@example.com"}))
    assert [to for to, _, _ in sent] == ["alice@example.com"] * 3
    with connect() as conn:
        before = conn.execute("SELECT token_hash FROM password_resets").fetchall()
    over = client.post("/api/auth/forgot-password", json={"email": "ALICE@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert over.status_code == 200 and over.json() == results[0].json() == unknown.json()
    assert len(sent) == 3
    with connect() as conn:
        assert conn.execute("SELECT token_hash FROM password_resets").fetchall() == before
