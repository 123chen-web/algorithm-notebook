"""AI 额度扣减 / 退还 / 并发槽位的边界补充测试。"""
import pytest
from fastapi import HTTPException

import ai
import ai_limits
import main
from db import connect
from test_app import client, mock_generated_practice, new_problem, register


def attempts(user_id, day="2026-09-19"):
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
    return row["attempts"] if row else 0


# ---------------- refund_on_server_failure 单元行为 ----------------

def test_refund_on_server_failure_restores_attempts_on_5xx(client):
    user_id = register(client)["id"]
    day = "2026-09-19"
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 1)",
            (user_id, day),
        )

    with pytest.raises(HTTPException):
        with ai_limits.refund_on_server_failure(user_id, day):
            raise HTTPException(503, "upstream down")

    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
    assert row["attempts"] == 0


@pytest.mark.parametrize("status", [400, 401, 404, 422, 429])
def test_refund_on_server_failure_does_not_refund_client_errors(client, status):
    user_id = register(client)["id"]
    day = "2026-09-19"
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 1)",
            (user_id, day),
        )
    with pytest.raises(HTTPException):
        with ai_limits.refund_on_server_failure(user_id, day):
            raise HTTPException(status, "client error")
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, day),
        ).fetchone()
    assert row["attempts"] == 1


# ---------------- note_usage 防御分支 ----------------

def test_note_usage_tolerates_broken_usage_object():
    state = {"model": "", "prompt_tokens": None, "completion_tokens": None}
    token = ai_limits._call_usage.set(state)
    try:
        class BrokenUsage:
            @property
            def prompt_tokens(self):
                raise RuntimeError("boom")

            completion_tokens = 3

        class FakeResponse:
            usage = BrokenUsage()

        ai_limits.note_usage("gpt-test", FakeResponse())
    finally:
        ai_limits._call_usage.reset(token)
    assert state["prompt_tokens"] is None
    assert state["completion_tokens"] == 3


# ---------------- max_concurrency 兜底 ----------------

@pytest.mark.parametrize("value,expected", [("3", 3), ("0", 6), ("-2", 6), ("not-a-number", 6)])
def test_max_concurrency_falls_back_to_6_on_bad_env(monkeypatch, value, expected):
    monkeypatch.setenv("AI_MAX_CONCURRENCY", value)
    monkeypatch.setattr(ai_limits, "_semaphore_state", None)
    assert ai_limits.max_concurrency() == expected


# ---------------- variants 接口：失败退还 ----------------

def test_variants_refund_quota_when_ai_returns_5xx(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]

    def fail_generate(item):
        raise HTTPException(503, "model overloaded")

    monkeypatch.setattr(ai, "generate", fail_generate)
    response = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert response.status_code == 503
    # 额度被扣后又被退回。
    assert attempts(user_id) == 0


def test_variants_keep_quota_when_ai_returns_422(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]

    def fail_generate(item):
        raise HTTPException(422, "user content refused")

    monkeypatch.setattr(ai, "generate", fail_generate)
    response = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert response.status_code == 422
    # 422 属于用户侧问题，不退还。
    assert attempts(user_id) == 1


def test_variants_charge_once_per_successful_call(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    response = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert response.status_code == 201
    assert attempts(user_id) == 1


def test_daily_quota_is_per_day(client, monkeypatch):
    # 额度按"日"分桶；换一天就不累计。
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    client.post(f"/api/mistakes/{mistake_id}/variants")
    assert attempts(user_id, "2026-09-19") == 1
    assert attempts(user_id, "2026-09-20") == 0


# ---------------- 并发槽位 ----------------

def test_ai_slot_rejects_when_all_slots_taken(client, monkeypatch):
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(ai_limits, "_semaphore_state", None)
    with ai_limits.ai_slot():
        with pytest.raises(HTTPException) as exc:
            with ai_limits.ai_slot():
                pass
        assert exc.value.status_code == 429
