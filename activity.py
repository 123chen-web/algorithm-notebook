"""按用户本地日期汇总复习与新增记录，供热力图、总览页等共用。

所有日期都按用户自己的时区划分本地日（与连续打卡、本周战报同一口径）；
只取原始时间戳，在 Python 里转成本地日期后分桶，不混用 SQL 日期函数。
"""

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from learning_stats import current_streak


def day_counts(conn, user_id, timezone_name):
    """返回 (reviews, records, mistakes)：三个 Counter，键为用户本地日期。

    reviews = 当天复习评分条数；records = 当天新增的题目记录数（至少带一条易错点）；
    mistakes = 当天新增的易错点条数（与本周战报的"新增易错点"同一口径）。
    调用者可在只读事务里调用以获得一致快照。一次遍历、内存分桶，不对每一天各查一次。
    """
    zone = ZoneInfo(timezone_name)

    def local_day(stamp):
        return datetime.fromisoformat(stamp).astimezone(zone).date()

    reviews = Counter()
    for row in conn.execute(
        """
        SELECT r.reviewed_at FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    ):
        reviews[local_day(row["reviewed_at"])] += 1
    records = Counter()
    mistakes = Counter()
    for row in conn.execute(
        """
        SELECT p.created_at, COUNT(m.id) AS mistake_count
        FROM problems p JOIN mistakes m ON m.problem_id = p.id
        WHERE p.user_id = ? GROUP BY p.id
        """,
        (user_id,),
    ):
        day = local_day(row["created_at"])
        records[day] += 1
        mistakes[day] += row["mistake_count"]
    return reviews, records, mistakes


def week_start(today):
    """本周一（周一为一周的第一天）。"""
    return today - timedelta(days=today.weekday())


def activity_summary(reviews, records, today, weeks):
    """热力图接口的响应主体；只返回有活动的日子，前端自己补零。"""
    first = week_start(today) - timedelta(weeks=weeks - 1)
    days = []
    for day in sorted(set(reviews) | set(records)):
        if first <= day <= today:
            days.append({
                "date": day.isoformat(),
                "reviews": reviews.get(day, 0),
                "records": records.get(day, 0),
            })
    return {
        "today": today.isoformat(),
        "from": first.isoformat(),
        "weeks": weeks,
        "days": days,
        "totals": {
            "reviews": sum(d["reviews"] for d in days),
            "records": sum(d["records"] for d in days),
            "active_days": len(days),
        },
        "streak_days": current_streak(set(reviews), today),
    }
