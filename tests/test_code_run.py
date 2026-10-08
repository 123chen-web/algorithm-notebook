"""Only an explicitly configured isolated runner may receive bounded source code."""
import base64
import json

import pytest

import main
from test_app import client, new_problem, register


def payload(**changes):
    return {"language": "Python", "code": "print(input())", "stdin": "hello", **changes}


def test_requires_login_owner_and_csrf(client):
    assert client.post("/api/mistakes/1/run", json=payload()).status_code == 401
    register(client)
    mistake = new_problem(client)[0]
    assert client.post(f"/api/mistakes/{mistake}/run", json=payload(), headers={"X-CSRF-Protection": ""}).status_code == 403
    assert client.post("/api/mistakes/99999/run", json=payload()).status_code == 404
    assert client.post("/api/mistakes/9223372036854775808/run", json=payload()).status_code == 404


def test_disabled_never_spawns_or_calls_a_service(client, monkeypatch):
    import code_runner
    monkeypatch.delenv("CODE_RUNNER_URL", raising=False)
    register(client)
    mistake = new_problem(client)[0]
    response = client.post(f"/api/mistakes/{mistake}/run", json=payload())
    assert response.status_code == 503
    assert "独立运行服务" in response.json()["detail"]


@pytest.mark.parametrize("changes", [{"language": "Bash"}, {"code": " "}, {"code": "x" * 40001}, {"stdin": "x" * 10001}, {"code": 123}, {"cpu_time_limit": 100}, {"callback_url": "http://localhost/"}, {"code": "\ud800"}, {"stdin": "\ud800"}])
def test_invalid_or_configurable_inputs_rejected(client, changes):
    register(client)
    mistake = new_problem(client)[0]
    assert client.post(f"/api/mistakes/{mistake}/run", content=json.dumps(payload(**changes)),
                       headers={"Content-Type": "application/json"}).status_code == 422


def test_fake_runner_result_does_not_change_notebook_or_ai_quota(client, monkeypatch):
    import code_runner
    register(client)
    mistake = new_problem(client)[0]
    before = client.get(f"/api/mistakes/{mistake}").json()
    quota = client.get("/api/me").json()["ai_daily_remaining"]
    calls = []
    def fake(language, code, stdin):
        calls.append((language, code, stdin))
        return {"status": "completed", "stdout": "hello\n", "stderr": "", "compile_output": "", "truncated": False}
    monkeypatch.setattr(code_runner, "run", fake)
    response = client.post(f"/api/mistakes/{mistake}/run", json=payload())
    assert response.status_code == 200
    assert response.json()["stdout"] == "hello\n"
    assert calls == [("Python", "print(input())", "hello")]
    assert client.get(f"/api/mistakes/{mistake}").json() == before
    assert client.get("/api/me").json()["ai_daily_remaining"] == quota


def test_other_users_record_is_hidden(client, monkeypatch):
    import code_runner
    from fastapi.testclient import TestClient
    register(client)
    mistake = new_problem(client)[0]
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as other:
        register(other, "bob")
        monkeypatch.setattr(code_runner, "run", lambda *args: pytest.fail("not the owner"))
        assert other.post(f"/api/mistakes/{mistake}/run", json=payload()).status_code == 404


def test_runner_request_limits_are_fixed_and_outputs_are_text(monkeypatch):
    import code_runner
    monkeypatch.setenv("CODE_RUNNER_URL", "https://runner.example.invalid")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    requests = []
    def request(url, token, data=None):
        requests.append((url, token, data))
        if data is not None:
            return {"token": "12345678-1234-1234-1234-123456789012"}
        return {"status": {"id": 3}, "stdout": base64.b64encode(b"<script>bad()</script>").decode(), "stderr": None, "compile_output": None}
    monkeypatch.setattr(code_runner, "_request", request)
    result = code_runner.run("Python", "print(1)", "")
    assert result == {"status": "completed", "stdout": "<script>bad()</script>", "stderr": "", "compile_output": "", "truncated": False}
    sent = requests[0][2]
    assert sent["language_id"] == 71
    assert sent["enable_network"] is False
    assert sent["cpu_time_limit"] == 2
    assert sent["memory_limit"] == 128000
    assert sent["max_processes_and_or_threads"] == 16
    assert set(sent) == {"source_code", "stdin", "language_id", "cpu_time_limit", "wall_time_limit", "memory_limit", "max_file_size", "max_processes_and_or_threads", "enable_network", "number_of_runs"}
    assert base64.b64decode(sent["source_code"]) == b"print(1)"
    assert "wait=false" in requests[0][0]


@pytest.mark.parametrize("url", ["http://external.example", "https://user:pass@runner.example", "https://runner.example/?secret=1", "file:///tmp/runner", "https://runner.example/#x"])
def test_bad_configuration_fails_closed_without_network(monkeypatch, url):
    import code_runner
    monkeypatch.setenv("CODE_RUNNER_URL", url)
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    monkeypatch.setattr(code_runner, "_request", lambda *a, **kw: pytest.fail("invalid config must not contact a service"))
    with pytest.raises(code_runner.RunnerUnavailable):
        code_runner.run("Python", "print(1)", "")


