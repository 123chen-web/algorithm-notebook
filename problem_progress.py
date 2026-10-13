"""题目级展示元数据，实时查询，不改变易错点队列或调度。"""
import json
import re
import unicodedata

from fastapi import HTTPException
from math import floor

from tags import tags_for_mistakes


import pattern_match
from core import config
def enrich_problems(conn, user_id, items, today):
    """单条：从没复习过为0；复习过至少算 40%，之后随间隔拉长涨到 100%
    （= 0.4 + 0.6 * min(1, interval_days/21)，21 天沿用成熟卡片口径）；暂停不计。

    以前单条只按 interval_days/21 算，刚复习完间隔才 1 天，只有约 5%，整题平均后常常显示 0%，
    让人觉得"明明复习了却没进度"。现在"复习过"本身就算进度，间隔拉长再继续涨。
    整题取未暂停条目的平均，按5%四舍五入（非银行家舍入）；全暂停为0。
    标签包含整题所有易错点，不受筛选/上限影响。
    """
    ids = sorted({item['problem_id'] for item in items})
    if not ids:
        return items
    rows = conn.execute(
        'SELECT m.* FROM mistakes m JOIN problems p ON p.id=m.problem_id '
        'WHERE p.user_id=? AND p.id IN (SELECT value FROM json_each(?)) ORDER BY m.id',
        (user_id, json.dumps(ids)),
    ).fetchall()
    tags = tags_for_mistakes(conn, [row['id'] for row in rows])
    groups = {pid: [] for pid in ids}
    for row in rows:
        groups[row['problem_id']].append(row)
    metadata = {}
    for pid, members in groups.items():
        active = [m for m in members if m['suspended_at'] is None]
        fractions = [0.4 + 0.6 * min(1, max(0, m['interval_days']) / 21) if m['last_reviewed_at'] else 0
                     for m in active]
        progress = floor(sum(fractions) / len(fractions) * 20 + 0.5) * 5 if fractions else 0
        metadata[pid] = {
            'progress': progress,
            'problem_tags': sorted({tag for m in members for tag in tags[m['id']]}),
            'problem_due_count': sum(m['due_date'] <= today for m in active),
            'problem_mistakes': [
                {key: m[key] for key in ('id', 'due_date', 'suspended_at', 'pending_reason')}
                for m in members
            ],
        }
    return [{**item, **metadata[item['problem_id']]} for item in items]


def enrich_insight(conn, user_id, state, today, kind):
    """展示用实时元数据放在报告之外；不改 AI 的已保存证据或专题成员。"""
    content = (state.get('insight') or {}).get('content', {})
    entries = [entry for group in content.get(kind, [])
               for entry in group.get('evidence' if kind == 'patterns' else 'members', [])]
    ids = sorted({entry['problem_id'] for entry in entries if 'problem_id' in entry})
    rows = conn.execute(
        'SELECT id AS problem_id, title, zone FROM problems '
        'WHERE user_id=? AND id IN (SELECT value FROM json_each(?))',
        (user_id, json.dumps(ids)),
    ).fetchall() if ids else []
    return {**state, 'problem_cards': enrich_problems(conn, user_id, [dict(row) for row in rows], today)}


def mistake_public(item):
    """对外 JSON 的易错点通用字段：pending_reason 转为布尔值。

    数据库里存的是 0/1 整数；对外一律给 true/false，保持字段加法兼容。
    """
    item["pending_reason"] = bool(item.get("pending_reason", 0))
    return item


def owned_mistake(conn, mistake_id, user_id):
    row = conn.execute(
        config.MISTAKE_SELECT + " WHERE m.id = ? AND p.user_id = ?",
        (mistake_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "记录不存在")
    return pattern_match.attach_pattern_ids(conn, [mistake_public(dict(row))])[0]


def owned_problem(conn, problem_id, user_id):
    row = conn.execute(
        "SELECT * FROM problems WHERE id = ? AND user_id = ?",
        (problem_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "题目不存在")
    return dict(row)


def normalize_math_answer(value: str) -> str:
    # 尽力而为的近似比较：只统一格式和数字项顺序，不处理分数化简、
    # 根号等数学等价形式，也不提供浮点误差容忍或符号运算。
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[、，;；]", ",", normalized)
    try:
        numbers = sorted(float(part.strip()) for part in normalized.split(","))
    except ValueError:
        return "".join(normalized.split()).lower()
    # 正零与负零数值相同，拼接前统一表示；重复数字仍按原重数保留。
    return ",".join(str(0.0 if number == 0 else number) for number in numbers)

