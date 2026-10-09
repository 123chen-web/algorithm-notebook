"""payments routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse
from typing import Literal
import manual_claims
import os
import payments
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/plans")
def list_plans(user=Depends(main.current_user)):
    return {"plans": payments.list_plans()}


@router.get("/api/manual-payment")
def get_manual_payment(user=Depends(main.current_user)):
    with main.connect() as conn:
        settings = main.manual_payment_settings(conn)
    return {**settings, "qr": {
        channel: main.pay_qr_path(channel).is_file() for channel in ("alipay", "wechat")
    }}


@router.get("/api/manual-payment/qr/{channel}")
def get_manual_payment_qr(channel: str, user=Depends(main.current_user)):
    path = main.pay_qr_path(channel)
    if not path.is_file():
        raise HTTPException(404, "收款码不存在")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"}
    )


@router.post("/api/redeem")
def redeem_code(data: main.RedeemInput, request: Request, user=Depends(main.current_user)):
    if user["is_trial"]:
        raise HTTPException(403, "体验账号不能兑换，请先注册正式账号")
    # 两个桶分别计数，包含失败尝试，IP 取法与登录一致。
    user_limited = main.rate_limited(f"redeem:{user['id']}", 10, 3600)
    ip_limited = main.rate_limited(f"redeem-ip:{main.client_ip(request)}", 30, 3600)
    if user_limited or ip_limited:
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    code_hash = main.redeem_code_hash(data.code)
    with main.connect(write=True) as conn:
        fresh = main.recheck_manual_account(conn, user["id"])
        if fresh["is_trial"]:
            raise HTTPException(403, "体验账号不能兑换，请先注册正式账号")
        now = main.utc_now()
        claimed = conn.execute(
            """
            UPDATE redeem_codes SET redeemed_by = ?, redeemed_at = ?
            WHERE code_hash = ? AND redeemed_by IS NULL AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (user["id"], now, code_hash, now),
        )
        if claimed.rowcount != 1:
            raise HTTPException(400, main.REDEEM_ERROR)
        code = conn.execute(
            "SELECT plan_id, period_days FROM redeem_codes WHERE code_hash = ?",
            (code_hash,),
        ).fetchone()
        plan = main.manual_plan(conn, code["plan_id"])
        expiry = main.activate_plan(conn, user["id"], code["plan_id"], code["period_days"], now)
        return {"plan_name": plan["name"], "period_days": code["period_days"],
                "plan_expires_at": expiry}


@router.post("/api/manual-claims", status_code=201)
def create_manual_claim(data: main.NewManualClaim, user=Depends(main.current_user)):
    if user["is_trial"]:
        raise HTTPException(403, "体验账号不能登记付款，请先注册正式账号")
    with main.connect(write=True) as conn:
        fresh = main.recheck_manual_account(conn, user["id"])
        if fresh["is_trial"]:
            raise HTTPException(403, "体验账号不能登记付款，请先注册正式账号")
        try:
            claim = manual_claims.create_claim(
                conn, user["id"], data.plan_id, data.payer_note, data.contact, main.utc_now(),
                actual_paid_cents=data.actual_paid_cents, payer_receipt=data.payer_receipt,
            )
        except manual_claims.ClaimError as error:
            raise main.claim_http_error(error) from None
        # 注意：这里是"先落库后限流"——限流触发时抛出 HTTPException，依赖 with 块
        # 的异常回滚撤销上面刚写入的申请。不要调整这个顺序（也不要在此处吞掉异常）。
        # 待处理上限通过后才计入每日次数，被拒的提交不占额度。
        if main.rate_limited(f"manual-claim:{user['id']}", manual_claims.DAILY_SUBMISSIONS, 86400):
            raise HTTPException(429, "今天提交的次数太多了，请明天再试或联系站长")
    return {"claim": claim}


@router.get("/api/manual-claims")
def list_manual_claims(user=Depends(main.current_user)):
    with main.connect() as conn:
        return {"claims": manual_claims.list_user_claims(conn, user["id"]),
                "plans": manual_claims.purchasable_plans(conn)}


@router.post("/api/orders", status_code=201)
def create_order(data: main.NewOrder, user=Depends(main.current_user)):
    return payments.create_order(user["id"], data.plan_id, data.channel)


@router.get("/api/orders/{order_id}")
def get_order(order_id: str, user=Depends(main.current_user)):
    return {"order": payments.get_order(user["id"], order_id)}


@router.get("/api/orders")
def list_orders(user=Depends(main.current_user)):
    return {"orders": payments.list_orders(user["id"])}


@router.post("/api/orders/{order_id}/refund")
def refund_order(order_id: str, user=Depends(main.current_user)):
    return {"order": payments.refund_order(user["id"], order_id)}


@router.post("/api/payments/mock/{channel}/callback")
async def mock_payment_callback(
    channel: Literal["alipay", "wechat"],
    request: Request,
    user=Depends(main.current_user),
):
    if os.getenv("PAYMENTS_MOCK_ENABLED", "0") != "1":
        raise HTTPException(404, "接口不存在")
    raw_body = await request.body()
    order = await main.run_in_threadpool(
        payments.handle_callback,
        channel,
        raw_body,
        request.headers,
        user_id=user["id"],
    )
    return {"ok": True, "order": order}


@router.post("/api/payments/alipay/callback")
async def alipay_payment_callback(request: Request):
    # 公开通知入口绝不能在本地 mock 模式下接收 HMAC 回调。
    if os.getenv("PAYMENTS_MOCK_ENABLED", "0") == "1":
        return PlainTextResponse("failure", status_code=404)
    raw_body = await request.body()
    try:
        await main.run_in_threadpool(
            payments.handle_callback,
            "alipay",
            raw_body,
            request.headers,
            user_id=None,
        )
    except HTTPException as exc:
        return PlainTextResponse("failure", status_code=exc.status_code)
    # 只有业务处理完成、事务提交后才确认；重复通知由业务层幂等处理。
    return PlainTextResponse("success")


@router.post("/api/payments/wechat/callback")
async def wechat_payment_callback(request: Request):
    # 公开通知入口绝不能在本地 mock 模式下接收 AEAD 回调。
    if os.getenv("PAYMENTS_MOCK_ENABLED", "0") == "1":
        return JSONResponse({"code": "FAILED", "message": "失败"}, status_code=404)
    raw_body = await request.body()
    try:
        await main.run_in_threadpool(
            payments.handle_callback,
            "wechat",
            raw_body,
            request.headers,
            user_id=None,
        )
    except HTTPException as exc:
        return JSONResponse(
            {"code": "FAILED", "message": "失败"}, status_code=exc.status_code
        )
    # 微信支付要求 2xx + {"code": "SUCCESS"}，纯文本 "success"/"failure" 是支付宝的约定。
    return JSONResponse({"code": "SUCCESS", "message": "成功"})
