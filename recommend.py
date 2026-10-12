"""今日推荐题的选题逻辑：按用户未掌握的错题标签，从 Codeforces 题库缓存里挑题。

- "未掌握"口径复用 mastery.py（retention_on < AT_RISK_BELOW），不自创；
- 不保存题面，只用缓存里的 contestId / index / name / rating / tags；
- 选题是确定性的：同用户、同一天、同一份缓存，结果稳定；
- 落库表 problem_recommendations（迁移 17）：同一用户同一道题只推荐一次。
"""

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from mastery import AT_RISK_BELOW, load_mistakes, retention_on

import ac_problems
import cf_problems

log = logging.getLogger("recommend")

# 中文错题标签 → Codeforces 标签。注释说明每个映射的归属；
# 映射不到的中文标签直接忽略，不参与弱点分。
TAG_MAP = {
    # 图的遍历与搜索：BFS/DFS 本质都是图遍历，CF 侧统一用这两个标签。
    "BFS": ("graphs", "dfs and similar"),
    "DFS": ("graphs", "dfs and similar"),
    "搜索": ("graphs", "dfs and similar"),
    # 图论大类先落到 graphs；树单独成类。
    "图论": ("graphs",),
    "树": ("trees",),
    "动态规划": ("dp",),
    "DP": ("dp",),
    "贪心": ("greedy",),
    "二分": ("binary search",),
    "字符串": ("strings",),
    # 数学类错题大多落在数论 / 纯数学题上。
    "数学": ("math", "number theory"),
    "模拟": ("implementation",),
    "排序": ("sortings",),
    "双指针": ("two pointers",),
    # 哈希散列题在 CF 侧常同时打 data structures。
    "哈希": ("hashing", "data structures"),
    "构造": ("constructive algorithms",),
    "位运算": ("bitmasks",),
}

RATING_MIN = 800
RATING_MAX_DEFAULT = 1200
# 复习 ≥10 次且近期平均掌握度 ≥0.8，认为基础扎实，难度上限提到 1400。
RATING_MAX_SKILLED = 1400
SKILLED_MIN_REVIEWS = 10
SKILLED_MIN_MASTERY = 0.8
RECENT_REVIEW_DAYS = 30
TOP_TAG_COUNT = 2
RECOMMEND_COUNT = 1

HINT_NO_CACHE = "推荐题库还没准备好"
HINT_NO_AC_CACHE = "AtCoder 题库还没准备好"

SOURCES = ("cf", "atcoder", "both")
# AtCoder 难度口径和 Codeforces 不同（同样水平的人，AtCoder 数字更低）。
# AtCoder 没有算法标签，所以只能按难度挑，不能按弱点标签挑。
AC_BAND_DEFAULT = (400, 1000)
AC_BAND_SKILLED = (400, 1400)
AC_REASON = "AtCoder 没有算法标签，这道题按你近期的水平挑选"
_AC_TASK_RE = re.compile(r"atcoder\.jp/contests/[^/\s]+/tasks/([A-Za-z0-9_]+)")
HINT_NO_MISTAKES = "先记几条错题，我才知道推荐什么"


def unmastered_ids(conn, user_id, timezone_name, today):
    """未掌握的易错点 id：口径与 typical.py 完全一致（当天保持率 < AT_RISK_BELOW）。"""
    items = load_mistakes(conn, user_id, timezone_name)
    result = []
    for mistake_id, item in items.items():
        if item["created"] > today:
            continue
        value = retention_on(item, today)
        if value is not None and value < AT_RISK_BELOW:
            result.append(mistake_id)
    return result


