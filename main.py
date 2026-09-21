import hashlib
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

import ai
import mailer
from db import ROOT, connect, init_db
from scheduler import schedule, today_in_timezone

logger = logging.getLogger("algorithm_notebook")

SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000
DUMMY_PASSWORD = "0" * 32 + ":" + "0" * 64

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
MistakeText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
ThinkingText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]

# 简单校验即可：真正确认邮箱能收到信，靠的是密码找回时能不能收到邮件，
# 而不是注册时的格式检查，所以没有引入额外的邮箱校验依赖。
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(InputModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    invite_code: str = Field(min_length=1, max_length=256)
    email: str = Field(min_length=3, max_length=254)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请填写有效时区，例如 Asia/Shanghai") from None
        return value


def normalize_email(value):
    value = value.strip().lower()
    if not EMAIL_PATTERN.fullmatch(value):
        raise ValueError("请填写有效的邮箱地址")
    return value


class ForgotPassword(InputModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class ResetPassword(InputModel):
    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=10, max_length=128)


class EmailUpdate(InputModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class ProblemFields(InputModel):
    title: Title
    language: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ]
    code: str = Field(min_length=1, max_length=40000)
    thinking: ThinkingText

    @field_validator("code")
    @classmethod
    def nonempty_code(cls, value):
        if not value.strip():
            raise ValueError("代码不能为空")
        # 保留原始缩进。
        return value


class NewProblem(ProblemFields):
    mistakes: list[MistakeText] = Field(min_length=1, max_length=10)


class ProblemEdit(ProblemFields):
    pass


class MistakeEdit(InputModel):
    description: MistakeText
    version: int = Field(strict=True, ge=0)


class ReviewInput(InputModel):
    quality: int = Field(strict=True, ge=0, le=5)
    version: int = Field(strict=True, ge=0)


class VariantResult(InputModel):
    result: Literal["unattempted", "solved", "partial", "failed"]
    answer_code: str = Field(default="", max_length=40000)
    notes: str = Field(default="", max_length=8000)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_for(user):
    return today_in_timezone(user["timezone"])


def ai_limit():
    return max(1, int(os.getenv("AI_DAILY_LIMIT", "10")))


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
    salt, expected = stored.split(":")
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_ITERATIONS,
    ).hex()
    return secrets.compare_digest(actual, expected)


def set_session(conn, user_id, response):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
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
        secure=os.getenv("COOKIE_SECURE", "0") == "1",
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
            SELECT u.id, u.username, u.email, u.timezone
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (token_hash(token), int(time.time())),
        ).fetchone()

    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    return dict(row)


MISTAKE_SELECT = """
SELECT m.*, p.title, p.language, p.code, p.thinking
FROM mistakes m
JOIN problems p ON p.id = m.problem_id
"""


def owned_mistake(conn, mistake_id, user_id):
    row = conn.execute(
        MISTAKE_SELECT + " WHERE m.id = ? AND p.user_id = ?",
        (mistake_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "记录不存在")
    return dict(row)


def owned_problem(conn, problem_id, user_id):
    row = conn.execute(
        "SELECT * FROM problems WHERE id = ? AND user_id = ?",
        (problem_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "题目不存在")
    return dict(row)


# 单实例的简单防刷：按客户端 IP 计数，进程重启即清零。
# 部署到多实例或反向代理之后，需要改用共享存储并校验可信的转发头。
REGISTER_LIMIT = 5
REGISTER_WINDOW_SECONDS = 15 * 60
LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 15 * 60
FORGOT_PASSWORD_LIMIT = 5
FORGOT_PASSWORD_WINDOW_SECONDS = 15 * 60
RESET_PASSWORD_LIMIT = 10
RESET_PASSWORD_WINDOW_SECONDS = 15 * 60
RESET_TOKEN_SECONDS = 30 * 60

_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)


def rate_limited(key, limit, window_seconds):
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        return False


def reset_rate_limits():
    with _rate_lock:
        _rate_buckets.clear()


def client_ip(request: Request):
    return request.client.host if request.client else "unknown"


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.middleware("http")
async def request_protection(request, call_next):
    # 前端与 API 同源，且本应用不启用 CORS。
    # 跨站表单不能携带这个自定义请求头。
    if (
        request.url.path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("X-CSRF-Protection") != "1"
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "请求缺少必要的安全校验"},
        )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
