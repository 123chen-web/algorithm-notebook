"""根据题目链接、题名、思路、标签，猜这道题属于学习路线里的哪几个套路。

纯本地规则，不联网、不用 AI、不花额度。结果只是"建议"，由用户点一下确认才会真正归类。

打分来源（分数越高越可信）：
1. 链接：题目是洛谷题，且正好是某个套路的经典例题 → 100；
2. 题名里含有某个套路经典例题的题名（≥3 字）→ 80；
3. 题名里含有套路名（如"二分""滑动窗口"）→ 60；标签里含有 → 45；思路/代码里含有 → 40；
4. 常见缩写（DP、BFS、LCA……）按别名表加分。
只在"算法"分区里猜（别的分区没有对应的套路）。
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

PATTERN_DATA_PATH = Path(__file__).resolve().parent / "static" / "data" / "patterns.json"

logger = logging.getLogger(__name__)
MIN_SCORE = 40
MAX_SUGGESTIONS = 3
SUGGEST_ZONES = ("算法",)

# 常见缩写/俗称 → 套路 id 前缀或套路 id（出现在题名/标签里时给相关套路加分）。
ALIASES = {
    "dp": ["linear-dp", "knapsack", "interval-dp", "bitmask-dp", "digit-dp", "tree-dp", "dp-"],
    "动态规划": ["linear-dp", "knapsack", "interval-dp", "bitmask-dp", "digit-dp", "tree-dp", "dp-"],
    "背包": ["knapsack"],
    "bfs": ["bfs-"],
    "广搜": ["bfs-"],
    "dfs": ["dfs-", "memo-dfs", "backtrack"],
    "深搜": ["dfs-", "memo-dfs", "backtrack"],
    "回溯": ["backtrack"],
    "二分": ["binary-search"],
    "双指针": ["two-pointers", "remove-inplace", "dutch-flag", "sliding-window"],
    "滑动窗口": ["sliding-window", "monotonic-queue"],
    "前缀和": ["prefix-sum"],
    "差分": ["difference-array", "spfa"],
    "单调栈": ["monotonic-stack"],
    "单调队列": ["monotonic-queue", "dp-monoqueue-opt"],
    "最短路": ["dijkstra", "bellman-ford", "floyd-warshall", "bfs-01", "spfa"],
    "dijkstra": ["dijkstra"],
    "spfa": ["spfa", "bellman-ford"],
    "并查集": ["union-find", "dsu-weighted"],
    "生成树": ["mst-"],
    "拓扑": ["topo-sort"],
    "lca": ["lca-tree", "binary-lifting"],
    "最近公共祖先": ["lca-tree"],
    "线段树": ["segment-tree", "persistent-seg"],
    "树状数组": ["fenwick"],
    "字典树": ["trie"],
    "trie": ["trie"],
    "kmp": ["kmp", "z-function"],
    "哈希": ["hash-counting", "rolling-hash"],
    "回文": ["manacher", "palindrome"],
    "堆": ["heap-", "k-way-merge", "median-two-heaps"],
    "贪心": ["interval-greedy", "regret-greedy", "greedy-"],
    "位运算": ["bit-"],
    "状压": ["bitmask-dp", "bit-subset-enum"],
    "数位": ["digit-dp"],
    "快速幂": ["fast-pow-gcd", "matrix-pow"],
    "质数": ["sieve-comb"],
    "素数": ["sieve-comb"],
    "gcd": ["fast-pow-gcd", "exgcd"],
    "逆元": ["mod-inverse"],
    "离散化": ["discretization"],
    "栈": ["bracket-stack", "expression-stack", "monotonic-stack", "design-min-stack"],
    "链表": ["linked-list"],
    "二叉树": ["tree-"],
    "网格": ["bfs-grid", "dfs-grid", "dp-grid"],
    "括号": ["bracket-stack", "backtrack-partition"],
    "排列": ["backtrack-permutation", "backtrack-pruning"],
    "子集": ["backtrack-subset"],
    "组合": ["backtrack-subset", "sieve-comb"],
    "逆序对": ["merge-sort-inversions", "fenwick"],
    "区间": ["interval-greedy", "interval-dp", "difference-array", "prefix-sum"],
}

_LINK_LUOGU = re.compile(r"luogu\.com\.cn/problem/([A-Za-z]{1,4}\d+[A-Za-z0-9_]*)", re.IGNORECASE)

from core import config


@lru_cache(maxsize=1)
def _catalog():
    try:
        raw = json.loads(PATTERN_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    entries = []
    for item in raw:
        title = str(item.get("title", ""))
        main_term = re.split(r"[（(]", title, maxsplit=1)[0].strip()
        terms = {main_term}
        for inner in re.findall(r"[（(]([^）)]*)[）)]", title):
            for piece in re.split(r"[、，,/\s]+", inner):
                if len(piece) >= 2:
                    terms.add(piece)
        terms = {t.lower() for t in terms if len(t) >= 2}
        entries.append({
            "id": item["id"],
            "title": title,
            "category": item.get("category", ""),
            "terms": terms,
            "example_keys": {f"{e['source']}:{e['id']}".lower() for e in item.get("examples", [])},
            "example_titles": [e["title"] for e in item.get("examples", []) if len(e.get("title", "")) >= 3],
        })
    return tuple(entries)


def reset_cache():
    """测试里换了套路数据文件之后调用。"""
    _catalog.cache_clear()


def _luogu_key(source_url):
    match = _LINK_LUOGU.search(source_url or "")
    return f"luogu:{match.group(1)}".lower() if match else ""


def _alias_hits(text):
    hits = set()
    for alias, prefixes in ALIASES.items():
        if alias in text:
            hits.update(prefixes)
    return hits


def suggest(title="", thinking="", source_url="", zone="算法", tags=(), code="", exclude=()):
    """返回最多 3 条 [{"pattern_id", "title", "category", "score", "reason"}]，按分数从高到低。"""
    if zone and zone not in SUGGEST_ZONES:
        return []
    title_l = str(title or "").lower()
    notes_l = f"{thinking or ''}\n{code or ''}".lower()
    tags_l = " ".join(str(t) for t in tags).lower()
    key = _luogu_key(source_url)
    title_alias = _alias_hits(title_l + " " + tags_l)
    notes_alias = _alias_hits(notes_l)
    excluded = set(exclude)

    scored = []
    for entry in _catalog():
        if entry["id"] in excluded:
            continue
        score, reason = 0, ""
        if key and key in entry["example_keys"]:
            score, reason = 100, "题目链接正是这个套路的经典例题"
        if score < 80:
            for example_title in entry["example_titles"]:
                if example_title.lower() in title_l:
                    score, reason = 80, f"题名和经典例题《{example_title}》相同"
                    break
        if score < 60:
            for term in entry["terms"]:
                if term in title_l:
                    score, reason = 60, f"题名里有“{term}”"
                    break
        if score < 45:
            for term in entry["terms"]:
                if term in tags_l:
                    score, reason = 45, f"你的标签里有“{term}”"
                    break
        if score < 50:
            bonus = 0
            if any(entry["id"].startswith(p) for p in title_alias):
                bonus = 50
                reason = reason or "题名或标签里提到了相关的说法"
            elif any(entry["id"].startswith(p) for p in notes_alias):
                bonus = 40
                reason = reason or "你的思路或代码里提到了相关的说法"
            score = max(score, bonus)
        if score < 40 and any(term in notes_l for term in entry["terms"]):
            score, reason = 40, "你的思路或代码里提到了它"
        if score >= MIN_SCORE:
            scored.append({
                "pattern_id": entry["id"],
                "title": entry["title"],
                "category": entry["category"],
                "score": score,
                "reason": reason,
            })
    scored.sort(key=lambda item: (-item["score"], item["pattern_id"]))
    return scored[:MAX_SUGGESTIONS]


def load_pattern_catalog(path=PATTERN_DATA_PATH):
    """返回 {pattern_id: {"title": ..., "example_keys": frozenset("<source>:<id>")}}。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("套路数据读取失败，学习路线白名单为空：%s", path, exc_info=True)
        return {}
    catalog = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pattern_id = entry.get("id")
        if not isinstance(pattern_id, str) or not pattern_id:
            continue
        keys = set()
        for example in entry.get("examples") or []:
            if isinstance(example, dict) and example.get("source") and example.get("id"):
                keys.add(f"{example['source']}:{example['id']}")
        catalog[pattern_id] = {
            "title": str(entry.get("title") or pattern_id),
            "example_keys": frozenset(keys),
        }
    return catalog