def weakness_scores(conn, user_id, timezone_name, today):
    """{cf_tag: {"count": 未掌握条数, "label": 中文标签}}，按条数降序取前 2 个。

    多个中文标签映射到同一 CF 标签时按易错点去重；理由用该中文标签的真实条数。
    """
    ids = unmastered_ids(conn, user_id, timezone_name, today)
    if not ids:
        return {}
    rows = conn.execute(
        """
        SELECT t.tag, t.mistake_id
        FROM mistake_tags t
        WHERE t.user_id = ?
          AND t.mistake_id IN (SELECT value FROM json_each(?))
        ORDER BY t.tag, t.mistake_id
        """,
        (user_id, json.dumps(ids)),
    ).fetchall()
    scores = {}
    for row in rows:
        cf_tags = TAG_MAP.get(row["tag"])
        if not cf_tags:
            continue  # 映射不到的标签直接忽略
        for cf_tag in cf_tags:
            bucket = scores.setdefault(cf_tag, {"ids": set(), "labels": Counter()})
            bucket["ids"].add(row["mistake_id"])
            bucket["labels"][row["tag"]] += 1
    ordered = sorted(scores.items(), key=lambda kv: (-len(kv[1]["ids"]), kv[0]))
    return {
        cf_tag: {"count": len(info["ids"]), "label": info["labels"].most_common(1)[0][0],
                 "label_count": info["labels"].most_common(1)[0][1]}
        for cf_tag, info in ordered[:TOP_TAG_COUNT]
    }


