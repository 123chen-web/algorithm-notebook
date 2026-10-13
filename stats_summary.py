"""总览页「趋势」区的数据：GET /api/stats/summary 的主体。

口径与热力图 / 连续打卡 / 周报一致：一律按用户本地日划分。区别在于这里不把全部
复习记录取回 Python 逐条分桶，而是把每个本地日的起点换算成 UTC 秒，交给 SQL 分组，
所以响应时间和查询次数都不随复习条数增长。

「到期」相关的查询只有两处（_due_counts、_forecast），都通过 review_pool() /
due_condition() / overdue_condition() 拼条件；以后哪些易错点不参与到期统计
（例如已暂停的），只改 review_pool() 这一处。
"""

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

from learning_stats import current_streak
from scheduler import today_in_timezone

ALLOWED_DAYS = (7, 30, 90)
FORECAST_DAYS = 14
RETENTION_WEEKS = 8
# 评分 >= 3 算"想起来了"（与调度器里 quality >= 3 才推进间隔一致）。
PASSING_QUALITY = 3
STREAK_CHUNK_DAYS = 366


from core import config, time
def review_pool(alias="m"):
    """只有未暂停的易错点参与「到期 / 逾期 / 预测」，历史复习统计不受影响。"""
    return f"{alias}.suspended_at IS NULL"


def due_condition(alias="m", day=":today"):
    """到期（含逾期）：due_date <= 今天。"""
    return f"({review_pool(alias)} AND {alias}.due_date <= {day})"


def overdue_condition(alias="m", day=":today"):
    """逾期：due_date < 今天。"""
    return f"({review_pool(alias)} AND {alias}.due_date < {day})"


def _day_start(zone, day):
    """本地日 day 的第一个瞬间（UTC 秒）。

    取"本地日期第一次变成 day 的那一秒"，与 astimezone(zone).date() 的结果一致；
    夏令时让 00:00 不存在或出现两次的时区也不会错位。
    """
    naive = datetime(day.year, day.month, day.day)
    candidates = sorted({int(naive.replace(tzinfo=zone, fold=fold).timestamp()) for fold in (0, 1)})
    for stamp in candidates:
        if (datetime.fromtimestamp(stamp, zone).date() >= day
                and datetime.fromtimestamp(stamp - 1, zone).date() < day):
            return stamp
    return candidates[0]


def _bucket_sql(column, boundaries, low, high):
    """把 column（UTC 秒）映射到第几个本地日的二分 CASE；boundaries[i] 是第 i 天的起点。"""
    if high - low == 1:
        return str(low)
    middle = (low + high) // 2
    return (
        f"CASE WHEN {column} < {int(boundaries[middle])} "
        f"THEN {_bucket_sql(column, boundaries, low, middle)} "
        f"ELSE {_bucket_sql(column, boundaries, middle, high)} END"
    )


def _review_days(conn, user_id, zone, first, last, *, retention=True):
    """{本地日: (复习次数, 非首次复习次数, 其中评分>=3 的次数)}，first..last 含两端。

    非首次复习：该易错点在这条记录之前（更早的时间，或同一秒内 id 更小）至少还有一条
    复习记录；只用 reviews 的老列，旧数据缺新日志列也适用。
    """
    count = (last - first).days + 1
    boundaries = [_day_start(zone, first + timedelta(days=offset)) for offset in range(count + 1)]
    stamp = "CAST(strftime('%s', r.reviewed_at) AS INTEGER)"
    repeat = (
        f"""EXISTS (
            SELECT 1 FROM reviews earlier
            WHERE earlier.mistake_id = r.mistake_id
              AND (CAST(strftime('%s', earlier.reviewed_at) AS INTEGER) < {stamp}
                   OR (CAST(strftime('%s', earlier.reviewed_at) AS INTEGER) = {stamp}
                       AND earlier.id < r.id)))"""
        if retention else "0"
    )
    # 先用文本前缀（YYYY-MM-DD，任何偏移量下与 UTC 日期最多差一天）粗筛，只对窗口内的行
    # 解析时间戳；每行的时间戳在物化的 CTE 里只算一次，再精确落进窗口、二分归日。
    rows = conn.execute(
        f"""
        WITH window_reviews AS MATERIALIZED (
            SELECT r.quality AS quality, {stamp} AS stamp, {repeat} AS repeat
            FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? AND r.reviewed_at >= ? AND r.reviewed_at < ?
        )
        SELECT {_bucket_sql("t.stamp", boundaries, 0, count)} AS bucket,
               COUNT(*) AS total,
               SUM(t.repeat) AS repeats,
               SUM(t.repeat AND t.quality >= {PASSING_QUALITY}) AS passed
        FROM window_reviews t
        WHERE t.stamp >= ? AND t.stamp < ?
        GROUP BY bucket
        """,
        (
            user_id, (first - timedelta(days=2)).isoformat(), (last + timedelta(days=3)).isoformat(),
            boundaries[0], boundaries[-1],
        ),
    ).fetchall()
    return {
        first + timedelta(days=row["bucket"]): (row["total"], row["repeats"] or 0, row["passed"] or 0)
        for row in rows
    }


