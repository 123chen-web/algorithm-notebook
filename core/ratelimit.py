"""限流：内存令牌桶。"""
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)

def rate_limited(key, limit, window_seconds):
    now = time.time()
    with _rate_lock:
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
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        bucket.append(now)


def reset_rate_limits():
    with _rate_lock:
        _rate_buckets.clear()


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


