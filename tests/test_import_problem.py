"""F1 题目导入（routers/import_problem.py）后端测试。

只测本模块：router 挂到独立 FastAPI() 上（main.py 的注册由协调人统一做）；
httpx 与 AI 视觉调用全部 mock，不发起真实网络请求。
DB fixture 仿照 tests/test_quick_record.py 的临时库做法，
但运行命令必须用 TMPDIR=~/workspace/pytest-tmp（仓库外）。
"""
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import routers.import_problem as ip


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_LEETCODE_PAYLOAD = {
    "data": {
        "question": {
            "questionId": "1",
            "title": "Two Sum",
            "translatedTitle": "两数之和",
            "content": "<p>给定一个整数数组 <code>nums</code>。</p>",
            "difficulty": "Easy",
            "topicTags": [
                {"name": "Array", "translatedName": "数组"},
                {"name": "Hash Table", "translatedName": "哈希表"},
            ],
        }
    }
}

_CODEFORCES_PAYLOAD = {
    "status": "OK",
    "result": {
        "problems": [
            {
                "contestId": 4,
                "index": "A",
                "name": "Watermelon",
                "rating": 800,
                "tags": ["brute force", "math"],
            }
        ]
    },
}

_SCREENSHOT_PREFILL = {
    "title": "截图题",
    "description": "题干内容",
    "difficulty": "中等",
    "tags": ["数组"],
}


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    # 与 test_app.client 一样的临时库隔离；AI 日额度设高，让截图 20 次上限先触发。
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("AI_DAILY_LIMIT", "30")
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


def _ai_usage(conn):
    row = conn.execute(
        "SELECT attempts FROM ai_usage WHERE user_id = 1 AND day = '2026-09-19'"
    ).fetchone()
    return row["attempts"] if row else 0


def _screenshot_usage(conn):
    # 校验失败（422）在建表之前就返回，表可能还不存在。
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'import_screenshot_daily'"
    ).fetchone()
    if not exists:
        return 0
    row = conn.execute(
        "SELECT attempts FROM import_screenshot_daily WHERE user_id = 1 AND day = '2026-09-19'"
    ).fetchone()
    return row["attempts"] if row else 0


def _fake_vision(content, media_type):
    return dict(_SCREENSHOT_PREFILL)


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
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(_LEETCODE_PAYLOAD))
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://zh.leetcode.com/problems/two-sum/"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "two sum"


def test_leetcode_fetch_ok(app_client, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResp(_LEETCODE_PAYLOAD))
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://leetcode.com/problems/two-sum/"},
    )
    assert response.status_code == 200
    body = response.json()
    # 中文标题优先 translatedTitle；content 去 HTML 标签；难度映射为中文。
    assert body["title"] == "two sum"
    assert body["description"] == ""
    assert "<" not in body["description"]
    assert body["difficulty"] == ""
    assert body["tags"] == []
    assert body["source_url"] == "https://leetcode.com/problems/two-sum/"


def test_codeforces_fetch_ok(app_client, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(_CODEFORCES_PAYLOAD))
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
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(_CODEFORCES_PAYLOAD))
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://codeforces.com/contest/4/problem/A"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Codeforces 4A"


def test_link_parsing_never_requests_third_party(app_client, monkeypatch):
    def boom(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", boom)
    response = app_client.post(
        "/api/problems/fetch-from-url",
        json={"url": "https://leetcode.com/problems/two-sum/"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "two sum"


def test_screenshot_success_and_quota_deduct(app_client, monkeypatch):
    monkeypatch.setattr(ip, "analyze_screenshot", _fake_vision)
    response = app_client.post(
        "/api/problems/parse-screenshot",
        files={"image": ("shot.png", b"\x89PNG-fake", "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "截图题"
    assert body["tags"] == ["数组"]
    assert body["screenshot_remaining"] == 19
    with main.connect() as conn:
        assert _ai_usage(conn) == 1
        assert _screenshot_usage(conn) == 1


def test_screenshot_ai_failure_refunds_quota(app_client, monkeypatch):
    def fail(content, media_type):
        raise HTTPException(502, "AI 失败")

    monkeypatch.setattr(ip, "analyze_screenshot", fail)
    response = app_client.post(
        "/api/problems/parse-screenshot",
        files={"image": ("shot.png", b"\x89PNG-fake", "image/png")},
    )
    assert response.status_code == 502
    with main.connect() as conn:
        # 通用 AI 额度按现有机制退还；截图 20 次上限不退（防重试刷额度）。
        assert _ai_usage(conn) == 0
        assert _screenshot_usage(conn) == 1


def test_screenshot_daily_limit_429(app_client, monkeypatch):
    monkeypatch.setattr(ip, "analyze_screenshot", _fake_vision)
    payload = {"image": ("shot.png", b"\x89PNG-fake", "image/png")}
    for _ in range(20):
        assert app_client.post("/api/problems/parse-screenshot", files=payload).status_code == 200
    response = app_client.post("/api/problems/parse-screenshot", files=payload)
    assert response.status_code == 429
    assert "20" in response.json()["detail"]


def test_screenshot_invalid_content_type_422(app_client, monkeypatch):
    monkeypatch.setattr(ip, "analyze_screenshot", _fake_vision)
    response = app_client.post(
        "/api/problems/parse-screenshot",
        files={"image": ("evil.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 422


def test_screenshot_oversize_422(app_client, monkeypatch):
    monkeypatch.setattr(ip, "analyze_screenshot", _fake_vision)
    response = app_client.post(
        "/api/problems/parse-screenshot",
        files={"image": ("big.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")},
    )
    assert response.status_code == 422
    with main.connect() as conn:
        # 校验失败不扣任何额度。
        assert _ai_usage(conn) == 0
        assert _screenshot_usage(conn) == 0
