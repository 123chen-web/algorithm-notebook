"""当前学习数据的只读统计；指标独立于徽章阈值，供其他学习概览复用。"""

from datetime import datetime, timedelta
from typing import TypedDict

from scheduler import today_in_timezone


class LearningMetrics(TypedDict):
    current_streak_days: int
    mistake_count: int
    recorded_zone_count: int
    generated_practice_count: int
    has_weakness_analysis: bool


def current_streak(review_dates, today):
    # 今天还没打卡但昨天打卡了，连续天数按"还没断"算，不因为今天没过完就清零。
    cursor = today
    if cursor not in review_dates:
        cursor -= timedelta(days=1)
        if cursor not in review_dates:
            return 0
    streak = 0
    while cursor in review_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


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
        "has_weakness_analysis": bool(counts["has_weakness_analysis"]),
    }
