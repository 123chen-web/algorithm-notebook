import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from db import connect
from payment_channels import (
    CallbackVerificationError,
    PaymentChannelError,
    get_channel,
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def list_plans():
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM plans WHERE is_active = 1 ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def get_order(user_id, order_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND user_id = ?",
            (order_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "订单不存在")
    return dict(row)


def channel_adapter(channel):
    try:
        return get_channel(channel)
    except ValueError:
        raise HTTPException(400, "不支持的支付渠道") from None
    except PaymentChannelError:
        raise HTTPException(503, "支付渠道尚未配置或未启用") from None


def create_order(user_id, plan_id, channel):
    if type(plan_id) is not int or plan_id <= 0:
        raise HTTPException(400, "套餐编号无效")
    adapter = channel_adapter(channel)
    with connect(write=True) as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None:
            raise HTTPException(404, "用户不存在")
        plan = conn.execute(
            "SELECT * FROM plans WHERE id = ? AND is_active = 1", (plan_id,)
        ).fetchone()
        if plan is None:
            raise HTTPException(404, "套餐不存在或已停用")
        order_id = secrets.token_hex(16)
        conn.execute(
            """
            INSERT INTO orders(
                id, user_id, plan_id, amount_cents, channel, status, created_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (order_id, user_id, plan_id, plan["price_cents"], channel, utc_now()),
        )
        order = dict(conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone())

    # 先提交订单再调用渠道，未来 SDK 的网络等待不能占用数据库写锁。
    try:
        payment = adapter.create_payment(dict(order))
    except PaymentChannelError:
        # 渠道可能已受理但响应丢失，保留 pending，允许稍后的有效回调入账。
        raise HTTPException(
            502,
            {"message": "支付发起失败，请查询订单状态", "order_id": order_id},
        ) from None
    # 回调可能比发起支付的响应更早到达，返回数据库里的最新状态。
    return {"order": get_order(user_id, order_id), "payment": payment}


def handle_callback(channel, raw_body, headers, *, user_id=None):
    adapter = channel_adapter(channel)
    try:
        callback = adapter.verify_callback(raw_body, headers)
    except CallbackVerificationError:
        raise HTTPException(400, "支付回调签名或内容无效") from None

    # 验签在事务外完成；拿到写锁后重新读取，串行处理重复回调和并发续费。
    with connect(write=True) as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE id = ?", (callback.order_id,)
        ).fetchone()
        if row is None or (user_id is not None and row["user_id"] != user_id):
            raise HTTPException(404, "订单不存在")
        order = dict(row)
        if callback.channel != channel or order["channel"] != channel:
            raise HTTPException(400, "回调支付渠道与订单不一致")
        if callback.amount_cents != order["amount_cents"]:
            raise HTTPException(400, "回调金额与订单不一致")
        if (
            order["provider_trade_no"] is not None
            and order["provider_trade_no"] != callback.provider_trade_no
        ):
            raise HTTPException(409, "订单的第三方交易号不一致")

        # 已支付的重放也必须先核对金额、渠道和交易号，不能直接返回成功。
        if order["status"] != "pending":
            if (
                order["status"] == callback.status
                and order["provider_trade_no"] == callback.provider_trade_no
            ):
                return order
            raise HTTPException(409, "订单状态不允许此变更")
        duplicate = conn.execute(
            """
            SELECT id FROM orders
            WHERE channel = ? AND provider_trade_no = ? AND id != ?
            """,
            (channel, callback.provider_trade_no, order["id"]),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(409, "第三方交易号已用于其他订单")

        now = utc_now()
        cursor = conn.execute(
            """
            UPDATE orders
            SET status = ?, provider_trade_no = ?, paid_at = ?, closed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                callback.status,
                callback.provider_trade_no,
                now if callback.status == "paid" else None,
                now if callback.status == "closed" else None,
                order["id"],
            ),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "订单状态已变更，请重新查询")

        if callback.status == "paid":
            plan = conn.execute(
                "SELECT period_days FROM plans WHERE id = ?", (order["plan_id"],)
            ).fetchone()
            current_expiry = conn.execute(
                "SELECT plan_expires_at FROM users WHERE id = ?", (order["user_id"],)
            ).fetchone()["plan_expires_at"]
            # 未过期则从当前到期时间顺延，允许提前续费；否则（无套餐或
            # 已过期）从现在开始算，不倒扣已经过去的时间。
            base = (
                datetime.fromisoformat(current_expiry)
                if current_expiry and current_expiry > now
                else datetime.fromisoformat(now)
            )
            new_expiry = (base + timedelta(days=plan["period_days"])).isoformat(
                timespec="seconds"
            )
            conn.execute(
                "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
                (order["plan_id"], new_expiry, order["user_id"]),
            )

        return dict(conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order["id"],)
        ).fetchone())
