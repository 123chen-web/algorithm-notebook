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
import logging
import os
import sys
import time
import urllib.request
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
        payload = json.loads(target.read_text(encoding="utf-8"))
        fetched_at = payload.get("fetched_at")
        if not fetched_at:
            return None
        return max(0.0, (time.time() - float(fetched_at)) / 86400.0)
    except (OSError, ValueError, TypeError):
        return None


def _respect_rate_limit():
    """同一进程/机器连续抓取时，遵守官方每 2 秒 1 次的限制。"""
    try:
        payload = json.loads(cache_path().read_text(encoding="utf-8"))
        last = float(payload.get("fetched_at") or 0)
    except (OSError, ValueError, TypeError):
        return
    wait = MIN_REQUEST_INTERVAL - (time.time() - last)
    if wait > 0:
        log.info("距离上次抓取不足 %.1f 秒，等待 %.1fs", MIN_REQUEST_INTERVAL, wait)
        time.sleep(wait)


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


def fetch_problems(opener=None):
    """抓取并裁剪题目列表；opener 参数只给测试注入假 HTTP 用。"""
    _respect_rate_limit()
    request = urllib.request.Request(
        API_URL, headers={"User-Agent": USER_AGENT}
    )
    open_url = opener or urllib.request.urlopen
    with open_url(request, timeout=TIMEOUT_SECONDS) as response:
        raw = _read_limited(response)
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("status") != "OK":
        raise ValueError(f"Codeforces 接口返回异常状态: {payload.get('status')!r}")
    problems = []
    for problem in payload["result"]["problems"]:
        problems.append({key: problem.get(key) for key in KEEP_FIELDS})
    return problems


def save_cache(problems):
    """原子写缓存：临时文件 + os.replace；返回写入的路径。"""
    target = cache_path()
    payload = {"fetched_at": time.time(), "problems": problems}
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    return target


def load_cache():
    """读缓存；文件不存在、JSON 损坏或结构不对时返回 None（调用方给降级提示）。"""
    try:
        payload = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    problems = payload.get("problems")
    if not isinstance(problems, list):
        return None
    return payload


def refresh():
    """抓取并刷新缓存；成功返回 0，失败保留旧缓存并返回 1。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        problems = fetch_problems()
    except Exception as exc:  # noqa: BLE001 —— 任何抓取失败都保留旧缓存
        log.error("抓取 Codeforces 题库失败，已保留旧缓存：%s", exc)
        return 1
    target = save_cache(problems)
    log.info("已写入 %d 道题 -> %s", len(problems), target)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "refresh":
        print("用法：python cf_problems.py refresh", file=sys.stderr)
        sys.exit(2)
    sys.exit(refresh())
