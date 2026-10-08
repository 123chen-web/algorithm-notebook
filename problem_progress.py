"""题目级展示元数据，实时查询，不改变易错点队列或调度。"""
import json
from math import floor

from tags import tags_for_mistakes


def enrich_problems(conn, user_id, items, today):
    """单条=min(1, interval_days/21)，未复习为0，暂停不计。

    整题取未暂停条目的平均，按5%四舍五入（非银行家舍入）；全暂停为0。
    21天沿用成熟卡片口径。标签包含整题所有易错点，不受筛选/上限影响。
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
        fractions = [min(1, max(0, m['interval_days']) / 21) if m['last_reviewed_at'] else 0
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
