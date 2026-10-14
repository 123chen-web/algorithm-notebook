"""auth routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
from core import config, ratelimit, security, time as core_time
from models.auth import Credentials
from models.auth import ForgotPassword
from models.auth import Registration
from models.auth import ResetPassword
from models.auth import TrialSignup
import db

from db import sec_username_key
from fastapi import BackgroundTasks
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi.responses import JSONResponse
from legal import TERMS_VERSION
import os
import secrets
import sqlite3
import time
from fastapi import APIRouter


router = APIRouter()


@router.post("/api/auth/register", status_code=201)
def register(data: Registration, request: Request, response: Response):
    if ratelimit.rate_limited(
        f"register:{ratelimit.client_ip(request)}", config.REGISTER_LIMIT, config.REGISTER_WINDOW_SECONDS
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    if not data.accept_terms:
        raise HTTPException(400, "请先阅读并同意服务条款和隐私政策")
    username = security.normalized_username(data.username)
    security.check_new_password(data.password, username=username, email=data.email)

    expected = os.getenv("INVITE_CODE", "").strip()
    if not expected or expected == "change-me":
        raise HTTPException(503, "管理员尚未设置内测邀请码")

    if not secrets.compare_digest(
        data.invite_code.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(403, "邀请码不正确")

    hashed = security.password_hash(data.password)
    try:
        with db.connect(write=True) as conn:
            security.check_username_available(username)
            now = core_time.utc_now()
            cursor = conn.execute(
                """
                INSERT INTO users(username, password_hash, email, timezone, created_at,
                                  is_admin, terms_accepted_at, terms_version,
                                  reminder_token, daily_review_cap)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username, hashed, data.email, data.timezone, now,
                  0, now, TERMS_VERSION, None, config.DEFAULT_DAILY_REVIEW_CAP),
            )
            user_id = cursor.lastrowid
            conn.execute("UPDATE users SET reminder_token = ? WHERE id = ?", (f"{user_id}.{secrets.token_urlsafe(24)}", user_id))
            security.set_session(conn, user_id, response, request)
    except sqlite3.IntegrityError as exc:
        if "users.email" in str(exc):
            raise HTTPException(409, "这个邮箱已经被使用") from None
        raise HTTPException(409, "用户名已被使用") from None

    return {"id": user_id, "username": username}


@router.post("/api/auth/trial", status_code=201)
def create_trial_account(data: TrialSignup, request: Request, response: Response):
    # 免邀请码的体验账号：任何人都能触发，靠 IP 限流控制建号速度，
    # 靠 is_trial 标记单独限制 AI 调用额度（见 trial_ai_limit）。
    if ratelimit.rate_limited(
        f"trial:{ratelimit.client_ip(request)}", config.TRIAL_LIMIT, config.TRIAL_WINDOW_SECONDS
    ):
        raise HTTPException(429, "体验账号创建过于频繁，请稍后再试")

    hashed = security.password_hash(secrets.token_urlsafe(24))
    for _ in range(5):
        username = f"trial_{secrets.token_hex(6)}"
        try:
            with db.connect(write=True) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users(
                        username, password_hash, email, timezone,
                        created_at, is_trial, daily_review_cap
                    ) VALUES (?, ?, NULL, ?, ?, 1, ?)
                    """,
                    (username, hashed, data.timezone, core_time.utc_now(),
                     config.DEFAULT_DAILY_REVIEW_CAP),
                )
                user_id = cursor.lastrowid
                security.set_session(conn, user_id, response, request)
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise HTTPException(503, "暂时无法创建体验账号，请稍后再试")

    return {"id": user_id, "username": username}


@router.post("/api/auth/login")
def login(data: Credentials, request: Request, response: Response):
    if ratelimit.rate_limited(f"login:{ratelimit.client_ip(request)}", config.LOGIN_LIMIT, config.LOGIN_WINDOW_SECONDS):
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    # 按账号再叠一把"失败次数"桶：分散 IP 对同一账号撞库也会被拦住。
    # 阈值比 IP 桶宽松，且只统计失败；命中时只返回 429，不改变账号状态。
    account_key = f"login_fail:{sec_username_key(data.username)}"
    if ratelimit.rate_peek(account_key, config.LOGIN_ACCOUNT_LIMIT, config.LOGIN_WINDOW_SECONDS):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    with db.connect() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL",
            (data.username,),
        ).fetchone()
        if user is None:
            key = sec_username_key(data.username)
            candidates = [row for row in conn.execute(
                "SELECT * FROM users WHERE deleted_at IS NULL ORDER BY id"
            ) if sec_username_key(row["username"]) == key]
            # 存量冲突只能用精确名称登录，不能猜测应匹配哪一个账号。
            user = candidates[0] if len(candidates) == 1 else None

    # 不存在的账号也执行一次密码计算。
    valid = security.password_matches(
        data.password,
        user["password_hash"] if user else config.DUMMY_PASSWORD,
    )
    if not user or not valid:
        ratelimit.rate_note(account_key, config.LOGIN_WINDOW_SECONDS)
        raise HTTPException(401, "用户名或密码不正确")
    # 封禁检查放在密码校验通过之后，避免向未认证的调用方泄露
    # "这个用户名存在且被封禁" 这类额外信息。
    if user["is_banned"]:
        raise HTTPException(403, "账号已被封禁，无法登录")

    with db.connect(write=True) as conn:
        fresh = conn.execute(
            "SELECT password_hash, is_banned FROM users WHERE id = ? AND deleted_at IS NULL",
            (user["id"],),
        ).fetchone()
        if fresh is None or fresh["password_hash"] != user["password_hash"]:
            raise HTTPException(401, "用户名或密码不正确")
        if fresh["is_banned"]:
            raise HTTPException(403, "账号已被封禁，无法登录")
        security.set_session(conn, user["id"], response, request)
    return {"ok": True}


@router.post("/api/auth/forgot-password")
def forgot_password(
    data: ForgotPassword, request: Request, background_tasks: BackgroundTasks
):
    if ratelimit.rate_limited(
        f"forgot:{ratelimit.client_ip(request)}",
        config.FORGOT_PASSWORD_LIMIT,
        config.FORGOT_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    email = data.email.strip().lower()
    # 同一邮箱每小时最多 3 次；不论邮箱是否存在都计数，超限时返回与正常相同的
    # 响应，只是不再发信也不动旧 token（不泄露邮箱是否存在，也防止被刷信/刷掉旧链接）。
    if ratelimit.rate_limited(
        f"forgot-email:{email}",
        config.FORGOT_PASSWORD_EMAIL_LIMIT,
        config.FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS,
    ):
        return {"ok": True}
    with db.connect() as conn:
        user = conn.execute(
            "SELECT id, username FROM users WHERE email = ? AND deleted_at IS NULL", (email,)
        ).fetchone()

    if user is not None:
        token = secrets.token_urlsafe(32)
        with db.connect(write=True) as conn:
            # 邮箱可能在首次查询后被修改，写入前再次确认归属。
            user = conn.execute(
                "SELECT id, username FROM users WHERE id = ? AND email = ? AND deleted_at IS NULL",
                (user["id"], email),
            ).fetchone()
            if user is not None:
                conn.execute(
                    "DELETE FROM password_resets WHERE user_id = ?", (user["id"],)
                )
                conn.execute(
                    """
                    INSERT INTO password_resets(token_hash, user_id, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        security.token_hash(token),
                        user["id"],
                        int(time.time()) + config.RESET_TOKEN_SECONDS,
                    ),
                )
        # token 已提交；发信放到响应之后的后台任务，SMTP 的耗时或失败都不会
        # 拖慢响应（也避免"邮箱存在时更慢"这种计时侧信道），更不会回滚 token。
        if user is not None:
            background_tasks.add_task(
                security.send_password_reset_email, email, user["username"], token
            )

    # 不论邮箱是否存在都返回同样的结果，避免被用来探测已注册账号。
    return {"ok": True}


