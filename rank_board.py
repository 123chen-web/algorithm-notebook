"""昨日榜单：北京时间自然日里复习次数最多的前 10 名，附一句模板生成的表扬语。

只用聚合 SQL（查询次数不随用户数增长）和进程内按日缓存，不调用 AI。
“昨日”固定按北京时间（Asia/Shanghai，无夏令时）的自然日算，与用户自己的时区无关。
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import rank_cache
from db import connect

BEIJING = ZoneInfo("Asia/Shanghai")
TOP_SIZE = 10
# 并列名次都显示，但总行数封顶，防止大量并列把榜单撑得过长。
MAX_ROWS = 30
# 防刷：同一天内对同一条错题的多次评分最多算这么多次。
SAME_MISTAKE_DAILY_CAP = 3

# 上榜资格：正式账号、未封禁、未注销、没有选择“不参与公开榜单”。
ELIGIBLE_SQL = (
    "u.is_trial = 0 AND u.is_banned = 0 AND u.deleted_at IS NULL "
    "AND u.public_rank_opt_out = 0"
)


def now_utc():
    return datetime.now(timezone.utc)


def beijing_today(now=None):
    return (now or now_utc()).astimezone(BEIJING).date()


def yesterday(now=None):
    return beijing_today(now) - timedelta(days=1)


def day_bounds(first_day, last_day=None):
    """北京时间 first_day 0 点到 last_day 次日 0 点（左闭右开），返回 UTC 的 ISO 字符串。

    reviews.reviewed_at / problems.created_at 都由 utc_now() 写成
    “YYYY-MM-DDTHH:MM:SS+00:00”，同一格式的字符串可以直接按字典序比较。
    """
    last_day = last_day or first_day
    start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=BEIJING)
    end_day = last_day + timedelta(days=1)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=BEIJING)
    fmt = lambda moment: moment.astimezone(timezone.utc).isoformat(timespec="seconds")
    return fmt(start), fmt(end)


# 内层先按 (用户, 错题) 数出当天评分次数并封顶，外层再按用户求和。
CAPPED_COUNTS_SQL = f"""
SELECT user_id, SUM(MIN(c, {SAME_MISTAKE_DAILY_CAP})) AS n FROM (
    SELECT p.user_id AS user_id, r.mistake_id AS mistake_id, COUNT(*) AS c
    FROM reviews r
    JOIN mistakes m ON m.id = r.mistake_id
    JOIN problems p ON p.id = m.problem_id
    WHERE r.reviewed_at >= :start AND r.reviewed_at < :end
    GROUP BY p.user_id, r.mistake_id
) GROUP BY user_id
"""

RANKING_SQL = f"""
SELECT t.user_id, t.n, COALESCE(NULLIF(u.rank_display_name, ''), u.username) AS username, u.avatar_version FROM ({CAPPED_COUNTS_SQL}) t
JOIN users u ON u.id = t.user_id
WHERE {ELIGIBLE_SQL} AND t.n > 0
ORDER BY t.n DESC, t.user_id ASC
"""


def assign_ranks(rows):
    """标准竞赛排名：次数相同并列同一名次，下一名次跳过并列的人数（1, 1, 3）。"""
    ranked = []
    previous_count = None
    rank = 0
    for index, row in enumerate(rows):
        if row["n"] != previous_count:
            rank = index + 1
            previous_count = row["n"]
        ranked.append((rank, row))
    return ranked


def consecutive_days_ending(days, last_day):
    streak = 0
    cursor = last_day
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def streaks_through(conn, user_ids, last_day):
    """这些用户截至 last_day（北京时间）的连续复习天数，一条查询。"""
    if not user_ids:
        return {}
    _, end = day_bounds(last_day)
    placeholders = ",".join("?" for _ in user_ids)
    days = {user_id: set() for user_id in user_ids}
    for row in conn.execute(
        f"""
        SELECT p.user_id, r.reviewed_at FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id IN ({placeholders}) AND r.reviewed_at < ?
        """,
        (*user_ids, end),
    ):
        moment = datetime.fromisoformat(row["reviewed_at"])
        days[row["user_id"]].add(moment.astimezone(BEIJING).date())
    return {user_id: consecutive_days_ending(found, last_day) for user_id, found in days.items()}


def praise(rank, count, streak):
    """按名次和数据挑模板，纯确定性：同样的数据永远得到同一句话。不含任何用户输入。"""
    if rank == 1:
        pool = [
            "昨天复习了 {count} 次，榜首！连续第 {streak} 天，实至名归。",
            "昨日第一：{count} 次复习，已连续 {streak} 天，红笔都写不及你勤。",
        ]
    elif rank <= 3:
        pool = [
            "昨天复习了 {count} 次，名列前茅，连续第 {streak} 天。",
            "{count} 次复习，稳居前三，连续 {streak} 天不间断。",
        ]
    elif streak >= 30:
        pool = [
            "连续第 {streak} 天了，昨天又复习了 {count} 次，毅力惊人。",
            "昨天复习了 {count} 次，连续第 {streak} 天，这份坚持很难得。",
        ]
    elif streak >= 7:
        pool = [
            "昨天复习了 {count} 次，连续第 {streak} 天，稳！",
            "连续第 {streak} 天，昨天又复习了 {count} 次，稳扎稳打。",
        ]
    elif count >= 20:
        pool = [
            "昨天一口气复习了 {count} 次，劲头十足！",
            "昨天复习了 {count} 次，下笔有神。",
        ]
    elif streak >= 2:
        pool = [
            "昨天复习了 {count} 次，已连续 {streak} 天，继续保持。",
            "连续第 {streak} 天，昨天复习了 {count} 次，好势头。",
        ]
    else:
        pool = [
            "昨天复习了 {count} 次，好的开始，继续！",
            "昨天动笔复习了 {count} 次，一步一步来。",
        ]
    return pool[(count + streak) % len(pool)].format(count=count, streak=streak)


def compute_board(day):
    """某个北京日的榜单；只含可公开的字段。day 是要统计的那一天（通常是昨天）。"""
    start, end = day_bounds(day)
    with connect() as conn:
        # 排名和连续天数读同一个快照。
        conn.execute("BEGIN")
        rows = conn.execute(RANKING_SQL, {"start": start, "end": end}).fetchall()
        ranked = assign_ranks(rows)
        shown = [(rank, row) for rank, row in ranked if rank <= TOP_SIZE][:MAX_ROWS]
        streaks = streaks_through(conn, [row["user_id"] for _, row in shown], day)
    # 想进前十至少要达到第十名的次数；不足十人上榜时，复习 1 次就能上榜。
    threshold = rows[TOP_SIZE - 1]["n"] if len(rows) >= TOP_SIZE else 1
    entries = [
        {
            "rank": rank,
            "user_id": row["user_id"],
            "username": row["username"],
            "avatar_version": row["avatar_version"],
            "count": row["n"],
            "streak_days": streaks[row["user_id"]],
            "praise": praise(rank, row["n"], streaks[row["user_id"]]),
        }
        for rank, row in shown
    ]
    return {"day": day.isoformat(), "top_size": TOP_SIZE, "threshold": threshold,
            "entries": entries}


def my_count(user_id, day):
    """当前用户自己昨天的复习次数（同样封顶）；与是否参与榜单无关。"""
    start, end = day_bounds(day)
    with connect() as conn:
        row = conn.execute(
            f"SELECT n FROM ({CAPPED_COUNTS_SQL}) WHERE user_id = :user_id",
            {"start": start, "end": end, "user_id": user_id},
        ).fetchone()
    return row["n"] if row else 0


def yesterday_response(user, now=None):
    now = now or now_utc()
    day = yesterday(now)
    board = rank_cache.get_or_compute("yesterday", day, lambda: compute_board(day))
    count = my_count(user["id"], day)
    opted_out = bool(user.get("public_rank_opt_out"))
    mine = next((entry for entry in board["entries"] if entry["user_id"] == user["id"]), None)
    in_top = mine is not None and not opted_out and not user["is_trial"]
    gap = None
    if not in_top and not opted_out and not user["is_trial"]:
        # 没进前十（或被并列封顶截掉）：至少还差 1 次。
        gap = max(1, board["threshold"] - count)
    return {
        "day": board["day"],
        "timezone": "Asia/Shanghai",
        "top_size": board["top_size"],
        "entries": [
            {**entry, "is_me": entry["user_id"] == user["id"]}
            for entry in board["entries"]
        ],
        "me": {
            "count": count,
            "rank": mine["rank"] if in_top else None,
            "in_top": in_top,
            "gap": gap,
            "is_trial": bool(user["is_trial"]),
            "opted_out": opted_out,
        },
    }