def home():
    return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/auth/register", status_code=201)
def register(data: Registration, request: Request, response: Response):
    if rate_limited(
        f"register:{client_ip(request)}", REGISTER_LIMIT, REGISTER_WINDOW_SECONDS
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    expected = os.getenv("INVITE_CODE", "").strip()
    if not expected or expected == "change-me":
        raise HTTPException(503, "管理员尚未设置内测邀请码")

    if not secrets.compare_digest(
        data.invite_code.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(403, "邀请码不正确")

    hashed = password_hash(data.password)
    try:
        with connect(write=True) as conn:
            cursor = conn.execute(
                """
                INSERT INTO users(username, password_hash, email, timezone, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (data.username.lower(), hashed, data.email, data.timezone, utc_now()),
            )
            user_id = cursor.lastrowid
            set_session(conn, user_id, response)
    except sqlite3.IntegrityError as exc:
        if "users.email" in str(exc):
            raise HTTPException(409, "这个邮箱已经被使用") from None
        raise HTTPException(409, "用户名已被使用") from None

    return {"id": user_id, "username": data.username.lower()}


@app.post("/api/auth/login")
def login(data: Credentials, request: Request, response: Response):
    if rate_limited(f"login:{client_ip(request)}", LOGIN_LIMIT, LOGIN_WINDOW_SECONDS):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    with connect() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (data.username.lower(),),
        ).fetchone()

    # 不存在的账号也执行一次密码计算。
    valid = password_matches(
        data.password,
        user["password_hash"] if user else DUMMY_PASSWORD,
    )
    if not user or not valid:
        raise HTTPException(401, "用户名或密码不正确")

    with connect(write=True) as conn:
        set_session(conn, user["id"], response)
    return {"ok": True}


def send_password_reset_email(to_address, username, token):
    link = f"{public_base_url()}/?reset_token={token}"
    body = (
        f"你好 {username}，\n\n"
        "有人（希望是你）在算法错题本申请了重置密码。\n"
        f"30 分钟内点击下面的链接设置新密码：\n{link}\n\n"
        "如果这不是你本人操作，忽略这封邮件即可，密码不会被改动。"
    )
    try:
        mailer.send_email(to_address, "算法错题本：重置密码", body)
    except Exception:
        # 发信失败不影响接口返回，避免把 SMTP 报错暴露给客户端；
        # 服务端日志里留一条记录方便自己排查。
        logger.exception("发送密码重置邮件失败：%s", to_address)


@app.post("/api/auth/forgot-password")
def forgot_password(data: ForgotPassword, request: Request):
    if rate_limited(
        f"forgot:{client_ip(request)}",
        FORGOT_PASSWORD_LIMIT,
        FORGOT_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    email = data.email.strip().lower()
    with connect() as conn:
        user = conn.execute(
            "SELECT id, username FROM users WHERE email = ?", (email,)
        ).fetchone()

    if user is not None:
        token = secrets.token_urlsafe(32)
        with connect(write=True) as conn:
            # 邮箱可能在首次查询后被修改，写入前再次确认归属。
            user = conn.execute(
                "SELECT id, username FROM users WHERE id = ? AND email = ?",
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
                        token_hash(token),
                        user["id"],
                        int(time.time()) + RESET_TOKEN_SECONDS,
                    ),
                )
        # token 已提交，SMTP 的耗时或失败都不会延长写锁或回滚 token。
        if user is not None:
            send_password_reset_email(email, user["username"], token)

    # 不论邮箱是否存在都返回同样的结果，避免被用来探测已注册账号。
    return {"ok": True}


@app.post("/api/auth/reset-password")
def reset_password(data: ResetPassword, request: Request):
    if rate_limited(
        f"reset:{client_ip(request)}",
        RESET_PASSWORD_LIMIT,
        RESET_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    hashed_token = token_hash(data.token)
    with connect() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM password_resets WHERE token_hash = ?",
            (hashed_token,),
        ).fetchone()
    if row is None or row["expires_at"] < int(time.time()):
        raise HTTPException(400, "重置链接无效或已过期，请重新申请")

    hashed_password = password_hash(data.password)
    with connect(write=True) as conn:
        # 哈希计算期间 token 可能被使用、替换或过期，必须在写事务中复查。
        row = conn.execute(
            "SELECT user_id, expires_at FROM password_resets WHERE token_hash = ?",
            (hashed_token,),
        ).fetchone()
        if row is None or row["expires_at"] < int(time.time()):
            raise HTTPException(400, "重置链接无效或已过期，请重新申请")
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


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        with connect(write=True) as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = ?",
                (token_hash(token),),
            )
    response.delete_cookie("session", path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {
        **user,
        "today": today_for(user).isoformat(),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY", "").strip()),
        "ai_daily_limit": ai_limit(),
    }


@app.put("/api/me/email")
def update_email(data: EmailUpdate, user=Depends(current_user)):
    # 在加入 email 列之前注册的老账号没有邮箱，没法用密码找回和复习
    # 提醒；这个接口让已登录用户自己补一个，不用重新注册。
    try:
        with connect(write=True) as conn:
            conn.execute(
                "UPDATE users SET email = ? WHERE id = ?", (data.email, user["id"])
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "这个邮箱已经被使用") from None
    return {"ok": True, "email": data.email}


@app.post("/api/problems", status_code=201)
def create_problem(data: NewProblem, user=Depends(current_user)):
    day = today_for(user).isoformat()
    with connect(write=True) as conn:
        cursor = conn.execute(
            """
            INSERT INTO problems(
                user_id, title, language, code, thinking, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], data.title, data.language,
                data.code, data.thinking, utc_now(),
            ),
        )
        problem_id = cursor.lastrowid
        mistake_ids = []
        for description in data.mistakes:
            cursor = conn.execute(
                """
                INSERT INTO mistakes(problem_id, description, due_date)
                VALUES (?, ?, ?)
                """,
                (problem_id, description, day),
            )
            mistake_ids.append(cursor.lastrowid)

    return {"id": problem_id, "mistake_ids": mistake_ids}


@app.put("/api/problems/{problem_id}")
def edit_problem(problem_id: int, data: ProblemEdit, user=Depends(current_user)):
    with connect(write=True) as conn:
        owned_problem(conn, problem_id, user["id"])
        conn.execute(
            """
            UPDATE problems
            SET title = ?, language = ?, code = ?, thinking = ?
            WHERE id = ?
            """,
            (data.title, data.language, data.code, data.thinking, problem_id),
        )
        updated = conn.execute(
            "SELECT * FROM problems WHERE id = ?", (problem_id,)
        ).fetchone()
    return dict(updated)


@app.delete("/api/problems/{problem_id}")
def delete_problem(problem_id: int, user=Depends(current_user)):
    # 级联删除该题下的全部易错点、复习记录和变体题。
    with connect(write=True) as conn:
        owned_problem(conn, problem_id, user["id"])
        conn.execute("DELETE FROM problems WHERE id = ?", (problem_id,))
    return {"ok": True}


@app.get("/api/mistakes")
def list_mistakes(due_only: bool = True, user=Depends(current_user)):
    day = today_for(user).isoformat()
    sql = MISTAKE_SELECT + " WHERE p.user_id = ?"
    params = [user["id"]]
    if due_only:
        sql += " AND m.due_date <= ?"
        params.append(day)
    sql += " ORDER BY m.due_date ASC, m.id ASC"

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"today": day, "items": [dict(row) for row in rows]}


