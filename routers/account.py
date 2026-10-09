"""account routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main
from routers import drawings as drawings_routes

from routers import note_files
from anki_export import build_anki_text
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import UploadFile
from fastapi.responses import FileResponse
from legal import PRODUCT_NAME
from urllib.parse import quote
import json
import mailer
import os
import push_channels
import rank_cache
import secrets
import sqlite3
import time
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/me")
def me(user=Depends(main.current_user)):
    with main.connect() as conn:
        quota = main.ai_quota(conn, user["id"], main.today_for(user).isoformat())
    return {
        **user,
        **quota,
        "has_avatar": main.sec_has_avatar(user["id"]),
        "today": main.today_for(user).isoformat(),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY", "").strip()),
    }


@router.get("/api/export")
def export_data(user=Depends(main.current_user)):
    # 整本导出成本高，按用户每小时限流（与 Anki 导出同一套额度参数）。
    if main.rate_limited(
        f"export:{user['id']}", main.EXPORT_LIMIT, main.EXPORT_WINDOW_SECONDS
    ):
        raise HTTPException(429, "导出过于频繁，请一小时后再试")
    # 只导出学习笔记本，显式选择字段，避免账号或后续新增字段意外进入文件。
    with main.connect() as conn:
        # 四层记录共享只读快照，避免并发编辑/删除时读到不一致的从属关系。
        conn.execute("BEGIN")
        total = conn.execute(
            "SELECT COUNT(*) FROM problems WHERE user_id = ?", (user["id"],)
        ).fetchone()[0]
        if total > main.EXPORT_MAX_RECORDS:
            raise HTTPException(
                413, f"记录超过 {main.EXPORT_MAX_RECORDS} 条，无法一次导出整本，请联系站长"
            )
        problems = {}
        for row in conn.execute(
            """
            SELECT id, title, zone, language, code, thinking, created_at
            FROM problems WHERE user_id = ? ORDER BY id
            """,
            (user["id"],),
        ):
            problems[row["id"]] = {**dict(row), "mistakes": []}

        mistakes = {}
        for row in conn.execute(
            """
            SELECT m.id, m.problem_id, m.description, m.repetitions,
                   m.interval_days, m.ease_factor, m.due_date, m.last_reviewed_at,
                   m.pending_reason
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? ORDER BY m.id
            """,
            (user["id"],),
        ):
            mistake = {**dict(row), "reviews": [], "variants": [], "tags": []}
            mistake["pending_reason"] = bool(mistake["pending_reason"])
            if mistake["pending_reason"]:
                mistake["description"] = main.PENDING_REASON_DISPLAY
            problems[mistake.pop("problem_id")]["mistakes"].append(mistake)
            mistakes[mistake["id"]] = mistake

        # 错因标签：只导出标签文字，按用户排好的顺序。
        for row in conn.execute(
            """
            SELECT t.mistake_id, t.tag
            FROM mistake_tags t
            JOIN mistakes m ON m.id = t.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? ORDER BY t.rowid
            """,
            (user["id"],),
        ):
            mistakes[row["mistake_id"]]["tags"].append(row["tag"])

        for row in conn.execute(
            """
            SELECT r.mistake_id, r.quality, r.reviewed_at, r.next_due_date
            FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? ORDER BY r.reviewed_at ASC, r.id ASC
            """,
            (user["id"],),
        ):
            review = dict(row)
            mistakes[review.pop("mistake_id")]["reviews"].append(review)

        # 导出完整原始内容，包含未作答的标准答案和旧版 notes。
        for row in conn.execute(
            """
            SELECT v.mistake_id, v.description, v.model, v.created_at, v.result,
                   v.answer_code, v.answer, v.expected_answer, v.notes,
                   v.result_updated_at
            FROM variants v
            JOIN mistakes m ON m.id = v.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? ORDER BY v.created_at ASC, v.id ASC
            """,
            (user["id"],),
        ):
            variant = dict(row)
            mistakes[variant.pop("mistake_id")]["variants"].append(variant)

        notes = []
        for row in conn.execute(
            "SELECT n.id, n.title, n.content, n.tags, n.problem_id, p.title AS problem_title, "
            "n.pinned, n.created_at, n.updated_at, n.deleted_at FROM notes n "
            "LEFT JOIN problems p ON p.id = n.problem_id AND p.user_id = n.user_id "
            "WHERE n.user_id = ? ORDER BY n.id", (user["id"],),
        ):
            note = dict(row)
            note["tags"] = [tag for tag in note["tags"].split(",") if tag]
            note["pinned"] = bool(note["pinned"])
            notes.append(note)

        # N1 双向链接：只导出本人的链接关系（种类、原文目标文字、别名、是否悬空）。
        note_links = []
        for row in conn.execute(
            "SELECT source_note_id, link_kind, target_text, alias_text, "
            "target_note_id, target_problem_id, created_at FROM note_links "
            "WHERE user_id = ? ORDER BY id", (user["id"],),
        ):
            note = dict(row)
            note["dangling"] = (
                note["target_note_id"] is None and note["target_problem_id"] is None
            )
            note_links.append(note)

        # N2 附件清单：按笔记正文里的 attachment:ID 引用关联本人附件行，
        # 只列出文件名/类型/大小，不导出二进制内容。
        attachment_rows = conn.execute(
            "SELECT id, mime, size_bytes FROM note_attachments WHERE user_id = ?",
            (user["id"],),
        ).fetchall()
        attachment_map = {row["id"]: row for row in attachment_rows}
        for note in notes:
            refs = note_files.ATTACHMENT_REF_RE.findall(note["content"] or "")
            note["attachments"] = [
                {
                    "id": int(ref),
                    "filename": note_files.attachment_export_name(int(ref), attachment_map[int(ref)]["mime"]),
                    "mime": attachment_map[int(ref)]["mime"],
                    "size_bytes": attachment_map[int(ref)]["size_bytes"],
                }
                for ref in dict.fromkeys(refs)
                if int(ref) in attachment_map
            ]
        # 画板随笔记一起导出：标题清单 + 完整 scene_json（缩略图是可再生文件，不导出）。
        drawings = []
        for row in conn.execute(
            "SELECT id, note_id, title, scene_json, version, created_at, updated_at, deleted_at "
            "FROM note_drawings WHERE user_id = ? ORDER BY id",
            (user["id"],),
        ):
            drawing = dict(row)
            scene_text = drawing.pop("scene_json") or ""
            try:
                drawing["scene"] = json.loads(scene_text) if scene_text else None
            except ValueError:
                drawing["scene"] = None
            drawings.append(drawing)
    payload = {
        "exported_at": main.utc_now(),
        "username": user["username"],
        "problems": list(problems.values()),
        "notes": notes,
        "note_links": note_links,
        "drawings": drawings,
    }
    filename = f"{PRODUCT_NAME}导出_{user['username']}_{main.today_for(user).isoformat()}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        },
    )


@router.get("/api/export/anki")
def export_anki(
    scope: str = "all",
    zone: str | None = None,
    user=Depends(main.current_user),
):
    # 参数校验先于账号限制和限流：格式不对的请求不消耗额度。
    if scope not in main.ANKI_SCOPES:
        raise HTTPException(400, "scope 只能是 all、zone、weak、mastered")
    if scope == "zone" and not zone:
        raise HTTPException(400, "按分区导出必须指定 zone")
    if scope == "zone" and zone not in main.PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    # 体验账号到期会整体清理，导出成本地文件没有意义，直接拒绝。
    main.require_not_trial(user, "导出到 Anki")
    if main.rate_limited(
        f"anki-export:{user['id']}", main.ANKI_EXPORT_LIMIT, main.ANKI_EXPORT_WINDOW_SECONDS
    ):
        raise HTTPException(429, "导出过于频繁，请一小时后再试")

    today = main.today_for(user)
    with main.connect() as conn:
        # 只读快照：计数、掌握度和内容组装在同一视图里完成。
        conn.execute("BEGIN")
        records = main.anki_export_records(
            conn, user["id"], user["timezone"], today, scope, zone
        )
        if len(records) > main.ANKI_MAX_RECORDS:
            raise HTTPException(413, "记录超过 5000 条，请按分区导出")
        body = build_anki_text(records)

    filename = "oy-anki-{}.txt".format(
        main.datetime.fromisoformat(main.utc_now()).strftime("%Y%m%d")
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def send_email_change_emails(new_address, old_address, username, token):
    """后台任务：给新邮箱发确认链接，给旧邮箱发提醒；失败只记脱敏日志。"""
    link = f"{main.public_base_url()}/?email_token={token}"
    confirm_body = (
        f"你好 {username}，\n\n"
        f"有人（希望是你）在{PRODUCT_NAME}申请把账号邮箱换成这个地址。\n"
        f"30 分钟内点击下面的链接确认，确认后才会生效：\n{link}\n\n"
        "如果这不是你本人操作，忽略这封邮件即可，账号邮箱不会被改动。"
    )
    try:
        mailer.send_email(new_address, f"{PRODUCT_NAME}：确认更换邮箱", confirm_body)
    except Exception:
        main.logger.exception("发送确认更换邮箱邮件失败（收件人 %s）", main.mask_email(new_address))
    if old_address:
        notice_body = (
            f"你好 {username}，\n\n"
            f"有人请求把你在{PRODUCT_NAME}的账号邮箱更换为 {main.mask_email(new_address)}。\n"
            "新邮箱确认之前，账号邮箱不会改变。如果这不是你本人操作，"
            "请尽快修改密码。"
        )
        try:
            mailer.send_email(old_address, f"{PRODUCT_NAME}：有人请求更换账号邮箱", notice_body)
        except Exception:
            main.logger.exception("发送更换邮箱提醒失败（收件人 %s）", main.mask_email(old_address))


@router.put("/api/me/email")
def update_email(
    data: main.EmailUpdate, request: Request, background_tasks: BackgroundTasks,
    user=Depends(main.current_user),
):
    # 两步：先验当前密码并登记待确认记录，邮箱要等新邮箱里的链接确认后才改。
    stored = main.verify_current_password(user["id"], data.password, request)
    token = secrets.token_urlsafe(32)
    with main.connect(write=True) as conn:
        main.sec_recheck_session(conn, user["id"], request)
        fresh = main.recheck_account(conn, user["id"], stored)
        taken = conn.execute(
            "SELECT 1 FROM users WHERE email = ? AND id != ?", (data.email, user["id"])
        ).fetchone()
        if taken is not None:
            raise HTTPException(409, "这个邮箱已经被使用")
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO email_changes(user_id, new_email, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                new_email = excluded.new_email, token_hash = excluded.token_hash,
                expires_at = excluded.expires_at, created_at = excluded.created_at
            """,
            (user["id"], data.email, main.token_hash(token), now + main.EMAIL_CHANGE_SECONDS, now),
        )
        old_address = fresh["email"]
    # 记录已提交；发信放到后台任务，SMTP 耗时或失败不影响响应。
    background_tasks.add_task(
        send_email_change_emails, data.email, old_address, user["username"], token
    )
    return {"ok": True, "pending": True, "email": data.email}