def _streak_days(conn, user_id, zone, today, known, fetched_from):
    """连续打卡天数（learning_stats.current_streak 的口径）。

    known 是已取过的 {日: 计数}（覆盖 fetched_from..today）。连续链没碰到窗口起点就直接
    得出结果；碰到了才一块一块往更早的日子取，直到链不再变长。
    """
    days = set(known)
    streak = current_streak(days, today)
    while streak >= (today - fetched_from).days:
        chunk_last = fetched_from - timedelta(days=1)
        chunk_first = chunk_last - timedelta(days=STREAK_CHUNK_DAYS - 1)
        earlier = _review_days(conn, user_id, zone, chunk_first, chunk_last, retention=False)
        days |= set(earlier)
        fetched_from = chunk_first
        longer = current_streak(days, today)
        if longer == streak:
            break
        streak = longer
    return streak


def _due_counts(conn, user_id, today):
    row = conn.execute(
        f"""
        SELECT COALESCE(SUM({due_condition("m")}), 0) AS due,
               COALESCE(SUM({overdue_condition("m")}), 0) AS overdue
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = :user_id
        """,
        {"today": today.isoformat(), "user_id": user_id},
    ).fetchone()
    return {"today": row["due"], "overdue": row["overdue"]}


def _forecast(conn, user_id, today):
    """今天起 FORECAST_DAYS 天每天到期多少；今天这一格含全部逾期。"""
    last = today + timedelta(days=FORECAST_DAYS - 1)
    rows = conn.execute(
        f"""
        SELECT CASE WHEN {due_condition("m")} THEN :today ELSE m.due_date END AS day,
               COUNT(*) AS due
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = :user_id AND {review_pool("m")} AND m.due_date <= :last
        GROUP BY day
        """,
        {"today": today.isoformat(), "last": last.isoformat(), "user_id": user_id},
    ).fetchall()
    by_day = {row["day"]: row["due"] for row in rows}
    days = [(today + timedelta(days=offset)).isoformat() for offset in range(FORECAST_DAYS)]
    return [{"date": day, "due": by_day.get(day, 0)} for day in days]


def _rate(passed, reviews):
    return round(passed / reviews, 3) if reviews else None


def _period(counts, first, last):
    """first..last 内的 (复习次数, 非首次次数, 通过次数)。"""
    reviews = repeats = passed = 0
    for offset in range((last - first).days + 1):
        total, repeat, ok = counts.get(first + timedelta(days=offset), (0, 0, 0))
        reviews += total
        repeats += repeat
        passed += ok
    return reviews, repeats, passed


def _retention(counts, first, last):
    _, repeats, passed = _period(counts, first, last)
    return {"reviews": repeats, "passed": passed, "rate": _rate(passed, repeats)}


def summary(conn, user_id, timezone_name, today, days):
    """调用者在只读事务里调用，几个查询得到同一份快照；查询次数是常数。"""
    zone = ZoneInfo(timezone_name)
    current_first = today - timedelta(days=days - 1)
    previous_last = current_first - timedelta(days=1)
    previous_first = previous_last - timedelta(days=days - 1)
    this_monday = today - timedelta(days=today.weekday())
    first_week = this_monday - timedelta(weeks=RETENTION_WEEKS - 1)
    window_first = min(previous_first, first_week)
    counts = _review_days(conn, user_id, zone, window_first, today)

    weekly = []
    for index in range(RETENTION_WEEKS):
        start = first_week + timedelta(weeks=index)
        weekly.append({"week_start": start.isoformat(),
                       **_retention(counts, start, min(start + timedelta(days=6), today))})
    return {
        "timezone": timezone_name,
        "today": today.isoformat(),
        "days": days,
        "due": _due_counts(conn, user_id, today),
        "streak_days": _streak_days(conn, user_id, zone, today, counts, window_first),
        "reviews": {
            "current": _period(counts, current_first, today)[0],
            "previous": _period(counts, previous_first, previous_last)[0],
        },
        "retention": {
            "current": _retention(counts, current_first, today),
            "previous": _retention(counts, previous_first, previous_last),
            "weekly": weekly,
        },
        "forecast": _forecast(conn, user_id, today),
        "daily_reviews": [
            {"date": day.isoformat(), "count": counts.get(day, (0, 0, 0))[0]}
            for day in (current_first + timedelta(days=offset) for offset in range(days))
        ],
    }


