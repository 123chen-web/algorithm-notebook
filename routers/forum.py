"""forum routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from ai_limits import release_attempt
from contextlib import ExitStack
from datetime import timezone
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from typing import Annotated
from typing import Literal
import json
import os
import sqlite3
import thread_summary
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/posts")
def list_posts(
    q: main.PostSearchQuery = "",
    sort: Literal["activity", "new", "hot"] = "activity",
    filter: Literal["all", "unanswered", "solved", "mine", "participated"] = "all",
    zone: str | None = None,
    limit: Annotated[int, Query(ge=1, le=main.POST_LIST_MAX_LIMIT)] = main.POST_LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=main.POST_LIST_MAX_OFFSET)] = 0,
    user=Depends(main.current_user),
):
    if zone is not None and zone != "none" and zone not in main.PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    # 体验账号不能发帖也不能评论，“我的 / 参与过”恒为空，is_mine 恒为 false。
    me = None if user["is_trial"] else user["id"]
    since = (main.datetime.now(timezone.utc) - main.POST_HOT_WINDOW).isoformat(timespec="seconds")
    conditions = [main.POST_LIST_FILTERS[filter]]
    params = {"since": since, "me": me}
    if q:
        # Escape LIKE metacharacters (including the escape character itself).
        # SQLite LIKE is case-insensitive for ASCII letters by default.
        keyword = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        params["pattern"] = f"%{keyword}%"
        conditions.append("(title LIKE :pattern ESCAPE '!' OR body LIKE :pattern ESCAPE '!')")
    if zone == "none":
        conditions.append("zone IS NULL")
    elif zone is not None:
        params["zone"] = zone
        conditions.append("zone = :zone")
    where = " AND ".join(conditions)
    with main.connect() as conn:
        # 总数、读数和本页数据共用同一个只读快照。
        conn.execute("BEGIN")
        total = conn.execute(
            f"{main.POST_BOARD_CTE} SELECT COUNT(*) FROM board WHERE {where}", params
        ).fetchone()[0]
        counts = {"all": 0, "unanswered": 0, "solved": 0, "mine": 0}
        zone_counts = {}
        # counts / zone_counts 不受 q / filter / zone 影响：全站可见帖子的口径。
        for row in conn.execute(
            f"""{main.POST_BOARD_CTE}
            SELECT COALESCE(zone, 'none') AS zone_key, COUNT(*) AS total,
                   SUM(comment_count = 0) AS unanswered, SUM(solved) AS solved,
                   SUM(CASE WHEN user_id = :me THEN 1 ELSE 0 END) AS mine
            FROM board GROUP BY zone_key
            """,
            {"since": since, "me": me},
        ):
            zone_counts[row["zone_key"]] = row["total"]
            counts["all"] += row["total"]
            counts["unanswered"] += row["unanswered"]
            counts["solved"] += row["solved"]
            counts["mine"] += row["mine"]
        rows = conn.execute(
            f"""{main.POST_BOARD_CTE}
            SELECT * FROM board WHERE {where}
            ORDER BY {main.POST_LIST_SORTS[sort]} LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": limit, "offset": offset},
        ).fetchall()
        participation = {}
        coded_posts = set()
        if rows:
            marks = ",".join("?" * len(rows))
            ids = [row["id"] for row in rows]
            # 参与者、最后评论者：整页一次分组查询取齐（key 用来比较“谁更晚”）。
            for item in conn.execute(
                f"""
                SELECT c.post_id, c.user_id, u.username, u.avatar_version,
                       MAX(c.created_at || '|' || printf('%020d', c.id)) AS last_key
                FROM post_comments c JOIN users u ON u.id = c.user_id
                WHERE c.deleted_at IS NULL AND c.post_id IN ({marks})
                GROUP BY c.post_id, c.user_id
                """,
                ids,
            ):
                participation.setdefault(item["post_id"], []).append(item)
            for item in conn.execute(
                f"""
                SELECT post_id, body FROM post_comments
                WHERE deleted_at IS NULL AND post_id IN ({marks}) AND body LIKE '%```%'
                """,
                ids,
            ):
                if main.has_fenced_code(item["body"]):
                    coded_posts.add(item["post_id"])

    avatars = {}

    def person(user_id, username, avatar_version):
        if user_id not in avatars:
            avatars[user_id] = main.avatar_path(user_id).is_file()
        return {"user_id": user_id, "username": username,
                "avatar_version": avatar_version, "has_avatar": avatars[user_id]}

    posts = []
    for row in rows:
        commenters = sorted(
            participation.get(row["id"], []), key=lambda item: item["last_key"], reverse=True
        )
        last_commenter = None
        if commenters:
            latest = commenters[0]
            last_commenter = person(latest["user_id"], latest["username"], latest["avatar_version"])
        people = [person(row["user_id"], row["username"], row["avatar_version"])]
        people += [
            person(item["user_id"], item["username"], item["avatar_version"])
            for item in commenters if item["user_id"] != row["user_id"]
        ]
        posts.append({
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "user_id": row["user_id"],
            "username": row["username"],
            "avatar_version": row["avatar_version"],
            "has_avatar": people[0]["has_avatar"],
            "zone": row["zone"],
            "excerpt": main.post_excerpt(row["body"]),
            "comment_count": row["comment_count"],
            "last_activity_at": row["last_activity_at"],
            "last_commenter": last_commenter,
            "participants": people[:main.POST_PARTICIPANT_LIMIT],
            "participant_count": len(people),
            "has_code": main.has_fenced_code(row["body"]) or row["id"] in coded_posts,
            "solved": bool(row["solved"]),
            "helpful_total": row["helpful_total"],
            "hot": row["hot_score"] >= main.POST_HOT_SCORE,
            "is_mine": me is not None and row["user_id"] == me,
        })
    return {
        "posts": posts,
        "total": total,
        "has_more": offset + len(posts) < total,
        "counts": counts,
        "zone_counts": zone_counts,
    }


