"""账号域服务：账号状态复核、会话管理、账号删除。"""
import logging
import time

import db
import images
from core import config, security

logger = logging.getLogger("algorithm_notebook")

import shutil
from fastapi import HTTPException
def recheck_account(conn, user_id, expected_hash=None):
    row = conn.execute(
        "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    if expected_hash is not None and row["password_hash"] != expected_hash:
        raise HTTPException(400, "当前密码不正确")
    return row


def rvb_account(conn, user_id):
    # 登录态检查之后仍可能被注销/封禁；写锁内重读账号，也读取最新时区/偏好。
    user = recheck_account(conn, user_id)
    if user["is_banned"]:
        raise HTTPException(401, "账号已被封禁，无法继续使用")
    return user


def sec_recheck_session(conn, user_id, request):
    token = request.cookies.get("session", "")
    row = conn.execute(
        "SELECT 1 FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.user_id = ? AND s.expires_at > ? "
        "AND u.deleted_at IS NULL AND u.is_banned = 0",
        (security.token_hash(token), user_id, int(time.time())),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")


def revoke_other_sessions(conn, user_id, request):
    current_token = security.token_hash(request.cookies.get("session", ""))
    return conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
        (user_id, current_token),
    ).rowcount


def require_deletable_account(user):
    if user["is_trial"]:
        raise HTTPException(403, "体验账号到期会自动清理")
    if user["is_admin"]:
        raise HTTPException(403, "管理员账号不能自助注销，请先用 admin_tool.py 撤销管理员身份")


def delete_account_data(conn, user_id, deleted_at):
    groups = conn.execute(
        """
        SELECT g.name FROM study_groups g
        WHERE g.created_by = ? AND EXISTS (
            SELECT 1 FROM study_group_members m
            WHERE m.group_id = g.id AND m.user_id != ?
        ) ORDER BY g.id
        """,
        (user_id, user_id),
    ).fetchall()
    if groups:
        names = "、".join(f"『{row['name']}』" for row in groups[:3])
        if len(groups) > 3:
            names += "等"
        raise HTTPException(409, f"你创建的小组{names}里还有其他成员，请先让成员退出或解散小组")

    for table in ("sessions", "password_resets", "user_push", "mistake_scratch", "problems",
                  "mistake_tags", "weakness_insights", "mistake_clusters",
                  "ai_usage", "duck_usage", "comment_votes", "ai_feedback",
                  "manual_payment_claims", "goals", "review_ops",
                  "problem_recommendations", "atcoder_recommendations", "import_previews", "notes", "note_links",
                  "note_attachments", "note_drawings", "email_changes",
                  "pattern_progress", "pattern_example_done"):
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    # 题目与套路的关联跟着题目走；problems 在本函数里是显式 DELETE，外键级联
    # 虽然也开着，但按顺序先清一遍更明确（也覆盖 problem_patterns 先于 problems 被删的情况）。
    conn.execute(
        "DELETE FROM problem_patterns WHERE problem_id IN "
        "(SELECT id FROM problems WHERE user_id = ?)",
        (user_id,),
    )
    # users 是匿名化 UPDATE 而非 DELETE，FK 级联不会触发；附件文件必须显式删目录。
    try:
        shutil.rmtree(images.note_files_dir() / str(user_id), ignore_errors=True)
    except OSError:
        logger.warning("注销账号笔记附件目录删除失败 user_id=%s", user_id, exc_info=True)
    # 画板缩略图存放在数据目录（不随数据库行删除），注销时一并清掉。
    from routers import drawings as _drawings_routes
    _drawings_routes.purge_user_thumbs(user_id)
    # 每周小目标属于小组资产，保留。
    # 论坛按既有规则匿名留存；采纳和摘要不能保留注销前的关联/提炼内容。
    conn.execute(
        "DELETE FROM post_summaries WHERE post_id IN (SELECT id FROM posts WHERE user_id = ?) "
        "OR post_id IN (SELECT post_id FROM post_comments WHERE user_id = ?)",
        (user_id, user_id),
    )
    conn.execute(
        "UPDATE posts SET accepted_comment_id = NULL WHERE user_id = ? "
        "OR accepted_comment_id IN (SELECT id FROM post_comments WHERE user_id = ?)",
        (user_id, user_id),
    )
    conn.execute(
        "DELETE FROM avatar_reports WHERE reporter_user_id = ? OR avatar_owner_id = ?",
        (user_id, user_id),
    )
    conn.execute("UPDATE ai_calls SET user_id = NULL WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM study_group_members WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM study_groups WHERE created_by = ?", (user_id,))
    sec_anonymous_name = f"已注销用户 #{user_id}"
    sec_suffix = 2
    while conn.execute(
        "SELECT 1 FROM users WHERE username = ? AND id != ?",
        (sec_anonymous_name, user_id),
    ).fetchone() is not None:
        sec_anonymous_name = f"已注销用户 #{user_id}-{sec_suffix}"
        sec_suffix += 1
    conn.execute(
        """
        UPDATE users SET username = ?, email = NULL, password_hash = ?,
            avatar_version = 0, bio = '', lifetime_problem_count = 0, rank_display_name = '',
            last_reminder_sent = NULL, reminder_token = NULL, reminder_opt_in = 0,
            api_token_hash = NULL, is_admin = 0, deleted_at = ?
        WHERE id = ?
        """,
        (sec_anonymous_name, config.DUMMY_PASSWORD, deleted_at, user_id),
    )


def push_settings_payload(conn, user_id):
    """组装对外的设置状态：密钥任何时候都只回尾号 4 位，不回原文。"""
    row = conn.execute(
        "SELECT channel, secret, enabled, fail_count FROM user_push WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {
            "channel": None, "enabled": False, "configured": False,
            "tail": "", "fail_count": 0,
        }
    secret = row["secret"]
    return {
        "channel": row["channel"],
        "enabled": bool(row["enabled"]),
        "configured": bool(secret),
        "tail": secret[-4:] if secret else "",
        "fail_count": row["fail_count"],
    }
