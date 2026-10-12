"""今日一签：像撕日历一样的"今日宜忌"。

内容来自用户自己的数据（今天到期的易错点、没学的套路、最近常犯的错因），
没有数据时用一份精选的小提示补上；同一个人同一天内容固定（按用户和日期生成种子），
刷新不会变。纯本地计算，不联网、不用 AI。

仅供学习提示和娱乐，措辞避免绝对化。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date as _date
from pathlib import Path

WEEKDAYS = "一二三四五六日"
PATTERN_DATA_PATH = Path(__file__).resolve().parent / "static" / "data" / "patterns.json"

# 没有个人数据时的通用提示：(一句话, 为什么)
GENERIC_YI = [
    ("先写暴力，再想优化", "暴力解是最好的对拍对象，也能让你看清瓶颈在哪。"),
    ("用最小的样例手算一遍", "手算能在写代码之前暴露理解上的偏差。"),
    ("把题目的数据范围先看一眼", "n 的大小直接决定该用 O(n²) 还是 O(n log n)。"),
    ("写完用 n=0、n=1 各跑一遍", "边界值是错题里出场率最高的常客。"),
    ("给自己的解法起一个套路名", "能说出名字的解法，下次才容易想起来。"),
    ("隔天回头看昨天的错题", "间隔一天再看，记得牢还是不牢一目了然。"),
    ("错一题，就记下一句话的错因", "一句话写清“我以为…其实…”，比抄答案管用。"),
]
GENERIC_JI = [
    ("一上来就敲代码", "没想清楚就写，调试的时间往往比想的时间长。"),
    ("看一眼题就觉得“我懂了”", "读两遍题面，圈出限制条件再动手。"),
    ("提交前不看输出格式", "多一个空格、少一个换行，都可能白白丢分。"),
    ("复制别人的模板却不理解", "不理解的模板，换个题面就用不上。"),
    ("跳过复杂度估算", "写之前估一下，能省下一次超时。"),
]
# 最近常犯的错因 → 忌什么、为什么
TAG_JI = {
    "边界": ("想当然的边界", "提交前手造 n=0、n=1、最大值各跑一遍。"),
    "粗心": ("匆忙提交", "写完先把题面和输出格式再读一遍。"),
    "概念混淆": ("凭印象套概念", "先用自己的话写一句定义，再用。"),
    "思路错误": ("没想清楚就动手", "先写下“每一步为什么对”，再写代码。"),
    "没读清题": ("只读半句题面", "把条件逐条圈出来再开始。"),
    "复杂度": ("先写了再说", "先估算复杂度，超时就别写。"),
    "公式记错": ("凭记忆套公式", "推一遍小例子验证公式再用。"),
    "语法或接口": ("记不清就硬写", "不确定的接口先写个小测试确认。"),
    "溢出": ("默认 int 够用", "累加、乘法先想想会不会超过 21 亿。"),
    "漏条件": ("漏掉题面里的一句话", "把限制条件列成清单逐条核对。"),
}
QUOTES = [
    "代码会骗人，样例不会。",
    "今天的 WA，是明天的“我见过这种题”。",
    "慢一点，是为了少调试一小时。",
    "每一个边界，都是一位沉默的考官。",
    "会写和会想，中间隔着一次复习。",
    "错题不是失败的证据，是尚未兑现的经验。",
    "先把题读懂，题就已经做了一半。",
    "复杂度先估，超时就少一次。",
    "记不住很正常，隔天再看一眼就好。",
    "一次一点点，山也会长出树来。",
    "写下“我以为…其实…”，就是在给大脑打补丁。",
    "不怕错，怕错得没名字。",
    "今天的你，比昨天多认得一个套路。",
    "能讲给小黄鸭听明白，就是真明白。",
]


def _seed(user_id: int, day: _date) -> int:
    digest = hashlib.sha256(f"{user_id}:{day.isoformat()}".encode()).hexdigest()
    return int(digest[:12], 16)


def _pick(options, seed: int, count: int):
    """按种子稳定地从列表里取 count 条（不重复）。"""
    if not options:
        return []
    start = seed % len(options)
    return [options[(start + step * 2) % len(options)] for step in range(min(count, len(options)))]


def _pattern_titles():
    try:
        data = json.loads(PATTERN_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {item["id"]: item["title"].split("（")[0].split("(")[0].strip() for item in data}


def _data_for(conn, user_id: int, day: _date):
    today = day.isoformat()
    due_by_pattern = {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT pp.pattern_id, COUNT(*) FROM mistakes m
            JOIN problems p ON p.id = m.problem_id
            JOIN problem_patterns pp ON pp.problem_id = p.id
            WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?
            GROUP BY pp.pattern_id ORDER BY COUNT(*) DESC, pp.pattern_id
            """,
            (user_id, today),
        )
    }
    status = {
        row[0]: row[1]
        for row in conn.execute("SELECT pattern_id, status FROM pattern_progress WHERE user_id = ?", (user_id,))
    }
    problems_by_pattern = {
        row[0]: row[1]
        for row in conn.execute(
            """
            SELECT pp.pattern_id, COUNT(DISTINCT p.id) FROM problem_patterns pp
            JOIN problems p ON p.id = pp.problem_id WHERE p.user_id = ?
            GROUP BY pp.pattern_id ORDER BY COUNT(DISTINCT p.id) DESC, pp.pattern_id
            """,
            (user_id,),
        )
    }
    tag_counts = [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT tag, COUNT(*) FROM mistake_tags WHERE user_id = ? GROUP BY tag "
            "ORDER BY COUNT(*) DESC, tag LIMIT 3",
            (user_id,),
        )
    ]
    return due_by_pattern, status, problems_by_pattern, tag_counts


