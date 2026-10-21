"""鉴权与账号安全：密码/令牌/会话/限流相关的共享函数。"""
import hashlib
import logging
import threading
from collections import defaultdict
import os
import re
import secrets
import time

from fastapi import HTTPException, Request

import mailer
from db import connect, normalize_username, sec_username_key
from legal import PRODUCT_NAME

from core.ratelimit import _rate_lock, _rate_buckets

from core.config import (
    COMMON_PASSWORDS,
    EMAIL_PATTERN,
    PASSWORD_ITERATIONS,
    RESERVED_USERNAMES,
    SESSION_SECONDS,
)
from core.ratelimit import client_ip, rate_limited

logger = logging.getLogger("algorithm_notebook")

_sec_password_locks = defaultdict(threading.Lock)

def public_base_url():
    # 拼重置密码链接用；本地开发默认指向 uvicorn 监听的地址，
    # 部署上线后要在 .env 里改成真实域名，否则邮件里的链接打不开。
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{salt}:{digest}"


def password_matches(password, stored):
    if not isinstance(stored, str):
        return False
    parts = stored.split(":")
    if len(parts) != 2:
        return False
    salt, expected = parts
    if not re.fullmatch(r"[0-9a-fA-F]{32}", salt) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected
    ):
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_ITERATIONS,
    ).hex()
    return secrets.compare_digest(actual, expected.lower())


def check_new_password(password, *, username="", email=""):
    if len(password) < 8:
        raise HTTPException(400, "密码至少 8 位")
    if len(password) > 128:
        raise HTTPException(400, "密码最多 128 位")
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) == 1:
        raise HTTPException(400, "这个密码太常见，请换一个")
    if lowered == username.lower() or (email and lowered == email.split("@", 1)[0].lower()):
        raise HTTPException(400, "密码不能和用户名或邮箱相同")


def normalized_username(value):
    try:
        return normalize_username(value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def check_username_available(username, *, allow_admin_name=False):
    admin_name = configured_admin_username()
    if username.startswith("已注销用户"):
        raise HTTPException(400, "这个用户名已被保留")
    if allow_admin_name and admin_name and username == admin_name:
        return
    if username in RESERVED_USERNAMES or (admin_name and username == admin_name):
        raise HTTPException(400, "这个用户名已被保留")


def configured_admin_username():
    value = os.getenv("ADMIN_USERNAME", "")
    if not value.strip():
        return ""
    return sec_username_key(value)


def is_https_request(request):
    """这个请求是不是走 https 来的：部署在反向代理后面（TRUST_PROXY=1）时看代理传来的 X-Forwarded-Proto。"""
    if os.getenv("TRUST_PROXY") == "1":
        proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
        if proto:
            return proto == "https"
    return request.url.scheme == "https"


def cookie_secure(request):
    """COOKIE_SECURE 显式设置时按显式值；未设置时按请求是否为 https 自动决定。"""
    explicit = os.getenv("COOKIE_SECURE")
    if explicit is not None and explicit.strip() != "":
        return explicit.strip() == "1"
    return is_https_request(request)


def set_session(conn, user_id, response, request, *, sec_cleanup_expired=True):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    if sec_cleanup_expired:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (token_hash(token), user_id, now + SESSION_SECONDS),
    )
    response.set_cookie(
        key="session",
        value=token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=cookie_secure(request),
        samesite="lax",
        path="/",
    )


def current_user(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(401, "请先登录")

    with connect() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, u.email, u.timezone, u.is_trial,
                   u.plan_id, u.plan_expires_at, u.is_banned, u.avatar_version,
                   u.is_admin, u.deleted_at, u.public_rank_opt_out,
                   u.reminder_opt_in,
                   u.show_group_today
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ? AND u.deleted_at IS NULL
            """,
            (token_hash(token), int(time.time())),
        ).fetchone()

    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    # 用不同的文案区分"被封禁"和"单纯过期"；状态码沿用 401，这样前端
    # 现有的 signedOut() 逻辑不用改就能把人立刻踢出去，具体原因走
    # error.message 正常显示。session 在被封禁后仍有效也要在下一次
    # 请求就拦下，不能等它自然过期才生效。
    if row["is_banned"]:
        raise HTTPException(401, "账号已被封禁，无法继续使用")
    user = dict(row)
    user["is_trial"] = bool(user["is_trial"])
    user["is_admin"] = bool(user["is_admin"])
    user["public_rank_opt_out"] = bool(user["public_rank_opt_out"])
    user["reminder_opt_in"] = bool(user["reminder_opt_in"])
    return user


def require_admin(user):
    if not user["is_admin"]:
        raise HTTPException(403, "需要管理员权限")


def require_not_trial(user, action):
    if user["is_trial"]:
        raise HTTPException(403, f"体验账号不支持{action}")


def mask_email(address):
    """日志里只留邮箱的首字符和域名，够排查又不把完整地址写进日志。"""
    name, _, domain = str(address).partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


def normalize_email(value):
    value = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(value):
        raise ValueError("请填写有效的邮箱地址")
    return value


def verify_current_password(user_id, password, request):
    # 共享现有限流桶。用户槽位在哈希前预占，阻止并发请求绕过失败上限；
    # 密码验证成功后清零，所以该桶只保留连续未成功的密码尝试。
    if rate_limited(f"sec-password-ip:{client_ip(request)}", 20, 15 * 60):
        raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")
    # 同一用户的验证串行，成功清零不会抹掉另一个在途验证的失败。
    with _rate_lock:
        lock = _sec_password_locks[user_id]
    with lock:
        return sec_verify_password_attempt(user_id, password)


def sec_verify_password_attempt(user_id, password):
    key = f"sec-password-user:{user_id}"
    if rate_limited(key, 5, 15 * 60):
        raise HTTPException(429, "尝试次数过多，请 15 分钟后再试")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    if not password_matches(password, row["password_hash"]):
        raise HTTPException(400, "当前密码不正确")
    with _rate_lock:
        _rate_buckets.pop(key, None)
    return row["password_hash"]


def send_password_reset_email(to_address, username, token):
    """在后台任务里执行：SMTP 可能很慢或失败，都不能影响接口响应。"""
    link = f"{public_base_url()}/?reset_token={token}"
    body = (
        f"你好 {username}，\n\n"
        f"有人（希望是你）在{PRODUCT_NAME}申请了重置密码。\n"
        f"30 分钟内点击下面的链接设置新密码：\n{link}\n\n"
        "如果这不是你本人操作，忽略这封邮件即可，密码不会被改动。"
    )
    try:
        mailer.send_email(to_address, f"{PRODUCT_NAME}：重置密码", body)
    except Exception:
        # 不向客户端暴露 SMTP 报错；服务端日志只记收件人的脱敏形式和失败原因
        # （mailer 的异常信息不含密码，这里也不记录令牌或链接）。
        logger.exception("发送密码重置邮件失败（收件人 %s）", mask_email(to_address))

