"""总览页「趋势」区的数据：GET /api/stats/summary 的主体。

口径与热力图 / 连续打卡 / 周报一致：一律按用户本地日划分。区别在于这里不把全部
复习记录取回 Python 逐条分桶，而是把每个本地日的起点换算成 UTC 秒，交给 SQL 分组，
所以响应时间和查询次数都不随复习条数增长。

「到期」相关的查询只有两处（_due_counts、_forecast），都通过 review_pool() /
due_condition() / overdue_condition() 拼条件；以后哪些易错点不参与到期统计
（例如已暂停的），只改 review_pool() 这一处。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from learning_stats import current_streak

ALLOWED_DAYS = (7, 30, 90)
FORECAST_DAYS = 14
RETENTION_WEEKS = 8
# 评分 >= 3 算"想起来了"（与调度器里 quality >= 3 才推进间隔一致）。
PASSING_QUALITY = 3
STREAK_CHUNK_DAYS = 366


def review_pool(alias="m"):
    """哪些易错点参与「到期 / 逾期 / 预测」统计；目前是全部。"""
    return "1 = 1"


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