@pytest.mark.parametrize("reply", [{"status": {"id": True}}, {"status": {"id": 3}, "stdout": "not base64"}, {"status": {"id": 999}}, {"status": {"id": 13}, "message": "PRIVATE SERVER KEY"}])
def test_invalid_or_internal_runner_errors_are_not_exposed(monkeypatch, reply):
    import code_runner
    monkeypatch.setenv("CODE_RUNNER_URL", "https://runner.example.invalid")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    monkeypatch.setattr(code_runner, "_request", lambda url, token, data=None: {"token": "12345678-1234-1234-1234-123456789012"} if data is not None else reply)
    with pytest.raises(code_runner.RunnerUnavailable) as error:
        code_runner.run("Python", "print(1)", "")
    assert "PRIVATE" not in str(error.value)


def test_trial_ban_and_rate_limits_fail_before_runner(client, monkeypatch):
    import code_runner
    from db import connect
    monkeypatch.setattr(code_runner, "run", lambda *args: pytest.fail("must fail before execution"))
    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    mistake = new_problem(client)[0]
    assert client.post(f"/api/mistakes/{mistake}/run", json=payload()).status_code == 403
    client.post("/api/logout")
    owner = register(client)
    mistake = new_problem(client)[0]
    monkeypatch.setattr(main, "rate_limited", lambda *args: True)
    assert client.post(f"/api/mistakes/{mistake}/run", json=payload()).status_code == 429
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (owner["id"],))
    assert client.post(f"/api/mistakes/{mistake}/run", json=payload()).status_code == 401


def test_response_bounds_redirects_and_private_errors(monkeypatch):
    import code_runner
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == 65537
            return b"x" * limit
    class Opener:
        def open(self, request, timeout):
            assert request.get_header("X-auth-token") == "private-token"
            assert timeout == 5
            return Response()
    handlers = []
    def opener(*args):
        handlers.extend(args)
        return Opener()
    monkeypatch.setattr(code_runner, "build_opener", opener)
    with pytest.raises(code_runner.RunnerUnavailable) as error:
        code_runner._request("https://example.invalid/", "private-token")
    assert "private-token" not in str(error.value)
    assert handlers[0].proxies == {}
    assert handlers[1].redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None


def test_queue_timeout_releases_slot_and_large_output_is_clipped(monkeypatch):
    import code_runner
    monkeypatch.setenv("CODE_RUNNER_URL", "https://runner.example.invalid")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    class Slot:
        releases = 0
        def acquire(self, **kwargs): return True
        def release(self): self.releases += 1
    slot = Slot(); monkeypatch.setattr(code_runner, "_slots", slot)
    clock = iter([0, 0, 11])
    monkeypatch.setattr(code_runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(code_runner.time, "sleep", lambda value: None)
    monkeypatch.setattr(code_runner, "_request", lambda url, token, data=None: {"token": "12345678-1234-1234-1234-123456789012"} if data else {"status": {"id": 1}})
    with pytest.raises(code_runner.RunnerUnavailable, match="超时"):
        code_runner.run("Python", "print(1)", "")
    assert slot.releases == 1
    assert code_runner._output(base64.b64encode(b"x" * 8001).decode()) == ("x" * 8000, True)


def test_exec_format_error_is_a_runtime_result(monkeypatch):
    import code_runner
    monkeypatch.setenv("CODE_RUNNER_URL", "https://runner.example.invalid")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    monkeypatch.setattr(code_runner, "_request", lambda url, token, data=None:
        {"token": "12345678-1234-1234-1234-123456789012"} if data is not None else
        {"status": {"id": 14}, "stderr": base64.b64encode(b"Exec Format Error").decode()})
    result = code_runner.run("C++", "int main() {}", "")
    assert result["status"] == "runtime_error"
    assert result["stderr"] == "Exec Format Error"


@pytest.mark.parametrize("change", ["ban", "revoke"])
def test_authentication_change_before_dispatch_does_not_submit_code(client, monkeypatch, change):
    import code_runner
    from db import connect
    from fastapi import Request
    owner = register(client)
    mistake = new_problem(client)[0]
    monkeypatch.setattr(code_runner, "run", lambda *args: pytest.fail("revoked request must not submit source"))
    def revoked(request: Request):
        user = main.current_user(request)
        with connect(write=True) as conn:
            if change == "ban":
                conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (owner["id"],))
            else:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (owner["id"],))
        return user
    main.app.dependency_overrides[main.current_user] = revoked
    try:
        assert client.post(f"/api/mistakes/{mistake}/run", json=payload()).status_code == 401
    finally:
        main.app.dependency_overrides.pop(main.current_user)
