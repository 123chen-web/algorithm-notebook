import sqlite3

import pytest
from fastapi import Request

import ai
import main
from db import connect
from test_app import client, new_problem, register


def add_plan(conn, limit=3):
    return conn.execute(
        """
        INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, created_at)
        VALUES ('配额套餐', 30, ?, 990, ?)
        """,
        (limit, main.utc_now()),
    ).lastrowid


@pytest.mark.parametrize("change", ["upgrade", "trial"])
def test_generation_reloads_subscription_after_authentication(
    client, monkeypatch, change
):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "1")
    calls = []

    def generate(item):
        calls.append(item["id"])
        return {"description": "新题目", "model": "mock-model"}

    monkeypatch.setattr(ai, "generate", generate)
    with connect(write=True) as conn:
        plan_id = add_plan(conn)
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 2)",
            (user_id, "2026-09-19"),
        )
        if change == "trial":
            conn.execute(
                "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
                (plan_id, "2099-01-01T00:00:00+00:00", user_id),
            )

    def authenticate_then_change_subscription(request: Request):
        user = main.current_user(request)
        # 模拟鉴权后、扣额前发生的支付成功或账号状态变更。
        with connect(write=True) as conn:
            if change == "upgrade":
                assert user["plan_id"] is None
                conn.execute(
                    "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
                    (plan_id, "2099-01-01T00:00:00+00:00", user_id),
                )
            else:
                assert user["is_trial"] is False
                conn.execute("UPDATE users SET is_trial = 1 WHERE id = ?", (user_id,))
        return user

    main.app.dependency_overrides[main.current_user] = authenticate_then_change_subscription
    try:
        response = client.post(f"/api/mistakes/{mistake_id}/variants")
    finally:
        main.app.dependency_overrides.pop(main.current_user)
    assert response.status_code == (201 if change == "upgrade" else 429)
    assert calls == ([mistake_id] if change == "upgrade" else [])
    with connect() as conn:
        attempts = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, "2026-09-19"),
        ).fetchone()["attempts"]
    assert attempts == (3 if change == "upgrade" else 2)


def test_quota_read_and_reservation_hold_write_lock_until_commit(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    with connect(write=True) as conn:
        plan_id = add_plan(conn, limit=1)
        conn.execute(
            "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
            (plan_id, "2099-01-01T00:00:00+00:00", user_id),
        )
    quota_read = main.ai_quota
    checks = []

    def competing_writer_is_blocked(conn, queried_user_id, day):
        # 在读取套餐前和读取后各尝试取得第二个真实 SQLite 写锁。
        # 不需要线程或等待：timeout=0 使竞争连接立即报告锁冲突。
        with connect() as competitor:
            competitor.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competitor.execute("BEGIN IMMEDIATE")
            quota = quota_read(conn, queried_user_id, day)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competitor.execute("BEGIN IMMEDIATE")
        checks.append("locked")
        return quota

    def generate_after_commit(item):
        with connect() as competitor:
            competitor.execute("PRAGMA busy_timeout = 0")
            competitor.execute("BEGIN IMMEDIATE")
            assert competitor.execute(
                "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
                (user_id, "2026-09-19"),
            ).fetchone()["attempts"] == 1
            # 模拟另一请求/支付写入；网络生成阶段必须已经释放写锁。
            competitor.execute("UPDATE plans SET is_active = 0 WHERE id = ?", (plan_id,))
        checks.append("committed")
        return {"description": "新题目", "model": "mock-model"}

    monkeypatch.setattr(main, "ai_quota", competing_writer_is_blocked)
    monkeypatch.setattr(ai, "generate", generate_after_commit)
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 201
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 429
    assert checks == ["locked", "committed", "locked"]