@router.post("/api/me/email/confirm")
def confirm_email_change(data: main.EmailChangeConfirm, request: Request):
    if main.rate_limited(
        f"email-confirm:{main.client_ip(request)}",
        main.RESET_PASSWORD_LIMIT,
        main.RESET_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    invalid = HTTPException(400, "确认链接无效或已过期")
    try:
        with main.connect(write=True) as conn:
            row = conn.execute(
                "SELECT user_id, new_email, expires_at FROM email_changes WHERE token_hash = ?",
                (main.token_hash(data.token),),
            ).fetchone()
            if row is None:
                raise invalid
            # 一次性：不论后面成败，使用过的记录都删除。
            conn.execute("DELETE FROM email_changes WHERE user_id = ?", (row["user_id"],))
            if row["expires_at"] < int(time.time()):
                raise invalid
            owner = conn.execute(
                "SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (row["user_id"],)
            ).fetchone()
            if owner is None:
                raise invalid
            taken = conn.execute(
                "SELECT 1 FROM users WHERE email = ? AND id != ?",
                (row["new_email"], row["user_id"]),
            ).fetchone()
            if taken is not None:
                raise HTTPException(409, "这个邮箱已经被使用")
            conn.execute(
                "UPDATE users SET email = ? WHERE id = ?", (row["new_email"], row["user_id"])
            )
            conn.execute("DELETE FROM password_resets WHERE user_id = ?", (row["user_id"],))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "这个邮箱已经被使用") from None
    return {"ok": True, "email": row["new_email"]}


