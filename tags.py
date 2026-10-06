"""错因标签：给每条易错点贴"边界""概念混淆""粗心"之类的标签，再按标签筛选记录。

标签属于某个用户的某条易错点（mistake_tags 带 user_id 冗余列，列标签/计数不用再联表）。
不区分大小写（英文标签）；删除易错点或账号时随外键级联删除。纯本地逻辑，不调用 AI。
"""

import json
from datetime import datetime, timezone

TAG_MAX_LENGTH = 20
TAGS_PER_MISTAKE = 8
TAGS_PER_USER = 60
# 常见的错因类型，作为"一键添加"的建议；用户仍然可以输入任何自己的说法。
SUGGESTED_TAGS = ("边界", "概念混淆", "粗心", "公式记错", "思路错误", "没读清题", "复杂度", "语法或接口")
# 补原因时的内置常用错因（GET /api/tags/suggest 用）；用户自己的标签优先展示，
# 这里只补用户还没用过的。
REASON_SUGGEST_TAGS = (
    "边界", "溢出", "漏条件", "思路错", "粗心",
    "语法坑", "公式记错", "没思路", "复杂度", "测试没覆盖",
)


class TagError(ValueError):
    """校验失败时的中文提示，接口层原样返回 422。"""


def normalize_tag(value):
    """折叠空白、去掉不可打印字符；空串返回 ''。"""
    cleaned = "".join(character for character in str(value) if character.isprintable() or character.isspace())
    return " ".join(cleaned.split())


def normalize_tags(values):
    """规范化并去重（不区分大小写，保留第一次出现的写法）。"""
    seen = set()
    result = []
    for value in values:
        tag = normalize_tag(value)
        if not tag:
            continue
        if len(tag) > TAG_MAX_LENGTH:
            raise TagError(f"标签最多 {TAG_MAX_LENGTH} 个字")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    if len(result) > TAGS_PER_MISTAKE:
        raise TagError(f"每条易错点最多 {TAGS_PER_MISTAKE} 个标签")
    return result


def tags_for_mistakes(conn, mistake_ids):
    """{mistake_id: [tag, ...]}，一次查询取回一批易错点的标签，按用户排好的顺序。"""
    ids = list(mistake_ids)
    if not ids:
        return {}
    grouped = {mistake_id: [] for mistake_id in ids}  # rowid 顺序 = 写入顺序 = 客户端给的顺序
    rows = conn.execute(
        "SELECT mistake_id, tag FROM mistake_tags "
        "WHERE mistake_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY rowid",
        (json.dumps(ids),),
    )
    for row in rows:
        grouped[row["mistake_id"]].append(row["tag"])
    return grouped


def user_tag_counts(conn, user_id):
    rows = conn.execute(
        "SELECT tag, COUNT(*) AS count FROM mistake_tags WHERE user_id = ? "
        "GROUP BY tag ORDER BY count DESC, tag",
        (user_id,),
    ).fetchall()
    return [{"tag": row["tag"], "count": row["count"]} for row in rows]


def replace_tags(conn, user_id, mistake_id, tags):
    """整组替换一条易错点的标签（调用者已在写事务里确认归属）。顺序就是客户端给的顺序。"""
    existing = {
        row["tag"].casefold()
        for row in conn.execute(
            "SELECT DISTINCT tag FROM mistake_tags WHERE user_id = ? AND mistake_id != ?",
            (user_id, mistake_id),
        )
    }
    distinct_after = existing | {tag.casefold() for tag in tags}
    if len(distinct_after) > TAGS_PER_USER:
        raise TagError(f"标签种类太多了（最多 {TAGS_PER_USER} 种），先把不用的标签清理掉")
    conn.execute("DELETE FROM mistake_tags WHERE mistake_id = ?", (mistake_id,))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for tag in tags:
        conn.execute(
            "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) VALUES (?, ?, ?, ?)",
            (mistake_id, user_id, tag, now),
        )
    return tags