def weakness_analysis_state(conn, user_id):
    count = conn.execute(
        "SELECT COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id "
        "WHERE p.user_id = ?",
        (user_id,),
    ).fetchone()[0]
    row = conn.execute(
        "SELECT content, created_at FROM weakness_insights WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    insight = (
        {"content": json.loads(row["content"]), "created_at": row["created_at"]}
        if row else None
    )
    if count < config.WEAKNESS_MIN_MISTAKES:
        status = "insufficient_data"
        message = (
            f"再积累几条错题就能看出真正的规律了。当前有 {count} 条易错点，"
            f"还差 {config.WEAKNESS_MIN_MISTAKES - count} 条；本次不会调用 AI，也不消耗额度。"
        )
    elif insight is None:
        status = "not_analyzed"
        message = "还没有分析过。让 AI 结合错题和复习历史，找出反复卡住你的根本原因。"
    else:
        status, message = "ready", None
    return {
        "status": status,
        "message": message,
        "minimum_mistakes": config.WEAKNESS_MIN_MISTAKES,
        "mistake_count": count,
        "insight": insight,
    }


def weakness_analysis_reference(conn, user_id, total_mistakes):
    # 最近的题目优先；同题下的易错点也按 ID 确定顺序，避免 LIMIT 不稳定。
    # 在 SQL 层限制文本长度，空错因才补充原始思路/解法片段，不修改原记录。
    rows = conn.execute(
        """
        SELECT m.id AS mistake_id, m.problem_id, substr(p.title, 1, 200) AS title,
               p.zone, substr(m.description, 1, 1000) AS description, p.created_at,
               substr(p.thinking, 1, 600) AS thinking,
               substr(p.code, 1, 800) AS work_excerpt,
               p.verdict AS verdict, substr(m.counterexample, 1, 500) AS counterexample
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        ORDER BY p.created_at DESC, m.id DESC LIMIT ?
        """,
        (user_id, config.WEAKNESS_MAX_MISTAKES),
    ).fetchall()
    mistakes = []
    for row in rows:
        item = dict(row)
        # 判题类型和最小反例是空就去掉，不占提示词长度。
        if not item.get("verdict"):
            item.pop("verdict", None)
        if not (item.get("counterexample") or "").strip():
            item.pop("counterexample", None)
        if item["description"].strip():
            item.pop("thinking")
            item.pop("work_excerpt")
        item.update(review_count=0, failed_review_count=0, recent_reviews=[])
        mistakes.append(item)
    by_id = {item["mistake_id"]: item for item in mistakes}
    placeholders = ",".join("?" for _ in by_id)
    # 聚合完整复习历史，但只发送最近几次明细，兼顾反复低分与近期改善。
    # 仅查询当前用户名下已经选中的错点 ID，不读取其他用户的复习记录。
    reviews = conn.execute(
        f"""
        WITH ranked AS (
            SELECT id, mistake_id, quality, reviewed_at,
                   COUNT(*) OVER (PARTITION BY mistake_id) AS review_count,
                   SUM(CASE WHEN quality < 3 THEN 1 ELSE 0 END)
                       OVER (PARTITION BY mistake_id) AS failed_review_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY mistake_id ORDER BY reviewed_at DESC, id DESC
                   ) AS recent_rank
            FROM reviews WHERE mistake_id IN ({placeholders})
        )
        SELECT * FROM ranked WHERE recent_rank <= ?
        ORDER BY reviewed_at, id
        """,
        (*by_id, config.WEAKNESS_RECENT_REVIEWS),
    ).fetchall()
    for row in reviews:
        item = by_id[row["mistake_id"]]
        item["review_count"] = row["review_count"]
        item["failed_review_count"] = row["failed_review_count"]
        item["recent_reviews"].append({
            "quality": row["quality"], "reviewed_at": row["reviewed_at"],
        })
    sample = {
        "mistake_count": len(mistakes),
        "problem_count": len({item["problem_id"] for item in mistakes}),
        "review_count": sum(item["review_count"] for item in mistakes),
        "period_start": min(item["created_at"] for item in mistakes),
        "period_end": max(item["created_at"] for item in mistakes),
    }
    return {"total_mistakes": total_mistakes, "sample": sample, "mistakes": mistakes}


