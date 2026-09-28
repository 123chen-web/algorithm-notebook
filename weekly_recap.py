"""按用户本地日期实时汇总连续两个七天窗口，不保存战报状态。"""

from datetime import datetime, timedelta
from typing import TypedDict

from learning_stats import current_streak
from scheduler import today_in_timezone


class WeeklyActivity(TypedDict):
    mistakes_recorded: int
    reviews_completed: int
    practice_generated: int
    active_days: int


class WeeklyRecap(WeeklyActivity):
    week_start: str
    week_end: str
    zones_touched: int
    current_streak_days: int
    previous_week: WeeklyActivity


def weekly_recap(conn, user_id, timezone_name, today) -> WeeklyRecap:
    """today 为用户本地日期；调用者在只读事务中获取一致快照。

    本周含今天及之前六天，上周为紧邻的前七天。只取原始时间戳，
    在 Python 中转为用户本地日期后比较，不混用 SQL 日期函数。
    """
    week_start = today - timedelta(days=6)
    previous_start = week_start - timedelta(days=7)
    current: WeeklyActivity = {
        "mistakes_recorded": 0,
        "reviews_completed": 0,
        "practice_generated": 0,
        "active_days": 0,
    }
    previous: WeeklyActivity = {
        "mistakes_recorded": 0,
        "reviews_completed": 0,
        "practice_generated": 0,
        "active_days": 0,
    }
    zones = set()

    # mistakes 没有 created_at，用所属 problems.created_at 代表录入时间，
    # 与成长趋势及薄弱点分析的 period_start/period_end 口径一致。
    mistake_rows = conn.execute(
        """
        SELECT p.zone, p.created_at FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )
    for row in mistake_rows:
        day = today_in_timezone(timezone_name, datetime.fromisoformat(row["created_at"]))
        if week_start <= day <= today:
            current["mistakes_recorded"] += 1
            zones.add(row["zone"])
        elif previous_start <= day < week_start:
            previous["mistakes_recorded"] += 1

    review_rows = conn.execute(
        """
        SELECT p.zone, r.reviewed_at FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )
    review_dates = set()
    for row in review_rows:
        day = today_in_timezone(timezone_name, datetime.fromisoformat(row["reviewed_at"]))
        review_dates.add(day)
        if week_start <= day <= today:
            current["reviews_completed"] += 1
            zones.add(row["zone"])
        elif previous_start <= day < week_start:
            previous["reviews_completed"] += 1
    current["active_days"] = sum(week_start <= day <= today for day in review_dates)
    previous["active_days"] = sum(previous_start <= day < week_start for day in review_dates)

    # 与 learning_metrics() 相同，成功练习逐条计 variants 行数；
    # ai_usage.attempts 还包含失败和其他 AI 功能，不能用作练习题数。
    practice_rows = conn.execute(
        """
        SELECT v.created_at FROM variants v
        JOIN mistakes m ON m.id = v.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )
    for row in practice_rows:
        day = today_in_timezone(timezone_name, datetime.fromisoformat(row["created_at"]))
        if week_start <= day <= today:
            current["practice_generated"] += 1
        elif previous_start <= day < week_start:
            previous["practice_generated"] += 1

    return {
        **current,
        "week_start": week_start.isoformat(),
        "week_end": today.isoformat(),
        "zones_touched": len(zones),
        # 全局连续打卡天数，不截断到本周，也沿用今天尚未打卡的宽限口径。
        "current_streak_days": current_streak(review_dates, today),
        "previous_week": previous,
    }
