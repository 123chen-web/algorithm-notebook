"""限流桶：只来过一次的 key 不会一直占着内存。"""
import time

from core import ratelimit


def setup_function():
    ratelimit.reset_rate_limits()


def test_idle_buckets_are_swept_but_recent_ones_stay(monkeypatch):
    monkeypatch.setattr(ratelimit, "SWEEP_EVERY", 5)
    base = 1_000_000.0
    clock = {"now": base}
    monkeypatch.setattr(ratelimit.time, "time", lambda: clock["now"])
    for index in range(3):
        ratelimit.rate_limited(f"old-{index}", 10, 60)
    clock["now"] = base + ratelimit.SWEEP_IDLE_SECONDS + 10
    ratelimit.rate_limited("recent", 10, 60)
    for _ in range(6):
        ratelimit.rate_limited("recent", 100, 60)
    keys = set(ratelimit._rate_buckets)
    assert keys == {"recent"}


def test_sweep_does_not_change_the_limit_itself(monkeypatch):
    monkeypatch.setattr(ratelimit, "SWEEP_EVERY", 2)
    results = [ratelimit.rate_limited("login:1.2.3.4", 3, 60) for _ in range(5)]
    assert results == [False, False, False, True, True]
    assert ratelimit.rate_peek("login:1.2.3.4", 3, 60) is True
