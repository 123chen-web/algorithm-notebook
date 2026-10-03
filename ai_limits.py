"""AI 并发名额和不包含用户内容的调用记账。"""
import logging
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from fastapi import HTTPException

from db import connect


logger = logging.getLogger(__name__)
_semaphore_lock = threading.Lock()
_semaphore_state = None
_call_usage = ContextVar("ai_call_usage", default=None)


def max_concurrency():
    try:
        limit = int(os.getenv("AI_MAX_CONCURRENCY", "6"))
    except (TypeError, ValueError):
        return 6
    return limit if limit >= 1 else 6


@contextmanager
def ai_slot():
    global _semaphore_state
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, feature, state["model"], state["prompt_tokens"],
                        state["completion_tokens"], ok, error, duration_ms,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
        except Exception as exc:
            logger.warning("AI 调用记账失败：%s", type(exc).__name__)