@router.put("/api/me/username")
def update_username(data: main.UsernameUpdate, user=Depends(main.current_user)):
    main.require_not_trial(user, "修改用户名")
    username = main.normalized_username(data.username)
    try:
        with main.connect(write=True) as conn:
            fresh = main.recheck_account(conn, user["id"])
            main.check_username_available(username, allow_admin_name=bool(fresh["is_admin"]))
            conn.execute(
                "UPDATE users SET username = ? WHERE id = ?",
                (username, user["id"]),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "这个用户名已经被使用") from None
    return {"ok": True, "username": username}


@router.get("/api/me/review-settings")
def rvb_get_settings(user=Depends(main.current_user)):
    with main.connect() as conn:
        fresh = main.rvb_account(conn, user["id"])
        return {"daily_review_cap": fresh["daily_review_cap"]}


@router.put("/api/me/review-settings")
def rvb_update_settings(data: main.RvbSettingsInput, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET daily_review_cap = ? WHERE id = ?",
            (data.daily_review_cap, user["id"]),
        )
    return {"daily_review_cap": data.daily_review_cap}


@router.get("/api/me/push")
def get_push_settings(user=Depends(main.current_user)):
    main.require_not_trial(user, "微信提醒")
    with main.connect() as conn:
        main.rvb_account(conn, user["id"])
        return main.push_settings_payload(conn, user["id"])


