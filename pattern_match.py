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
import re
from functools import lru_cache
from pathlib import Path

PATTERN_DATA_PATH = Path(__file__).resolve().parent / "static" / "data" / "patterns.json"
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
