"""论坛域服务：帖子/评论的可见性、序列化、举报处理。"""
import re

import accounts
import db
import images
from core import config, ratelimit, security, time

import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
def visible_post(conn, post_id):
    if not -(2**63) <= post_id <= 2**63 - 1:
        raise HTTPException(404, "帖子不存在")
    row = conn.execute(
        "SELECT * FROM posts WHERE id = ? AND deleted_at IS NULL",
        (post_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "帖子不存在")
    return dict(row)


def owned_post(conn, post_id, user_id):
    post = visible_post(conn, post_id)
    if post["user_id"] != user_id:
        raise HTTPException(404, "帖子不存在")
    return post


def visible_comment(conn, comment_id):
    if not -(2**63) <= comment_id <= 2**63 - 1:
        raise HTTPException(404, "评论不存在")
    row = conn.execute(
        "SELECT * FROM post_comments WHERE id = ? AND deleted_at IS NULL",
        (comment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "评论不存在")
    return dict(row)


def owned_comment(conn, comment_id, user_id):
    comment = visible_comment(conn, comment_id)
    if comment["user_id"] != user_id:
        raise HTTPException(404, "评论不存在")
    return comment


def _joins_previous_character(character):
    """肤色、变体选择符、零宽连接符、标签字符和各类组合符号，都和前一个字符是一个整体。"""
    code = ord(character)
    return (
        character in (config.ZERO_WIDTH_JOINER, "\ufe0e", "\ufe0f")
        or 0x1F3FB <= code <= 0x1F3FF
        or 0xE0020 <= code <= 0xE007F
        or unicodedata.category(character) in ("Mn", "Me", "Mc")
    )


def truncate_text(text, limit):
    """按字符数截断，但不把一个表情（肤色、👩‍💻 这样的连接序列、国旗、键帽）劈成两半。

    返回 (截断后的文本, 是否被截断)。前端 app.js 里有一份同样逻辑的 truncateExcerpt。
    """
    if len(text) <= limit:
        return text, False
    cut = limit
    while cut > 0 and (_joins_previous_character(text[cut]) or text[cut - 1] == config.ZERO_WIDTH_JOINER):
        cut -= 1
    regional = 0
    while regional < cut and 0x1F1E6 <= ord(text[cut - 1 - regional]) <= 0x1F1FF:
        regional += 1
    if regional % 2:
        cut -= 1  # 国旗由两个区域指示符组成，不能只留一个
    return text[:cut], True


def comment_reply_to(target, floor):
    if target is None:
        return None
    reply_to = {
        "id": target["id"],
        "floor": floor,
        "deleted": target["deleted_at"] is not None,
    }
    if not reply_to["deleted"]:
        shortened, was_cut = truncate_text(" ".join(target["body"].split()), 60)
        reply_to.update(
            username=target["username"],
            excerpt=shortened + ("…" if was_cut else ""),
        )
    return reply_to


def comment_response(row, post_author_id, floor, reply_to, votes=None):
    return {
        **dict(row),
        "has_avatar": images.avatar_path(row["user_id"]).is_file(),
        "floor": floor,
        "is_op": row["user_id"] == post_author_id,
        "reply_to": reply_to,
        "helpful_count": votes["helpful_count"] if votes is not None else 0,
        "viewer_helpful": bool(votes["viewer_helpful"]) if votes is not None else False,
    }


def serialize_comment(conn, row, post_author_id, viewer_id):
    context = conn.execute(
        """
        SELECT COUNT(*) AS floor,
               EXISTS(SELECT 1 FROM posts WHERE id = ? AND deleted_at IS NULL)
                   AS post_visible
        FROM post_comments WHERE post_id = ? AND id <= ?
        """,
        (row["post_id"], row["post_id"], row["id"]),
    ).fetchone()
    reply_to = None
    # Editing an old own comment is still allowed after its post is deleted,
    # but the response must not read another author's hidden reply target.
    if context["post_visible"] and row["reply_to_id"] is not None:
        target = conn.execute(
            """
            SELECT c.*, u.username,
                   (SELECT COUNT(*) FROM post_comments floors
                    WHERE floors.post_id = c.post_id AND floors.id <= c.id) AS floor
            FROM post_comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.id = ? AND c.post_id = ?
            """,
            (row["reply_to_id"], row["post_id"]),
        ).fetchone()
        if target is not None:
            reply_to = comment_reply_to(target, target["floor"])
    votes = conn.execute(
        "SELECT COUNT(*) AS helpful_count, "
        "COALESCE(MAX(user_id = ?), 0) AS viewer_helpful "
        "FROM comment_votes WHERE comment_id = ?",
        (viewer_id, row["id"]),
    ).fetchone()
    return comment_response(row, post_author_id, context["floor"], reply_to, votes)


def post_excerpt(body):
    """帖子摘要：去掉围栏代码块、折叠空白、按 140 字（表情安全）截断。

    围栏规则与 static/thread.js 的 renderBody 一致：以 ``` 开头的行开始，
    遇到只有 ``` 的行结束，未闭合则一直到结尾。
    """
    kept = []
    in_fence = False
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if in_fence:
            if re.fullmatch(r"```[ \t]*", line):
                in_fence = False
        elif line.startswith("```"):
            in_fence = True
            kept.append(" ")
        else:
            kept.append(line)
    text = " ".join(" ".join(kept).split())
    shortened, was_cut = truncate_text(text, config.POST_EXCERPT_LENGTH)
    return shortened + ("…" if was_cut else "")


def has_fenced_code(body):
    return re.search(r"^```", body, re.MULTILINE) is not None


def checked_post_zone(zone):
    if zone is not None and zone not in config.PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    return zone


def next_comment_update(previous):
    """Every edit changes the signature, including identical text in one clock tick."""
    current = datetime.now(timezone.utc)
    if previous:
        try:
            earlier = datetime.fromisoformat(previous)
            if earlier.tzinfo is None:
                earlier = earlier.replace(tzinfo=timezone.utc)
            if current <= earlier:
                current = earlier + timedelta(microseconds=1)
        except ValueError:
            pass
    return current.isoformat(timespec="microseconds")


def clear_deleted_thread_state(conn, *, post_id=None, comment_id=None):
    """Clear private derived text and accepted references in the deletion transaction."""
    if comment_id is not None:
        conn.execute(
            "UPDATE posts SET accepted_comment_id = NULL WHERE accepted_comment_id = ?",
            (comment_id,),
        )
        conn.execute(
            "DELETE FROM post_summaries WHERE post_id = "
            "(SELECT post_id FROM post_comments WHERE id = ?)",
            (comment_id,),
        )
    else:
        conn.execute("UPDATE posts SET accepted_comment_id = NULL WHERE id = ?", (post_id,))
        conn.execute("DELETE FROM post_summaries WHERE post_id = ?", (post_id,))


def change_comment_helpful(comment_id, user, *, helpful):
    security.require_not_trial(user, "点有用")
    with db.connect(write=True) as conn:
        accounts.recheck_account(conn, user["id"])
        if not -(2**63) <= comment_id <= 2**63 - 1:
            raise HTTPException(404, "评论不存在")
        comment = conn.execute(
            "SELECT c.user_id FROM post_comments c JOIN posts p ON p.id = c.post_id "
            "WHERE c.id = ? AND c.deleted_at IS NULL AND p.deleted_at IS NULL",
            (comment_id,),
        ).fetchone()
        if comment is None:
            raise HTTPException(404, "评论不存在")
        if comment["user_id"] == user["id"]:
            raise HTTPException(400, "不能给自己的评论点有用")
        if ratelimit.rate_limited(f"helpful:{user['id']}", 60, 60):
            raise HTTPException(429, "操作过于频繁，请稍后再试")
        if helpful:
            conn.execute(
                "INSERT INTO comment_votes(comment_id, user_id, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(comment_id, user_id) DO NOTHING",
                (comment_id, user["id"], time.utc_now()),
            )
        else:
            conn.execute(
                "DELETE FROM comment_votes WHERE comment_id = ? AND user_id = ?",
                (comment_id, user["id"]),
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM comment_votes WHERE comment_id = ?", (comment_id,),
        ).fetchone()[0]
    return {"helpful_count": count, "viewer_helpful": helpful}


def thread_privacy_state(conn, post, comments):
    # Anonymous retention does not change text signatures. Detect account deletion
    # during an AI call too, so a completed old request cannot recreate cleared text.
    authors = {post["user_id"], *(comment["user_id"] for comment in comments)}
    placeholders = ",".join("?" for _ in authors)
    return tuple(
        (row["id"], row["deleted_at"])
        for row in conn.execute(
            f"SELECT id, deleted_at FROM users WHERE id IN ({placeholders}) ORDER BY id",
            tuple(authors),
        )
    )


def create_report(user, *, post_id=None, comment_id=None, reason):
    security.require_not_trial(user, "举报")
    if ratelimit.rate_limited(f"report:{user['id']}", config.REPORT_LIMIT, config.REPORT_WINDOW_SECONDS):
        raise HTTPException(429, "举报过于频繁，请稍后再试")
    with db.connect(write=True) as conn:
        accounts.recheck_account(conn, user["id"])
        target = (
            visible_post(conn, post_id)
            if post_id is not None
            else visible_comment(conn, comment_id)
        )
        if target["user_id"] == user["id"]:
            raise HTTPException(400, "不能举报自己发布的内容")
        try:
            conn.execute(
                """
                INSERT INTO reports(
                    reporter_user_id, post_id, comment_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], post_id, comment_id, reason, time.utc_now()),
            )
        except sqlite3.IntegrityError:
            # 部分唯一索引挡住了对同一目标的重复待处理举报。
            raise HTTPException(409, "你已经举报过这条内容，管理员正在处理") from None
    return {"ok": True}


def resolve_reports_for(conn, *, post_id=None, comment_id=None):
    now = time.utc_now()
    if post_id is not None:
        conn.execute(
            "UPDATE reports SET resolved_at = ? WHERE post_id = ? AND resolved_at IS NULL",
            (now, post_id),
        )
    else:
        conn.execute(
            "UPDATE reports SET resolved_at = ? WHERE comment_id = ? AND resolved_at IS NULL",
            (now, comment_id),
        )

