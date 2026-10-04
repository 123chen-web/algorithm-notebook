"""总览页"趋势"区的汇总统计：待复习预测、真实保持率、与上一周期的对比。

全部按用户本地日划分，SQL 分组，只用两条查询（复习按 15 分钟桶分组、易错点按到期日分组），
查询次数不随复习条数增长。纯统计，不调用 AI。
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from learning_stats import current_streak

FORECAST_DAYS = 14
RETENTION_WEEKS = 8
PASS_QUALITY = 3
# 所有现行时区的 UTC 偏移都是 15 分钟的整数倍，本地午夜因此一定落在桶边界上。
BUCKET_SECONDS = 900


def due_condition(day_param=":day"):
    """"到期"的唯一定义：易错点 m 在 day_param 当天或之前到期。

    之后"已暂停"的易错点要从所有到期统计里排除，只需要改这一处。
    """
    return f"m.due_date <= {day_param}"


def local_midnight_epoch(day, zone):
    local = datetime.combine(day, time.min, tzinfo=zone)
    return int(local.astimezone(timezone.utc).timestamp())


def week_start(day):
    return day - timedelta(days=day.weekday())


def _rate(passed, reviews):
    return round(passed / reviews, 3) if reviews else None


def _retention_block(reviews, passed):
    return {"reviews": reviews, "passed": passed, "rate": _rate(passed, reviews)}


def review_buckets(conn, user_id):
    """按 15 分钟桶汇总该用户全部复习：(桶起点的 epoch 秒, 总数, 非首次数, 非首次中通过数)。

    非首次 = 该易错点在这条记录之前还有复习记录（EXISTS，按 id 先后）。
    """
    rows = conn.execute(
        """
        SELECT CAST(strftime('%s', r.reviewed_at) AS INTEGER) / :size AS bucket,
               COUNT(*) AS total,
               SUM(later) AS later_total,
               SUM(later AND r.quality >= :pass_quality) AS later_passed
        FROM (
            SELECT r.*, EXISTS(
                SELECT 1 FROM reviews earlier
                WHERE earlier.mistake_id = r.mistake_id AND earlier.id < r.id
            ) AS later
            FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = :user_id
        ) r
        GROUP BY bucket
        """,
        {"size": BUCKET_SECONDS, "pass_quality": PASS_QUALITY, "user_id": user_id},
    ).fetchall()
    return [
        (row["bucket"] * BUCKET_SECONDS, row["total"], row["later_total"], row["later_passed"])
        for row in rows
        if row["bucket"] is not None
    ]


def due_by_date(conn, user_id, today, last_day):
    """{到期日: 条数}，只含 last_day 及以前（用 due_condition 的口径）。"""
    rows = conn.execute(
        f"""
        SELECT m.due_date, COUNT(*) AS n
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = :user_id AND {due_condition(':last_day')}
        GROUP BY m.due_date
        """,
        {"user_id": user_id, "last_day": last_day.isoformat()},
    ).fetchall()
    return {row["due_date"]: row["n"] for row in rows}


def stats_summary(conn, user_id, timezone_name, today, days):
    zone = ZoneInfo(timezone_name)
    # 每个桶映射到本地日；多个桶共用同一天时累加。
    per_day = {}
    for start, total, later_total, later_passed in review_buckets(conn, user_id):
        day = datetime.fromtimestamp(start, tz=timezone.utc).astimezone(zone).date()
        cell = per_day.setdefault(day, [0, 0, 0])
        cell[0] += total
        cell[1] += later_total
        cell[2] += later_passed

    def window(first, last):
        reviews = later = passed = 0
        for day, (total, later_total, later_passed) in per_day.items():
            if first <= day <= last:
                reviews += total
                later += later_total
                passed += later_passed
        return reviews, later, passed

    current_first = today - timedelta(days=days - 1)
    previous_first = current_first - timedelta(days=days)
    previous_last = current_first - timedelta(days=1)
    current_reviews, current_later, current_passed = window(current_first, today)
    previous_reviews, previous_later, previous_passed = window(previous_first, previous_last)

    this_monday = week_start(today)
    weekly = []
    for offset in range(RETENTION_WEEKS - 1, -1, -1):
        monday = this_monday - timedelta(weeks=offset)
        _, later, passed = window(monday, monday + timedelta(days=6))
        weekly.append({"week_start": monday.isoformat(), **_retention_block(later, passed)})

    forecast_last = today + timedelta(days=FORECAST_DAYS - 1)
    due = due_by_date(conn, user_id, today, forecast_last)
    today_text = today.isoformat()
    due_today = sum(count for day, count in due.items() if day <= today_text)
    overdue = sum(count for day, count in due.items() if day < today_text)
    forecast = []
    for offset in range(FORECAST_DAYS):
        day = today + timedelta(days=offset)
        forecast.append({"date": day.isoformat(), "due": due_today if offset == 0 else due.get(day.isoformat(), 0)})

    return {
        "timezone": timezone_name,
        "today": today_text,
        "days": days,
        "due": {"today": due_today, "overdue": overdue},
        "streak_days": current_streak({day for day, cell in per_day.items() if cell[0] > 0}, today),
        "reviews": {"current": current_reviews, "previous": previous_reviews},
        "retention": {
            "current": _retention_block(current_later, current_passed),
            "previous": _retention_block(previous_later, previous_passed),
            "weekly": weekly,
        },
        "forecast": forecast,
        "daily_reviews": [
            {"date": (current_first + timedelta(days=index)).isoformat(),
             "count": per_day.get(current_first + timedelta(days=index), (0,))[0]}
            for index in range(days)
        ],
    }
