"""admin routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from admin_metrics import PERIOD_CHOICES
from admin_metrics import compute_metrics as compute_admin_metrics
from datetime import timedelta
from datetime import timezone
from db import normalize_username
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import Query
from fastapi import UploadFile
from fastapi.responses import JSONResponse
from typing import Annotated
from typing import Literal
import hashlib
import manual_claims
import os
import rank_board
import rank_cache
import rank_notice
import secrets
import sqlite3
from fastapi import APIRouter


router = APIRouter()


@router.post("/api/admin/redeem-codes", status_code=201)
def generate_redeem_codes(data: main.NewRedeemCodes, user=Depends(main.current_user)):
    main.require_admin(user)
    codes = []
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        plan = main.manual_plan(conn, data.plan_id)
        now = main.utc_now()
        expires = ((main.datetime.fromisoformat(now) + timedelta(days=data.expires_in_days))
                   .isoformat(timespec="seconds")) if data.expires_in_days else None
        for _ in range(data.count):
            # 碰撞时重试；明文从不落库、不写日志，仅在这次响应返回。
            for attempt in range(10):
                raw = "".join(secrets.choice(main.REDEEM_ALPHABET) for _ in range(16))
                try:
                    conn.execute(
                        """
                        INSERT INTO redeem_codes(code_hash, code_hint, plan_id, period_days,
                            note, created_by, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (main.redeem_code_hash(raw), raw[-4:], data.plan_id,
                         data.days if data.days is not None else plan["period_days"],
                         data.note, user["id"], now, expires),
                    )
                    break
                except sqlite3.IntegrityError:
                    if attempt == 9:
                        raise
            codes.append("-".join(raw[index:index + 4] for index in range(0, 16, 4)))
    return JSONResponse(status_code=201, content={"codes": codes},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/admin/redeem-codes")
def list_redeem_codes(
    status: Literal["all", "unused", "redeemed", "revoked"] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user=Depends(main.current_user),
):
    main.require_admin(user)
    filters = {
        "all": "1 = 1",
        "unused": "c.redeemed_by IS NULL AND c.revoked_at IS NULL",
        "redeemed": "c.redeemed_by IS NOT NULL",
        "revoked": "c.revoked_at IS NOT NULL",
    }
    with main.connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.code_hint AS hint, p.name AS plan_name, c.period_days,
                   c.note, CASE WHEN c.redeemed_by IS NOT NULL THEN 'redeemed'
                                WHEN c.revoked_at IS NOT NULL THEN 'revoked'
                                ELSE 'unused' END AS status,
                   c.created_at, c.expires_at, u.username AS redeemed_by, c.redeemed_at
            FROM redeem_codes c JOIN plans p ON p.id = c.plan_id
            LEFT JOIN users u ON u.id = c.redeemed_by
            WHERE """ + filters[status] + " ORDER BY c.created_at DESC, c.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"codes": [dict(row) for row in rows]}


@router.post("/api/admin/redeem-codes/{code_id}/revoke")
def revoke_redeem_code(code_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        cursor = conn.execute(
            "UPDATE redeem_codes SET revoked_at = ? "
            "WHERE id = ? AND redeemed_by IS NULL AND revoked_at IS NULL",
            (main.utc_now(), code_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "只能撤销未使用的兑换码")
    return {"ok": True}


@router.post("/api/admin/manual-grant")
def manual_grant(data: main.ManualGrant, user=Depends(main.current_user)):
    main.require_admin(user)
    try:
        username = normalize_username(data.username)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        target = conn.execute(
            "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL", (username,)
        ).fetchone()
        if target is None:
            target = conn.execute(
                "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL",
                (data.username.strip().lower(),),
            ).fetchone()
        if target is None:
            raise HTTPException(404, "用户不存在")
        main.recheck_manual_account(conn, target["id"])
        if target["is_trial"]:
            raise HTTPException(403, "体验账号不能开通，请先注册正式账号")
        plan = main.manual_plan(conn, data.plan_id)
        days = data.days if data.days is not None else plan["period_days"]
        now = main.utc_now()
        expiry = main.activate_plan(conn, target["id"], data.plan_id, days, now)
        # 此哈希没有对应的16位兑换码，同时已占用，用作直接开通的审计记录。
        conn.execute(
            """
            INSERT INTO redeem_codes(code_hash, code_hint, plan_id, period_days, note,
                created_by, created_at, redeemed_by, redeemed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (hashlib.sha256(secrets.token_bytes(32)).hexdigest(), "----", data.plan_id,
             days, "管理员直接开通：" + target["username"], user["id"], now, target["id"], now),
        )
    return {"username": target["username"], "plan_name": plan["name"],
            "plan_expires_at": expiry}


@router.put("/api/admin/manual-payment/qr/{channel}")
async def upload_manual_payment_qr(
    channel: str, user=Depends(main.current_user), file: UploadFile = File(...),
):
    main.require_admin(user)
    path = main.pay_qr_path(channel)
    if file.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(400, "只支持 PNG 或 JPEG 格式的图片")
    content = await file.read(main.PAY_QR_MAX_BYTES + 1)
    if len(content) > main.PAY_QR_MAX_BYTES:
        raise HTTPException(413, "图片太大，最多 2MB")
    png = await main.run_in_threadpool(main.encode_pay_qr, content)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{channel}-{secrets.token_hex(8)}.tmp"
        try:
            temporary.write_bytes(png)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"ok": True}


