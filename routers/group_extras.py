"""Group extras routes: weekly goals and today feed.

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import APIRouter

from core import security, time
from models.groups import GroupTodayVisibilityInput
from models.groups import WeeklyGoalInput
import accounts
import db
import groups_lib


router = APIRouter()


# ---------- 功能 1：每周小目标 ----------

@router.put("/api/groups/{group_id}/weekly-goal")
def set_weekly_goal(group_id: int, data: WeeklyGoalInput,
                    user=Depends(security.current_user)):
    with db.connect(write=True) as conn:
        group = groups_lib.member_group(conn, group_id, user["id"])
        if group["created_by"] != user["id"]:
            raise HTTPException(403, "只有组长可以设置本周目标")
        week_start = groups_lib.group_week_start()
        now = time.utc_now()
        conn.execute(
            """
            INSERT INTO group_weekly_goals
                (group_id, week_start, goal_type, target, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, week_start) DO UPDATE SET
                goal_type = excluded.goal_type,
                target = excluded.target,
                updated_at = excluded.updated_at
            """,
            (group_id, week_start, data.goal_type, data.target,
             user["id"], now, now),
        )
        return {"weekly_goal": groups_lib.group_weekly_goal(conn, group_id)}


# ---------- 功能 2：组内今日动态 ----------

@router.get("/api/groups/{group_id}/today")
def get_group_today(group_id: int, user=Depends(security.current_user)):
    with db.connect() as conn:
        groups_lib.member_group(conn, group_id, user["id"])
        return {"today": groups_lib.group_today_feed(conn, group_id)}


@router.put("/api/me/group-today")
def update_group_today_visibility(data: GroupTodayVisibilityInput,
                                  user=Depends(security.current_user)):
    with db.connect(write=True) as conn:
        accounts.recheck_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET show_group_today = ? WHERE id = ?",
            (1 if data.show else 0, user["id"]),
        )
    return {"show": data.show}
