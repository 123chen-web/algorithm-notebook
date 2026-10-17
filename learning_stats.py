"""当前学习数据的只读统计；指标独立于徽章阈值，供其他学习概览复用。"""

from datetime import datetime, timedelta
from typing import TypedDict

from scheduler import today_in_timezone


class LearningMetrics(TypedDict):
    current_streak_days: int
    mistake_count: int
    recorded_zone_count: int
    generated_practice_count: int
    verified_pass_count: int
    revenge_count: int
    has_weakness_analysis: bool


# 宽松的连续打卡（10/14 评估建议：断签焦虑会让人放弃）：中间歇 1 天不算断，连着 2 天没复习才断。
# 数的是“有复习的天”，歇的那天不算进去。今天还没复习也不算断（今天还没过完）。
REST_DAYS = 1


def current_streak(review_dates, today):
    days = {day for day in review_dates if day <= today}
    if not days:
        return 0
    cursor = max(days)
    # 最近一次复习离今天最多隔 1 天歇的日子（今天本身还没过完，不算）。
    if (today - cursor).days > REST_DAYS + 1:
        return 0
    streak = 0
    while cursor in days:
        streak += 1
        previous = next(
            (cursor - timedelta(days=gap) for gap in range(1, REST_DAYS + 2) if cursor - timedelta(days=gap) in days),
            None,
        )
        if previous is None:
            break
        cursor = previous
    return streak


def streak_breaks_today(review_dates, today):
    """今天再不复习，连续打卡就会断：今天、昨天都没复习，但还有一段没断的连续。"""
    return (
        current_streak(review_dates, today) > 0
        and today not in review_dates
        and (today - timedelta(days=1)) not in review_dates
    )


def week_review_days(review_dates, today):
    """本周（周一到今天，用户本地日期）有几天复习过。"""
    monday = today - timedelta(days=today.weekday())
    return sum(1 for day in review_dates if monday <= day <= today)


def learning_metrics(conn, user_id, timezone_name, today) -> LearningMetrics:
    """统计当前仍存在的数据；today 为用户本地日期，不推断历史解锁状态。

    调用者可在只读事务中调用以获得一致快照。成功练习按 variants 行数
    计「道」，不按 ai_usage.attempts 计「次」：后者也包含失败和其他 AI 功能。
    """
    counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM mistakes m
             JOIN problems p ON p.id = m.problem_id
             WHERE p.user_id = :user_id) AS mistake_count,
            (SELECT COUNT(DISTINCT zone) FROM problems
             WHERE user_id = :user_id) AS recorded_zone_count,
            (SELECT COUNT(*) FROM variants v
             JOIN mistakes m ON m.id = v.mistake_id
             JOIN problems p ON p.id = m.problem_id
             WHERE p.user_id = :user_id) AS generated_practice_count,
            (SELECT COUNT(*) FROM reviews r
             JOIN mistakes m ON m.id = r.mistake_id
             JOIN problems p ON p.id = m.problem_id
             WHERE p.user_id = :user_id AND r.mode IN ('rewrite', 'redo', 'similar')
               AND r.quality >= 4) AS verified_pass_count,
            (SELECT COUNT(*) FROM mistakes m
             JOIN problems p ON p.id = m.problem_id
             WHERE p.user_id = :user_id AND m.revenge_at IS NOT NULL) AS revenge_count,
            EXISTS(SELECT 1 FROM weakness_insights
                   WHERE user_id = :user_id) AS has_weakness_analysis
        """,
        {"user_id": user_id},
    ).fetchone()
    review_rows = conn.execute(
        """
        SELECT r.reviewed_at FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    )
    review_dates = {
        today_in_timezone(timezone_name, datetime.fromisoformat(row["reviewed_at"]))
        for row in review_rows
    }
    return {
        "current_streak_days": current_streak(review_dates, today),
        "mistake_count": counts["mistake_count"],
        "recorded_zone_count": counts["recorded_zone_count"],
        "generated_practice_count": counts["generated_practice_count"],
        "verified_pass_count": counts["verified_pass_count"],
        "revenge_count": counts["revenge_count"],
        "has_weakness_analysis": bool(counts["has_weakness_analysis"]),
    }
