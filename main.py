import hashlib
import io
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

import ai
import mailer
import payments
from db import ROOT, connect, init_db
from scheduler import schedule, today_in_timezone

logger = logging.getLogger("algorithm_notebook")

SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000
DUMMY_PASSWORD = "0" * 32 + ":" + "0" * 64

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
# 错因描述现在是可选的：留空时由 AI 在生成练习题时自动诊断并回填，
# 不再要求用户先自己说清楚错在哪。
MistakeText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000)
]
ThinkingText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

# 题目分区：先给一版常见方向，后续要增删分区目前需要改这里。
# CODE_ZONES 要求填写编程语言，且"当时的代码"就是字面意义的代码；
# 数学类分区没有编程语言，"当时的代码"是解题过程/演算，字段名不变但语义更宽。
CODE_ZONES = ("算法", "前端", "后端", "数据库", "系统设计")
NON_CODE_ZONES = ("高等数学", "线性代数", "概率统计")
PROBLEM_ZONES = CODE_ZONES + NON_CODE_ZONES

# 头像：只接受这几种真实解码出来的格式（不看文件名后缀或请求头，
# 防止伪装成图片的其他文件类型）；上传后统一重新编码成正方形 JPEG，
# 顺带清掉原图可能带的 EXIF 等元数据、绝不直接保存用户上传的原始字节。
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_SIZE = 256
ALLOWED_AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}

# 简单校验即可：真正确认邮箱能收到信，靠的是密码找回时能不能收到邮件，
# 而不是注册时的格式检查，所以没有引入额外的邮箱校验依赖。
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(InputModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=6, max_length=128)


def normalize_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("请填写有效时区，例如 Asia/Shanghai") from None
    return value


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
        return normalize_timezone(value)


class TrialSignup(InputModel):
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        return normalize_timezone(value)


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
    password: str = Field(min_length=6, max_length=128)


class EmailUpdate(InputModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class ProblemFields(InputModel):
    title: Title
    zone: str
    # 数学类分区没有编程语言，允许留空；是否必填由下面的整体校验按分区判断。
    language: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=40)
    ] = ""
    code: str = Field(min_length=1, max_length=40000)
    thinking: ThinkingText

    @field_validator("zone")
    @classmethod
    def valid_zone(cls, value):
        if value not in PROBLEM_ZONES:
            raise ValueError("请选择一个有效的题目分区")
        return value

    @model_validator(mode="after")
    def language_required_for_code_zones(self):
        if self.zone in CODE_ZONES and not self.language:
            raise ValueError("这个分区需要填写编程语言")
        return self

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


class NewOrder(InputModel):
    plan_id: int = Field(strict=True, gt=0)
    channel: Literal["alipay", "wechat"]


class PostFields(InputModel):
    title: PostTitle
    body: PostBody


class NewPost(PostFields):
    pass


class PostEdit(PostFields):
    pass


class NewComment(InputModel):
    body: CommentBody


class CommentEdit(InputModel):
    body: CommentBody


class ReportInput(InputModel):
    reason: str = Field(default="", max_length=500)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_for(user):
    return today_in_timezone(user["timezone"])


def current_streak(review_dates, today):
    # 今天还没打卡但昨天打卡了，连续天数按"还没断"算，不因为今天没过完就清零。
    cursor = today
    if cursor not in review_dates:
        cursor -= timedelta(days=1)
        if cursor not in review_dates:
            return 0
    streak = 0
    while cursor in review_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def ai_limit():
    return max(1, int(os.getenv("AI_DAILY_LIMIT", "10")))


def trial_ai_limit():
    # 体验账号任何人都能开，额度要远低于正式账号，否则等于把
    # AI_DAILY_LIMIT 变成"任何人每天可用次数 × 无限个体验账号"。
    return max(0, int(os.getenv("TRIAL_AI_DAILY_LIMIT", "2")))