def build_sign(conn, user_id: int, day: _date) -> dict:
    titles = _pattern_titles()
    due_by_pattern, status, problems_by_pattern, tag_counts = _data_for(conn, user_id, day)
    seed = _seed(user_id, day)

    yi = []
    for pattern_id, due in due_by_pattern.items():
        if pattern_id in titles and len(yi) < 1:
            yi.append({
                "text": f"复习「{titles[pattern_id]}」",
                "detail": f"这个套路下有 {due} 条易错点今天到期，趁热复习一遍。",
                "source": "data",
            })
    for pattern_id, count in problems_by_pattern.items():
        if len(yi) >= 2:
            break
        if pattern_id in titles and status.get(pattern_id) != "mastered":
            label = "学习" if status.get(pattern_id) != "learning" else "再练一练"
            yi.append({
                "text": f"{label}「{titles[pattern_id]}」",
                "detail": f"你在它上面记过 {count} 道错题，还没完全吃透。",
                "source": "data",
            })
    for text, detail in _pick(GENERIC_YI, seed, 2):
        if len(yi) >= 2:
            break
        yi.append({"text": text, "detail": detail, "source": "tip"})

    ji = []
    for tag, count in tag_counts:
        if tag in TAG_JI and len(ji) < 1:
            text, detail = TAG_JI[tag]
            ji.append({"text": text, "detail": f"你有 {count} 条错因标成了「{tag}」。{detail}", "source": "data"})
    for text, detail in _pick(GENERIC_JI, seed >> 3, 2):
        if len(ji) >= 2:
            break
        ji.append({"text": text, "detail": detail, "source": "tip"})

    return {
        "date": day.isoformat(),
        "day": day.day,
        "month": day.month,
        "weekday": "周" + WEEKDAYS[day.weekday()],
        "yi": yi[:2],
        "ji": ji[:2],
        "quote": QUOTES[(seed >> 5) % len(QUOTES)],
        "note": "仅供学习提示与娱乐，内容来自你自己的复习记录。",
    }