@router.put("/api/me/push")
def update_push_settings(data: main.PushSettingsInput, user=Depends(main.current_user)):
    main.require_not_trial(user, "微信提醒")
    # 空白输入等同于「保持原密钥不变」。
    incoming_secret = data.secret.strip() or None if isinstance(data.secret, str) else None
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        row = conn.execute(
            "SELECT channel, secret FROM user_push WHERE user_id = ?", (user["id"],)
        ).fetchone()
        if incoming_secret is None:
            if row is None:
                raise HTTPException(422, "请先填写 SendKey/token 后再保存。")
            if row["channel"] != data.channel:
                raise HTTPException(422, "切换推送渠道需要重新填写 SendKey/token。")
            incoming_secret = row["secret"]
        elif not push_channels.valid_key(data.channel, incoming_secret):
            raise HTTPException(422, "SendKey/token 格式不正确，请核对后重新填写。")
        # 只要这次显式提交了密钥，就把连续失败计数清零，给新配置一个干净起点。
        reset_failures = isinstance(data.secret, str) and bool(data.secret.strip())
        conn.execute(
            "INSERT INTO user_push"
            "(user_id, channel, secret, enabled, fail_count, last_ok_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, NULL, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "channel = excluded.channel, secret = excluded.secret, "
            "enabled = excluded.enabled, "
            "fail_count = CASE WHEN ? THEN 0 ELSE user_push.fail_count END, "
            "updated_at = excluded.updated_at",
            (user["id"], data.channel, incoming_secret, int(data.enabled),
             main.utc_now(), int(reset_failures)),
        )
        return main.push_settings_payload(conn, user["id"])


@router.post("/api/me/push/test")
def send_push_test(user=Depends(main.current_user)):
    main.require_not_trial(user, "微信提醒")
    with main.connect() as conn:
        main.rvb_account(conn, user["id"])
        row = conn.execute(
            "SELECT channel, secret FROM user_push WHERE user_id = ?", (user["id"],)
        ).fetchone()
    if row is None or not row["secret"]:
        raise HTTPException(400, "尚未配置 SendKey/token，请先填写并保存。")
    # 封禁 / 体验检查之后再占用限流名额；每用户每小时最多 5 条测试消息。
    if main.rate_limited(
        f"push-test-user:{user['id']}", main.PUSH_TEST_LIMIT, main.PUSH_TEST_WINDOW_SECONDS
    ):
        raise HTTPException(429, "测试消息发送太频繁，请每小时最多发送 5 条。")
    result = push_channels.send(
        row["channel"], row["secret"], main.PUSH_TEST_TITLE, main.PUSH_TEST_BODY
    )
    return {"ok": result["ok"], "message": main.PUSH_RESULT_MESSAGES[result["kind"]]}