def ai_quota(conn, user_id, day):
    # 生成请求必须传入已取得写锁的连接，不能使用鉴权阶段读到的套餐快照。
    # 单条查询也让 /api/me 的套餐信息和当天已用次数来自同一数据库快照。
    row = conn.execute(
        """
        SELECT u.is_trial, u.plan_id, u.plan_expires_at,
               p.name AS plan_name, p.ai_daily_limit AS plan_limit,
               COALESCE(a.attempts, 0) AS ai_daily_used
        FROM users u
        LEFT JOIN plans p ON p.id = u.plan_id
        LEFT JOIN ai_usage a ON a.user_id = u.id AND a.day = ?
        WHERE u.id = ?
        """,
        (day, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")

    # 到期时间按 UTC 时刻判断；用户时区只用于 day，不参与套餐有效期判断。
    now = datetime.now(timezone.utc)
    plan_active = bool(
        not row["is_trial"]
        and row["plan_id"] is not None
        and row["plan_expires_at"]
        and datetime.fromisoformat(row["plan_expires_at"]) > now
    )
    if row["is_trial"]:
        limit = trial_ai_limit()
    elif plan_active:
        limit = row["plan_limit"]
    else:
        limit = ai_limit()
    return {
        "is_trial": bool(row["is_trial"]),
        "plan_id": row["plan_id"],
        "plan_name": row["plan_name"],
        "plan_expires_at": row["plan_expires_at"],
        "plan_active": plan_active,
        "ai_daily_limit": limit,
        "ai_daily_used": row["ai_daily_used"],
        "ai_daily_remaining": max(0, limit - row["ai_daily_used"]),
    }


def public_base_url():
    # 拼重置密码链接用；本地开发默认指向 uvicorn 监听的地址，
    # 部署上线后要在 .env 里改成真实域名，否则邮件里的链接打不开。
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def avatar_dir():
    # 相对路径以项目目录为基准，跟 DATABASE_PATH 的解析方式一致；
    # 放在 data/ 下面，备份时复制整个 data 目录就会一起带上。
    path = Path(os.getenv("AVATAR_DIR", "data/avatars")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def avatar_path(user_id):
    return avatar_dir() / f"{user_id}.jpg"


def decode_avatar_image(content: bytes) -> Image.Image:
    # verify() 只检查文件没有损坏，之后这个 Image 对象不能再用来处理，
    # 必须从同一份字节重新 open 一次；再调用 load() 强制完整解码，
    # 防止头部合法但数据被截断的文件绕过 verify()。
    try:
        Image.open(io.BytesIO(content)).verify()
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        # DecompressionBombError：Pillow 按文件头声明的像素数提前拒绝，
        # 不会真的去解码一个几万乘几万像素的图片撑爆内存；这里只是把它
        # 也归为"文件不是有效的图片"，不让它变成一个裸的 500。
        raise HTTPException(400, "文件不是有效的图片") from None
    if image.format not in ALLOWED_AVATAR_FORMATS:
        raise HTTPException(400, "只支持 JPEG、PNG 或 WebP 格式的图片")
    return image


def resize_avatar_to_square_jpeg(image: Image.Image) -> bytes:
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image = image.resize((AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


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
            SELECT u.id, u.username, u.email, u.timezone, u.is_trial,
                   u.plan_id, u.plan_expires_at, u.is_banned, u.avatar_version
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
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
    # 单管理员账号：由环境变量指定用户名，不需要额外的数据库列或登录方式。
    admin_username = os.getenv("ADMIN_USERNAME", "").strip().lower()
    user["is_admin"] = bool(admin_username) and user["username"] == admin_username
    return user


def require_admin(user):
    if not user["is_admin"]:
        raise HTTPException(403, "需要管理员权限")


MISTAKE_SELECT = """
SELECT m.*, p.title, p.zone, p.language, p.code, p.thinking
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
TRIAL_LIMIT = 3
TRIAL_WINDOW_SECONDS = 60 * 60
LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 15 * 60
FORGOT_PASSWORD_LIMIT = 5
FORGOT_PASSWORD_WINDOW_SECONDS = 15 * 60
RESET_PASSWORD_LIMIT = 10
RESET_PASSWORD_WINDOW_SECONDS = 15 * 60
RESET_TOKEN_SECONDS = 30 * 60
# 举报是登录后的操作，按 user_id 限流比按 IP 更准（不会误伤同一 IP 下的其他人）。
REPORT_LIMIT = 10
REPORT_WINDOW_SECONDS = 60 * 60

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
    # 支付宝/微信支付服务器的通知不携带浏览器请求头，由渠道各自的验签保护。
    provider_notification = request.method == "POST" and request.url.path in (
        "/api/payments/alipay/callback",
        "/api/payments/wechat/callback",
    )
    if (
        request.url.path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("X-CSRF-Protection") != "1"
        and not provider_notification
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
    # 入口文档不能被浏览器无条件缓存：它引用的 CSS/JS 靠 ?v= 查询参数
    # 手动失效，但前提是浏览器每次都真的重新请求这份 HTML 去看新的
    # ?v= 号。no-cache 允许缓存副本，但强制每次先用 ETag 向服务端验证，
    # 没变就是很快的 304，变了才重新下载，不会让用户长期卡在旧版本。
    return FileResponse(
        ROOT / "static" / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


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


@app.post("/api/auth/trial", status_code=201)
def create_trial_account(data: TrialSignup, request: Request, response: Response):
    # 免邀请码的体验账号：任何人都能触发，靠 IP 限流控制建号速度，
    # 靠 is_trial 标记单独限制 AI 调用额度（见 trial_ai_limit）。
    if rate_limited(
        f"trial:{client_ip(request)}", TRIAL_LIMIT, TRIAL_WINDOW_SECONDS
    ):
        raise HTTPException(429, "体验账号创建过于频繁，请稍后再试")

    hashed = password_hash(secrets.token_urlsafe(24))
    for _ in range(5):
        username = f"trial_{secrets.token_hex(6)}"
        try:
            with connect(write=True) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users(
                        username, password_hash, email, timezone,
                        created_at, is_trial
                    ) VALUES (?, ?, NULL, ?, ?, 1)
                    """,
                    (username, hashed, data.timezone, utc_now()),
                )
                user_id = cursor.lastrowid
                set_session(conn, user_id, response)
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise HTTPException(503, "暂时无法创建体验账号，请稍后再试")

    return {"id": user_id, "username": username}


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
    # 封禁检查放在密码校验通过之后，避免向未认证的调用方泄露
    # "这个用户名存在且被封禁" 这类额外信息。
    if user["is_banned"]:
        raise HTTPException(403, "账号已被封禁，无法登录")

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
    with connect() as conn:
        quota = ai_quota(conn, user["id"], today_for(user).isoformat())
    return {
        **user,
        **quota,
        "today": today_for(user).isoformat(),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY", "").strip()),
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


@app.post("/api/me/avatar")
async def upload_avatar(user=Depends(current_user), file: UploadFile = File(...)):
    require_not_trial(user, "上传头像")
    content = await file.read(AVATAR_MAX_BYTES + 1)
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "图片太大，最多 2MB")
    if not content:
        raise HTTPException(400, "文件是空的")

    image = decode_avatar_image(content)
    jpeg_bytes = await run_in_threadpool(resize_avatar_to_square_jpeg, image)

    directory = avatar_dir()
    final_path = avatar_path(user["id"])
    # 先写临时文件再原子替换，避免另一个请求读到写了一半的文件。
    temp_path = directory / f".{user['id']}-{secrets.token_hex(8)}.tmp"
    temp_path.write_bytes(jpeg_bytes)
    os.replace(temp_path, final_path)

    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ?",
            (user["id"],),
        )
        avatar_version = conn.execute(
            "SELECT avatar_version FROM users WHERE id = ?", (user["id"],)
        ).fetchone()["avatar_version"]
    return {"ok": True, "avatar_version": avatar_version}


@app.delete("/api/me/avatar")
def delete_own_avatar(user=Depends(current_user)):
    avatar_path(user["id"]).unlink(missing_ok=True)
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ?",
            (user["id"],),
        )
        avatar_version = conn.execute(
            "SELECT avatar_version FROM users WHERE id = ?", (user["id"],)
        ).fetchone()["avatar_version"]
    return {"ok": True, "avatar_version": avatar_version}


@app.get("/api/users/{user_id}/avatar")
def get_avatar(user_id: int, user=Depends(current_user)):
    path = avatar_path(user_id)
    if not path.is_file():
        raise HTTPException(404, "这个用户还没有头像")
    # URL 本身不带版本号；前端用 ?v=avatar_version 做缓存失效，
    # 这里可以放心用较长的缓存时间。
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"}
    )


@app.get("/api/plans")
def list_plans(user=Depends(current_user)):
    return {"plans": payments.list_plans()}


@app.post("/api/orders", status_code=201)
def create_order(data: NewOrder, user=Depends(current_user)):
    return payments.create_order(user["id"], data.plan_id, data.channel)


@app.get("/api/orders/{order_id}")
def get_order(order_id: str, user=Depends(current_user)):
    return {"order": payments.get_order(user["id"], order_id)}


@app.get("/api/orders")
def list_orders(user=Depends(current_user)):
    return {"orders": payments.list_orders(user["id"])}


@app.post("/api/orders/{order_id}/refund")
def refund_order(order_id: str, user=Depends(current_user)):
    return {"order": payments.refund_order(user["id"], order_id)}


@app.post("/api/payments/mock/{channel}/callback")
async def mock_payment_callback(
    channel: Literal["alipay", "wechat"],
    request: Request,
    user=Depends(current_user),
):
    if os.getenv("PAYMENTS_MOCK_ENABLED", "0") != "1":
        raise HTTPException(404, "接口不存在")
    raw_body = await request.body()
    order = await run_in_threadpool(
        payments.handle_callback,
        channel,
        raw_body,
        request.headers,
        user_id=user["id"],
    )
    return {"ok": True, "order": order}


@app.post("/api/payments/alipay/callback")
async def alipay_payment_callback(request: Request):
    # 公开通知入口绝不能在本地 mock 模式下接收 HMAC 回调。
    if os.getenv("PAYMENTS_MOCK_ENABLED", "0") == "1":
        return PlainTextResponse("failure", status_code=404)
    raw_body = await request.body()
    try:
        await run_in_threadpool(
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


@app.post("/api/payments/wechat/callback")
async def wechat_payment_callback(request: Request):
    # 公开通知入口绝不能在本地 mock 模式下接收 AEAD 回调。
    if os.getenv("PAYMENTS_MOCK_ENABLED", "0") == "1":
        return JSONResponse({"code": "FAILED", "message": "失败"}, status_code=404)
    raw_body = await request.body()
    try:
        await run_in_threadpool(
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


@app.get("/api/zones")
def list_zones():
    return {"zones": list(PROBLEM_ZONES), "code_zones": list(CODE_ZONES)}


@app.post("/api/problems", status_code=201)
def create_problem(data: NewProblem, user=Depends(current_user)):
    day = today_for(user).isoformat()
    with connect(write=True) as conn:
        cursor = conn.execute(
            """
            INSERT INTO problems(
                user_id, title, zone, language, code, thinking, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], data.title, data.zone, data.language,
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
            SET title = ?, zone = ?, language = ?, code = ?, thinking = ?
            WHERE id = ?
            """,
            (
                data.title, data.zone, data.language,
                data.code, data.thinking, problem_id,
            ),
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
def list_mistakes(due_only: bool = True, zone: str | None = None, user=Depends(current_user)):
    if zone is not None and zone not in PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    day = today_for(user).isoformat()
    sql = MISTAKE_SELECT + " WHERE p.user_id = ?"
    params = [user["id"]]
    if due_only:
        sql += " AND m.due_date <= ?"
        params.append(day)
    if zone is not None:
        sql += " AND p.zone = ?"
        params.append(zone)
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
    # BEGIN IMMEDIATE 后重读套餐并扣额，与支付回调等写入串行执行。
    # 配额在短事务内原子扣除，网络请求期间不持有数据库写锁。
    with connect(write=True) as conn:
        item = owned_mistake(conn, mistake_id, user["id"])
        if not os.getenv("OPENAI_API_KEY", "").strip():
            raise HTTPException(503, "服务端尚未配置 OpenAI API Key")

        day = today_for(user).isoformat()
        limit = ai_quota(conn, user["id"], day)["ai_daily_limit"]
        cursor = conn.execute(
            """
            INSERT INTO ai_usage(user_id, day, attempts)
            SELECT ?, ?, 1 WHERE ? > 0
            ON CONFLICT(user_id, day) DO UPDATE
            SET attempts = ai_usage.attempts + 1
            WHERE ai_usage.attempts < ?
            """,
            (
                user["id"],
                day,
                limit,
                limit,
            ),
        )
        if cursor.rowcount != 1:
            raise HTTPException(429, "今天的 AI 生成次数已用完")

    generated = ai.generate(item)

    with connect(write=True) as conn:
        # 重新读取当前错因：生成期间用户可能已经自己编辑过，不能用生成前的
        # 旧快照来判断是否需要回填，否则可能覆盖掉用户刚写的内容。
        current = owned_mistake(conn, mistake_id, user["id"])
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
        if not current["description"].strip():
            conn.execute(
                "UPDATE mistakes SET description = ? WHERE id = ?",
                (generated["mistake_summary"], mistake_id),
            )
            current["description"] = generated["mistake_summary"]
        variant = conn.execute(
            "SELECT * FROM variants WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return {**dict(variant), "mistake_description": current["description"]}


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


LEADERBOARD_SIZE = 50


@app.get("/api/leaderboard")
def leaderboard(user=Depends(current_user)):
    with connect() as conn:
        users = conn.execute("SELECT id, timezone, is_trial FROM users").fetchall()
        review_rows = conn.execute(
            """
            SELECT p.user_id AS user_id, r.reviewed_at
            FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            """
        ).fetchall()

    timezones = {row["id"]: row["timezone"] for row in users}
    review_dates = defaultdict(set)
    for row in review_rows:
        tz = timezones.get(row["user_id"])
        if tz is None:
            continue
        reviewed_at = datetime.fromisoformat(row["reviewed_at"])
        review_dates[row["user_id"]].add(today_in_timezone(tz, reviewed_at))

    streaks = {}
    for row in users:
        streaks[row["id"]] = current_streak(
            review_dates.get(row["id"], set()),
            today_for(row),
        )

    # 体验账号不参与排行榜——跟体验账号能看 /api/plans 但不能真的下单是
    # 同一种"能看不能上榜"的模式；已排除的账号不占用前 LEADERBOARD_SIZE 名额。
    eligible = sorted(
        (row for row in users if not row["is_trial"] and streaks[row["id"]] >= 1),
        key=lambda row: (-streaks[row["id"]], row["id"]),
    )

    entries = [
        {
            "rank": index + 1,
            # 匿名标识，不带出真实 username；跟 rank 是两个独立字段，
            # 数字含义不同，不能把标识里的号码当成排名。
            "display_name": f"用户 #{row['id']}",
            "streak_days": streaks[row["id"]],
        }
        for index, row in enumerate(eligible[:LEADERBOARD_SIZE])
    ]

    my_rank = None
    if not user["is_trial"] and streaks[user["id"]] >= 1:
        for index, row in enumerate(eligible):
            if row["id"] == user["id"]:
                my_rank = index + 1
                break

    return {
        "entries": entries,
        "leaderboard_size": LEADERBOARD_SIZE,
        "me": {
            "streak_days": streaks[user["id"]],
            "rank": my_rank,
            "is_trial": user["is_trial"],
        },
    }


POST_LIST_LIMIT = 100


def require_not_trial(user, action):
    if user["is_trial"]:
        raise HTTPException(403, f"体验账号不支持{action}")


def visible_post(conn, post_id):
    row = conn.execute(
        "SELECT * FROM posts WHERE id = ? AND deleted_at IS NULL",
        (post_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "帖子不存在")
    return dict(row)


def owned_post(conn, post_id, user_id):
    post = visible_post(conn, post_id)
    if post["user_id"] != user_id:
        raise HTTPException(404, "帖子不存在")
    return post


def visible_comment(conn, comment_id):
    row = conn.execute(
        "SELECT * FROM post_comments WHERE id = ? AND deleted_at IS NULL",
        (comment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "评论不存在")
    return dict(row)


def owned_comment(conn, comment_id, user_id):
    comment = visible_comment(conn, comment_id)
    if comment["user_id"] != user_id:
        raise HTTPException(404, "评论不存在")
    return comment


@app.get("/api/posts")
def list_posts(user=Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.title, p.created_at, p.user_id, u.username,
                   u.avatar_version,
                   (
                       SELECT COUNT(*) FROM post_comments c
                       WHERE c.post_id = p.id AND c.deleted_at IS NULL
                   ) AS comment_count
            FROM posts p
            JOIN users u ON u.id = p.user_id
            WHERE p.deleted_at IS NULL
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (POST_LIST_LIMIT,),
        ).fetchall()
    return {"posts": [dict(row) for row in rows]}


@app.post("/api/posts", status_code=201)
def create_post(data: NewPost, user=Depends(current_user)):
    require_not_trial(user, "发帖")
    with connect(write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO posts(user_id, title, body, created_at) VALUES (?, ?, ?, ?)",
            (user["id"], data.title, data.body, utc_now()),
        )
        row = conn.execute(
            """
            SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


@app.get("/api/posts/{post_id}")
def get_post(post_id: int, user=Depends(current_user)):
    with connect() as conn:
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
        post = dict(row)
        comments = conn.execute(
            """
            SELECT c.id, c.user_id, c.body, c.created_at, c.updated_at,
                   u.username, u.avatar_version
            FROM post_comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.post_id = ? AND c.deleted_at IS NULL
            ORDER BY c.created_at ASC
            """,
            (post_id,),
        ).fetchall()
    post["comments"] = [dict(row) for row in comments]
    return post


@app.put("/api/posts/{post_id}")
def edit_post(post_id: int, data: PostEdit, user=Depends(current_user)):
    require_not_trial(user, "发帖")
    with connect(write=True) as conn:
        owned_post(conn, post_id, user["id"])
        conn.execute(
            "UPDATE posts SET title = ?, body = ?, updated_at = ? WHERE id = ?",
            (data.title, data.body, utc_now(), post_id),
        )
        row = conn.execute(
            """
            SELECT p.*, u.username FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.id = ?
            """,
            (post_id,),
        ).fetchone()
    return dict(row)


@app.delete("/api/posts/{post_id}")
def delete_post(post_id: int, user=Depends(current_user)):
    require_not_trial(user, "发帖")
    with connect(write=True) as conn:
        owned_post(conn, post_id, user["id"])
        # 软删除：标记 deleted_at，不物理删除，也不级联标记这个帖子下的
        # 评论——帖子对所有人不可见之后，正常业务路径本来就到达不了
        # 这些评论，不需要逐条标记。
        conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ?",
            (utc_now(), post_id),
        )
    return {"ok": True}


@app.post("/api/posts/{post_id}/comments", status_code=201)
def create_comment(post_id: int, data: NewComment, user=Depends(current_user)):
    require_not_trial(user, "评论")
    with connect(write=True) as conn:
        visible_post(conn, post_id)
        cursor = conn.execute(
            "INSERT INTO post_comments(post_id, user_id, body, created_at) "
            "VALUES (?, ?, ?, ?)",
            (post_id, user["id"], data.body, utc_now()),
        )
        row = conn.execute(
            """
            SELECT c.*, u.username FROM post_comments c
            JOIN users u ON u.id = c.user_id WHERE c.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


@app.put("/api/comments/{comment_id}")
def edit_comment(comment_id: int, data: CommentEdit, user=Depends(current_user)):
    require_not_trial(user, "评论")
    with connect(write=True) as conn:
        owned_comment(conn, comment_id, user["id"])
        conn.execute(
            "UPDATE post_comments SET body = ?, updated_at = ? WHERE id = ?",
            (data.body, utc_now(), comment_id),
        )
        row = conn.execute(
            """
            SELECT c.*, u.username FROM post_comments c
            JOIN users u ON u.id = c.user_id WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
    return dict(row)


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(current_user)):
    require_not_trial(user, "评论")
    with connect(write=True) as conn:
        owned_comment(conn, comment_id, user["id"])
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?",
            (utc_now(), comment_id),
        )
    return {"ok": True}


def create_report(user, *, post_id=None, comment_id=None, reason):
    require_not_trial(user, "举报")
    if rate_limited(f"report:{user['id']}", REPORT_LIMIT, REPORT_WINDOW_SECONDS):
        raise HTTPException(429, "举报过于频繁，请稍后再试")
    with connect(write=True) as conn:
        target = (
            visible_post(conn, post_id)
            if post_id is not None
            else visible_comment(conn, comment_id)
        )
        if target["user_id"] == user["id"]:
            raise HTTPException(400, "不能举报自己发布的内容")
        try:
            conn.execute(
                """
                INSERT INTO reports(
                    reporter_user_id, post_id, comment_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user["id"], post_id, comment_id, reason, utc_now()),
            )
        except sqlite3.IntegrityError:
            # 部分唯一索引挡住了对同一目标的重复待处理举报。
            raise HTTPException(409, "你已经举报过这条内容，管理员正在处理") from None
    return {"ok": True}


@app.post("/api/posts/{post_id}/report", status_code=201)
def report_post(post_id: int, data: ReportInput, user=Depends(current_user)):
    return create_report(user, post_id=post_id, reason=data.reason)


@app.post("/api/comments/{comment_id}/report", status_code=201)
def report_comment(comment_id: int, data: ReportInput, user=Depends(current_user)):
    return create_report(user, comment_id=comment_id, reason=data.reason)


@app.post("/api/users/{user_id}/avatar/report", status_code=201)
def report_avatar(user_id: int, data: ReportInput, user=Depends(current_user)):
    require_not_trial(user, "举报")
    if user_id == user["id"]:
        raise HTTPException(400, "不能举报自己的头像")
    # 复用和帖子/评论举报同一个限流计数：同一个人短时间内狂发举报，
    # 不管举报的是什么内容，都是同一类滥用。
    if rate_limited(f"report:{user['id']}", REPORT_LIMIT, REPORT_WINDOW_SECONDS):
        raise HTTPException(429, "举报过于频繁，请稍后再试")
    with connect(write=True) as conn:
        target = conn.execute(
            "SELECT id FROM users WHERE id = ?", (user_id,)
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
                (user["id"], user_id, data.reason, utc_now()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "你已经举报过这个头像，管理员正在处理") from None
    return {"ok": True}


def resolve_reports_for(conn, *, post_id=None, comment_id=None):
    now = utc_now()
    if post_id is not None:
        conn.execute(
            "UPDATE reports SET resolved_at = ? WHERE post_id = ? AND resolved_at IS NULL",
            (now, post_id),
        )
    else:
        conn.execute(
            "UPDATE reports SET resolved_at = ? WHERE comment_id = ? AND resolved_at IS NULL",
            (now, comment_id),
        )


@app.get("/api/admin/reports")
def list_reports(user=Depends(current_user)):
    require_admin(user)
    with connect() as conn:
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
            ORDER BY a.created_at ASC
            """
        ).fetchall()
    reports = [{"type": ("post" if row["post_id"] is not None else "comment"), **dict(row)} for row in rows]
    reports += [{"type": "avatar", **dict(row)} for row in avatar_rows]
    reports.sort(key=lambda report: report["created_at"])
    return {"reports": reports}


@app.post("/api/admin/reports/{report_id}/resolve")
def admin_resolve_report(report_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE reports SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (utc_now(), report_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "举报不存在或已处理")
    return {"ok": True}


@app.post("/api/admin/avatar-reports/{report_id}/resolve")
def admin_resolve_avatar_report(report_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE avatar_reports SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL",
            (utc_now(), report_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "举报不存在或已处理")
    return {"ok": True}


@app.delete("/api/admin/posts/{post_id}")
def admin_delete_post(post_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        visible_post(conn, post_id)
        conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ?", (utc_now(), post_id)
        )
        resolve_reports_for(conn, post_id=post_id)
    return {"ok": True}


@app.delete("/api/admin/comments/{comment_id}")
def admin_delete_comment(comment_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        visible_comment(conn, comment_id)
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?",
            (utc_now(), comment_id),
        )
        resolve_reports_for(conn, comment_id=comment_id)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}/avatar")
def admin_clear_avatar(user_id: int, user=Depends(current_user)):
    require_admin(user)
    avatar_path(user_id).unlink(missing_ok=True)
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ?",
            (user_id,),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
        conn.execute(
            """
            UPDATE avatar_reports SET resolved_at = ?
            WHERE avatar_owner_id = ? AND resolved_at IS NULL
            """,
            (utc_now(), user_id),
        )
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/ban")
def admin_ban_user(user_id: int, user=Depends(current_user)):
    require_admin(user)
    if user_id == user["id"]:
        raise HTTPException(400, "不能封禁自己")
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,)
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/unban")
def admin_unban_user(user_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,)
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
    return {"ok": True}
