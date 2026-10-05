"""手动收款登记：用户付款后登记，站长确认后自动开通套餐。

所有函数接收调用方已打开的连接，确认/驳回必须在同一个写事务（connect(write=True)）里
调用；错误用 ClaimError 表示，接口层再转成 HTTP 响应。
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import mailer
from payments import activate_plan

logger = logging.getLogger("algorithm_notebook")

MAX_PENDING = 3
DAILY_SUBMISSIONS = 5
PAGE_SIZE = 20
STATUSES = ("pending", "confirmed", "rejected")

CLAIM_SELECT = """
SELECT c.id, c.plan_id, p.name AS plan_name, c.payer_note, c.contact, c.status,
       c.reject_reason, c.created_at, c.decided_at
FROM manual_payment_claims c JOIN plans p ON p.id = c.plan_id
"""


class ClaimError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def price_text(price_cents):
    return f"¥{price_cents / 100:.2f}"


def purchasable_plans(conn):
    rows = conn.execute(
        "SELECT id, name, price_cents FROM plans WHERE is_active = 1 ORDER BY id"
    ).fetchall()
    return [{"id": row["id"], "name": row["name"],
             "price_text": price_text(row["price_cents"])} for row in rows]


def get_claim(conn, claim_id):
    row = conn.execute(CLAIM_SELECT + " WHERE c.id = ?", (claim_id,)).fetchone()
    return dict(row) if row else None


def list_user_claims(conn, user_id):
    rows = conn.execute(
        CLAIM_SELECT + " WHERE c.user_id = ? ORDER BY c.id DESC LIMIT 50", (user_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def create_claim(conn, user_id, plan_id, payer_note, contact, now):
    plan = conn.execute(
        "SELECT id FROM plans WHERE id = ? AND is_active = 1", (plan_id,)
    ).fetchone()
    if plan is None:
        raise ClaimError(422, "这个套餐当前不可购买")
    pending = conn.execute(
        "SELECT COUNT(*) FROM manual_payment_claims WHERE user_id = ? AND status = 'pending'",
        (user_id,),
    ).fetchone()[0]
    if pending >= MAX_PENDING:
        raise ClaimError(429, f"你已有 {MAX_PENDING} 条待处理的登记，请等站长确认后再提交")
    claim_id = conn.execute(
        "INSERT INTO manual_payment_claims(user_id, plan_id, payer_note, contact, status, "
        "created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (user_id, plan_id, payer_note, contact, now),
    ).lastrowid
    return get_claim(conn, claim_id)


def list_admin_claims(conn, status, page):
    where, params = ("", ())
    if status != "all":
        where, params = (" WHERE c.status = ?", (status,))
    total = conn.execute(
        "SELECT COUNT(*) FROM manual_payment_claims c" + where, params
    ).fetchone()[0]
    # 待处理按先来后到；其余看最近处理的。
    order = "c.id ASC" if status == "pending" else "c.id DESC"
    rows = conn.execute(
        CLAIM_SELECT.replace("FROM manual_payment_claims c", "FROM manual_payment_claims c "
                             "JOIN users u ON u.id = c.user_id", 1)
        .replace("c.decided_at", "c.decided_at, u.username", 1)
        + where + f" ORDER BY {order} LIMIT ? OFFSET ?",
        params + (PAGE_SIZE, (page - 1) * PAGE_SIZE),
    ).fetchall()
    return {"claims": [dict(row) for row in rows], "page": page,
            "pages": max(1, -(-total // PAGE_SIZE))}


def confirm_claim(conn, claim_id, admin_id, now=None):
    """确认并开通套餐；返回 (claim, 邮件所需信息)。调用方须在写事务内。"""
    now = _now() if now is None else now
    claim = conn.execute(
        "SELECT * FROM manual_payment_claims WHERE id = ?", (claim_id,)
    ).fetchone()
    if claim is None:
        raise ClaimError(404, "登记不存在")
    if claim["status"] != "pending":
        raise ClaimError(409, "这条登记已经处理过了")
    user = conn.execute("SELECT * FROM users WHERE id = ?", (claim["user_id"],)).fetchone()
    if user is None or user["deleted_at"] is not None:
        raise ClaimError(409, "用户已注销，无法开通")
    if user["is_banned"]:
        raise ClaimError(409, "用户已被封禁，无法开通")
    if user["is_trial"]:
        raise ClaimError(409, "体验账号不能开通，请先让用户注册正式账号")
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (claim["plan_id"],)).fetchone()
    if plan is None:
        raise ClaimError(409, "套餐不存在")
    updated = conn.execute(
        "UPDATE manual_payment_claims SET status = 'confirmed', decided_at = ?, "
        "decided_by = ? WHERE id = ? AND status = 'pending'",
        (now, admin_id, claim_id),
    )
    if updated.rowcount != 1:
        raise ClaimError(409, "这条登记已经处理过了")
    expiry = activate_plan(conn, user["id"], plan["id"], plan["period_days"], now)
    # 与“管理员直接开通”一致：占用一条没有明文的兑换码记录，方便对账。
    conn.execute(
        """
        INSERT INTO redeem_codes(code_hash, code_hint, plan_id, period_days, note,
            created_by, created_at, redeemed_by, redeemed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (hashlib.sha256(secrets.token_bytes(32)).hexdigest(), "----", plan["id"],
         plan["period_days"], f"手动收款确认 #{claim_id}：{user['username']}",
         admin_id, now, user["id"], now),
    )
    logger.info("手动收款登记 #%s 已确认（用户 %s，套餐 %s，操作者 %s）",
                claim_id, user["id"], plan["id"], admin_id if admin_id else "命令行")
    return get_claim(conn, claim_id), {
        "email": user["email"], "username": user["username"],
        "plan_name": plan["name"], "plan_expires_at": expiry,
    }


def reject_claim(conn, claim_id, admin_id, reason, now=None):
    now = _now() if now is None else now
    claim = conn.execute(
        "SELECT status FROM manual_payment_claims WHERE id = ?", (claim_id,)
    ).fetchone()
    if claim is None:
        raise ClaimError(404, "登记不存在")
    updated = conn.execute(
        "UPDATE manual_payment_claims SET status = 'rejected', reject_reason = ?, "
        "decided_at = ?, decided_by = ? WHERE id = ? AND status = 'pending'",
        (reason, now, admin_id, claim_id),
    )
    if updated.rowcount != 1:
        raise ClaimError(409, "这条登记已经处理过了")
    logger.info("手动收款登记 #%s 已驳回（操作者 %s）", claim_id,
                admin_id if admin_id else "命令行")
    return get_claim(conn, claim_id)


def send_confirmation_email(info):
    """后台发送确认邮件；未配置邮件或没有邮箱时跳过，失败只写日志。"""
    if not info.get("email") or not mailer.smtp_configured():
        return
    body = (
        f"你好 {info['username']}，\n\n"
        f"你登记的付款已确认，「{info['plan_name']}」已开通，"
        f"有效期至 {info['plan_expires_at']}（UTC）。\n"
        "打开套餐页即可查看。"
    )
    try:
        mailer.send_email(info["email"], "欧叶OY：付款已确认，套餐已开通", body)
    except Exception:
        # 不记录收件人全文，也不影响已完成的确认。
        logger.exception("发送收款确认邮件失败（用户 %s）", info.get("username"))
