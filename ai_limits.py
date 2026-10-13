"""AI 并发名额和不包含用户内容的调用记账。"""
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from core import time as core_time

from fastapi import HTTPException

from db import connect


logger = logging.getLogger(__name__)
_semaphore_lock = threading.Lock()
_semaphore_state = None
_call_usage = ContextVar("ai_call_usage", default=None)


def release_attempt(conn, user_id, day):
    """退回本次新接口已扣的一次额度；调用方提供写事务。"""
    conn.execute(
        "UPDATE ai_usage SET attempts = attempts - 1 "
        "WHERE user_id = ? AND day = ? AND attempts > 0",
        (user_id, day),
    )


SERVER_FAILURE_STATUSES = (502, 503, 504)


@contextmanager
def refund_on_server_failure(user_id, day, connect_fn=connect, *, duck=False):
    """AI 服务端/供应商侧失败（502/503/504）时退还这一次额度；422、429 等不退。"""
    try:
        yield
    except HTTPException as exc:
        if exc.status_code in SERVER_FAILURE_STATUSES:
            with connect_fn(write=True) as conn:
                if duck:
                    conn.execute('UPDATE duck_usage SET attempts = attempts - 1 WHERE user_id = ? AND day = ? AND attempts > 0', (user_id, day))
                else:
                    release_attempt(conn, user_id, day)
        raise


def duck_daily_limit():
    try:
        limit = int(os.getenv('DUCK_DAILY_LIMIT', '10'))
    except ValueError:
        return 10
    return limit if limit >= 0 else 10


def max_concurrency():
    try:
        limit = int(os.getenv("AI_MAX_CONCURRENCY", "6"))
    except (TypeError, ValueError):
        return 6
    return limit if limit >= 1 else 6


SITE_BUDGET_MESSAGE = "今天全站的 AI 用量已到上限，明天再来吧；这次没有消耗你的额度。"


def site_daily_limit():
    """全站每日 AI 调用总上限（熔断）；未设置、0 或非法值表示不限制。"""
    try:
        limit = int(os.getenv("AI_SITE_DAILY_LIMIT", "0"))
    except (TypeError, ValueError):
        return 0
    return limit if limit > 0 else 0


def site_budget_exhausted(connect_fn=connect):
    """按服务器 UTC 日统计 ai_calls（成功和失败都算，因为都花了钱）。

    记账在调用结束时才写入，进行中的调用不计数；并发上限很小，
    所以最多超出 AI_MAX_CONCURRENCY 次，作为熔断足够。
    """
    limit = site_daily_limit()
    if not limit:
        return False
    day_start = core_time.datetime.now(timezone.utc).date().isoformat()
    with connect_fn() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM ai_calls WHERE created_at >= ?", (day_start,)
        ).fetchone()[0]
    return used >= limit


@contextmanager
def ai_slot():
    global _semaphore_state
    # 先看全站熔断，再占并发名额；调用方都在扣用户额度之前进入这里，
    # 所以被熔断时用户额度不受影响。
    if site_budget_exhausted():
        raise HTTPException(503, SITE_BUDGET_MESSAGE)
    with _semaphore_lock:
        limit = max_concurrency()
        if _semaphore_state is None or _semaphore_state[0] != limit:
            _semaphore_state = (limit, threading.BoundedSemaphore(limit))
        semaphore = _semaphore_state[1]
        acquired = semaphore.acquire(blocking=False)
    if not acquired:
        raise HTTPException(429, "AI 现在比较忙，请稍后再试；这次没有消耗额度。")
    try:
        yield
    finally:
        semaphore.release()


def note_usage(model, response):
    state = _call_usage.get()
    if state is None:
        return
    state["model"] = model if isinstance(model, str) else ""
    for name in ("prompt_tokens", "completion_tokens"):
        try:
            value = getattr(getattr(response, "usage", None), name, None)
        except Exception:
            value = None
        state[name] = value if type(value) is int and value >= 0 else None


@contextmanager
def track_call(user_id, feature):
    # AnyIO 在线程池复制上下文；共享容器让异步拍照入口收到工作线程的用量。
    state = {"model": "", "prompt_tokens": None, "completion_tokens": None}
    token = _call_usage.set(state)
    started = time.perf_counter_ns()
    ok, error = 1, ""
    try:
        yield
    except BaseException as exc:
        ok = 0
        error = f"http_{exc.status_code}" if isinstance(exc, HTTPException) else type(exc).__name__
        raise
    finally:
        _call_usage.reset(token)
        duration_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        try:
            with connect(write=True) as conn:
                conn.execute(
                    """
                    INSERT INTO ai_calls(
                        user_id, feature, model, prompt_tokens, completion_tokens,
                        ok, error, duration_ms, created_at
                    ) VALUES (
                        (SELECT id FROM users WHERE id = ? AND deleted_at IS NULL),
                        ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        user_id, feature, state["model"], state["prompt_tokens"],
                        state["completion_tokens"], ok, error, duration_ms,
                        core_time.datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
        except Exception as exc:
            logger.warning("AI 调用记账失败：%s", type(exc).__name__)


def ai_limit():
    return max(1, int(os.getenv("AI_DAILY_LIMIT", "20")))


def trial_ai_limit():
    # 体验账号任何人都能开，额度要远低于正式账号，否则等于把
    # AI_DAILY_LIMIT 变成"任何人每天可用次数 × 无限个体验账号"。
    return max(0, int(os.getenv("TRIAL_AI_DAILY_LIMIT", "4")))


def ai_quota(conn, user_id, day):
    # 生成请求必须传入已取得写锁的连接，不能使用鉴权阶段读到的套餐快照。
    # 单条查询也让 /api/me 的套餐信息和当天已用次数来自同一数据库快照。
    row = conn.execute(
        """
        SELECT u.is_trial, u.plan_id, u.plan_expires_at,
               p.name AS plan_name, p.ai_daily_limit AS plan_limit,
               COALESCE(a.attempts, 0) AS ai_daily_used
        FROM users u
        LEFT JOIN plans p ON p.id = u.plan_id
        LEFT JOIN ai_usage a ON a.user_id = u.id AND a.day = ?
        WHERE u.id = ? AND u.deleted_at IS NULL
        """,
        (day, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")

    # 到期时间按 UTC 时刻判断；用户时区只用于 day，不参与套餐有效期判断。
    now = core_time.datetime.now(timezone.utc)
    plan_active = bool(
        not row["is_trial"]
        and row["plan_id"] is not None
        and row["plan_expires_at"]
        and core_time.datetime.fromisoformat(row["plan_expires_at"]) > now
    )
    if row["is_trial"]:
        limit = trial_ai_limit()
    elif plan_active:
        limit = row["plan_limit"]
    else:
        limit = ai_limit()
    return {
        "is_trial": bool(row["is_trial"]),
        "plan_id": row["plan_id"],
        "plan_name": row["plan_name"],
        "plan_expires_at": row["plan_expires_at"],
        "plan_active": plan_active,
        "ai_daily_limit": limit,
        "ai_daily_used": row["ai_daily_used"],
        "ai_daily_remaining": max(0, limit - row["ai_daily_used"]),
    }

