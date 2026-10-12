"""AtCoder 题库缓存：从社区维护的 AtCoder Problems（kenkoooo）公开数据拉取，裁剪后原子写入缓存。

AtCoder 官方没有题库接口；kenkoooo.com 的 JSON 是社区通用来源，随时可能失效，
所以失败时一律保留旧缓存、推荐照旧用旧数据，不影响别的功能。
只保留 id / contest_id / index / title / difficulty 五项，不保存题面。
缓存写到 data/ac_problems.json（与 cf_problems.json 同目录）。

命令行：python ac_problems.py refresh（cf_problems.py refresh 结束后也会顺带跑一次）。
"""

import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROBLEMS_URL = "https://kenkoooo.com/atcoder/resources/merged-problems.json"
MODELS_URL = "https://kenkoooo.com/atcoder/resources/problem-models.json"
USER_AGENT = "oy-recommend/1 (+https://ouyeoy.com)"
TIMEOUT_SECONDS = 30
MAX_BODY_BYTES = 30 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
# 对方请求两次之间至少隔 1 秒。
REQUEST_GAP_SECONDS = 1.5
CACHE_MAX_AGE_DAYS = 14

# 只推荐常规比赛的题：ABC / ARC / AGC。
_CONTEST_RE = re.compile(r"^(abc|arc|agc)\d{3}$")
_ID_RE = re.compile(r"^(abc|arc|agc)\d{3}_[a-z0-9]{1,3}$")

log = logging.getLogger("ac_problems")


def clip_difficulty(raw):
    """AtCoder 官网的显示口径：低于 400 的难度做平滑，避免出现负数。"""
    if raw is None:
        return None
    value = float(raw)
    if value < 400:
        value = 400 / math.exp(1.0 - value / 400)
    return int(round(value))


def cache_path():
    from db import ROOT  # 局部导入：本模块也被当作独立脚本运行

    path = Path(os.getenv("DATABASE_PATH", "data/notebook.db")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.parent / "ac_problems.json"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise ValueError("AtCoder Problems 数据地址重定向，放弃抓取")


def _read_limited(response):
    chunks, total = [], 0
    while True:
        chunk = response.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise ValueError("响应体超过上限，放弃")
        chunks.append(chunk)
    return b"".join(chunks)


def _get_json(url, opener):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    open_url = opener or urllib.request.build_opener(_NoRedirect()).open
    with open_url(request, timeout=TIMEOUT_SECONDS) as response:
        if response.getcode() != 200:
            raise ValueError(f"HTTP 状态不是 200: {response.getcode()}")
        return json.loads(_read_limited(response).decode("utf-8"))


def build_problems(listing, models):
    """合并题目清单与难度表，校验并裁剪字段；没有难度的题（新题/实验性）不收。"""
    if not isinstance(listing, list) or not isinstance(models, dict):
        raise ValueError("AtCoder 数据结构无效")
    result = []
    for item in listing:
        if not isinstance(item, dict):
            continue
        pid, contest = item.get("id"), item.get("contest_id")
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            continue
        if not isinstance(contest, str) or not _CONTEST_RE.match(contest):
            continue
        model = models.get(pid)
        if not isinstance(model, dict) or model.get("is_experimental"):
            continue
        difficulty = model.get("difficulty")
        if type(difficulty) not in (int, float) or not math.isfinite(difficulty):
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else pid
        index = item.get("problem_index")
        result.append({
            "id": pid,
            "contest_id": contest,
            "index": index if isinstance(index, str) and len(index) <= 4 else pid.rsplit("_", 1)[1].upper(),
            "title": title[:120],
            "difficulty": clip_difficulty(difficulty),
        })
    return result


def fetch_problems(opener=None, sleep=time.sleep):
    listing = _get_json(PROBLEMS_URL, opener)
    sleep(REQUEST_GAP_SECONDS)
    models = _get_json(MODELS_URL, opener)
    return build_problems(listing, models)


def save_cache(problems):
    target = cache_path()
    payload = {"fetched_at": time.time(), "problems": problems}
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                prefix="ac_problems-", suffix=".tmp", delete=False) as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return target


def load_cache():
    """读缓存；文件不存在、损坏或结构不对时返回 None。"""
    try:
        with cache_path().open("rb") as handle:
            payload = json.loads(_read_limited(handle).decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("problems"), list):
        return None
    return payload


def refresh():
    """成功返回 0；失败保留旧缓存并返回 1。"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        problems = fetch_problems()
        if len(problems) < 100:
            raise ValueError(f"题目数量异常（{len(problems)}），放弃覆盖已有缓存")
        target = save_cache(problems)
    except Exception as exc:  # noqa: BLE001 —— 任何失败都保留旧缓存
        log.error("抓取 AtCoder 题库失败，已保留旧缓存：%s", exc)
        return 1
    log.info("已写入 %d 道题 -> %s", len(problems), target)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "refresh":
        print("用法：python ac_problems.py refresh", file=sys.stderr)
        sys.exit(2)
    sys.exit(refresh())
