"""POST /api/client-errors：前端错误上报（只写日志）的后端测试。"""
import json
import logging

import pytest

from test_app import client, register  # noqa: F401  (client 是 fixture)

URL = "/api/client-errors"
LOGGER = "oy.client_error"


def valid(**override):
    body = {
        "message": "Cannot read properties of undefined (reading 'x')",
        "source": "app.js",
        "line": 12,
        "col": 5,
        "stack": "TypeError: boom\n    at f (/static/app.js:12:5)",
        "route": "#/app",
        "ua": "Mozilla/5.0 test",
        "build": "103",
    }
    body.update(override)
    return body


def records(caplog):
    return [r for r in caplog.records if r.name == LOGGER]


def test_valid_report_is_accepted_without_login_and_logged_once(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client.post(URL, json=valid())
    assert response.status_code == 204
    assert response.content == b""
    found = records(caplog)
    assert len(found) == 1
    entry = json.loads(found[0].getMessage())
    assert entry["message"].startswith("Cannot read")
    assert entry["source"] == "app.js" and entry["line"] == 12 and entry["route"] == "#/app"
    assert "user" not in entry  # 匿名


def test_logged_in_report_carries_only_the_user_id_and_nothing_sensitive(client, caplog):
    register(client, "alice", "alice-secret@example.com")
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client.post(
        URL, json=valid(), headers={"Authorization": "Bearer hunter2", "X-Forwarded-For": "203.0.113.9"}
    )
    assert response.status_code == 204
    (record,) = records(caplog)
    entry = json.loads(record.getMessage())
    assert isinstance(entry["user"], int)
    dump = record.getMessage() + repr(record.__dict__)
    for secret in ("alice-secret", "hunter2", "203.0.113.9", "session=", "testclient"):
        assert secret not in dump
    assert set(entry) <= {"message", "source", "line", "col", "stack", "route", "ua", "build", "user"}


def test_bad_session_cookie_is_treated_as_anonymous(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    client.cookies.set("session", "not-a-real-token")
    assert client.post(URL, json=valid()).status_code == 204
    assert "user" not in json.loads(records(caplog)[0].getMessage())


@pytest.mark.parametrize("bad", [
    {"message": ""},
    {"message": 5},
    {"line": "12"},
    {"line": -1},
    {"line": True},
    {"route": "#/app/123?x=1"},
    {"source": "https://evil.example/app.js"},
    {"build": "has space"},
    {"extra": "field"},
])
def test_invalid_or_extra_fields_are_rejected(client, caplog, bad):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client.post(URL, json=valid(**bad))
    assert response.status_code == 422
    assert records(caplog) == []


def test_missing_message_and_non_json_bodies_are_rejected(client):
    body = valid()
    del body["message"]
    assert client.post(URL, json=body).status_code == 422
    assert client.post(URL, content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post(URL, json=["a"]).status_code == 422


def test_overlong_text_is_truncated_not_trusted(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client.post(URL, json=valid(message="m" * 400, stack="s" * 1800, ua="u" * 300))
    assert response.status_code == 204
    entry = json.loads(records(caplog)[0].getMessage())
    assert len(entry["message"]) == 300 and len(entry["stack"]) == 1500 and len(entry["ua"]) == 200


def test_oversized_body_is_413(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    response = client.post(URL, json=valid(stack="s" * 5000))
    assert response.status_code == 413
    assert records(caplog) == []


def test_oversized_chunked_body_is_413(client):
    def chunks():
        for _ in range(10):
            yield b"x" * 1000

    response = client.post(URL, content=chunks(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_missing_csrf_header_is_still_refused(client):
    client.headers.pop("X-CSRF-Protection")
    assert client.post(URL, json=valid()).status_code == 403


def test_rate_limit_per_ip_returns_quiet_429(client, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    for _ in range(20):
        assert client.post(URL, json=valid()).status_code == 204
    response = client.post(URL, json=valid())
    assert response.status_code == 429
    assert response.content == b""
    assert len(records(caplog)) == 20