def difficulty_band(conn, user_id, timezone_name, today):
    """难度带：默认 800–1200；复习 ≥10 次且近期平均掌握度 ≥0.8 时上限提到 1400。

    近期均值只取最近 30 个用户本地日（含今天）复习过的易错点的当前保持率。
    """
    zone = ZoneInfo(timezone_name)
    earliest = today - timedelta(days=RECENT_REVIEW_DAYS - 1)
    review_count = 0
    recent_ids = set()
    for row in conn.execute(
        """
        SELECT r.mistake_id, r.reviewed_at FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    ):
        reviewed = datetime.fromisoformat(row["reviewed_at"]).astimezone(zone).date()
        if reviewed <= today:
            review_count += 1
            if reviewed >= earliest:
                recent_ids.add(row["mistake_id"])
    items = load_mistakes(conn, user_id, timezone_name)
    values = []
    for mistake_id in recent_ids:
        item = items[mistake_id]
        if item["created"] > today:
            continue
        value = retention_on(item, today)
        if value is not None:
            values.append(value)
    average = sum(values) / len(values) if values else None
    if (
        review_count >= SKILLED_MIN_REVIEWS
        and average is not None
        and average >= SKILLED_MIN_MASTERY
    ):
        return (RATING_MIN, RATING_MAX_SKILLED)
    return (RATING_MIN, RATING_MAX_DEFAULT)


def _build_item(problem, cf_tag, tag_scores, state="new"):
    contest_id, index = problem["contestId"], problem["index"]
    info = tag_scores.get(cf_tag)
    return {
        "id": f"{contest_id}{index}",
        "contest_id": contest_id,
        "idx": index,
        "name": problem.get("name") or f"{contest_id}{index}",
        "rating": problem.get("rating"),
        "tags": list(problem.get("tags") or []),
        "url": f"https://codeforces.com/problemset/problem/{contest_id}/{index}",
        # 理由只含用户自己的标签名和数字。
        "reason": f"你在{info['label']}上有{info['label_count']}条未掌握的错题" if info else "",
        "state": state,
    }


def select_problems(problems, tag_scores, band, excluded, count=RECOMMEND_COUNT):
    """纯函数：从缓存题目里选题。

    excluded 是已推荐过的 (contest_id, idx) 集合；按弱点分取前 2 个 CF 标签，
    每道题优先从与上一道不同的标签池里取（(contestId, index) 排序保证确定性），
    尽量让题目分散在不同标签上。没有 rating 的题不选。
    """
    pools = {}
    for cf_tag in tag_scores:
        pool = [
            problem
            for problem in problems
            if problem.get("rating") is not None
            and band[0] <= problem["rating"] <= band[1]
            and cf_tag in (problem.get("tags") or [])
            and (problem.get("contestId"), problem.get("index")) not in excluded
        ]
        pool.sort(key=lambda p: (p.get("contestId") or 0, str(p.get("index"))))
        pools[cf_tag] = pool
    tags = list(tag_scores)  # 弱点分降序
    picked = []
    seen = set()
    last_tag = None
    while len(picked) < count:
        progressed = False
        # 优先换标签：先试与上一道不同的标签（按弱点分排序），再试相同的；
        # 某个标签池子空了就顺延，不浪费轮次。
        ordered = sorted(tags, key=lambda t: (t == last_tag, tags.index(t)))
        for cf_tag in ordered:
            pool = pools[cf_tag]
            while pool:
                candidate = pool.pop(0)
                key = (candidate.get("contestId"), candidate.get("index"))
                if key not in seen:
                    seen.add(key)
                    picked.append(_build_item(candidate, cf_tag, tag_scores))
                    last_tag = cf_tag
                    progressed = True
                    break
            if progressed:
                break
        if not progressed:
            break
    return picked


def _previously_recommended(conn, user_id, day):
    """day 之前已推荐过的 (contest_id, idx) 集合：这些题以后不再推荐。"""
    rows = conn.execute(
        """
        SELECT contest_id, idx FROM problem_recommendations
        WHERE user_id = ? AND substr(recommended_at, 1, 10) < ?
        """,
        (user_id, day),
    ).fetchall()
    return {(row["contest_id"], row["idx"]) for row in rows}


def _today_rows(conn, user_id, day):
    return conn.execute(
        """
        SELECT contest_id, idx, state FROM problem_recommendations
        WHERE user_id = ? AND substr(recommended_at, 1, 10) = ?
        ORDER BY rowid
        """,
        (user_id, day),
    ).fetchall()


def _stamp(day):
    # recommended_at 存"用户本地日" + "T" + UTC 时间；同一天内重复请求
    # 用 substr(recommended_at, 1, 10) = 本地日 取回当天记录，保证当天稳定。
    return f"{day}T{datetime.now(timezone.utc).strftime('%H:%M:%S+00:00')}"


def _recommend_cf(conn, user, today):
    """Codeforces 来源：返回 (items, hint)。items 为空时 hint 说明原因。

    调用方用写连接：第一次返回的题会落库（state=new）。
    """
    payload = cf_problems.load_cache()
    if payload is None:
        return [], HINT_NO_CACHE
    age = cf_problems.cache_age_days()
    if age is not None and age > cf_problems.CACHE_MAX_AGE_DAYS:
        log.warning("题库缓存已 %d 天未刷新，建议跑 python cf_problems.py refresh", int(age))

    user_id = user["id"]
    timezone_name = user["timezone"]
    day = today.isoformat()
    problems = payload.get("problems") or []
    tag_scores = weakness_scores(conn, user_id, timezone_name, today)
    rows = _today_rows(conn, user_id, day)
    if rows:
        # Restore the saved identities in insertion order, independent of later
        # reviews, tag edits, difficulty changes, or additions to the cache.
        by_key = {(problem["contestId"], problem["index"]): problem for problem in problems}
        items = []
        for row in rows:
            problem = by_key.get((row["contest_id"], row["idx"]))
            if problem is None:
                # The refreshed cache may no longer include a saved identity.
                problem = {"contestId": row["contest_id"], "index": row["idx"]}
            cf_tag = next((tag for tag in tag_scores if tag in (problem.get("tags") or [])), None)
            items.append(_build_item(problem, cf_tag, tag_scores, row["state"]))
        return items, ""

    if not tag_scores:
        return [], HINT_NO_MISTAKES
    band = difficulty_band(conn, user_id, timezone_name, today)
    excluded = _previously_recommended(conn, user_id, day)
    picked = select_problems(problems, tag_scores, band, excluded)
    stamp = _stamp(day)
    for item in picked:
        conn.execute(
            """
            INSERT INTO problem_recommendations(user_id, contest_id, idx, recommended_at, state)
            VALUES (?, ?, ?, ?, 'new')
            """,
            (user_id, item["contest_id"], item["idx"], stamp),
        )
    return picked, ""


# ---------------------------------------------------------------- AtCoder 来源


def source_of(conn, user_id):
    row = conn.execute("SELECT recommend_source FROM users WHERE id = ?", (user_id,)).fetchone()
    value = row["recommend_source"] if row else "cf"
    return value if value in SOURCES else "cf"


def set_source(conn, user_id, source):
    if source not in SOURCES:
        raise ValueError("推荐来源无效")
    conn.execute("UPDATE users SET recommend_source = ? WHERE id = ?", (source, user_id))


def _ac_item(problem, state="new"):
    pid = problem["id"]
    contest = problem.get("contest_id") or pid.rsplit("_", 1)[0]
    idx = problem.get("index") or pid.rsplit("_", 1)[-1].upper()
    # 数据里的标题形如 "A. Multiply"；前面补上比赛名，省掉重复的题号前缀。
    plain = re.sub(r"^[A-Za-z0-9]{1,3}\.\s*", "", problem.get("title") or "") or pid
    return {
        "oj": "atcoder",
        "id": pid,
        "contest_id": contest,
        "idx": idx,
        "name": f"{contest.upper()} {idx} · {plain}",
        "rating": problem.get("difficulty"),
        "tags": [],
        "url": f"https://atcoder.jp/contests/{contest}/tasks/{pid}",
        "reason": AC_REASON,
        "state": state,
    }


def select_atcoder(problems, band, excluded, user_id, day):
    """纯函数：同用户同一天同一份缓存结果稳定；不同用户/不同天散开，不总是最旧的题。"""
    pool = sorted(
        (p for p in problems
         if type(p.get("difficulty")) is int and band[0] <= p["difficulty"] <= band[1]
         and p.get("id") not in excluded),
        key=lambda p: p["id"],
    )
    if not pool:
        return None
    seed = int(hashlib.sha256(f"{user_id}:{day}".encode()).hexdigest()[:8], 16)
    return pool[seed % len(pool)]


def _ac_excluded(conn, user_id, day):
    """以前推荐过的，加上用户错题本里已经有链接的 AtCoder 题。"""
    excluded = {
        row["problem_id"] for row in conn.execute(
            "SELECT problem_id FROM atcoder_recommendations "
            "WHERE user_id = ? AND substr(recommended_at, 1, 10) < ?",
            (user_id, day),
        )
    }
    for row in conn.execute(
        "SELECT source_url FROM problems WHERE user_id = ? AND source_url LIKE '%atcoder.jp/contests/%'",
        (user_id,),
    ):
        found = _AC_TASK_RE.search(row["source_url"] or "")
        if found:
            excluded.add(found.group(1))
    return excluded


def _recommend_atcoder(conn, user, today):
    payload = ac_problems.load_cache()
    if payload is None:
        return [], HINT_NO_AC_CACHE
    problems = [p for p in payload["problems"] if isinstance(p, dict) and isinstance(p.get("id"), str)]
    user_id, day = user["id"], today.isoformat()
    rows = conn.execute(
        "SELECT problem_id, state FROM atcoder_recommendations "
        "WHERE user_id = ? AND substr(recommended_at, 1, 10) = ? ORDER BY rowid",
        (user_id, day),
    ).fetchall()
    by_id = {p["id"]: p for p in problems}
    if rows:
        return [_ac_item(by_id.get(r["problem_id"]) or {"id": r["problem_id"]}, r["state"]) for r in rows], ""
    skilled = difficulty_band(conn, user_id, user["timezone"], today)[1] == RATING_MAX_SKILLED
    band = AC_BAND_SKILLED if skilled else AC_BAND_DEFAULT
    picked = select_atcoder(problems, band, _ac_excluded(conn, user_id, day), user_id, day)
    if picked is None:
        return [], HINT_NO_AC_CACHE
    conn.execute(
        "INSERT OR IGNORE INTO atcoder_recommendations(user_id, problem_id, recommended_at, state) "
        "VALUES (?, ?, ?, 'new')",
        (user_id, picked["id"], _stamp(day)),
    )
    return [_ac_item(picked)], ""


def recommend_for_today(conn, user, today):
    """按用户选的来源（cf / atcoder / both）返回 (items, hint)。默认只用 Codeforces。"""
    source = source_of(conn, user["id"])
    if source == "cf":
        return _recommend_cf(conn, user, today)
    ac_items, ac_hint = _recommend_atcoder(conn, user, today)
    if source == "atcoder":
        return ac_items, ac_hint
    cf_items, cf_hint = _recommend_cf(conn, user, today)
    items = cf_items + ac_items
    return items, "" if items else (cf_hint or ac_hint)
