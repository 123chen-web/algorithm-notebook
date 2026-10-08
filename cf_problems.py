"""Codeforces 题库缓存：从官方公开接口拉取全部题目，裁剪字段后原子写入缓存。

只用标准库 urllib；一次请求拿全量；不保存题面（版权），只保留
contestId / index / name / rating / tags 五个字段。
缓存写到 data/cf_problems.json（路径跟 DATABASE_PATH 的解析方式一致：
相对路径以项目根目录为基准），先写临时文件再 os.replace，保证原子性。

命令行：python cf_problems.py refresh
失败时保留旧缓存，退出码非 0。缓存超过 14 天只在日志里提醒，不影响推荐
（提醒逻辑在 recommend.py 里读缓存时触发）。
"""

import json
import errno
import logging
import math
import os
import re
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

API_URL = "https://codeforces.com/api/problemset.problems"
USER_AGENT = "oy-recommend/1 (+https://ouyeoy.com)"
TIMEOUT_SECONDS = 20
MAX_BODY_BYTES = 15 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
# 官方限制：同一 IP 每 2 秒最多 1 次请求。
MIN_REQUEST_INTERVAL = 2.0
CACHE_MAX_AGE_DAYS = 14

KEEP_FIELDS = ("contestId", "index", "name", "rating", "tags")

log = logging.getLogger("cf_problems")


@contextmanager
def _cache_lock():
    """Serialize cache operations across processes without a new dependency."""
    lock_path = cache_path().with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + TIMEOUT_SECONDS
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError("题库缓存锁等待超时") from exc
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def cache_path():
    """缓存文件路径；data 目录不存在时建出来。"""
    from db import ROOT  # 局部导入：本模块也被当作独立脚本运行

    path = Path(os.getenv("DATABASE_PATH", "data/notebook.db")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "cf_problems.json"


def cache_age_days(path=None):
    """缓存距上次成功抓取的天数；没有缓存或读不出时返回 None。"""
    target = path or cache_path()
    try:
        payload = _load_cache_at(target)
        if payload is None:
            return None
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return None
        return max(0.0, (time.time() - float(fetched_at)) / 86400.0)
    except (OSError, ValueError, TypeError):
        return None


def _respect_rate_limit(handle):
    """Persist attempted request times while holding the process-shared lock."""
    handle.seek(0)
    try:
        last = float(handle.read(64).decode("ascii") or "0")
        if not math.isfinite(last):
            last = 0
    except (ValueError, UnicodeError):
        last = 0
    wait = min(MIN_REQUEST_INTERVAL, MIN_REQUEST_INTERVAL - (time.time() - last))
    if wait > 0:
        log.info("距离上次抓取不足 %.1f 秒，等待 %.1fs", MIN_REQUEST_INTERVAL, wait)
        time.sleep(wait)
    # Record before opening HTTP: even a failed attempt counts toward the limit.
    handle.seek(0)
    handle.write(str(time.time()).encode("ascii"))
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())


def _read_limited(response):
    """分块读取响应体，超过上限直接放弃（防内存耗尽）。"""
    chunks = []
    total = 0
    while True:
        chunk = response.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise ValueError(f"响应体超过 {MAX_BODY_BYTES} 字节上限，放弃")
        chunks.append(chunk)
    return b"".join(chunks)


def _metadata(problems):
    """Validate external/cache metadata and keep only the five public fields."""
    if not isinstance(problems, list):
        raise ValueError("题目列表结构无效")
    result = []
    for problem in problems:
        if not isinstance(problem, dict):
            raise ValueError("题目元信息结构无效")
        contest_id, index = problem.get("contestId"), problem.get("index")
        if (type(contest_id) is not int or not 0 < contest_id <= 2 ** 63 - 1
                or not isinstance(index, str)
                or re.fullmatch(r"[A-Za-z0-9]{1,16}", index) is None):
            raise ValueError("题目编号无效")
        name, rating, tags = problem.get("name"), problem.get("rating"), problem.get("tags")
        if (name is not None and not isinstance(name, str)
                or rating is not None and (type(rating) is not int or rating < 0)
                or tags is not None and (not isinstance(tags, list)
                    or any(not isinstance(tag, str) for tag in tags))):
            raise ValueError("题目字段类型无效")
        result.append({key: problem[key] for key in KEEP_FIELDS if key in problem})
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        # Fail closed: never follow the fixed official endpoint to another source.
        raise ValueError("Codeforces 接口重定向，放弃抓取")


def fetch_problems(opener=None):
    """抓取并裁剪题目列表；opener 参数只给测试注入假 HTTP 用。"""
    with _cache_lock() as handle:
        _respect_rate_limit(handle)
        return _fetch_official(opener)


def _fetch_official(opener):
    request = urllib.request.Request(
        API_URL, headers={"User-Agent": USER_AGENT}
    )
    open_url = opener or urllib.request.build_opener(_NoRedirect()).open
    with open_url(request, timeout=TIMEOUT_SECONDS) as response:
        if response.getcode() != 200:
            raise ValueError(f"Codeforces HTTP 状态不是 200: {response.getcode()}")
        raw = _read_limited(response)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Codeforces 接口响应结构无效")
    if payload.get("status") != "OK":
        raise ValueError(f"Codeforces 接口返回异常状态: {payload.get('status')!r}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Codeforces 接口 result 结构无效")
    return _metadata(result.get("problems"))


def save_cache(problems):
    """原子写缓存：临时文件 + os.replace；返回写入的路径。"""
    target = cache_path()
    payload = {"fetched_at": time.time(), "problems": _metadata(problems)}
    tmp = None
    try:
        # Each writer gets its own file, on the same filesystem for atomic replace.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                prefix="cf_problems-", suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        with _cache_lock():
            os.replace(tmp, target)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return target


def load_cache():
    """读缓存；文件不存在、JSON 损坏或结构不对时返回 None（调用方给降级提示）。"""
    return _load_cache_at(cache_path())


def _load_cache_at(target):
    try:
        with target.open("rb") as handle:
            payload = json.loads(_read_limited(handle).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        fetched_at = payload.get("fetched_at")
        if fetched_at is not None and (type(fetched_at) not in (int, float)
                or not math.isfinite(fetched_at) or fetched_at < 0):
            return None
        payload["problems"] = _metadata(payload.get("problems"))
    except (OSError, ValueError, OverflowError):
        return None
    return payload


def refresh():
    """抓取并刷新缓存；成功返回 0，失败保留旧缓存并返回 1。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        problems = fetch_problems()
        if not problems:
            raise ValueError('题库为空，放弃覆盖已有候选数据')
        target = save_cache(problems)
    except Exception as exc:  # noqa: BLE001 —— 任何抓取失败都保留旧缓存
        log.error("抓取 Codeforces 题库失败，已保留旧缓存：%s", exc)
        return 1
    log.info("已写入 %d 道题 -> %s", len(problems), target)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "refresh":
        print("用法：python cf_problems.py refresh", file=sys.stderr)
        sys.exit(2)
    sys.exit(refresh())