@router.delete("/api/admin/manual-payment/qr/{channel}")
def delete_manual_payment_qr(channel: str, user=Depends(main.current_user)):
    main.require_admin(user)
    path = main.pay_qr_path(channel)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        path.unlink(missing_ok=True)
    return {"ok": True}


@router.put("/api/admin/manual-payment/settings")
def update_manual_payment_settings(data: main.ManualPaymentSettings, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        conn.executemany(
            "INSERT INTO app_settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [("manual_payment_enabled", "1" if data.enabled else "0"),
             ("manual_payment_contact", data.contact)],
        )
    return {"enabled": data.enabled, "contact": data.contact}


@router.get("/api/admin/manual-claims")
def admin_list_manual_claims(
    status: Literal["pending", "confirmed", "rejected", "all"] = "pending",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    user=Depends(main.current_user),
):
    main.require_admin(user)
    with main.connect() as conn:
        return manual_claims.list_admin_claims(conn, status, page)


@router.post("/api/admin/manual-claims/{claim_id}/confirm")
def admin_confirm_manual_claim(
    claim_id: int, background_tasks: BackgroundTasks, user=Depends(main.current_user)
):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        try:
            claim, mail_info = manual_claims.confirm_claim(
                conn, claim_id, user["id"], main.utc_now()
            )
        except manual_claims.ClaimError as error:
            raise main.claim_http_error(error) from None
    # 事务提交后才发邮件；SMTP 慢或失败都不影响确认。
    background_tasks.add_task(manual_claims.send_confirmation_email, mail_info)
    return {"claim": claim, "plan_expires_at": mail_info["plan_expires_at"]}


@router.post("/api/admin/manual-claims/{claim_id}/reject")
def admin_reject_manual_claim(
    claim_id: int, data: main.RejectManualClaim, user=Depends(main.current_user)
):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        try:
            claim = manual_claims.reject_claim(conn, claim_id, user["id"], data.reason, main.utc_now())
        except manual_claims.ClaimError as error:
            raise main.claim_http_error(error) from None
    return {"claim": claim}


