"""目标卡（GET / PUT / DELETE /api/goal）的读写与计算组装。

- 每个用户最多一条进行中的目标（goals.user_id 唯一）；「结束目标」只是写上
  ended_at，行保留，再次设置时复活同一行；
- 每天建议复习多少条由 goal_plan.plan() 纯函数算，这里只负责取数：
  pending_now / due_by_day 只数未暂停的记录（与 stats_summary 的到期口径一致，
  都走 review_pool() / due_condition()），done_today 是用户本地时区当天的评分条数
  （暂停的记录当天评了也算——活已经干完了）。
"""

import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from goal_plan import plan
from stats_summary import _day_start, due_condition, review_pool

NAME_MAX = 30
RANGE_DAYS = 365
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate(name, goal_date_text, today):
    """返回 (name, goal_date)；不合格直接 422，detail 是给人看的整句。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(422, "请填写目标名称")
    if len(name) > NAME_MAX:
        raise HTTPException(422, f"名称最多 {NAME_MAX} 字")
    if not DATE_RE.match(goal_date_text or ""):
        raise HTTPException(422, "目标日期必须是 YYYY-MM-DD 格式")
    try:
        goal_date = date.fromisoformat(goal_date_text)
    except ValueError:
        raise HTTPException(422, "目标日期不是一个真实存在的日期") from None
    if not today + timedelta(days=1) <= goal_date <= today + timedelta(days=RANGE_DAYS):
        raise HTTPException(422, f"日期必须是未来 1–{RANGE_DAYS} 天内")
    return name, goal_date


def _pending_now(conn, user_id, today):
    """现在已经到期（含逾期）的条数；只数未暂停的记录。"""
    return conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = :user_id AND {due_condition("m")}
        """,
        {"user_id": user_id, "today": today.isoformat()},
    ).fetchone()["n"]


def _due_by_day(conn, user_id, today, days_left):
    """今天之后第 1..days_left 天每天自然到期的条数；只数未暂停的记录。"""
    if days_left <= 0:
        return []
    last = today + timedelta(days=days_left)
    rows = conn.execute(
        f"""
        SELECT m.due_date AS day, COUNT(*) AS n
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = :user_id AND {review_pool("m")}
          AND m.due_date > :today AND m.due_date <= :last
        GROUP BY m.due_date
        """,
        {"user_id": user_id, "today": today.isoformat(), "last": last.isoformat()},
    ).fetchall()
    by_day = {row["day"]: row["n"] for row in rows}
    return [
        by_day.get((today + timedelta(days=offset + 1)).isoformat(), 0)
        for offset in range(days_left)
    ]


def _done_today(conn, user_id, timezone_name, today):
    """用户本地时区当天的评分条数（含已暂停记录的评分：活已经干完了）。"""
    zone = ZoneInfo(timezone_name)
    start = _day_start(zone, today)
    end = _day_start(zone, today + timedelta(days=1))
    stamp = "CAST(strftime('%s', r.reviewed_at) AS INTEGER)"
    return conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND {stamp} >= ? AND {stamp} < ?
        """,
        (user_id, start, end),
    ).fetchone()["n"]


def read(conn, user_id, timezone_name, today):
    """当前进行中的目标；没有返回 None。"""
    row = conn.execute(
        "SELECT name, goal_date FROM goals WHERE user_id = ? AND ended_at IS NULL",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    goal_date = date.fromisoformat(row["goal_date"])
    days_left = max((goal_date - today).days, 0)
    done = _done_today(conn, user_id, timezone_name, today)
    result = plan(
        today,
        goal_date,
        _pending_now(conn, user_id, today),
        _due_by_day(conn, user_id, today, days_left),
        done,
    )
    return {
        "name": row["name"],
        "goal_date": row["goal_date"],
        "days_left": result["days_left"],
        "status": result["status"],
        "total_workload": result["total_workload"],
        "daily_target": result["daily_target"],
        "done_today": done,
        "remaining_today": result["remaining_today"],
        "on_track": result["on_track"],
    }


def save(conn, user_id, timezone_name, name, goal_date_text, created_at, today):
    """设置或修改目标；同一用户只有一行，结束后再次设置会复活同一行。"""
    name, goal_date = _validate(name, goal_date_text, today)
    row = conn.execute(
        "SELECT id, ended_at FROM goals WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO goals(user_id, name, goal_date, created_at) VALUES (?, ?, ?, ?)",
            (user_id, name, goal_date.isoformat(), created_at),
        )
    elif row["ended_at"] is None:
        conn.execute(
            "UPDATE goals SET name = ?, goal_date = ? WHERE id = ?",
            (name, goal_date.isoformat(), row["id"]),
        )
    else:
        # 结束后重新设定算一个新目标：重置创建时间、清除结束时间。
        conn.execute(
            "UPDATE goals SET name = ?, goal_date = ?, created_at = ?, ended_at = NULL WHERE id = ?",
            (name, goal_date.isoformat(), created_at, row["id"]),
        )
    return read(conn, user_id, timezone_name, today)


def end(conn, user_id, ended_at):
    """结束目标：写 ended_at，行保留（注销账号时才整行删除）。"""
    conn.execute(
        "UPDATE goals SET ended_at = ? WHERE user_id = ? AND ended_at IS NULL",
        (ended_at, user_id),
    )
