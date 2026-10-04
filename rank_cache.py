"""榜单类接口共用的进程内按日缓存。

缓存键包含北京时间日期：日期一变旧值自然作废。用户设置、封禁、注销等会改变
榜单成员的操作调用 invalidate() 立刻清空。另设较短的 TTL：多进程部署时，别的
进程里的缓存收不到这次 invalidate()，TTL 保证“不参与公开榜单”最迟几分钟内
在所有进程生效。
"""
import time

CACHE_TTL_SECONDS = 600

_generation = 0
_entries = {}


def _clock():
    return time.monotonic()


def invalidate():
    global _generation
    _generation += 1
    _entries.clear()


def get_or_compute(name, day, compute, extra=()):
    """按 (name, 北京日期, extra) 缓存 compute() 的结果；同一名字只保留最新一天。"""
    key = (name, day, tuple(extra))
    now = _clock()
    entry = _entries.get(key)
    if entry and entry[0] == _generation and now - entry[1] < CACHE_TTL_SECONDS:
        return entry[2]
    generation = _generation
    value = compute()
    # compute 期间发生过 invalidate()：这份结果可能已过时，不写入缓存。
    if generation == _generation:
        for stale in [k for k in _entries if k[0] == name and k != key]:
            del _entries[stale]
        _entries[key] = (generation, now, value)
    return value
