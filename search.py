"""全局搜索（Ctrl K 命令面板）：我自己的题目/错因 + 讨论区帖子。

只做本地 SQLite 的 LIKE 查询，不调用 AI、不占额度。别人的题目永远查不到；
讨论区本来对所有登录用户可见，所以帖子不按用户过滤，但已删除的不返回。
"""

LIKE_ESCAPE = "!"
SNIPPET_RADIUS = 28
# 命中位置的先后顺序也是排序依据：标题 > 错因 > 分区/语言 > 思路 > 代码。
MATCH_ORDER = ("title", "description", "zone", "thinking", "code")


def like_pattern(keyword):
    """把用户输入当字面量：转义 LIKE 的元字符（包括转义符本身）。"""
    escaped = (
        keyword.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"


def snippet_around(text, keyword):
    """命中在思路/代码里时，给出关键词附近的一小段，方便在结果里认出是哪条。"""
    if not text:
        return None
    position = text.casefold().find(keyword.casefold())
    if position < 0:
        return None
    start = max(0, position - SNIPPET_RADIUS)
    end = min(len(text), position + len(keyword) + SNIPPET_RADIUS)
    piece = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def search_records(conn, user_id, keyword, limit):
    pattern = like_pattern(keyword)
    rows = conn.execute(
        f"""
        SELECT m.id AS mistake_id, p.id AS problem_id, p.title, p.zone,
               m.description, m.due_date,
               CASE
                   WHEN p.title LIKE :pattern ESCAPE '{LIKE_ESCAPE}' THEN 0
                   WHEN m.description LIKE :pattern ESCAPE '{LIKE_ESCAPE}' THEN 1
                   WHEN p.zone LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
                        OR p.language LIKE :pattern ESCAPE '{LIKE_ESCAPE}' THEN 2
                   WHEN p.thinking LIKE :pattern ESCAPE '{LIKE_ESCAPE}' THEN 3
                   ELSE 4
               END AS rank
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = :user_id
          AND (p.title LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
               OR m.description LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
               OR p.zone LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
               OR p.language LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
               OR p.thinking LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
               OR p.code LIKE :pattern ESCAPE '{LIKE_ESCAPE}')
        ORDER BY rank, m.due_date, m.id
        LIMIT :limit
        """,
        {"pattern": pattern, "user_id": user_id, "limit": limit + 1},
    ).fetchall()
    results = []
    for row in rows[:limit]:
        matched = MATCH_ORDER[row["rank"]]
        snippet = None
        if matched in ("thinking", "code"):
            # 只对最终留下的几条读完整文本再截取，命中在长文本后半段也找得到上下文。
            text = conn.execute(f"SELECT {matched} FROM problems WHERE id = ?", (row["problem_id"],)).fetchone()[0]
            snippet = snippet_around(text, keyword)
        results.append({
            "mistake_id": row["mistake_id"],
            "problem_id": row["problem_id"],
            "title": row["title"],
            "zone": row["zone"],
            "description": (row["description"] or "")[:160],
            "due_date": row["due_date"],
            "matched": matched,
            "snippet": snippet,
        })
    return results, len(rows) > limit


def search_posts(conn, keyword, limit):
    pattern = like_pattern(keyword)
    rows = conn.execute(
        f"""
        SELECT p.id, p.title, p.created_at, u.username,
               (SELECT COUNT(*) FROM post_comments c
                WHERE c.post_id = p.id AND c.deleted_at IS NULL) AS comment_count
        FROM posts p
        JOIN users u ON u.id = p.user_id
        WHERE p.deleted_at IS NULL
          AND (p.title LIKE :pattern ESCAPE '{LIKE_ESCAPE}'
               OR p.body LIKE :pattern ESCAPE '{LIKE_ESCAPE}')
        ORDER BY (p.title LIKE :pattern ESCAPE '{LIKE_ESCAPE}') DESC, p.created_at DESC, p.id DESC
        LIMIT :limit
        """,
        {"pattern": pattern, "limit": limit + 1},
    ).fetchall()
    return [dict(row) for row in rows[:limit]], len(rows) > limit


def search_all(conn, user_id, keyword, limit):
    if not keyword:
        return {"q": "", "records": [], "posts": [], "truncated": {"records": False, "posts": False}}
    records, more_records = search_records(conn, user_id, keyword, limit)
    posts, more_posts = search_posts(conn, keyword, limit)
    return {
        "q": keyword,
        "records": records,
        "posts": posts,
        "truncated": {"records": more_records, "posts": more_posts},
    }
