"""我的三大典型失误：GET /api/stats/typical 的主体。

只统计自己的数据，纯本地计算，不调用 AI：
- 类别来自错因标签（mistake_tags）；每条记录是否"仍未掌握"沿用 mastery.py 的判定
  （当天保持率 < AT_RISK_BELOW）。
- 选出未掌握记录数最多的前 3 类，并列按最近一次复习失败时间（没有失败复习则按录入时间）。
- 每类带最多 3 条代表性记录（最近出错在前）与模板生成的提醒句；另给 6–8 条考前自查清单。
- 有效记录（在今天之前已录入的易错点）不足 MIN_EFFECTIVE_RECORDS 条时 enough=false。

聚合查询次数是常数，不随记录数增长。
"""

import json

from mastery import AT_RISK_BELOW, load_mistakes, retention_on
import typical_templates as templates

MIN_EFFECTIVE_RECORDS = 5
MAX_CATEGORIES = 3
REPRESENTATIVES_PER_CATEGORY = 3
TITLE_LIMIT = 200
DESCRIPTION_LIMIT = 300


def _unmastered_ids(items, today):
    ids = []
    for mistake_id, item in items.items():
        if item["created"] > today:
            continue
        value = retention_on(item, today)
        if value is not None and value < AT_RISK_BELOW:
            ids.append(mistake_id)
    return ids


def _category_rows(conn, user_id, mistake_ids):
    """一次查询取回这些未掌握记录上的标签、代表性字段与每条记录的最近失败复习。

     correlated 子查询仍是同一条 SQL，查询条数不随记录数增长。
    """
    return conn.execute(
        """
        SELECT t.tag AS tag, m.id AS mistake_id,
               substr(p.title, 1, ?) AS title,
               substr(m.description, 1, ?) AS description,
               p.created_at AS created_at,
               (SELECT MAX(r.reviewed_at) FROM reviews r
                  WHERE r.mistake_id = m.id AND r.quality < 3) AS last_fail_at
        FROM mistake_tags t
        JOIN mistakes m ON m.id = t.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE t.user_id = ?
          AND t.mistake_id IN (SELECT value FROM json_each(?))
        """,
        (TITLE_LIMIT, DESCRIPTION_LIMIT, user_id, json.dumps(mistake_ids)),
    ).fetchall()


def _build_items(rows):
    categories = {}
    for row in rows:
        bucket = categories.setdefault(row["tag"], [])
        bucket.append({
            "id": row["mistake_id"],
            "title": row["title"],
            "description": row["description"],
            "created_at": row["created_at"],
            "last_fail_at": row["last_fail_at"],
        })
    items = []
    for tag, members in categories.items():
        members.sort(
            key=lambda member: (member["last_fail_at"] or "", member["created_at"], member["id"]),
            reverse=True,
        )
        representatives = [
            {"id": member["id"], "title": member["title"], "description": member["description"]}
            for member in members[:REPRESENTATIVES_PER_CATEGORY]
        ]
        last_wrong = max(
            (member["last_fail_at"] for member in members if member["last_fail_at"]),
            default=None,
        )
        if last_wrong is None:
            last_wrong = max(member["created_at"] for member in members)
        items.append({
            "key": tag,
            "name": tag,
            "count": len(members),
            "last_wrong_at": last_wrong,
            "hint": templates.hint_for(tag),
            "ids": representatives,
        })
    # 多键稳定排序（后排序的键优先级最高）：按键名升序 → 最近出错时间降序 → 出现数降序。
    items.sort(key=lambda item: item["key"])
    items.sort(key=lambda item: item["last_wrong_at"], reverse=True)
    items.sort(key=lambda item: -item["count"])
    return items[:MAX_CATEGORIES]


def typical_report(conn, user_id, timezone_name, today):
    items_by_id = load_mistakes(conn, user_id, timezone_name)
    effective = {
        mistake_id: item for mistake_id, item in items_by_id.items() if item["created"] <= today
    }
    if len(effective) < MIN_EFFECTIVE_RECORDS:
        return {"enough": False, "items": [], "checklist": []}
    unmastered = _unmastered_ids(effective, today)
    if not unmastered:
        return {"enough": True, "items": [], "checklist": templates.checklist()}
    rows = _category_rows(conn, user_id, unmastered)
    return {
        "enough": True,
        "items": _build_items(rows),
        "checklist": templates.checklist(),
    }