def growth_by_zone(conn, user_id, today):
    # 不用 SQL 日期函数：created_at 是 UTC 时间戳，"今天"要按用户自己的
    # 时区算，两者混在一条 SQL 里容易出偏差；取出来后在 Python 里统一按
    # 日期比较，跟 current_streak() 的思路一致。mistakes 本身没有
    # created_at，用所属 problem 的记录时间代表这条易错点的录入时间——
    # 这跟"薄弱点分析"里 period_start/period_end 的口径一致。
    rows = conn.execute(
        "SELECT p.zone, p.created_at FROM mistakes m JOIN problems p ON p.id = m.problem_id "
        "WHERE p.user_id = ?",
        (user_id,),
    ).fetchall()

    by_zone = {}
    for row in rows:
        created_date = datetime.fromisoformat(row["created_at"]).date()
        by_zone.setdefault(row["zone"], []).append(created_date)

    zones = []
    for zone, dates in by_zone.items():
        most_recent = max(dates)
        days_since_last = (today - most_recent).days
        recent = sum(
            1 for created_date in dates
            if 0 <= (today - created_date).days < config.GROWTH_RECENT_WINDOW_DAYS
        )
        prior = sum(
            1 for created_date in dates
            if config.GROWTH_RECENT_WINDOW_DAYS <= (today - created_date).days < 2 * config.GROWTH_RECENT_WINDOW_DAYS
        )
        zones.append({
            "zone": zone,
            "total_mistakes": len(dates),
            "days_since_last_mistake": days_since_last,
            "recent_30_days": recent,
            "prior_30_days": prior,
            "quiet_streak": recent == 0 and days_since_last >= config.GROWTH_QUIET_THRESHOLD_DAYS,
        })

    zones.sort(key=lambda item: item["zone"])
    return zones


def weakness_by_zone(conn, user_ids=None, min_cohort=config.COMMUNITY_MIN_COHORT):
    # 聚合只返回分区计数，不带用户名或错题内容。体验数据不代表真实学习
    # 信号，因此全站和小组都排除；这不影响体验账号加入小组和展示打卡天数。
    # None 表示全站；空集合表示没有成员，不能退回全站统计。
    params = ()
    user_filter = ""
    if user_ids is not None:
        params = tuple(set(user_ids))
        if not params:
            return {}
        user_filter = f" AND u.id IN ({','.join('?' for _ in params)})"
    rows = conn.execute(
        f"""
        SELECT p.zone, COUNT(*) AS mistake_count
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        JOIN users u ON u.id = p.user_id
        WHERE u.is_trial = 0 AND u.deleted_at IS NULL{user_filter}
        GROUP BY p.zone, p.user_id
        """,
        params,
    ).fetchall()
    by_zone = {}
    for row in rows:
        stats = by_zone.setdefault(row["zone"], {"total_users": 0, "struggling_users": 0})
        stats["total_users"] += 1
        if row["mistake_count"] >= config.COMMUNITY_STRUGGLING_THRESHOLD:
            stats["struggling_users"] += 1

    result = {}
    for zone, stats in by_zone.items():
        total = stats["total_users"]
        result[zone] = {
            "sample_size": total,
            "struggling_ratio": (
                round(stats["struggling_users"] / total, 4)
                if total >= min_cohort else None
            ),
        }
    return result


def community_weakness_by_zone(conn):
    return weakness_by_zone(conn)


def window_total(counter, first, last):
    return sum(count for day, count in counter.items() if first <= day <= last)


def review_streaks_by_user(users, review_rows):
    # 全站排行榜和小组共用：每个人的复习日期和今天都按自己的时区计算。
    timezones = {row["id"]: row["timezone"] for row in users}
    review_dates = defaultdict(set)
    for row in review_rows:
        tz = timezones.get(row["user_id"])
        if tz is None:
            continue
        reviewed_at = datetime.fromisoformat(row["reviewed_at"])
        review_dates[row["user_id"]].add(today_in_timezone(tz, reviewed_at))

    streaks = {}
    for row in users:
        streaks[row["id"]] = current_streak(
            review_dates.get(row["id"], set()),
            time.today_for(row),
        )
    return streaks