@app.get("/api/mistakes/{mistake_id}")
def get_mistake(mistake_id: int, user=Depends(current_user)):
    with connect() as conn:
        item = owned_mistake(conn, mistake_id, user["id"])
        item["reviews"] = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id DESC",
                (mistake_id,),
            )
        ]
        item["variants"] = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM variants WHERE mistake_id = ? ORDER BY id DESC",
                (mistake_id,),
            )
        ]
    item["today"] = today_for(user).isoformat()
    return item


@app.put("/api/mistakes/{mistake_id}")
def edit_mistake(mistake_id: int, data: MistakeEdit, user=Depends(current_user)):
    with connect(write=True) as conn:
        item = owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")

        conn.execute(
            "UPDATE mistakes SET description = ?, version = version + 1 WHERE id = ?",
            (data.description, mistake_id),
        )
    return {**item, "description": data.description, "version": item["version"] + 1}


@app.delete("/api/mistakes/{mistake_id}")
def delete_mistake(mistake_id: int, user=Depends(current_user)):
    # 只删除这一条易错点；同一题下的其他易错点不受影响。
    with connect(write=True) as conn:
        owned_mistake(conn, mistake_id, user["id"])
        conn.execute("DELETE FROM mistakes WHERE id = ?", (mistake_id,))
    return {"ok": True}


