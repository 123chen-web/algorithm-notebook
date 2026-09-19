import hashlib
import os
import secrets
import sqlite3
import time
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
from db import ROOT, connect, init_db
from scheduler import schedule, today_in_timezone

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


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(InputModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=10, max_length=128)


class Registration(Credentials):
    invite_code: str = Field(min_length=1, max_length=256)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请填写有效时区，例如 Asia/Shanghai") from None
        return value


class NewProblem(InputModel):
    title: Title
    language: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ]
    code: str = Field(min_length=1, max_length=40000)
    thinking: ThinkingText
    mistakes: list[MistakeText] = Field(min_length=1, max_length=10)

    @field_validator("code")
    @classmethod
    def nonempty_code(cls, value):
        if not value.strip():
            raise ValueError("代码不能为空")
        # 保留原始缩进。
        return value


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
            SELECT u.id, u.username, u.timezone
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
def register(data: Registration, response: Response):
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
                INSERT INTO users(username, password_hash, timezone, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (data.username.lower(), hashed, data.timezone, utc_now()),
            )
            user_id = cursor.lastrowid
            set_session(conn, user_id, response)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "用户名已被使用") from None

    return {"id": user_id, "username": data.username.lower()}


@app.post("/api/auth/login")
def login(data: Credentials, response: Response):
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
