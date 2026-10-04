"""管理后台运营指标：只读统计，不改任何业务数据。

口径（站长视角，统一按服务器 UTC 日切分）：
- 本周期 = 最近 ``days`` 天（含今天），上一周期 = 紧邻的前 ``days`` 天。
- 用户相关的计数只算正式、未注销的账号；体验账号与已注销账号一律排除。
  论坛帖子 / 评论里被软删除的不算（被管理员删掉的垃圾内容不应让人显得“活跃”）。
- AI 调用按成本记账：所有 ``ai_calls`` 都算（注销时 user_id 会被置空，体验账号
  同样花服务商的钱），不按账号过滤。
- 兑换码的“直接开通”审计记录（code_hint 为 ``----``）不是真正的兑换码，不计入。
- 所有统计都在 SQL 里分组，查询条数固定，不随数据量增长。

时间戳都是 UTC 的 ISO 8601 字符串，所以 ``col >= 'YYYY-MM-DD'`` 的字符串比较等价于
按日界比较，并且能用上 created_at 一类的索引；按天分组用 ``substr(col, 1, 10)``。
"""
from datetime import timedelta, timezone

PERIOD_CHOICES = (7, 30)

# 周期内算数的账号：正式（非体验）且未注销。
ELIGIBLE_USER = "u.deleted_at IS NULL AND u.is_trial = 0"
DIRECT_GRANT_HINT = "----"

# 任一“行为”事件：新增错题记录 / 复习 / 发帖 / 评论，统一成 (user_id, at)。
EVENTS = """
    SELECT p.user_id AS user_id, p.created_at AS at FROM problems p
    UNION ALL
    SELECT pr.user_id, r.reviewed_at FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems pr ON pr.id = m.problem_id
    UNION ALL
    SELECT user_id, created_at FROM posts WHERE deleted_at IS NULL
    UNION ALL
    SELECT user_id, created_at FROM post_comments WHERE deleted_at IS NULL
"""


def _per_day(conn, sql, params):
    """执行 ``SELECT 日期, 数量 ... GROUP BY 日期`` 并返回 {日期: 数量}。"""
    return {row[0]: row[1] for row in conn.execute(sql, params)}


def _pair(per_day, days_cur, days_prev):
    return {
        "current": sum(per_day.get(day, 0) for day in days_cur),
        "previous": sum(per_day.get(day, 0) for day in days_prev),
    }


