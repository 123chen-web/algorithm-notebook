"""Group extras routes: weekly goals, today feed, shared problems, message board.

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import APIRouter

import main


router = APIRouter()


def _serialize_member_ref(conn, group_id, user_id):
    """推荐人/留言作者展示：仍在组内显示用户名，否则显示"已离开的成员"。"""
    row = conn.execute(
        """
        SELECT u.username, u.avatar_version FROM study_group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ? AND gm.user_id = ? AND u.deleted_at IS NULL
        """,
        (group_id, user_id),
    ).fetchone()
    if row is None:
        return {"id": user_id, "username": "已离开的成员", "avatar_version": 0,
                "has_avatar": False}
    return {
        "id": user_id,
        "username": row["username"],
        "avatar_version": row["avatar_version"],
        "has_avatar": main.avatar_path(user_id).is_file(),
    }


# ---------- 功能 1：每周小目标 ----------

@router.put("/api/groups/{group_id}/weekly-goal")
def set_weekly_goal(group_id: int, data: main.WeeklyGoalInput,
                    user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        if group["created_by"] != user["id"]:
            raise HTTPException(403, "只有组长可以设置本周目标")
        week_start = main.group_week_start()
        now = main.utc_now()
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
        return {"weekly_goal": main.group_weekly_goal(conn, group_id)}


# ---------- 功能 2：组内今日动态 ----------

@router.get("/api/groups/{group_id}/today")
def get_group_today(group_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        main.member_group(conn, group_id, user["id"])
        return {"today": main.group_today_feed(conn, group_id)}


@router.put("/api/me/group-today")
def update_group_today_visibility(data: main.GroupTodayVisibilityInput,
                                  user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET show_group_today = ? WHERE id = ?",
            (1 if data.show else 0, user["id"]),
        )
    return {"show": data.show}


# ---------- 功能 3：小组共享题单 ----------

def _serialize_shared(conn, group, row, user_id):
    collected = conn.execute(
        "SELECT problem_id FROM group_problem_collections "
        "WHERE user_id = ? AND shared_id = ?",
        (user_id, row["id"]),
    ).fetchone()
    return {
        "id": row["id"],
        "title": row["title"],
        "zone": row["zone"],
        "source_url": row["source_url"],
        "note": row["note"],
        "created_at": row["created_at"],
        "recommender": _serialize_member_ref(conn, group["id"], row["user_id"]),
        "is_mine": row["user_id"] == user_id,
        "can_delete": row["user_id"] == user_id or group["created_by"] == user_id,
        "collected": collected is not None,
        "collected_problem_id": collected["problem_id"] if collected else None,
    }


@router.post("/api/groups/{group_id}/shared-problems", status_code=201)
def recommend_problem(group_id: int, data: main.SharedProblemInput,
                      user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        main.recheck_account(conn, user["id"])
        active = conn.execute(
            "SELECT COUNT(*) FROM group_shared_problems "
            "WHERE group_id = ? AND withdrawn_at IS NULL AND deleted_at IS NULL",
            (group_id,),
        ).fetchone()[0]
        if active >= main.GROUP_SHARED_MAX_PER_GROUP:
            raise HTTPException(403, "本组共享题单已满（100 条），请联系组长清理")
        # 每人每天上限：按用户自己时区的本地日统计。
        local_today = main.today_in_timezone(user["timezone"])
        day_start = datetime.combine(
            local_today, datetime.min.time()
        ).replace(tzinfo=ZoneInfo(user["timezone"])).astimezone(timezone.utc)
        day_count = conn.execute(
            "SELECT COUNT(*) FROM group_shared_problems "
            "WHERE group_id = ? AND user_id = ? AND created_at >= ?",
            (group_id, user["id"], day_start.isoformat()),
        ).fetchone()[0]
        if day_count >= main.GROUP_SHARED_MAX_PER_DAY:
            raise HTTPException(403, "今天已推荐 5 条，明天再来分享")
        key = main.normalize_shared_key(data.source_url, data.title)
        for row in conn.execute(
            "SELECT source_url, title FROM group_shared_problems "
            "WHERE group_id = ? AND withdrawn_at IS NULL AND deleted_at IS NULL",
            (group_id,),
        ):
            if main.normalize_shared_key(row["source_url"], row["title"]) == key:
                raise HTTPException(409, "这道题已经在共享题单里了")
        cursor = conn.execute(
            """
            INSERT INTO group_shared_problems
                (group_id, user_id, title, zone, source_url, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (group_id, user["id"], data.title.strip(), data.zone,
             data.source_url, data.note.strip(), main.utc_now()),
        )
        row = conn.execute(
            "SELECT * FROM group_shared_problems WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        return _serialize_shared(conn, group, row, user["id"])


@router.get("/api/groups/{group_id}/shared-problems")
def list_shared_problems(
    group_id: int,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    user=Depends(main.current_user),
):
    with main.connect() as conn:
        group = main.member_group(conn, group_id, user["id"])
        rows = conn.execute(
            """
            SELECT * FROM group_shared_problems
            WHERE group_id = ? AND withdrawn_at IS NULL AND deleted_at IS NULL
            ORDER BY id DESC LIMIT ? OFFSET ?
            """,
            (group_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM group_shared_problems "
            "WHERE group_id = ? AND withdrawn_at IS NULL AND deleted_at IS NULL",
            (group_id,),
        ).fetchone()[0]
        return {
            "items": [_serialize_shared(conn, group, row, user["id"]) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.delete("/api/groups/{group_id}/shared-problems/{shared_id}")
def delete_shared_problem(group_id: int, shared_id: int,
                          user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        row = conn.execute(
            "SELECT * FROM group_shared_problems "
            "WHERE id = ? AND group_id = ? AND withdrawn_at IS NULL AND deleted_at IS NULL",
            (shared_id, group_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "这条推荐不存在或已删除")
        if row["user_id"] != user["id"] and group["created_by"] != user["id"]:
            raise HTTPException(403, "只有推荐人或组长可以删除这条推荐")
        # 推荐人撤回记 withdrawn_at，组长删除记 deleted_at，便于区分。
        column = "withdrawn_at" if row["user_id"] == user["id"] else "deleted_at"
        conn.execute(
            f"UPDATE group_shared_problems SET {column} = ? WHERE id = ?",
            (main.utc_now(), shared_id),
        )
    return {"ok": True}


@router.post("/api/groups/{group_id}/shared-problems/{shared_id}/collect")
def collect_shared_problem(group_id: int, shared_id: int,
                           user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        main.recheck_account(conn, user["id"])
        row = conn.execute(
            "SELECT * FROM group_shared_problems "
            "WHERE id = ? AND group_id = ? AND withdrawn_at IS NULL AND deleted_at IS NULL",
            (shared_id, group_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "这条推荐不存在或已删除")
        existing = conn.execute(
            "SELECT problem_id FROM group_problem_collections "
            "WHERE user_id = ? AND shared_id = ?",
            (user["id"], shared_id),
        ).fetchone()
        if existing is not None:
            return {"collected": True, "problem_id": existing["problem_id"]}
        # 沿用建题字段校验（title/zone 在推荐时已校验）；来源写入思路备注。
        thinking = f"来自小组「{group['name']}」的推荐"
        if row["note"]:
            thinking += f"\n推荐语：{row['note']}"
        cursor = conn.execute(
            """
            INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user["id"], row["title"], row["zone"], "",
             main.QUICK_CODE_PLACEHOLDER, thinking, main.utc_now()),
        )
        problem_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO group_problem_collections(user_id, shared_id, problem_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (user["id"], shared_id, problem_id, main.utc_now()),
        )
        return {"collected": True, "problem_id": problem_id}


# ---------- 功能 4：小组留言板 ----------

def _serialize_message(conn, group_id, row):
    return {
        "id": row["id"],
        "body": row["body"],
        "created_at": row["created_at"],
        "author": _serialize_member_ref(conn, group_id, row["user_id"]),
    }


@router.get("/api/groups/{group_id}/messages")
def list_messages(
    group_id: int,
    limit: Annotated[int, Query(ge=1, le=100)] = main.GROUP_MESSAGE_LIST_DEFAULT,
    before_id: Annotated[int, Query(ge=1)] | None = None,
    user=Depends(main.current_user),
):
    with main.connect() as conn:
        main.member_group(conn, group_id, user["id"])
        # 简单方式实现"最多展示最近 500 条"：先算出第 500 条的 id 下界，
        # 再在其上做 before_id 分页，保证翻页也翻不出 500 条范围。
        floor_row = conn.execute(
            """
            SELECT id FROM group_messages
            WHERE group_id = ? AND deleted_at IS NULL
            ORDER BY id DESC LIMIT 1 OFFSET ?
            """,
            (group_id, main.GROUP_MESSAGE_LIST_MAX - 1),
        ).fetchone()
        floor_id = floor_row["id"] if floor_row else 0
        if before_id is None:
            rows = conn.execute(
                """
                SELECT * FROM group_messages
                WHERE group_id = ? AND deleted_at IS NULL AND id >= ?
                ORDER BY id DESC LIMIT ?
                """,
                (group_id, floor_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM group_messages
                WHERE group_id = ? AND deleted_at IS NULL AND id >= ? AND id < ?
                ORDER BY id DESC LIMIT ?
                """,
                (group_id, floor_id, before_id, limit),
            ).fetchall()
        return {
            "messages": [_serialize_message(conn, group_id, row) for row in rows],
            "limit": limit,
        }


@router.post("/api/groups/{group_id}/messages", status_code=201)
def post_message(group_id: int, data: main.GroupMessageInput,
                 user=Depends(main.current_user)):
    body = data.body.strip()
    if not body:
        raise HTTPException(400, "留言不能为空")
    if main.rate_limited(
        f"group_msg_min:{group_id}:{user['id']}",
        main.GROUP_MESSAGE_MAX_PER_MINUTE, 60,
    ):
        raise HTTPException(429, "发言太快了，稍后再试")
    if main.rate_limited(
        f"group_msg_day:{group_id}:{user['id']}",
        main.GROUP_MESSAGE_MAX_PER_DAY, 86400,
    ):
        raise HTTPException(429, "今天发言已达上限（100 条），明天再来")
    with main.connect(write=True) as conn:
        main.member_group(conn, group_id, user["id"])
        main.recheck_account(conn, user["id"])
        # 重复刷屏检测：5 分钟内发过完全相同的内容。
        five_min_ago = (
            datetime.now(timezone.utc) - timedelta(minutes=5)
        ).isoformat(timespec="seconds")
        duplicate = conn.execute(
            "SELECT 1 FROM group_messages "
            "WHERE group_id = ? AND user_id = ? AND body = ? "
            "AND deleted_at IS NULL AND created_at >= ?",
            (group_id, user["id"], body, five_min_ago),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(429, "请不要重复发送相同内容")
        cursor = conn.execute(
            "INSERT INTO group_messages(group_id, user_id, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (group_id, user["id"], body, main.utc_now()),
        )
        row = conn.execute(
            "SELECT * FROM group_messages WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return _serialize_message(conn, group_id, row)


@router.delete("/api/groups/{group_id}/messages/{message_id}")
def delete_message(group_id: int, message_id: int,
                   user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        row = conn.execute(
            "SELECT * FROM group_messages "
            "WHERE id = ? AND group_id = ? AND deleted_at IS NULL",
            (message_id, group_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "留言不存在或已删除")
        if row["user_id"] != user["id"] and group["created_by"] != user["id"]:
            raise HTTPException(403, "只能删除自己的留言，组长可删除任意留言")
        conn.execute(
            "UPDATE group_messages SET deleted_at = ? WHERE id = ?",
            (main.utc_now(), message_id),
        )
    return {"ok": True}
