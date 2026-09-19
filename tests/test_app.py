from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import ai
import main
from db import connect
from scheduler import schedule, today_in_timezone


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    monkeypatch.setattr(
        main, "today_for", lambda user: date(2026, 9, 19)
    )

    # 所有 AI 测试都会替换 ai.generate，不会发起真实 API 请求。
    with TestClient(
        main.app,
        headers={"X-CSRF-Protection": "1"},
    ) as instance:
        yield instance


def register(client, username="alice"):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 201
    return response.json()


def new_problem(client):
    response = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "维护闭区间，但对结束条件理解不清楚。",
            "mistakes": [
                "循环结束条件漏掉 left == right。",
                "没有处理空数组。",
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["mistake_ids"]


def test_sm2_success_failure_and_floor():
    day = date(2026, 9, 19)

    first = schedule(0, 0, 2.5, 4, day)
    assert first["interval_days"] == 1
    assert first["due_date"] == "2026-09-20"

    second = schedule(1, 1, first["ease_factor"], 4, day)
    assert second["interval_days"] == 6

    third = schedule(2, 6, second["ease_factor"], 4, day)
    assert third["interval_days"] == 15

    failed = schedule(3, 15, 2.5, 0, day)
    assert failed["repetitions"] == 0
    assert failed["interval_days"] == 1
    assert failed["ease_factor"] == 1.7

    floor = schedule(0, 1, 1.3, 0, day)
    assert floor["ease_factor"] == 1.3

    # 浮点间隔向上取整。
    assert schedule(2, 6, 2.6, 5, day)["interval_days"] == 16

    with pytest.raises(ValueError):
        schedule(0, 0, 2.5, 6, day)


def test_timezone_day_boundary():
    now = datetime(2026, 9, 19, 16, 30, tzinfo=timezone.utc)
    assert today_in_timezone("Asia/Taipei", now) == date(2026, 9, 20)
    assert today_in_timezone("America/Los_Angeles", now) == date(2026, 9, 19)


def test_review_is_per_mistake_and_prevents_duplicate(client):
    register(client)
    first, second = new_problem(client)

    # 模拟逾期，验证下一次日期从实际复习日计算。
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET due_date = ? WHERE id = ?",
            ("2026-09-01", first),
        )

    due = client.get("/api/mistakes").json()
    assert len(due["items"]) == 2

    response = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 0},
    )
    assert response.status_code == 200
    assert response.json()["due_date"] == "2026-09-20"

    # 相同版本的重复提交不能再次延长间隔。
    duplicate = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 0},
    )
    assert duplicate.status_code == 409

    # 即使带最新版本，未到期也不能再次评分。
    early = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 1},
    )
    assert early.status_code == 409

    remaining = client.get("/api/mistakes").json()["items"]
    assert [item["id"] for item in remaining] == [second]
    assert remaining[0]["version"] == 0

    detail = client.get(f"/api/mistakes/{first}").json()
    assert len(detail["reviews"]) == 1

    all_items = client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"]
    assert len(all_items) == 2


def test_ai_persists_results_and_limits_attempts(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]

    def failed_generation(item):
        raise HTTPException(502, "模拟 AI 失败")

    monkeypatch.setattr(ai, "generate", failed_generation)
    failed = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert failed.status_code == 502
    assert client.get(
        f"/api/mistakes/{mistake_id}"
    ).json()["variants"] == []

    monkeypatch.setattr(
        ai,
        "generate",
        lambda item: {
            "description": "新题目：在有序数组中查找第一个不小于目标值的位置。",
            "model": "mock-model",
        },
    )
    generated = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert generated.status_code == 201
    variant_id = generated.json()["id"]

    saved = client.put(
        f"/api/variants/{variant_id}/result",
        json={
            "result": "solved",
            "answer_code": "def lower_bound(a, x):\n    pass\n",
            "notes": "这次正确处理了闭区间的结束条件。",
        },
    )
    assert saved.status_code == 200

    # 失败的一次也计入今日两次额度。
    limited = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert limited.status_code == 429

    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    assert detail["variants"][0]["result"] == "solved"
    assert detail["variants"][0]["notes"]
    assert detail["version"] == 0
    assert detail["due_date"] == "2026-09-19"


def test_user_isolation(client, monkeypatch):
    register(client, "alice")
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(
        ai,
        "generate",
        lambda item: {"description": "模拟题目", "model": "mock-model"},
    )
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["id"]

    client.post("/api/auth/logout")
    register(client, "bob")

    assert client.get("/api/mistakes").json()["items"] == []
    assert client.get(f"/api/mistakes/{mistake_id}").status_code == 404

    assert client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 5, "version": 0},
    ).status_code == 404

    assert client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).status_code == 404

    assert client.put(
        f"/api/variants/{variant_id}/result",
        json={"result": "failed", "answer_code": "", "notes": ""},
    ).status_code == 404


def test_auth_and_write_request_protection(client):
    assert client.get("/api/me").status_code == 401

    register(client)
    response = client.post(
        "/api/problems",
        headers={"X-CSRF-Protection": ""},
        json={},
    )
    assert response.status_code == 403

    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401