PATTERN_CATALOG = load_pattern_catalog()


def pattern_is_known(pattern_id):
    """pattern_id 是否是清单里真实存在的套路。"""
    return isinstance(pattern_id, str) and pattern_id in PATTERN_CATALOG


def pattern_example_keys(pattern_id):
    """该套路全部例题的 key（<source>:<id>）集合；未知套路返回空集合。"""
    entry = PATTERN_CATALOG.get(pattern_id)
    return entry["example_keys"] if entry else frozenset()


def normalize_pattern_ids(values):
    """校验并去重（保持原顺序）。

    None 表示"调用方没有传"（编辑题目时代表保持不变）；空列表代表清空。
    未知 pattern_id 或去重后超过 MAX_PATTERNS_PER_PROBLEM 都抛 ValueError。
    """
    if values is None:
        return None
    if not isinstance(values, list):
        raise ValueError("套路格式不正确")
    result = []
    for value in values:
        if not isinstance(value, str) or not pattern_is_known(value):
            raise ValueError("请选择一个有效的套路")
        if value not in result:
            result.append(value)
    if len(result) > config.MAX_PATTERNS_PER_PROBLEM:
        raise ValueError(f"一道题最多关联 {config.MAX_PATTERNS_PER_PROBLEM} 个套路")
    return result


