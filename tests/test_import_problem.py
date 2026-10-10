"""F1 local link prefill: no external metadata requests or AI."""
from datetime import date
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import main
import routers.import_problem as ip


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    # Only local parsing; no AI configuration or external requests.
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 9, 19))
    main.init_db()

    app = FastAPI()
    app.include_router(ip.router)
    app.dependency_overrides[main.current_user] = lambda: {
        "id": 1,
        "username": "alice",
        "timezone": "Asia/Shanghai",
    }
    with main.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at)"
            " VALUES ('alice', 'x', 'Asia/Shanghai', '2026-09-19T00:00:00+00:00')"
        )
    return TestClient(app)


def test_illegal_domain_422(app_client):
    response = app_client.post(
        "/api/problems/fetch-from-url", json={"url": "https://example.com/problems/x"}
    )
    assert response.status_code == 422


def test_spoof_domain_422(app_client):
    # leetcode.com.evil.com 以 .com 结尾但不是 leetcode.com 子域，必须拒绝。
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://leetcode.com.evil.com/problems/two-sum/"},
    )
    assert response.status_code == 422


def test_subdomain_allowed(app_client, monkeypatch):
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://zh.leetcode.com/problems/two-sum/"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "two sum"


def test_leetcode_fetch_ok(app_client, monkeypatch):
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://leetcode.com/problems/two-sum/"},
    )
    assert response.status_code == 200
    body = response.json()
    # Local identifier only: metadata remains for the user to complete.
    assert body["title"] == "two sum"
    assert body["description"] == ""
    assert "<" not in body["description"]
    assert body["difficulty"] == ""
    assert body["tags"] == []
    assert body["source_url"] == "https://leetcode.com/problems/two-sum/"


def test_codeforces_fetch_ok(app_client, monkeypatch):
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://codeforces.com/problemset/problem/4/A"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Codeforces 4A"
    assert body["difficulty"] == ""
    assert body["tags"] == []
    assert body["source_url"] == "https://codeforces.com/problemset/problem/4/A"


def test_codeforces_contest_url_ok(app_client, monkeypatch):
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://codeforces.com/contest/4/problem/A"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Codeforces 4A"


def test_link_parsing_never_requests_third_party(app_client, monkeypatch):
    import socket
    original = socket.socket.connect
    def forbidden(sock, address):
        # Windows asyncio uses a loopback socketpair to run TestClient.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original(sock, address)
        pytest.fail("Link parsing must not request third-party metadata")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for url in ("https://leetcode.com/problems/two-sum/", "https://codeforces.com/contest/4/problem/A"):
        response = app_client.post("/api/problems/fetch-from-url", json={"url": url})
        assert response.status_code == 200
        assert response.json()["source_url"] == url


def test_luogu_fetch_ok(app_client):
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://www.luogu.com.cn/problem/p1001?contestId=1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "洛谷 P1001"
    assert body["description"] == ""


def test_luogu_bad_path_and_spoof_422(app_client):
    for url in (
        "https://www.luogu.com.cn/contest/1",
        "https://luogu.com.cn.evil.com/problem/P1001",
        "https://notluogu.com.cn/problem/P1001",
    ):
        response = app_client.post("/api/problems/fetch-from-url", json={"url": url})
        assert response.status_code == 422, url