@router.post("/api/posts", status_code=201)
def create_post(data: main.NewPost, user=Depends(main.current_user)):
    main.require_not_trial(user, "发帖")
    zone = main.checked_post_zone(data.zone)
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        cursor = conn.execute(
            "INSERT INTO posts(user_id, title, body, zone, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], data.title, data.body, zone, main.utc_now()),
        )
        row = conn.execute(
            """
            SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


@router.get("/api/posts/{post_id}")
def get_post(post_id: int, user=Depends(main.current_user)):
    if not -(2**63) <= post_id <= 2**63 - 1:
        raise HTTPException(404, "帖子不存在")
    with main.connect() as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            """
            SELECT p.*, u.username, u.avatar_version
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.id = ? AND p.deleted_at IS NULL
            """,
            (post_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "帖子不存在")
        post = {**dict(row), "has_avatar": main.avatar_path(row["user_id"]).is_file()}
        rows = conn.execute(
            """
            SELECT c.*, u.username, u.avatar_version
            FROM post_comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.post_id = ?
            ORDER BY c.id ASC
            """,
            (post_id,),
        ).fetchall()
        # Tombstones keep their floor; reply lookups all use the same snapshot.
        by_id = {row["id"]: (row, floor) for floor, row in enumerate(rows, 1)}
        accepted, _ = by_id.get(post["accepted_comment_id"], (None, None))
        if accepted is None or accepted["deleted_at"] is not None:
            post["accepted_comment_id"] = None
        votes_by_id = {
            vote["comment_id"]: vote
            for vote in conn.execute(
                """
                SELECT v.comment_id, COUNT(*) AS helpful_count,
                       MAX(v.user_id = ?) AS viewer_helpful
                FROM comment_votes v
                JOIN post_comments c ON c.id = v.comment_id
                WHERE c.post_id = ? AND c.deleted_at IS NULL
                GROUP BY v.comment_id
                """,
                (user["id"], post_id),
            )
        }
        comments = sorted(
            (row for row in rows if row["deleted_at"] is None),
            key=lambda row: row["created_at"],
        )
        post["comments"] = []
        for row in comments:
            target, target_floor = by_id.get(row["reply_to_id"], (None, None))
            post["comments"].append(
                main.comment_response(
                    row, post["user_id"], by_id[row["id"]][1],
                    main.comment_reply_to(target, target_floor),
                    votes_by_id.get(row["id"]),
                )
            )
    return post


@router.put("/api/posts/{post_id}")
def edit_post(post_id: int, data: main.PostEdit, user=Depends(main.current_user)):
    main.require_not_trial(user, "发帖")
    # 省略 zone 表示不改；显式传 null 表示改回“未分区”。
    change_zone = "zone" in data.model_fields_set
    zone = main.checked_post_zone(data.zone)
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        post = main.owned_post(conn, post_id, user["id"])
        conn.execute(
            "UPDATE posts SET title = ?, body = ?, zone = ?, updated_at = ? WHERE id = ?",
            (data.title, data.body, zone if change_zone else post["zone"], main.utc_now(), post_id),
        )
        row = conn.execute(
            """
            SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.id = ?
            """,
            (post_id,),
        ).fetchone()
    return dict(row)


@router.delete("/api/posts/{post_id}")
def delete_post(post_id: int, user=Depends(main.current_user)):
    main.require_not_trial(user, "发帖")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.owned_post(conn, post_id, user["id"])
        # 软删除：标记 deleted_at，不物理删除，也不级联标记这个帖子下的
        # 评论——帖子对所有人不可见之后，正常业务路径本来就到达不了
        # 这些评论，不需要逐条标记。
        conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ?",
            (main.utc_now(), post_id),
        )
        main.clear_deleted_thread_state(conn, post_id=post_id)
    return {"ok": True}


@router.post("/api/posts/{post_id}/comments", status_code=201)
def create_comment(post_id: int, data: main.NewComment, user=Depends(main.current_user)):
    main.require_not_trial(user, "评论")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        post = main.visible_post(conn, post_id)
        if data.reply_to_id is not None:
            # SQLite IDs are signed 64-bit integers; larger positive IDs are
            # nonexistent targets, rather than binding errors or invalid bodies.
            if data.reply_to_id > 2**63 - 1 or conn.execute(
                "SELECT 1 FROM post_comments "
                "WHERE id = ? AND post_id = ? AND deleted_at IS NULL",
                (data.reply_to_id, post_id),
            ).fetchone() is None:
                raise HTTPException(400, "被回复的评论不存在或已删除")
        cursor = conn.execute(
            "INSERT INTO post_comments(post_id, user_id, body, created_at, reply_to_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (post_id, user["id"], data.body, main.utc_now(), data.reply_to_id),
        )
        row = conn.execute(
            """
            SELECT c.*, u.username, u.avatar_version FROM post_comments c
            JOIN users u ON u.id = c.user_id WHERE c.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        comment = main.serialize_comment(conn, row, post["user_id"], user["id"])
    return comment


@router.put("/api/comments/{comment_id}")
def edit_comment(comment_id: int, data: main.CommentEdit, user=Depends(main.current_user)):
    main.require_not_trial(user, "评论")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        owned = main.owned_comment(conn, comment_id, user["id"])
        conn.execute(
            "UPDATE post_comments SET body = ?, updated_at = ? WHERE id = ?",
            (data.body, main.next_comment_update(owned["updated_at"]), comment_id),
        )
        row = conn.execute(
            """
            SELECT c.*, u.username, u.avatar_version FROM post_comments c
            JOIN users u ON u.id = c.user_id WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
        post_author = conn.execute(
            "SELECT user_id FROM posts WHERE id = ?", (owned["post_id"],)
        ).fetchone()
        comment = main.serialize_comment(conn, row, post_author["user_id"], user["id"])
    return comment


@router.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(main.current_user)):
    main.require_not_trial(user, "评论")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.owned_comment(conn, comment_id, user["id"])
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?",
            (main.utc_now(), comment_id),
        )
        main.clear_deleted_thread_state(conn, comment_id=comment_id)
    return {"ok": True}


@router.put("/api/posts/{post_id}/accepted")
def accept_comment(post_id: int, data: main.AcceptedComment, user=Depends(main.current_user)):
    main.require_not_trial(user, "采纳")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.owned_post(conn, post_id, user["id"])
        if not -(2**63) <= data.comment_id <= 2**63 - 1:
            raise HTTPException(400, "这条评论不存在或已删除")
        comment = conn.execute(
            "SELECT user_id FROM post_comments "
            "WHERE id = ? AND post_id = ? AND deleted_at IS NULL",
            (data.comment_id, post_id),
        ).fetchone()
        if comment is None:
            raise HTTPException(400, "这条评论不存在或已删除")
        if comment["user_id"] == user["id"]:
            raise HTTPException(400, "不能采纳自己的评论")
        conn.execute(
            "UPDATE posts SET accepted_comment_id = ? WHERE id = ?",
            (data.comment_id, post_id),
        )
    return {"accepted_comment_id": data.comment_id}


@router.delete("/api/posts/{post_id}/accepted")
def unaccept_comment(post_id: int, user=Depends(main.current_user)):
    main.require_not_trial(user, "采纳")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.owned_post(conn, post_id, user["id"])
        conn.execute("UPDATE posts SET accepted_comment_id = NULL WHERE id = ?", (post_id,))
    return {"accepted_comment_id": None}


@router.put("/api/comments/{comment_id}/helpful")
def mark_comment_helpful(comment_id: int, user=Depends(main.current_user)):
    return main.change_comment_helpful(comment_id, user, helpful=True)


@router.delete("/api/comments/{comment_id}/helpful")
def unmark_comment_helpful(comment_id: int, user=Depends(main.current_user)):
    return main.change_comment_helpful(comment_id, user, helpful=False)


@router.get("/api/posts/{post_id}/summary")
def get_thread_summary(post_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        conn.execute("BEGIN")
        post, comments = thread_summary.load_thread(conn, post_id)
        row = conn.execute("SELECT * FROM post_summaries WHERE post_id = ?", (post_id,)).fetchone()
        if row is None:
            return {"summary": None}
        signature = thread_summary.thread_signature(post, comments)
        return {"summary": thread_summary.serialize_summary(row, signature)}


@router.post("/api/posts/{post_id}/summary")
def create_thread_summary(post_id: int, user=Depends(main.current_user)):
    main.require_not_trial(user, " AI 要点")
    with ExitStack() as stack:
        with main.connect(write=True) as conn:
            main.recheck_account(conn, user["id"])
            post, comments = thread_summary.load_thread(conn, post_id)
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
            if len(comments) < 2:
                raise HTTPException(400, "回复太少，暂时不需要提炼")
            if main.rate_limited(f"summary:{user['id']}", 10, 3600):
                raise HTTPException(429, "AI 要点请求太频繁，请稍后再试")
            signature = thread_summary.thread_signature(post, comments)
            row = conn.execute("SELECT * FROM post_summaries WHERE post_id = ?", (post_id,)).fetchone()
            if row is not None and row["signature"] == signature:
                return {"summary": thread_summary.serialize_summary(row, signature), "cached": True}
            reference = thread_summary.thread_reference(post, comments)
            privacy_state = main.thread_privacy_state(conn, post, comments)
            day = main.today_for(user).isoformat()
            quota = main.ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(main.ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO ai_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = ai_usage.attempts + 1
                WHERE ai_usage.attempts < ?
                """,
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")

        try:
            with main.track_call(user["id"], "thread_summary"):
                content = thread_summary.validate_summary(
                    thread_summary.summarize_thread(reference), reference,
                )
        except HTTPException as exc:
            if exc.status_code in (502, 503, 504):
                with main.connect(write=True) as conn:
                    release_attempt(conn, user["id"], day)
            raise

        created_at = main.utc_now()
        with main.connect(write=True) as conn:
            main.recheck_account(conn, user["id"])
            current_post, current_comments = thread_summary.load_thread(conn, post_id)
            if (thread_summary.thread_signature(current_post, current_comments) != signature
                    or main.thread_privacy_state(conn, current_post, current_comments) != privacy_state):
                raise HTTPException(409, "讨论内容已变化，请重新提炼")
            conn.execute(
                """
                INSERT INTO post_summaries(post_id, signature, content, comment_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET signature = excluded.signature,
                    content = excluded.content, comment_count = excluded.comment_count,
                    created_at = excluded.created_at
                """,
                (post_id, signature, json.dumps(content, ensure_ascii=False),
                 len(reference["comments"]), created_at),
            )
        return {"summary": {**content, "generated_at": created_at,
                            "comment_count": len(reference["comments"]), "stale": False},
                "cached": False}


@router.post("/api/posts/{post_id}/report", status_code=201)
def report_post(post_id: int, data: main.ReportInput, user=Depends(main.current_user)):
    return main.create_report(user, post_id=post_id, reason=data.reason)


@router.post("/api/comments/{comment_id}/report", status_code=201)
def report_comment(comment_id: int, data: main.ReportInput, user=Depends(main.current_user)):
    return main.create_report(user, comment_id=comment_id, reason=data.reason)


@router.post("/api/users/{user_id}/avatar/report", status_code=201)
def report_avatar(user_id: int, data: main.ReportInput, user=Depends(main.current_user)):
    main.require_not_trial(user, "举报")
    if user_id == user["id"]:
        raise HTTPException(400, "不能举报自己的头像")
    # 复用和帖子/评论举报同一个限流计数：同一个人短时间内狂发举报，
    # 不管举报的是什么内容，都是同一类滥用。
    if main.rate_limited(f"report:{user['id']}", main.REPORT_LIMIT, main.REPORT_WINDOW_SECONDS):
        raise HTTPException(429, "举报过于频繁，请稍后再试")
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        target = conn.execute(
            "SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(404, "用户不存在")
        try:
            conn.execute(
                """
                INSERT INTO avatar_reports(
                    reporter_user_id, avatar_owner_id, reason, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (user["id"], user_id, data.reason, main.utc_now()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "你已经举报过这个头像，管理员正在处理") from None
    return {"ok": True}