def validated_pattern_ids(values):
    """接口层用：校验失败转成 422，错误消息直接给用户看。"""
    try:
        return normalize_pattern_ids(values)
    except ValueError as error:
        raise HTTPException(422, str(error))


def set_problem_patterns(conn, problem_id, pattern_ids):
    """全量替换这道题的套路关联。

    pattern_ids 为 None 表示"调用方没传"（编辑时保持不变），此时什么都不做并
    返回 None；传 [] 表示清空。
    """
    if pattern_ids is None:
        return None
    conn.execute("DELETE FROM problem_patterns WHERE problem_id = ?", (problem_id,))
    for pattern_id in pattern_ids:
        conn.execute(
            "INSERT OR IGNORE INTO problem_patterns(problem_id, pattern_id) VALUES (?, ?)",
            (problem_id, pattern_id),
        )
    return pattern_ids


def attach_pattern_ids(conn, items):
    """给错题列表补上 pattern_ids（题目 → 套路），编辑表单要用它的初始值。

   就地改 items 并返回；没有关联的题目给空列表（前端据此判断"还没挂过套路"）。
    """
    mapping = problem_pattern_ids(conn, [item.get("problem_id") for item in items])
    for item in items:
        item["pattern_ids"] = mapping.get(item.get("problem_id"), [])
    return items


def problem_pattern_ids(conn, problem_ids):
    """{problem_id: [pattern_id, ...]}，按 rowid 顺序；没有关联的题目不出现在结果里。"""
    result = {}
    if not problem_ids:
        return result
    placeholders = ",".join("?" for _ in problem_ids)
    for row in conn.execute(
        f"SELECT problem_id, pattern_id FROM problem_patterns "
        f"WHERE problem_id IN ({placeholders}) ORDER BY rowid",
        list(problem_ids),
    ):
        result.setdefault(row["problem_id"], []).append(row["pattern_id"])
    return result


def pattern_info(pattern_id):
    """{"title", "category"}；未知套路返回 None。"""
    for entry in _catalog():
        if entry["id"] == pattern_id:
            return {"title": entry["title"], "category": entry["category"]}
    return None


def ai_candidates_text():
    """给 AI 看的候选清单：一行一个套路 “id | 大类 | 名称”。AI 只能从里面选。"""
    return "\n".join(f"{e['id']} | {e['category']} | {e['title']}" for e in _catalog())
