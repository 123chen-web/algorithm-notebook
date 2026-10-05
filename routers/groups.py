"""groups routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from group_levels import LEVELS
from group_levels import RULES
from group_levels import level_summary
import sqlite3
from fastapi import APIRouter


router = APIRouter()


@router.post("/api/groups", status_code=201)
def create_group(data: main.NewGroup, user=Depends(main.current_user)):
    # 限额检查、建组和加入在同一写事务中，避免并发请求突破限额或留下空组。
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.require_group_capacity_for_user(conn, user["id"])
        created_at = main.utc_now()
        for _ in range(2):
            try:
                group_id = conn.execute(
                    """
                    INSERT INTO study_groups(name, invite_code, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (data.name, main.generate_group_invite_code(), user["id"], created_at),
                ).lastrowid
                break
            except sqlite3.IntegrityError as exc:
                if "study_groups.invite_code" not in str(exc):
                    raise
        else:
            raise HTTPException(503, "暂时无法生成小组邀请码，请稍后再试")
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user["id"], created_at),
        )
        return main.group_detail(conn, group_id, user["id"])


@router.get("/api/groups")
def list_groups(user=Depends(main.current_user)):
    with main.connect() as conn:
        conn.execute("BEGIN")
        groups = conn.execute(
            """
            SELECT g.id, g.name, g.created_by, g.created_at,
                   (SELECT COUNT(*) FROM study_group_members gm
                    JOIN users u ON u.id = gm.user_id
                    WHERE gm.group_id = g.id AND u.deleted_at IS NULL)
                   AS member_count
            FROM study_groups g
            JOIN study_group_members m ON m.group_id = g.id
            WHERE m.user_id = ?
            ORDER BY g.created_at DESC, g.id DESC
            """,
            (user["id"],),
        ).fetchall()
        members_by_group = main.group_members_by_id(
            conn, tuple(group["id"] for group in groups)
        )
        points_by_group = main.group_points_by_id(conn, members_by_group)
    return {"groups": [
        {
            "id": group["id"],
            "name": group["name"],
            "member_count": group["member_count"],
            "member_limit": main.GROUP_MAX_MEMBERS,
            "is_creator": group["created_by"] == user["id"],
            "created_at": group["created_at"],
            "level": level_summary(sum(points_by_group[group["id"]].values())),
            "members_preview": [
                main.group_member_avatar(member)
                for member in members_by_group[group["id"]][:5]
            ],
        }
        for group in groups
    ]}


@router.get("/api/group-levels")
def get_group_levels(user=Depends(main.current_user)):
    return {
        "member_limit": main.GROUP_MAX_MEMBERS,
        "levels": [dict(level) for level in LEVELS],
        "rules": [dict(rule) for rule in RULES],
    }


@router.post("/api/groups/join")
def join_group(data: main.JoinGroup, request: Request, user=Depends(main.current_user)):
    if main.rate_limited(
        f"group_join:{main.client_ip(request)}", main.GROUP_JOIN_LIMIT, main.GROUP_JOIN_WINDOW_SECONDS
    ):
        raise HTTPException(429, "加入尝试次数过多，请稍后再试")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        group = conn.execute(
            "SELECT id FROM study_groups WHERE invite_code = ?", (data.invite_code,)
        ).fetchone()
        if group is None:
            raise HTTPException(404, "邀请码对应的小组不存在")
        group_id = group["id"]
        if conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user["id"]),
        ).fetchone():
            raise HTTPException(409, "你已经加入了这个小组")
        member_count = conn.execute(
            "SELECT COUNT(*) FROM study_group_members WHERE group_id = ?", (group_id,)
        ).fetchone()[0]
        if member_count >= main.GROUP_MAX_MEMBERS:
            raise HTTPException(403, f"小组已达到 {main.GROUP_MAX_MEMBERS} 人上限")
        main.require_group_capacity_for_user(conn, user["id"])
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user["id"], main.utc_now()),
        )
        return main.group_detail(conn, group_id, user["id"])


@router.get("/api/groups/{group_id}")
def get_group(group_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        # 权限、成员和统计共享只读快照，不混用退出/加入前后的成员集合。
        conn.execute("BEGIN")
        return main.group_detail(conn, group_id, user["id"])


@router.post("/api/groups/{group_id}/leave")
def leave_group(group_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.member_group(conn, group_id, user["id"])
        conn.execute(
            "DELETE FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user["id"]),
        )
        if conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ?", (group_id,)
        ).fetchone() is None:
            conn.execute("DELETE FROM study_groups WHERE id = ?", (group_id,))
    return {"ok": True}


@router.delete("/api/groups/{group_id}/members/{user_id}")
def remove_group_member(group_id: int, user_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        if group["created_by"] != user["id"]:
            raise HTTPException(403, "只有创建者可以移除成员")
        if user_id == user["id"]:
            raise HTTPException(400, "请使用退出小组或解散小组")
        if conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone() is None:
            raise HTTPException(404, "成员不存在")
        conn.execute(
            "DELETE FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
    return {"ok": True}


@router.delete("/api/groups/{group_id}")
def delete_group(group_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        group = main.member_group(conn, group_id, user["id"])
        if group["created_by"] != user["id"]:
            raise HTTPException(403, "只有创建者可以解散小组")
        # 成员关系由外键 ON DELETE CASCADE 一并清除。
        conn.execute("DELETE FROM study_groups WHERE id = ?", (group_id,))
    return {"ok": True}