@app.post("/api/mistakes/{mistake_id}/review")
def review_mistake(
    mistake_id: int,
    data: ReviewInput,
    user=Depends(current_user),
):
    with connect(write=True) as conn:
        item = owned_mistake(conn, mistake_id, user["id"])
        day = today_for(user)

        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["due_date"] > day.isoformat():
            raise HTTPException(409, "这条易错点尚未到期，今天不需要再次评分")

        state = schedule(
            repetitions=item["repetitions"],
            interval_days=item["interval_days"],
            ease_factor=item["ease_factor"],
            quality=data.quality,
            reviewed_on=day,
        )
        reviewed_at = utc_now()

        conn.execute(
            """
            UPDATE mistakes
            SET repetitions = ?, interval_days = ?, ease_factor = ?,
                due_date = ?, last_reviewed_at = ?, version = version + 1
            WHERE id = ?
            """,
            (
                state["repetitions"], state["interval_days"],
                state["ease_factor"], state["due_date"],
                reviewed_at, mistake_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO reviews(
                mistake_id, quality, reviewed_at, next_due_date
            ) VALUES (?, ?, ?, ?)
            """,
            (mistake_id, data.quality, reviewed_at, state["due_date"]),
        )

    return {**state, "version": item["version"] + 1}


@app.post("/api/mistakes/{mistake_id}/variants", status_code=201)
def create_variant(mistake_id: int, user=Depends(current_user)):
    # 配额在短事务内原子扣除，网络请求期间不持有数据库写锁。
    with connect(write=True) as conn:
        item = owned_mistake(conn, mistake_id, user["id"])
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise HTTPException(503, "服务端尚未配置 OpenAI API Key")

        cursor = conn.execute(
            """
            INSERT INTO ai_usage(user_id, day, attempts)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, day) DO UPDATE
            SET attempts = ai_usage.attempts + 1
            WHERE ai_usage.attempts < ?
            """,
            (user["id"], today_for(user).isoformat(), ai_limit()),
        )
        if cursor.rowcount != 1:
            raise HTTPException(429, "今天的 AI 生成次数已用完")

    generated = ai.generate(item)

    with connect(write=True) as conn:
        owned_mistake(conn, mistake_id, user["id"])
        cursor = conn.execute(
            """
            INSERT INTO variants(mistake_id, description, model, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                mistake_id, generated["description"],
                generated["model"], utc_now(),
            ),
        )
        variant = conn.execute(
            "SELECT * FROM variants WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(variant)


@app.put("/api/variants/{variant_id}/result")
def save_variant_result(
    variant_id: int,
    data: VariantResult,
    user=Depends(current_user),
):
    with connect(write=True) as conn:
        owned = conn.execute(
            """
            SELECT v.id
            FROM variants v
            JOIN mistakes m ON m.id = v.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE v.id = ? AND p.user_id = ?
            """,
            (variant_id, user["id"]),
        ).fetchone()
        if owned is None:
            raise HTTPException(404, "变体题不存在")

        conn.execute(
            """
            UPDATE variants
            SET result = ?, answer_code = ?, notes = ?, result_updated_at = ?
            WHERE id = ?
            """,
            (
                data.result, data.answer_code, data.notes,
                utc_now(), variant_id,
            ),
        )
        result = conn.execute(
            "SELECT * FROM variants WHERE id = ?",
            (variant_id,),
        ).fetchone()

    # 保存练习结果不会隐式修改原易错点的复习计划。
    return dict(result)