def compute_metrics(conn, days, now):
    """返回 ``GET /api/admin/metrics`` 的响应体。``now`` 是带时区的 datetime。"""
    if days not in PERIOD_CHOICES:
        raise ValueError(f"days must be one of {PERIOD_CHOICES}")
    now = now.astimezone(timezone.utc)
    today = now.date()
    current_days = [(today - timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    previous_days = [(today - timedelta(days=2 * days - 1 - i)).isoformat() for i in range(days)]
    params = {
        "prev_start": previous_days[0],
        "cur_start": current_days[0],
        "end": (today + timedelta(days=1)).isoformat(),
        "now": now.isoformat(timespec="seconds"),
        "hint": DIRECT_GRANT_HINT,
    }
    window = "{col} >= :prev_start AND {col} < :end"

    # 多条聚合读取同一个快照，避免并发写入让各数字互相对不上。
    if not conn.in_transaction:
        conn.execute("BEGIN")

    new_users = _per_day(conn, f"""
        SELECT substr(u.created_at, 1, 10), COUNT(*) FROM users u
        WHERE {ELIGIBLE_USER} AND {window.format(col="u.created_at")}
        GROUP BY 1
    """, params)
    records = _per_day(conn, f"""
        SELECT substr(p.created_at, 1, 10), COUNT(*)
        FROM problems p JOIN users u ON u.id = p.user_id
        WHERE {ELIGIBLE_USER} AND {window.format(col="p.created_at")}
        GROUP BY 1
    """, params)
    reviews = _per_day(conn, f"""
        SELECT substr(r.reviewed_at, 1, 10), COUNT(*)
        FROM reviews r JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            JOIN users u ON u.id = p.user_id
        WHERE {ELIGIBLE_USER} AND {window.format(col="r.reviewed_at")}
        GROUP BY 1
    """, params)
    posts = _per_day(conn, f"""
        SELECT substr(po.created_at, 1, 10), COUNT(*)
        FROM posts po JOIN users u ON u.id = po.user_id
        WHERE po.deleted_at IS NULL AND {ELIGIBLE_USER}
            AND {window.format(col="po.created_at")}
        GROUP BY 1
    """, params)
    comments = _per_day(conn, f"""
        SELECT substr(c.created_at, 1, 10), COUNT(*)
        FROM post_comments c JOIN users u ON u.id = c.user_id
        WHERE c.deleted_at IS NULL AND {ELIGIBLE_USER}
            AND {window.format(col="c.created_at")}
        GROUP BY 1
    """, params)

    active_events = f"""
        SELECT e.user_id AS user_id, substr(e.at, 1, 10) AS day
        FROM ({EVENTS}) e JOIN users u ON u.id = e.user_id
        WHERE {ELIGIBLE_USER} AND {window.format(col="e.at")}
    """
    active_daily = _per_day(conn, f"""
        SELECT day, COUNT(DISTINCT user_id) FROM ({active_events}) GROUP BY day
    """, params)
    active = conn.execute(f"""
        SELECT COUNT(DISTINCT CASE WHEN day >= :cur_start THEN user_id END),
               COUNT(DISTINCT CASE WHEN day < :cur_start THEN user_id END)
        FROM ({active_events})
    """, params).fetchone()

    ai_rows = conn.execute("""
        SELECT feature,
            SUM(created_at >= :cur_start) AS calls,
            SUM(created_at < :cur_start) AS prev_calls,
            SUM(created_at >= :cur_start AND ok = 0) AS failed,
            SUM(created_at < :cur_start AND ok = 0) AS prev_failed,
            SUM(CASE WHEN created_at >= :cur_start
                THEN COALESCE(prompt_tokens, 0) ELSE 0 END) AS prompt_tokens,
            SUM(CASE WHEN created_at >= :cur_start
                THEN COALESCE(completion_tokens, 0) ELSE 0 END) AS completion_tokens
        FROM ai_calls
        WHERE created_at >= :prev_start AND created_at < :end
        GROUP BY feature
    """, params).fetchall()

    reports = conn.execute("""
        SELECT (SELECT COUNT(*) FROM reports WHERE resolved_at IS NULL)
             + (SELECT COUNT(*) FROM avatar_reports a
                JOIN users reporter ON reporter.id = a.reporter_user_id
                JOIN users owner ON owner.id = a.avatar_owner_id
                WHERE a.resolved_at IS NULL
                  AND reporter.deleted_at IS NULL AND owner.deleted_at IS NULL)
    """).fetchone()[0]

    redeem = conn.execute("""
        SELECT COALESCE(SUM(created_at >= :cur_start AND created_at < :end), 0),
               COALESCE(SUM(redeemed_by IS NOT NULL AND redeemed_at >= :cur_start
                            AND redeemed_at < :end), 0),
               COALESCE(SUM(redeemed_by IS NULL AND revoked_at IS NULL
                            AND (expires_at IS NULL OR expires_at > :now)), 0)
        FROM redeem_codes WHERE code_hint != :hint
    """, params).fetchone()

    # 漏斗：本周期内注册的正式账号。“次日回访”要求注册日之后的某个更晚的 UTC 日
    # 还有过行为，同一天内回来不算。
    funnel = conn.execute("""
        SELECT COUNT(*),
            COALESCE(SUM(EXISTS(
                SELECT 1 FROM problems p WHERE p.user_id = u.id
                  AND julianday(p.created_at) >= julianday(u.created_at))), 0),
            COALESCE(SUM(EXISTS(
                SELECT 1 FROM reviews r JOIN mistakes m ON m.id = r.mistake_id
                  JOIN problems p ON p.id = m.problem_id
                WHERE p.user_id = u.id
                  AND julianday(r.reviewed_at) >= julianday(u.created_at))), 0),
            COALESCE(SUM(
                EXISTS(SELECT 1 FROM problems p WHERE p.user_id = u.id
                    AND substr(p.created_at, 1, 10) > substr(u.created_at, 1, 10))
                OR EXISTS(SELECT 1 FROM reviews r JOIN mistakes m ON m.id = r.mistake_id
                    JOIN problems p ON p.id = m.problem_id
                    WHERE p.user_id = u.id
                    AND substr(r.reviewed_at, 1, 10) > substr(u.created_at, 1, 10))
                OR EXISTS(SELECT 1 FROM posts po WHERE po.user_id = u.id
                    AND po.deleted_at IS NULL
                    AND substr(po.created_at, 1, 10) > substr(u.created_at, 1, 10))
                OR EXISTS(SELECT 1 FROM post_comments c WHERE c.user_id = u.id
                    AND c.deleted_at IS NULL
                    AND substr(c.created_at, 1, 10) > substr(u.created_at, 1, 10))
            ), 0)
        FROM users u
        WHERE """ + ELIGIBLE_USER + " AND u.created_at >= :cur_start AND u.created_at < :end",
        params).fetchone()

    by_feature = sorted(
        (
            {"feature": row["feature"], "calls": row["calls"] or 0, "failed": row["failed"] or 0,
             "tokens": (row["prompt_tokens"] or 0) + (row["completion_tokens"] or 0)}
            for row in ai_rows if row["calls"]
        ),
        key=lambda item: (-item["calls"], item["feature"]),
    )

    def total(column):
        return sum(row[column] or 0 for row in ai_rows)

    return {
        "days": days,
        "generated_at": now.isoformat(),
        "new_users": _pair(new_users, current_days, previous_days),
        "active_users": {"current": active[0], "previous": active[1]},
        "records": _pair(records, current_days, previous_days),
        "reviews": _pair(reviews, current_days, previous_days),
        "posts": _pair(posts, current_days, previous_days),
        "comments": _pair(comments, current_days, previous_days),
        "ai": {
            "calls": {"current": total("calls"), "previous": total("prev_calls")},
            "failed": {"current": total("failed"), "previous": total("prev_failed")},
            "tokens": {"prompt": total("prompt_tokens"), "completion": total("completion_tokens")},
            "by_feature": by_feature,
        },
        "pending_reports": reports,
        "redeem": {"created": redeem[0], "redeemed": redeem[1], "unused": redeem[2]},
        "funnel": {
            "registered": funnel[0], "first_record": funnel[1],
            "first_review": funnel[2], "returned_next_day": funnel[3],
        },
        "daily": [
            {"date": day, "new_users": new_users.get(day, 0),
             "active_users": active_daily.get(day, 0), "reviews": reviews.get(day, 0)}
            for day in current_days
        ],
    }