@router.post("/api/auth/reset-password")
def reset_password(data: ResetPassword, request: Request):
    if ratelimit.rate_limited(
        f"reset:{ratelimit.client_ip(request)}",
        config.RESET_PASSWORD_LIMIT,
        config.RESET_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    hashed_token = security.token_hash(data.token)
    with db.connect() as conn:
        row = conn.execute(
            """SELECT r.user_id, r.expires_at, u.username, u.email
               FROM password_resets r JOIN users u ON u.id = r.user_id
               WHERE r.token_hash = ? AND u.deleted_at IS NULL""",
            (hashed_token,),
        ).fetchone()
    if row is None or row["expires_at"] <= int(time.time()):
        return JSONResponse(status_code=400, content={
            "detail": "重置链接无效或已过期，请重新申请", "code": "invalid_token",
        })

    try:
        security.check_new_password(data.password, username=row["username"], email=row["email"] or "")
    except HTTPException as error:
        return JSONResponse(status_code=400, content={"detail": error.detail, "code": "weak_password"})
    hashed_password = security.password_hash(data.password)
    with db.connect(write=True) as conn:
        # 哈希计算期间 token 可能被使用、替换或过期，必须在写事务中复查。
        row = conn.execute(
            """SELECT r.user_id, r.expires_at, u.username, u.email
               FROM password_resets r JOIN users u ON u.id = r.user_id
               WHERE r.token_hash = ? AND u.deleted_at IS NULL""",
            (hashed_token,),
        ).fetchone()
        if row is None or row["expires_at"] <= int(time.time()):
            return JSONResponse(status_code=400, content={
                "detail": "重置链接无效或已过期，请重新申请", "code": "invalid_token",
            })
        try:
            security.check_new_password(data.password, username=row["username"], email=row["email"] or "")
        except HTTPException as error:
            return JSONResponse(status_code=400, content={"detail": error.detail, "code": "weak_password"})
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hashed_password, row["user_id"]),
        )
        conn.execute(
            "DELETE FROM password_resets WHERE user_id = ?", (row["user_id"],)
        )
        # 重置后让所有已登录会话失效，防止旧会话（可能已被盗用）继续有效。
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))

    return {"ok": True}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        with db.connect(write=True) as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (security.token_hash(token),),
            )
    response.delete_cookie("session", path="/")
    return {"ok": True}
