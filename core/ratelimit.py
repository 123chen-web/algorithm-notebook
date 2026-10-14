"""限流：内存令牌桶。"""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)

# 桶按 key（IP、用户、接口名）建，过了窗口的旧时间戳只在同一个 key 再被访问时才清理，
# 所以只来过一次的 key（比如陌生 IP）会一直留在内存里。每调用 SWEEP_EVERY 次顺手扫一遍，
# 把最近一次记录早于 SWEEP_IDLE_SECONDS（比代码里最长的 24 小时窗口还久）的桶整个删掉。
SWEEP_EVERY = 2000
SWEEP_IDLE_SECONDS = 2 * 24 * 3600
_calls_since_sweep = 0


def _maybe_sweep(now):
    """必须在持有 _rate_lock 时调用。"""
    global _calls_since_sweep
    _calls_since_sweep += 1
    if _calls_since_sweep < SWEEP_EVERY:
        return
    _calls_since_sweep = 0
    stale = [key for key, bucket in _rate_buckets.items() if not bucket or now - bucket[-1] > SWEEP_IDLE_SECONDS]
    for key in stale:
        del _rate_buckets[key]

def rate_limited(key, limit, window_seconds):
    now = time.time()
    with _rate_lock:
        _maybe_sweep(now)
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False


def rate_peek(key, limit, window_seconds):
    """只查看桶是否已满，不记录本次调用。"""
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        return len(bucket) >= limit


def rate_note(key, window_seconds):
    """记录一次事件（例如一次登录失败），不判断是否超限。"""
    now = time.time()
    with _rate_lock:
        _maybe_sweep(now)
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        bucket.append(now)


def reset_rate_limits():
    global _calls_since_sweep
    with _rate_lock:
        _rate_buckets.clear()
        _calls_since_sweep = 0


def client_ip(request: Request):
    # 部署在 Caddy 之类的反向代理后面时，socket 地址永远是代理的内网 IP，
    # 限流会变成全站共用一个桶；设置 TRUST_PROXY=1（且应用端口不对公网开放）后，
    # 取代理追加在 X-Forwarded-For 最右侧的那一项（客户端自己伪造的项都在它左边）。
    if os.getenv("TRUST_PROXY") == "1":
        forwarded = request.headers.get("X-Forwarded-For", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