@router.post("/api/me/password")
def change_password(data: main.PasswordChange, request: Request, response: Response, user=Depends(main.current_user)):
    main.require_not_trial(user, "修改密码")
    stored = main.verify_current_password(user["id"], data.current_password, request)
    main.check_new_password(data.new_password, username=user["username"], email=user["email"] or "")
    if data.new_password == data.current_password:
        raise HTTPException(400, "新密码不能和当前密码相同")
    hashed = main.password_hash(data.new_password)
    with main.connect(write=True) as conn:
        main.sec_recheck_session(conn, user["id"], request)
        fresh = main.recheck_account(conn, user["id"], stored)
        main.check_new_password(data.new_password, username=fresh["username"], email=fresh["email"] or "")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user["id"])
        )
        revoked = main.revoke_other_sessions(conn, user["id"], request)
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        main.set_session(conn, user["id"], response, request, sec_cleanup_expired=False)
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
    return {"ok": True, "revoked_sessions": revoked}


@router.post("/api/me/sessions/revoke-others")
def revoke_sessions(request: Request, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.sec_recheck_session(conn, user["id"], request)
        main.recheck_account(conn, user["id"])
        revoked = main.revoke_other_sessions(conn, user["id"], request)
    return {"ok": True, "revoked": revoked}


@router.post("/api/me/delete-account")
def delete_account(
    data: main.AccountDeletion, request: Request, response: Response, user=Depends(main.current_user)
):
    main.require_deletable_account(user)
    stored = main.verify_current_password(user["id"], data.password, request)
    with main.connect(write=True) as conn:
        main.sec_recheck_session(conn, user["id"], request)
        fresh = main.recheck_account(conn, user["id"], stored)
        main.require_deletable_account(fresh)
        main.delete_account_data(conn, user["id"], main.utc_now())
    rank_cache.invalidate()
    try:
        main.avatar_path(user["id"]).unlink(missing_ok=True)
    except OSError:
        main.logger.warning("注销账号头像删除失败 user_id=%s", user["id"], exc_info=True)
    # 画板行已在 delete_account_data 内删除；缩略图文件在数据目录，单独清理。
    try:
        drawings_routes.purge_user_thumbs(user["id"])
    except OSError:
        main.logger.warning("注销账号画板缩略图删除失败 user_id=%s", user["id"], exc_info=True)
    return {"ok": True}


@router.post("/api/me/avatar")
async def upload_avatar(user=Depends(main.current_user), file: UploadFile = File(...)):
    main.require_not_trial(user, "上传头像")
    content = await file.read(main.AVATAR_MAX_BYTES + 1)
    if len(content) > main.AVATAR_MAX_BYTES:
        raise HTTPException(413, "图片太大，最多 2MB")
    if not content:
        raise HTTPException(400, "文件是空的")

    image = main.decode_uploaded_image(content)
    jpeg_bytes = await main.run_in_threadpool(main.resize_avatar_to_square_jpeg, image)

    with main.connect(write=True) as conn:
        # 解码期间账号可能已注销，写锁内复查后才落盘。
        main.recheck_account(conn, user["id"])
        directory = main.avatar_dir()
        final_path = main.avatar_path(user["id"])
        temp_path = directory / f".{user['id']}-{secrets.token_hex(8)}.tmp"
        try:
            temp_path.write_bytes(jpeg_bytes)
            os.replace(temp_path, final_path)
        finally:
            temp_path.unlink(missing_ok=True)
        conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ?",
            (user["id"],),
        )
        avatar_version = conn.execute(
            "SELECT avatar_version FROM users WHERE id = ?", (user["id"],)
        ).fetchone()["avatar_version"]
    return {"ok": True, "avatar_version": avatar_version, "has_avatar": True}


@router.delete("/api/me/avatar")
def delete_own_avatar(user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.avatar_path(user["id"]).unlink(missing_ok=True)
        conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ?",
            (user["id"],),
        )
        avatar_version = conn.execute(
            "SELECT avatar_version FROM users WHERE id = ?", (user["id"],)
        ).fetchone()["avatar_version"]
    return {"ok": True, "avatar_version": avatar_version, "has_avatar": False}


@router.get("/api/users/{user_id}/avatar")
def get_avatar(user_id: int, user=Depends(main.current_user)):
    if not main.sec_has_avatar(user_id):
        raise HTTPException(404, "这个用户还没有头像")
    path = main.avatar_path(user_id)
    # URL 本身不带版本号；前端用 ?v=avatar_version 做缓存失效，
    # 这里可以放心用较长的缓存时间。
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"}
    )