@router.get("/api/admin/daily-notices")
def admin_list_daily_notices(user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect() as conn:
        today = rank_board.beijing_today()
        return {"today": today.isoformat(), "notices": rank_notice.list_notices(conn, today)}


@router.post("/api/admin/daily-notices", status_code=201)
def admin_create_daily_notice(data: rank_notice.NoticeInput, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        notice_id = rank_notice.create_notice(conn, data, user["id"], main.utc_now())
    return {"id": notice_id}


@router.put("/api/admin/daily-notices/{notice_id}")
def admin_update_daily_notice(
    notice_id: int, data: rank_notice.NoticeInput, user=Depends(main.current_user)
):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_manual_account(conn, user["id"], admin=True)
        if rank_notice.update_notice(conn, notice_id, data, main.utc_now()) != 1:
            raise HTTPException(404, "这条记录不存在")
    return {"ok": True}


@router.get("/api/admin/dashboard")
def admin_dashboard(user=Depends(main.current_user)):
    main.require_admin(user)
    now = main.datetime.fromisoformat(main.utc_now()).astimezone(timezone.utc)
    # 运营看板统一用 UTC；时间戳按过去 N × 24 小时，AI 日汇总按含今天的 7 天。
    bounds = {
        "now": now.isoformat(),
        "since_7_days": (now - timedelta(days=7)).isoformat(),
        "since_30_days": (now - timedelta(days=30)).isoformat(),
        "today": now.date().isoformat(),
        "first_ai_day": (now.date() - timedelta(days=6)).isoformat(),
    }
    with main.connect() as conn:
        # 多个聚合读取同一个快照，避免并发写入让总数和分区明细对不上。
        conn.execute("BEGIN")
        users = dict(conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(CASE WHEN is_trial = 1 THEN 1 END) AS trial,
                   COUNT(CASE WHEN is_trial = 0 THEN 1 END) AS registered,
                   COUNT(CASE WHEN julianday(created_at)
                       BETWEEN julianday(:since_7_days) AND julianday(:now)
                       THEN 1 END) AS new_7_days,
                   COUNT(CASE WHEN julianday(created_at)
                       BETWEEN julianday(:since_30_days) AND julianday(:now)
                       THEN 1 END) AS new_30_days,
                   COUNT(CASE WHEN plan_id IS NOT NULL
                       AND julianday(plan_expires_at) > julianday(:now)
                       THEN 1 END) AS active_subscriptions
            FROM users WHERE deleted_at IS NULL
            """, bounds,
        ).fetchone())
        active_users = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT user_id FROM problems
                WHERE julianday(created_at)
                    BETWEEN julianday(:since_7_days) AND julianday(:now)
                UNION
                SELECT p.user_id FROM reviews r
                JOIN mistakes m ON m.id = r.mistake_id
                JOIN problems p ON p.id = m.problem_id
                WHERE julianday(r.reviewed_at)
                    BETWEEN julianday(:since_7_days) AND julianday(:now)
            ) active JOIN users u ON u.id = active.user_id
            WHERE u.deleted_at IS NULL
            """, bounds,
        ).fetchone()[0]
        ai_usage = dict(conn.execute(
            """
            SELECT COALESCE(SUM(CASE WHEN day = :today THEN attempts ELSE 0 END), 0)
                       AS today,
                   COALESCE(SUM(attempts), 0) AS last_7_days
            FROM ai_usage WHERE day BETWEEN :first_ai_day AND :today
            """, bounds,
        ).fetchone())
        content = dict(conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM problems) AS problems,
                   (SELECT COUNT(*) FROM mistakes) AS mistakes,
                   (SELECT COUNT(*) FROM posts WHERE deleted_at IS NULL) AS posts,
                   (SELECT COUNT(*) FROM reports WHERE resolved_at IS NULL)
                     + (SELECT COUNT(*) FROM avatar_reports WHERE resolved_at IS NULL)
                       AS pending_reports
            """
        ).fetchone())
        zone_counts = dict.fromkeys(main.PROBLEM_ZONES, 0)
        zone_counts.update({
            row["zone"]: row["mistake_count"]
            for row in conn.execute(
                """
                SELECT p.zone, COUNT(m.id) AS mistake_count
                FROM problems p LEFT JOIN mistakes m ON m.problem_id = p.id
                GROUP BY p.zone
                """
            )
        })
        subscriptions = dict(conn.execute(
            """
            SELECT COUNT(*) AS paid_orders,
                   COALESCE(SUM(amount_cents), 0) AS paid_amount_cents
            FROM orders WHERE status = 'paid'
            """
        ).fetchone())
    subscriptions["active"] = users.pop("active_subscriptions")
    pending_reports = content.pop("pending_reports")
    return {
        "generated_at": bounds["now"],
        "users": users,
        "activity": {"active_users_7_days": active_users},
        "ai_usage": ai_usage,
        "content": content,
        "pending_reports": pending_reports,
        "zones": [
            {"zone": zone, "mistake_count": count}
            for zone, count in sorted(zone_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "subscriptions": subscriptions,
    }


@router.get("/api/admin/metrics")
def admin_metrics_overview(days: int = 7, user=Depends(main.current_user)):
    main.require_admin(user)
    if days not in PERIOD_CHOICES:
        raise HTTPException(422, "days 只能是 7 或 30")
    now = main.datetime.fromisoformat(main.utc_now()).astimezone(timezone.utc)
    with main.connect() as conn:
        return compute_admin_metrics(conn, days, now)


@router.get("/api/admin/reports")
def list_reports(user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.id, r.reason, r.created_at, r.post_id, r.comment_id,
                reporter.username AS reporter_username,
                post.title AS post_title, post.body AS post_body,
                post.deleted_at AS post_deleted_at,
                post.user_id AS post_author_id,
                post_author.username AS post_author_username,
                comment.body AS comment_body,
                comment.deleted_at AS comment_deleted_at,
                comment.user_id AS comment_author_id,
                comment_author.username AS comment_author_username
            FROM reports r
            JOIN users reporter ON reporter.id = r.reporter_user_id
            LEFT JOIN posts post ON post.id = r.post_id
            LEFT JOIN users post_author ON post_author.id = post.user_id
            LEFT JOIN post_comments comment ON comment.id = r.comment_id
            LEFT JOIN users comment_author ON comment_author.id = comment.user_id
            WHERE r.resolved_at IS NULL
            ORDER BY r.created_at ASC
            """
        ).fetchall()
        avatar_rows = conn.execute(
            """
            SELECT
                a.id, a.reason, a.created_at, a.avatar_owner_id,
                reporter.username AS reporter_username,
                owner.username AS avatar_owner_username,
                owner.avatar_version AS avatar_owner_avatar_version
            FROM avatar_reports a
            JOIN users reporter ON reporter.id = a.reporter_user_id
            JOIN users owner ON owner.id = a.avatar_owner_id
            WHERE a.resolved_at IS NULL
              AND reporter.deleted_at IS NULL AND owner.deleted_at IS NULL
            ORDER BY a.created_at ASC
            """
        ).fetchall()
    reports = [{"type": ("post" if row["post_id"] is not None else "comment"), **dict(row)} for row in rows]
    reports += [
        {
            "type": "avatar",
            **dict(row),
            "avatar_owner_has_avatar": main.avatar_path(row["avatar_owner_id"]).is_file(),
        }
        for row in avatar_rows
    ]
    reports.sort(key=lambda report: report["created_at"])
    return {"reports": reports}


@router.post("/api/admin/reports/{report_id}/resolve")
def admin_resolve_report(report_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE reports SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (main.utc_now(), report_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "举报不存在或已处理")
    return {"ok": True}


@router.post("/api/admin/avatar-reports/{report_id}/resolve")
def admin_resolve_avatar_report(report_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE avatar_reports SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (main.utc_now(), report_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "举报不存在或已处理")
    return {"ok": True}


@router.delete("/api/admin/posts/{post_id}")
def admin_delete_post(post_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.visible_post(conn, post_id)
        conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ?", (main.utc_now(), post_id)
        )
        main.clear_deleted_thread_state(conn, post_id=post_id)
        main.resolve_reports_for(conn, post_id=post_id)
    return {"ok": True}


@router.delete("/api/admin/comments/{comment_id}")
def admin_delete_comment(comment_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        main.visible_comment(conn, comment_id)
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?",
            (main.utc_now(), comment_id),
        )
        main.clear_deleted_thread_state(conn, comment_id=comment_id)
        main.resolve_reports_for(conn, comment_id=comment_id)
    return {"ok": True}


@router.delete("/api/admin/users/{user_id}/avatar")
def admin_clear_avatar(user_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    main.avatar_path(user_id).unlink(missing_ok=True)
    with main.connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
        conn.execute(
            """
            UPDATE avatar_reports SET resolved_at = ?
            WHERE avatar_owner_id = ? AND resolved_at IS NULL
            """,
            (main.utc_now(), user_id),
        )
    return {"ok": True, "has_avatar": False}


@router.post("/api/admin/users/{user_id}/ban")
def admin_ban_user(user_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    if user_id == user["id"]:
        raise HTTPException(400, "不能封禁自己")
    with main.connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET is_banned = 1 WHERE id = ? AND deleted_at IS NULL", (user_id,)
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
    rank_cache.invalidate()
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/unban")
def admin_unban_user(user_id: int, user=Depends(main.current_user)):
    main.require_admin(user)
    with main.connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET is_banned = 0 WHERE id = ? AND deleted_at IS NULL", (user_id,)
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
    rank_cache.invalidate()
    return {"ok": True}
