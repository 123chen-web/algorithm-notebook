import hashlib
import io
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
import warnings
from collections import defaultdict, deque
from contextlib import ExitStack, asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

import ai
import clusters
import hot_problems
import mailer
import manual_claims
import payments
from payments import activate_plan
import rank_board
import rank_cache
import rank_notice
import thread_summary
from achievements import evaluate_achievements
from activity import activity_summary, day_counts
from admin_metrics import PERIOD_CHOICES, compute_metrics as compute_admin_metrics
from ai_limits import ai_slot, release_attempt, track_call
from db import ROOT, connect, init_db, normalize_username, schema_version
from legal import PRODUCT_NAME, TERMS_VERSION, render_legal_page
from group_levels import GroupPointsAccumulator, LEVELS, RULES, level_summary
from learning_stats import current_streak, learning_metrics
from stats_summary import ALLOWED_DAYS as ALLOWED_SUMMARY_DAYS, summary as stats_summary
from scheduler import preview_all, schedule, today_in_timezone
from mastery import mastery_report
from search import search_all
from tags import (
    SUGGESTED_TAGS, TAG_MAX_LENGTH, TAGS_PER_MISTAKE, TagError, normalize_tags,
    replace_tags, tags_for_mistakes, user_tag_counts,
)
from share_card import render_achievement_card
from weekly_recap import weekly_recap

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
PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
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

WEAKNESS_MIN_MISTAKES = 5
WEAKNESS_MAX_MISTAKES = 40
WEAKNESS_RECENT_REVIEWS = 8

# 头像、拍照识别题目都只接受这几种真实解码出来的格式（不看文件名后缀或
# 请求头，防止伪装成图片的其他文件类型）；上传后统一重新编码，顺带清掉
# 原图可能带的 EXIF 等元数据、绝不直接保存或转发用户上传的原始字节。
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_SIZE = 256
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
# 拍照识别：手机拍的一整页手写解题过程可能有好几 MB，上限比头像更宽松；
# 识别前会重新编码压缩，不会把原始大图直接传给 AI。
PHOTO_MAX_BYTES = 8 * 1024 * 1024
PHOTO_MAX_DIMENSION = 1600

# 简单校验即可：真正确认邮箱能收到信，靠的是密码找回时能不能收到邮件，
# 而不是注册时的格式检查，所以没有引入额外的邮箱校验依赖。
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(InputModel):
    # 不再限制字符集（原来只认 ASCII 字母/数字/下划线，中文用户名会被拒绝），
    # 只保留最基本的边界：非空、去掉首尾空白、长度封顶，避免空白串或
    # 超长字符串搞坏列表展示；讨论区渲染用户名走 textContent，不走
    # innerHTML，这里放开字符集不会引入 XSS。
    username: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
    ]
    password: str = Field(min_length=6, max_length=128)


def normalize_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("请填写有效时区，例如 Asia/Shanghai") from None
    return value


class Registration(Credentials):
    password: str = Field(min_length=1, max_length=128)
    accept_terms: bool = False
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
    password: str = Field(min_length=1, max_length=128)


class EmailUpdate(InputModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class UsernameUpdate(InputModel):
    username: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
    ]


class PasswordChange(InputModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AccountDeletion(InputModel):
    password: str = Field(min_length=1, max_length=128)


class NewGroup(InputModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ]


class JoinGroup(InputModel):
    invite_code: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=64)
    ]


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


class MistakeTags(InputModel):
    # 单个标签的字数和每条的个数上限在 tags.normalize_tags 里统一校验并给出中文提示；
    # 这里只挡掉明显离谱的请求体大小。
    tags: list[str] = Field(max_length=50)


class ReviewInput(InputModel):
    quality: int = Field(strict=True, ge=0, le=5)
    version: int = Field(strict=True, ge=0)


class RvbVersionInput(InputModel):
    version: int = Field(strict=True, ge=0)


class RvbSnoozeInput(RvbVersionInput):
    days: StrictInt

    @field_validator("days")
    @classmethod
    def allowed_days(cls, value):
        if value not in (1, 3, 7):
            raise ValueError("推迟天数只能是 1、3 或 7")
        return value


class RvbSettingsInput(InputModel):
    daily_review_cap: Annotated[StrictInt, Field(ge=5, le=200)] | None


class VariantResult(InputModel):
    result: Literal["unattempted", "solved", "partial", "failed"]
    answer_code: str = Field(default="", max_length=40000)
    answer: str = Field(default="", max_length=500)


class NewOrder(InputModel):
    plan_id: int = Field(strict=True, gt=0)
    channel: Literal["alipay", "wechat"]


class RedeemInput(InputModel):
    code: str = Field(strict=True, max_length=64)


class NewRedeemCodes(InputModel):
    plan_id: int = Field(strict=True, gt=0)
    count: int = Field(strict=True, ge=1, le=50)
    days: int | None = Field(default=None, strict=True, ge=1, le=3650)
    note: str = Field(default="", strict=True, max_length=100)
    expires_in_days: int | None = Field(default=None, strict=True, ge=1, le=365)


class ManualGrant(InputModel):
    username: str = Field(strict=True, min_length=1, max_length=128)
    plan_id: int = Field(strict=True, gt=0)
    days: int | None = Field(default=None, strict=True, ge=1, le=3650)


def _plain_text(value):
    # 不含换行、制表符等控制/格式字符，登记内容会原样展示给站长。
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("内容不能包含控制字符")
    return value


class NewManualClaim(InputModel):
    plan_id: int = Field(strict=True, gt=0)
    payer_note: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=60)]
    contact: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, max_length=60)] = ""

    @field_validator("payer_note", "contact")
    @classmethod
    def plain_text(cls, value):
        return _plain_text(value)


class RejectManualClaim(InputModel):
    reason: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=80)]

    @field_validator("reason")
    @classmethod
    def plain_text(cls, value):
        return _plain_text(value)


class ManualPaymentSettings(InputModel):
    enabled: bool = Field(strict=True)
    contact: str = Field(strict=True, max_length=200)


class PostFields(InputModel):
    title: PostTitle
    body: PostBody
    # 分区是否合法在接口里检查（400“分区不存在”）；省略或 null 表示未分区。
    zone: str | None = None


class NewPost(PostFields):
    pass


class PostEdit(PostFields):
    pass


class NewComment(InputModel):
    body: CommentBody
    reply_to_id: Annotated[StrictInt, Field(gt=0)] | None = None


class CommentEdit(InputModel):
    body: CommentBody


class AcceptedComment(InputModel):
    comment_id: StrictInt


class ReportInput(InputModel):
    reason: str = Field(default="", max_length=500)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_for(user):
    return today_in_timezone(user["timezone"])


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
        WHERE u.id = ? AND u.deleted_at IS NULL
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


def decode_uploaded_image(content: bytes) -> Image.Image:
    # verify() 只检查文件没有损坏，之后这个 Image 对象不能再用来处理，
    # 必须从同一份字节重新 open 一次；再调用 load() 强制完整解码，
    # 防止头部合法但数据被截断的文件绕过 verify()。
    try:
        Image.open(io.BytesIO(content)).verify()
        image = Image.open(io.BytesIO(content))
        image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        # SyntaxError: Pillow reports broken PNG checksums with this exception.
        # DecompressionBombError：Pillow 按文件头声明的像素数提前拒绝，
        # 不会真的去解码一个几万乘几万像素的图片撑爆内存；这里只是把它
        # 也归为"文件不是有效的图片"，不让它变成一个裸的 500。
        raise HTTPException(400, "文件不是有效的图片") from None
    if image.format not in ALLOWED_IMAGE_FORMATS:
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


def resize_photo_for_recognition(image: Image.Image) -> bytes:
    # 保留原始长宽比（不像头像那样裁成正方形），只在图片过大时按最长边
    # 缩小；手写题目和代码要保持完整可读，裁剪会丢内容。
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    width, height = image.size
    longest = max(width, height)
    if longest > PHOTO_MAX_DIMENSION:
        scale = PHOTO_MAX_DIMENSION / longest
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
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


COMMON_PASSWORDS = frozenset("""
12345678 123456789 1234567890 password password1 password123 qwertyui qwerty123
iloveyou abc12345 11111111 00000000 1q2w3e4r admin123 letmein1 welcome1
12341234 12344321 87654321 987654321 01234567 0123456789 123456abc abcdefgh
abcdefghij qwertyuiop asdfghjk asdfghjkl zxcvbnmm zxcvbnm1 zxcvbnm123
asdf1234 abcd1234 1234abcd 1qaz2wsx 1qazxsw2 1q2w3e4r5t 1q2w3e4r5t6y
qazwsxed qazwsx123 zaq12wsx qazwsx12 password12 password1234 password12345
password! password01 passw0rd passw0rd1 p@ssword p@ssw0rd p@ssw0rd1
changeme changeme1 letmein123 welcome123 welcome1234 welcome! admin1234
admin12345 administrator root1234 root12345 secret123 sunshine sunshine1
princess princess1 football football1 baseball baseball1 basketball superman
superman1 batman123 dragon123 michael1 jennifer computer computer1 internet
internet1 whatever whatever1 trustno1 freedom1 hello123 hello1234 hello12345
test1234 test12345 testing123 guest123 guest1234 login123 login1234 default1
88888888 66666666 99999999 22222222 33333333 44444444 55555555 77777777
12121212 123123123 11223344 1122334455 123456a1 123456q1 123456qq woaini520
woaini1314 nihao123 nihao1234 woaini123 52013145 1314520a 65432100 15975300
14725836 123456789a 123456789! 12345678a 12345678! 12345678910 20202020
""".split())


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


RESERVED_USERNAMES = frozenset({
    "admin", "administrator", "root", "system", "support", "official",
    "moderator", "staff", "官方", "管理员", "客服", "系统", "站长", "版主",
    "欧叶", "欧叶oy", "算法错题本",
})


def configured_admin_username():
    value = os.getenv("ADMIN_USERNAME", "")
    if not value.strip():
        return ""
    return normalize_username(value)


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


def bootstrap_admin():
    username = configured_admin_username()
    if not username:
        return
    with connect(write=True) as conn:
        if conn.execute(
            "SELECT 1 FROM users WHERE is_admin = 1 AND deleted_at IS NULL LIMIT 1"
        ).fetchone():
            return
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? AND deleted_at IS NULL AND is_trial = 0",
            (username,),
        ).fetchone()
        if existing is not None:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (existing["id"],))
            return
        rows = conn.execute(
            "SELECT id, username FROM users WHERE deleted_at IS NULL AND is_trial = 0 ORDER BY id"
        ).fetchall()
        for row in rows:
            try:
                candidate = normalize_username(row["username"])
            except ValueError:
                continue
            if candidate == username:
                conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (row["id"],))
                break


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
                   u.plan_id, u.plan_expires_at, u.is_banned, u.avatar_version,
                   u.is_admin, u.deleted_at, u.public_rank_opt_out
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
    return user


def require_admin(user):
    if not user["is_admin"]:
        raise HTTPException(403, "需要管理员权限")


MISTAKE_SELECT = """
SELECT m.*, p.title, p.zone, p.language, p.code, p.thinking
FROM mistakes m
JOIN problems p ON p.id = m.problem_id
"""


# 常规练习接口不再读取或返回 notes；数据导出仍保留这部分旧笔记。
VARIANT_SELECT = """
SELECT v.id, v.mistake_id, v.description, v.model, v.created_at,
       v.result, v.answer_code, v.answer, v.expected_answer, v.result_updated_at
FROM variants v
"""


def redact_pending_answer(variant):
    # 还没提交过练习结果的变体，标准答案只用来告诉前端"这道题能不能自动判对
    # 错"（真假即可），具体文字要等提交之后 save_variant_result 的响应才
    # 给出，否则打开网络面板就能在动手之前看到答案，等于白设计这个校验。
    if variant["expected_answer"] and not variant["result_updated_at"]:
        variant["expected_answer"] = "***"
    return variant


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
GROUP_JOIN_LIMIT = 10
GROUP_JOIN_WINDOW_SECONDS = 15 * 60
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
    bootstrap_admin()
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
        "connect-src 'self'; img-src 'self' blob:; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    manual_qr_response = (
        request.method == "GET" and response.status_code == 200
        and request.url.path in (
            "/api/manual-payment/qr/alipay", "/api/manual-payment/qr/wechat"
        )
    )
    if request.url.path.startswith("/api/") and not manual_qr_response:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz():
    headers = {"Cache-Control": "no-store"}
    try:
        with connect(create=False) as conn:
            conn.execute("SELECT 1").fetchone()
            version = schema_version(conn)
        with connect(write=True, create=False):
            pass
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"}, headers=headers)
    return JSONResponse(content={"status": "ok", "schema_version": version}, headers=headers)


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


@app.get("/terms", response_class=HTMLResponse)
def terms():
    return HTMLResponse(
        render_legal_page("terms"), headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    return HTMLResponse(
        render_legal_page("privacy"), headers={"Cache-Control": "public, max-age=300"}
    )


@app.post("/api/auth/register", status_code=201)
def register(data: Registration, request: Request, response: Response):
    if rate_limited(
        f"register:{client_ip(request)}", REGISTER_LIMIT, REGISTER_WINDOW_SECONDS
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    if not data.accept_terms:
        raise HTTPException(400, "请先阅读并同意服务条款和隐私政策")
    username = normalized_username(data.username)
    check_new_password(data.password, username=username, email=data.email)

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
            admin_name = configured_admin_username()
            has_admin = conn.execute(
                "SELECT 1 FROM users WHERE is_admin = 1 AND deleted_at IS NULL LIMIT 1"
            ).fetchone() is not None
            is_admin = bool(admin_name and username == admin_name and not has_admin)
            check_username_available(username, allow_admin_name=is_admin)
            now = utc_now()
            cursor = conn.execute(
                """
                INSERT INTO users(username, password_hash, email, timezone, created_at,
                                  is_admin, terms_accepted_at, terms_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username, hashed, data.email, data.timezone, now,
                 int(is_admin), now, TERMS_VERSION),
            )
            user_id = cursor.lastrowid
            set_session(conn, user_id, response)
    except sqlite3.IntegrityError as exc:
        if "users.email" in str(exc):
            raise HTTPException(409, "这个邮箱已经被使用") from None
        raise HTTPException(409, "用户名已被使用") from None

    return {"id": user_id, "username": username}


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

    try:
        username = normalize_username(data.username)
    except ValueError:
        # 旧名字可能在 NFKC 展开后超过新长度上限，仍允许按旧名字登录。
        username = data.username.lower()
    with connect() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL",
            (username,),
        ).fetchone()
        if user is None:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ? AND deleted_at IS NULL",
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
        fresh = conn.execute(
            "SELECT password_hash, is_banned FROM users WHERE id = ? AND deleted_at IS NULL",
            (user["id"],),
        ).fetchone()
        if fresh is None or fresh["password_hash"] != user["password_hash"]:
            raise HTTPException(401, "用户名或密码不正确")
        if fresh["is_banned"]:
            raise HTTPException(403, "账号已被封禁，无法登录")
        set_session(conn, user["id"], response)
    return {"ok": True}


def mask_email(address):
    """日志里只留邮箱的首字符和域名，够排查又不把完整地址写进日志。"""
    name, _, domain = str(address).partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


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


@app.post("/api/auth/forgot-password")
def forgot_password(
    data: ForgotPassword, request: Request, background_tasks: BackgroundTasks
):
    if rate_limited(
        f"forgot:{client_ip(request)}",
        FORGOT_PASSWORD_LIMIT,
        FORGOT_PASSWORD_WINDOW_SECONDS,
    ):
        raise HTTPException(429, "尝试次数过多，请稍后再试")

    email = data.email.strip().lower()
    with connect() as conn:
        user = conn.execute(
            "SELECT id, username FROM users WHERE email = ? AND deleted_at IS NULL", (email,)
        ).fetchone()

    if user is not None:
        token = secrets.token_urlsafe(32)
        with connect(write=True) as conn:
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
                        token_hash(token),
                        user["id"],
                        int(time.time()) + RESET_TOKEN_SECONDS,
                    ),
                )
        # token 已提交；发信放到响应之后的后台任务，SMTP 的耗时或失败都不会
        # 拖慢响应（也避免"邮箱存在时更慢"这种计时侧信道），更不会回滚 token。
        if user is not None:
            background_tasks.add_task(
                send_password_reset_email, email, user["username"], token
            )

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
            """SELECT r.user_id, r.expires_at, u.username, u.email
               FROM password_resets r JOIN users u ON u.id = r.user_id
               WHERE r.token_hash = ? AND u.deleted_at IS NULL""",
            (hashed_token,),
        ).fetchone()
    if row is None or row["expires_at"] < int(time.time()):
        raise HTTPException(400, "重置链接无效或已过期，请重新申请")

    check_new_password(data.password, username=row["username"], email=row["email"] or "")
    hashed_password = password_hash(data.password)
    with connect(write=True) as conn:
        # 哈希计算期间 token 可能被使用、替换或过期，必须在写事务中复查。
        row = conn.execute(
            """SELECT r.user_id, r.expires_at, u.username, u.email
               FROM password_resets r JOIN users u ON u.id = r.user_id
               WHERE r.token_hash = ? AND u.deleted_at IS NULL""",
            (hashed_token,),
        ).fetchone()
        if row is None or row["expires_at"] < int(time.time()):
            raise HTTPException(400, "重置链接无效或已过期，请重新申请")
        check_new_password(data.password, username=row["username"], email=row["email"] or "")
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
        "has_avatar": avatar_path(user["id"]).is_file(),
        "today": today_for(user).isoformat(),
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY", "").strip()),
    }


@app.get("/api/export")
def export_data(user=Depends(current_user)):
    # 只导出学习笔记本，显式选择字段，避免账号或后续新增字段意外进入文件。
    with connect() as conn:
        # 四层记录共享只读快照，避免并发编辑/删除时读到不一致的从属关系。
        conn.execute("BEGIN")
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
                   m.interval_days, m.ease_factor, m.due_date, m.last_reviewed_at
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? ORDER BY m.id
            """,
            (user["id"],),
        ):
            mistake = {**dict(row), "reviews": [], "variants": [], "tags": []}
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

    payload = {
        "exported_at": utc_now(),
        "username": user["username"],
        "problems": list(problems.values()),
    }
    filename = f"{PRODUCT_NAME}导出_{user['username']}_{today_for(user).isoformat()}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        },
    )


@app.put("/api/me/email")
def update_email(data: EmailUpdate, user=Depends(current_user)):
    # 在加入 email 列之前注册的老账号没有邮箱，没法用密码找回和复习
    # 提醒；这个接口让已登录用户自己补一个，不用重新注册。
    stored = verify_current_password(user["id"], data.password)
    try:
        with connect(write=True) as conn:
            recheck_account(conn, user["id"], stored)
            conn.execute(
                "UPDATE users SET email = ? WHERE id = ?", (data.email, user["id"])
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "这个邮箱已经被使用") from None
    return {"ok": True, "email": data.email}


@app.put("/api/me/username")
def update_username(data: UsernameUpdate, user=Depends(current_user)):
    require_not_trial(user, "修改用户名")
    username = normalized_username(data.username)
    try:
        with connect(write=True) as conn:
            fresh = recheck_account(conn, user["id"])
            check_username_available(username, allow_admin_name=bool(fresh["is_admin"]))
            conn.execute(
                "UPDATE users SET username = ? WHERE id = ?",
                (username, user["id"]),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "这个用户名已经被使用") from None
    return {"ok": True, "username": username}


def verify_current_password(user_id, password):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    if not password_matches(password, row["password_hash"]):
        raise HTTPException(400, "当前密码不正确")
    return row["password_hash"]


def recheck_account(conn, user_id, expected_hash=None):
    row = conn.execute(
        "SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    if expected_hash is not None and row["password_hash"] != expected_hash:
        raise HTTPException(400, "当前密码不正确")
    return row


def rvb_account(conn, user_id):
    # 登录态检查之后仍可能被注销/封禁；写锁内重读账号，也读取最新时区/偏好。
    user = recheck_account(conn, user_id)
    if user["is_banned"]:
        raise HTTPException(401, "账号已被封禁，无法继续使用")
    return user


@app.get("/api/me/review-settings")
def rvb_get_settings(user=Depends(current_user)):
    with connect() as conn:
        fresh = rvb_account(conn, user["id"])
        return {"daily_review_cap": fresh["daily_review_cap"]}


@app.put("/api/me/review-settings")
def rvb_update_settings(data: RvbSettingsInput, user=Depends(current_user)):
    with connect(write=True) as conn:
        rvb_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET daily_review_cap = ? WHERE id = ?",
            (data.daily_review_cap, user["id"]),
        )
    return {"daily_review_cap": data.daily_review_cap}


def revoke_other_sessions(conn, user_id, request):
    current_token = token_hash(request.cookies.get("session", ""))
    return conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
        (user_id, current_token),
    ).rowcount


@app.post("/api/me/password")
def change_password(data: PasswordChange, request: Request, user=Depends(current_user)):
    require_not_trial(user, "修改密码")
    if rate_limited(f"pwchange:{user['id']}", 5, 15 * 60):
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    stored = verify_current_password(user["id"], data.current_password)
    check_new_password(data.new_password, username=user["username"], email=user["email"] or "")
    if data.new_password == data.current_password:
        raise HTTPException(400, "新密码不能和当前密码相同")
    hashed = password_hash(data.new_password)
    with connect(write=True) as conn:
        fresh = recheck_account(conn, user["id"], stored)
        check_new_password(data.new_password, username=fresh["username"], email=fresh["email"] or "")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (hashed, user["id"])
        )
        revoked = revoke_other_sessions(conn, user["id"], request)
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
    return {"ok": True, "revoked_sessions": revoked}


@app.post("/api/me/sessions/revoke-others")
def revoke_sessions(request: Request, user=Depends(current_user)):
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        revoked = revoke_other_sessions(conn, user["id"], request)
    return {"ok": True, "revoked": revoked}


def require_deletable_account(user):
    if user["is_trial"]:
        raise HTTPException(403, "体验账号到期会自动清理")
    if user["is_admin"]:
        raise HTTPException(403, "管理员账号不能自助注销，请先用 admin_tool.py 撤销管理员身份")


def delete_account_data(conn, user_id, deleted_at):
    groups = conn.execute(
        """
        SELECT g.name FROM study_groups g
        WHERE g.created_by = ? AND EXISTS (
            SELECT 1 FROM study_group_members m
            WHERE m.group_id = g.id AND m.user_id != ?
        ) ORDER BY g.id
        """,
        (user_id, user_id),
    ).fetchall()
    if groups:
        names = "、".join(f"『{row['name']}』" for row in groups[:3])
        if len(groups) > 3:
            names += "等"
        raise HTTPException(409, f"你创建的小组{names}里还有其他成员，请先让成员退出或解散小组")

    for table in ("sessions", "password_resets", "problems", "mistake_tags",
                  "weakness_insights", "mistake_clusters", "ai_usage", "comment_votes",
                  "manual_payment_claims"):
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
    # 论坛按既有规则匿名留存；采纳和摘要不能保留注销前的关联/提炼内容。
    conn.execute(
        "DELETE FROM post_summaries WHERE post_id IN (SELECT id FROM posts WHERE user_id = ?) "
        "OR post_id IN (SELECT post_id FROM post_comments WHERE user_id = ?)",
        (user_id, user_id),
    )
    conn.execute(
        "UPDATE posts SET accepted_comment_id = NULL WHERE user_id = ? "
        "OR accepted_comment_id IN (SELECT id FROM post_comments WHERE user_id = ?)",
        (user_id, user_id),
    )
    conn.execute(
        "DELETE FROM avatar_reports WHERE reporter_user_id = ? OR avatar_owner_id = ?",
        (user_id, user_id),
    )
    conn.execute("UPDATE ai_calls SET user_id = NULL WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM study_group_members WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM study_groups WHERE created_by = ?", (user_id,))
    conn.execute(
        """
        UPDATE users SET username = ?, email = NULL, password_hash = ?,
            avatar_version = 0, last_reminder_sent = NULL, is_admin = 0, deleted_at = ?
        WHERE id = ?
        """,
        (f"已注销用户 #{user_id}", DUMMY_PASSWORD, deleted_at, user_id),
    )


@app.post("/api/me/delete-account")
def delete_account(
    data: AccountDeletion, request: Request, response: Response, user=Depends(current_user)
):
    require_deletable_account(user)
    if rate_limited(f"accdel:{user['id']}", 5, 15 * 60):
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    stored = verify_current_password(user["id"], data.password)
    with connect(write=True) as conn:
        fresh = recheck_account(conn, user["id"], stored)
        require_deletable_account(fresh)
        delete_account_data(conn, user["id"], utc_now())
    rank_cache.invalidate()
    avatar_path(user["id"]).unlink(missing_ok=True)
    response.delete_cookie("session", path="/")
    return {"ok": True}


@app.post("/api/me/avatar")
async def upload_avatar(user=Depends(current_user), file: UploadFile = File(...)):
    require_not_trial(user, "上传头像")
    content = await file.read(AVATAR_MAX_BYTES + 1)
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(413, "图片太大，最多 2MB")
    if not content:
        raise HTTPException(400, "文件是空的")

    image = decode_uploaded_image(content)
    jpeg_bytes = await run_in_threadpool(resize_avatar_to_square_jpeg, image)

    with connect(write=True) as conn:
        # 解码期间账号可能已注销，写锁内复查后才落盘。
        recheck_account(conn, user["id"])
        directory = avatar_dir()
        final_path = avatar_path(user["id"])
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


@app.delete("/api/me/avatar")
def delete_own_avatar(user=Depends(current_user)):
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        avatar_path(user["id"]).unlink(missing_ok=True)
        conn.execute(
            "UPDATE users SET avatar_version = avatar_version + 1 WHERE id = ?",
            (user["id"],),
        )
        avatar_version = conn.execute(
            "SELECT avatar_version FROM users WHERE id = ?", (user["id"],)
        ).fetchone()["avatar_version"]
    return {"ok": True, "avatar_version": avatar_version, "has_avatar": False}


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


REDEEM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
REDEEM_ERROR = "兑换码不正确、已使用或已过期"
PAY_QR_MAX_BYTES = 2 * 1024 * 1024


def normalize_redeem_code(value):
    value = unicodedata.normalize("NFKC", value).upper()
    return "".join(char for char in value if not char.isspace() and char != "-")


def redeem_code_hash(value):
    return hashlib.sha256(normalize_redeem_code(value).encode("utf-8")).hexdigest()


def recheck_manual_account(conn, user_id, *, admin=False):
    fresh = recheck_account(conn, user_id)
    if fresh["is_banned"]:
        raise HTTPException(401, "账号已被封禁，无法继续使用")
    if admin:
        require_admin(fresh)
    return fresh


def manual_plan(conn, plan_id):
    # 已发行的码与已有订单一致，不因套餐停用而失效。
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        raise HTTPException(404, "套餐不存在")
    return plan


def pay_qr_path(channel):
    if channel not in ("alipay", "wechat"):
        raise HTTPException(404, "收款码不存在")
    directory = Path(os.getenv("PAY_QR_DIR", "data/pay-qr")).expanduser()
    if not directory.is_absolute():
        directory = ROOT / directory
    # 路径只取白名单渠道，不读取上传文件名；读取时不创建目录。
    return directory / f"{channel}.png"


def manual_payment_settings(conn):
    settings = dict(conn.execute(
        "SELECT key, value FROM app_settings WHERE key IN (?, ?)",
        ("manual_payment_enabled", "manual_payment_contact"),
    ).fetchall())
    return {
        "enabled": settings.get("manual_payment_enabled", "0") == "1",
        "contact": settings.get("manual_payment_contact", ""),
    }


@app.get("/api/manual-payment")
def get_manual_payment(user=Depends(current_user)):
    with connect() as conn:
        settings = manual_payment_settings(conn)
    return {**settings, "qr": {
        channel: pay_qr_path(channel).is_file() for channel in ("alipay", "wechat")
    }}


@app.get("/api/manual-payment/qr/{channel}")
def get_manual_payment_qr(channel: str, user=Depends(current_user)):
    path = pay_qr_path(channel)
    if not path.is_file():
        raise HTTPException(404, "收款码不存在")
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, max-age=300"}
    )


@app.post("/api/redeem")
def redeem_code(data: RedeemInput, request: Request, user=Depends(current_user)):
    if user["is_trial"]:
        raise HTTPException(403, "体验账号不能兑换，请先注册正式账号")
    # 两个桶分别计数，包含失败尝试，IP 取法与登录一致。
    user_limited = rate_limited(f"redeem:{user['id']}", 10, 3600)
    ip_limited = rate_limited(f"redeem-ip:{client_ip(request)}", 30, 3600)
    if user_limited or ip_limited:
        raise HTTPException(429, "尝试次数过多，请稍后再试")
    code_hash = redeem_code_hash(data.code)
    with connect(write=True) as conn:
        fresh = recheck_manual_account(conn, user["id"])
        if fresh["is_trial"]:
            raise HTTPException(403, "体验账号不能兑换，请先注册正式账号")
        now = utc_now()
        claimed = conn.execute(
            """
            UPDATE redeem_codes SET redeemed_by = ?, redeemed_at = ?
            WHERE code_hash = ? AND redeemed_by IS NULL AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > ?)
            """,
            (user["id"], now, code_hash, now),
        )
        if claimed.rowcount != 1:
            raise HTTPException(400, REDEEM_ERROR)
        code = conn.execute(
            "SELECT plan_id, period_days FROM redeem_codes WHERE code_hash = ?",
            (code_hash,),
        ).fetchone()
        plan = manual_plan(conn, code["plan_id"])
        expiry = activate_plan(conn, user["id"], code["plan_id"], code["period_days"], now)
        return {"plan_name": plan["name"], "period_days": code["period_days"],
                "plan_expires_at": expiry}


@app.post("/api/admin/redeem-codes", status_code=201)
def generate_redeem_codes(data: NewRedeemCodes, user=Depends(current_user)):
    require_admin(user)
    codes = []
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        plan = manual_plan(conn, data.plan_id)
        now = utc_now()
        expires = ((datetime.fromisoformat(now) + timedelta(days=data.expires_in_days))
                   .isoformat(timespec="seconds")) if data.expires_in_days else None
        for _ in range(data.count):
            # 碰撞时重试；明文从不落库、不写日志，仅在这次响应返回。
            for attempt in range(10):
                raw = "".join(secrets.choice(REDEEM_ALPHABET) for _ in range(16))
                try:
                    conn.execute(
                        """
                        INSERT INTO redeem_codes(code_hash, code_hint, plan_id, period_days,
                            note, created_by, created_at, expires_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (redeem_code_hash(raw), raw[-4:], data.plan_id,
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


@app.get("/api/admin/redeem-codes")
def list_redeem_codes(
    status: Literal["all", "unused", "redeemed", "revoked"] = "all",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    user=Depends(current_user),
):
    require_admin(user)
    filters = {
        "all": "1 = 1",
        "unused": "c.redeemed_by IS NULL AND c.revoked_at IS NULL",
        "redeemed": "c.redeemed_by IS NOT NULL",
        "revoked": "c.revoked_at IS NOT NULL",
    }
    with connect() as conn:
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


@app.post("/api/admin/redeem-codes/{code_id}/revoke")
def revoke_redeem_code(code_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        cursor = conn.execute(
            "UPDATE redeem_codes SET revoked_at = ? "
            "WHERE id = ? AND redeemed_by IS NULL AND revoked_at IS NULL",
            (utc_now(), code_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "只能撤销未使用的兑换码")
    return {"ok": True}


@app.post("/api/admin/manual-grant")
def manual_grant(data: ManualGrant, user=Depends(current_user)):
    require_admin(user)
    try:
        username = normalize_username(data.username)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
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
        recheck_manual_account(conn, target["id"])
        if target["is_trial"]:
            raise HTTPException(403, "体验账号不能开通，请先注册正式账号")
        plan = manual_plan(conn, data.plan_id)
        days = data.days if data.days is not None else plan["period_days"]
        now = utc_now()
        expiry = activate_plan(conn, target["id"], data.plan_id, days, now)
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


def encode_pay_qr(content):
    # warning 级别的炸弹也拒绝，复用头像的 verify + 完整 decode 校验。
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = decode_uploaded_image(content)
        except Image.DecompressionBombWarning:
            raise HTTPException(400, "文件不是有效的图片") from None
    if image.format not in ("PNG", "JPEG"):
        image.close()
        raise HTTPException(400, "只支持 PNG 或 JPEG 格式的图片")
    try:
        image.thumbnail((2000, 2000), Image.Resampling.LANCZOS)
        converted = image.convert("RGBA" if "A" in image.getbands() or
                                  "transparency" in image.info else "RGB")
        # 创建纯像素图片，丢弃 EXIF、PNG 文本、ICC 等上传元数据。
        clean = Image.new(converted.mode, converted.size)
        clean.paste(converted)
        buffer = io.BytesIO()
        clean.save(buffer, format="PNG")
        clean.close()
        converted.close()
        return buffer.getvalue()
    finally:
        image.close()


@app.put("/api/admin/manual-payment/qr/{channel}")
async def upload_manual_payment_qr(
    channel: str, user=Depends(current_user), file: UploadFile = File(...),
):
    require_admin(user)
    path = pay_qr_path(channel)
    if file.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(400, "只支持 PNG 或 JPEG 格式的图片")
    content = await file.read(PAY_QR_MAX_BYTES + 1)
    if len(content) > PAY_QR_MAX_BYTES:
        raise HTTPException(413, "图片太大，最多 2MB")
    png = await run_in_threadpool(encode_pay_qr, content)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{channel}-{secrets.token_hex(8)}.tmp"
        try:
            temporary.write_bytes(png)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"ok": True}


@app.delete("/api/admin/manual-payment/qr/{channel}")
def delete_manual_payment_qr(channel: str, user=Depends(current_user)):
    require_admin(user)
    path = pay_qr_path(channel)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        path.unlink(missing_ok=True)
    return {"ok": True}


@app.put("/api/admin/manual-payment/settings")
def update_manual_payment_settings(data: ManualPaymentSettings, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        conn.executemany(
            "INSERT INTO app_settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [("manual_payment_enabled", "1" if data.enabled else "0"),
             ("manual_payment_contact", data.contact)],
        )
    return {"enabled": data.enabled, "contact": data.contact}


def claim_http_error(error):
    return HTTPException(error.status_code, error.detail)


@app.post("/api/manual-claims", status_code=201)
def create_manual_claim(data: NewManualClaim, user=Depends(current_user)):
    if user["is_trial"]:
        raise HTTPException(403, "体验账号不能登记付款，请先注册正式账号")
    with connect(write=True) as conn:
        fresh = recheck_manual_account(conn, user["id"])
        if fresh["is_trial"]:
            raise HTTPException(403, "体验账号不能登记付款，请先注册正式账号")
        try:
            claim = manual_claims.create_claim(
                conn, user["id"], data.plan_id, data.payer_note, data.contact, utc_now()
            )
        except manual_claims.ClaimError as error:
            raise claim_http_error(error) from None
        # 待处理上限通过后才计入每日次数，被拒的提交不占额度。
        if rate_limited(f"manual-claim:{user['id']}", manual_claims.DAILY_SUBMISSIONS, 86400):
            raise HTTPException(429, "今天提交的次数太多了，请明天再试或联系站长")
    return {"claim": claim}


@app.get("/api/manual-claims")
def list_manual_claims(user=Depends(current_user)):
    with connect() as conn:
        return {"claims": manual_claims.list_user_claims(conn, user["id"]),
                "plans": manual_claims.purchasable_plans(conn)}


@app.get("/api/admin/manual-claims")
def admin_list_manual_claims(
    status: Literal["pending", "confirmed", "rejected", "all"] = "pending",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    user=Depends(current_user),
):
    require_admin(user)
    with connect() as conn:
        return manual_claims.list_admin_claims(conn, status, page)


@app.post("/api/admin/manual-claims/{claim_id}/confirm")
def admin_confirm_manual_claim(
    claim_id: int, background_tasks: BackgroundTasks, user=Depends(current_user)
):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        try:
            claim, mail_info = manual_claims.confirm_claim(
                conn, claim_id, user["id"], utc_now()
            )
        except manual_claims.ClaimError as error:
            raise claim_http_error(error) from None
    # 事务提交后才发邮件；SMTP 慢或失败都不影响确认。
    background_tasks.add_task(manual_claims.send_confirmation_email, mail_info)
    return {"claim": claim, "plan_expires_at": mail_info["plan_expires_at"]}


@app.post("/api/admin/manual-claims/{claim_id}/reject")
def admin_reject_manual_claim(
    claim_id: int, data: RejectManualClaim, user=Depends(current_user)
):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        try:
            claim = manual_claims.reject_claim(conn, claim_id, user["id"], data.reason, utc_now())
        except manual_claims.ClaimError as error:
            raise claim_http_error(error) from None
    return {"claim": claim}


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
        recheck_account(conn, user["id"])
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


def weakness_analysis_state(conn, user_id):
    count = conn.execute(
        "SELECT COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id "
        "WHERE p.user_id = ?",
        (user_id,),
    ).fetchone()[0]
    row = conn.execute(
        "SELECT content, created_at FROM weakness_insights WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    insight = (
        {"content": json.loads(row["content"]), "created_at": row["created_at"]}
        if row else None
    )
    if count < WEAKNESS_MIN_MISTAKES:
        status = "insufficient_data"
        message = (
            f"再积累几条错题就能看出真正的规律了。当前有 {count} 条易错点，"
            f"还差 {WEAKNESS_MIN_MISTAKES - count} 条；本次不会调用 AI，也不消耗额度。"
        )
    elif insight is None:
        status = "not_analyzed"
        message = "还没有分析过。让 AI 结合错题和复习历史，找出反复卡住你的根本原因。"
    else:
        status, message = "ready", None
    return {
        "status": status,
        "message": message,
        "minimum_mistakes": WEAKNESS_MIN_MISTAKES,
        "mistake_count": count,
        "insight": insight,
    }


def weakness_analysis_reference(conn, user_id, total_mistakes):
    # 最近的题目优先；同题下的易错点也按 ID 确定顺序，避免 LIMIT 不稳定。
    # 在 SQL 层限制文本长度，空错因才补充原始思路/解法片段，不修改原记录。
    rows = conn.execute(
        """
        SELECT m.id AS mistake_id, m.problem_id, substr(p.title, 1, 200) AS title,
               p.zone, substr(m.description, 1, 1000) AS description, p.created_at,
               substr(p.thinking, 1, 600) AS thinking,
               substr(p.code, 1, 800) AS work_excerpt
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        ORDER BY p.created_at DESC, m.id DESC LIMIT ?
        """,
        (user_id, WEAKNESS_MAX_MISTAKES),
    ).fetchall()
    mistakes = []
    for row in rows:
        item = dict(row)
        if item["description"].strip():
            item.pop("thinking")
            item.pop("work_excerpt")
        item.update(review_count=0, failed_review_count=0, recent_reviews=[])
        mistakes.append(item)
    by_id = {item["mistake_id"]: item for item in mistakes}
    placeholders = ",".join("?" for _ in by_id)
    # 聚合完整复习历史，但只发送最近几次明细，兼顾反复低分与近期改善。
    # 仅查询当前用户名下已经选中的错点 ID，不读取其他用户的复习记录。
    reviews = conn.execute(
        f"""
        WITH ranked AS (
            SELECT id, mistake_id, quality, reviewed_at,
                   COUNT(*) OVER (PARTITION BY mistake_id) AS review_count,
                   SUM(CASE WHEN quality < 3 THEN 1 ELSE 0 END)
                       OVER (PARTITION BY mistake_id) AS failed_review_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY mistake_id ORDER BY reviewed_at DESC, id DESC
                   ) AS recent_rank
            FROM reviews WHERE mistake_id IN ({placeholders})
        )
        SELECT * FROM ranked WHERE recent_rank <= ?
        ORDER BY reviewed_at, id
        """,
        (*by_id, WEAKNESS_RECENT_REVIEWS),
    ).fetchall()
    for row in reviews:
        item = by_id[row["mistake_id"]]
        item["review_count"] = row["review_count"]
        item["failed_review_count"] = row["failed_review_count"]
        item["recent_reviews"].append({
            "quality": row["quality"], "reviewed_at": row["reviewed_at"],
        })
    sample = {
        "mistake_count": len(mistakes),
        "problem_count": len({item["problem_id"] for item in mistakes}),
        "review_count": sum(item["review_count"] for item in mistakes),
        "period_start": min(item["created_at"] for item in mistakes),
        "period_end": max(item["created_at"] for item in mistakes),
    }
    return {"total_mistakes": total_mistakes, "sample": sample, "mistakes": mistakes}


@app.get("/api/insights/weakness-analysis")
def get_weakness_analysis(user=Depends(current_user)):
    with connect() as conn:
        return weakness_analysis_state(conn, user["id"])


@app.post("/api/insights/weakness-analysis")
def create_weakness_analysis(user=Depends(current_user)):
    with ExitStack() as stack:
        with connect(write=True) as conn:
            recheck_account(conn, user["id"])
            state = weakness_analysis_state(conn, user["id"])
            # 先判断数量，哪怕额度已用完或未配置 Key，也只返回积累材料的提示。
            if state["mistake_count"] < WEAKNESS_MIN_MISTAKES:
                return state
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
            reference = weakness_analysis_reference(conn, user["id"], state["mistake_count"])
            # 与生成练习题、拍照识别完全相同的套餐读取及原子扣额 SQL。
            # 失败仍占用次数；在发起外部请求前提交，网络调用不持有写锁。
            day = today_for(user).isoformat()
            quota = ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO ai_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = ai_usage.attempts + 1
                WHERE ai_usage.attempts < ?
                """,
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            # 记录材料快照时间，微秒精度区分同秒请求，旧请求晚完成也不覆盖新快照。
            created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

        with track_call(user["id"], "weakness"):
            content = ai.analyze_weaknesses(reference)
        content["sample"] = reference["sample"]
        by_id = {item["mistake_id"]: item for item in reference["mistakes"]}
        for pattern in content["patterns"]:
            for evidence in pattern["evidence"]:
                source = by_id[evidence["mistake_id"]]
                # 标题和分区由可信的来源映射补齐，不让模型虚构题目归属。
                evidence.update({key: source[key] for key in ("problem_id", "title", "zone")})

        with connect(write=True) as conn:
            recheck_account(conn, user["id"])
            conn.execute(
                """
                INSERT INTO weakness_insights(user_id, content, created_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE
                SET content = excluded.content, created_at = excluded.created_at
                WHERE excluded.created_at >= weakness_insights.created_at
                """,
                (user["id"], json.dumps(content, ensure_ascii=False), created_at),
            )
            return weakness_analysis_state(conn, user["id"])


@app.get("/api/insights/clusters")
def get_mistake_clusters(user=Depends(current_user)):
    with connect() as conn:
        return clusters.cluster_state(conn, user["id"], today_for(user).isoformat())


@app.post("/api/insights/clusters")
def create_mistake_clusters(user=Depends(current_user)):
    return clusters.create_clusters(user, today_for, ai_quota)


GROWTH_RECENT_WINDOW_DAYS = 30
GROWTH_QUIET_THRESHOLD_DAYS = 60


def growth_by_zone(conn, user_id, today):
    # 不用 SQL 日期函数：created_at 是 UTC 时间戳，"今天"要按用户自己的
    # 时区算，两者混在一条 SQL 里容易出偏差；取出来后在 Python 里统一按
    # 日期比较，跟 current_streak() 的思路一致。mistakes 本身没有
    # created_at，用所属 problem 的记录时间代表这条易错点的录入时间——
    # 这跟"薄弱点分析"里 period_start/period_end 的口径一致。
    rows = conn.execute(
        "SELECT p.zone, p.created_at FROM mistakes m JOIN problems p ON p.id = m.problem_id "
        "WHERE p.user_id = ?",
        (user_id,),
    ).fetchall()

    by_zone = {}
    for row in rows:
        created_date = datetime.fromisoformat(row["created_at"]).date()
        by_zone.setdefault(row["zone"], []).append(created_date)

    zones = []
    for zone, dates in by_zone.items():
        most_recent = max(dates)
        days_since_last = (today - most_recent).days
        recent = sum(
            1 for created_date in dates
            if 0 <= (today - created_date).days < GROWTH_RECENT_WINDOW_DAYS
        )
        prior = sum(
            1 for created_date in dates
            if GROWTH_RECENT_WINDOW_DAYS <= (today - created_date).days < 2 * GROWTH_RECENT_WINDOW_DAYS
        )
        zones.append({
            "zone": zone,
            "total_mistakes": len(dates),
            "days_since_last_mistake": days_since_last,
            "recent_30_days": recent,
            "prior_30_days": prior,
            "quiet_streak": recent == 0 and days_since_last >= GROWTH_QUIET_THRESHOLD_DAYS,
        })

    zones.sort(key=lambda item: item["zone"])
    return zones


COMMUNITY_STRUGGLING_THRESHOLD = 3
COMMUNITY_MIN_COHORT = 5
GROUP_WEAKNESS_MIN_COHORT = 3


def weakness_by_zone(conn, user_ids=None, min_cohort=COMMUNITY_MIN_COHORT):
    # 聚合只返回分区计数，不带用户名或错题内容。体验数据不代表真实学习
    # 信号，因此全站和小组都排除；这不影响体验账号加入小组和展示打卡天数。
    # None 表示全站；空集合表示没有成员，不能退回全站统计。
    params = ()
    user_filter = ""
    if user_ids is not None:
        params = tuple(set(user_ids))
        if not params:
            return {}
        user_filter = f" AND u.id IN ({','.join('?' for _ in params)})"
    rows = conn.execute(
        f"""
        SELECT p.zone, COUNT(*) AS mistake_count
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        JOIN users u ON u.id = p.user_id
        WHERE u.is_trial = 0 AND u.deleted_at IS NULL{user_filter}
        GROUP BY p.zone, p.user_id
        """,
        params,
    ).fetchall()
    by_zone = {}
    for row in rows:
        stats = by_zone.setdefault(row["zone"], {"total_users": 0, "struggling_users": 0})
        stats["total_users"] += 1
        if row["mistake_count"] >= COMMUNITY_STRUGGLING_THRESHOLD:
            stats["struggling_users"] += 1

    result = {}
    for zone, stats in by_zone.items():
        total = stats["total_users"]
        result[zone] = {
            "sample_size": total,
            "struggling_ratio": (
                round(stats["struggling_users"] / total, 4)
                if total >= min_cohort else None
            ),
        }
    return result


def community_weakness_by_zone(conn):
    return weakness_by_zone(conn)


@app.get("/api/insights/growth")
def get_growth_insights(user=Depends(current_user)):
    # 纯统计，不调用 AI、不涉及配额——跟"薄弱点分析"是两个独立入口。
    with connect() as conn:
        zones = growth_by_zone(conn, user["id"], today_for(user))
        community = community_weakness_by_zone(conn)
    for zone in zones:
        stats = community.get(zone["zone"], {"sample_size": 0, "struggling_ratio": None})
        zone["community_sample_size"] = stats["sample_size"]
        zone["community_struggling_ratio"] = stats["struggling_ratio"]
    return {"zones": zones}


@app.get("/api/insights/weekly-recap")
def get_weekly_recap(user=Depends(current_user)):
    # 纯统计，不调用 AI、不涉及配额。
    with connect() as conn:
        # 多项统计共享只读快照，避免生成/删除记录时跨查询读到不同版本。
        conn.execute("BEGIN")
        recap = weekly_recap(conn, user["id"], user["timezone"], today_for(user))
    return recap


@app.get("/api/stats/activity")
def get_activity_stats(
    weeks: Annotated[int, Query(ge=1, le=52)] = 26, user=Depends(current_user)
):
    # 热力图用：按用户本地日汇总复习/新增记录；只返回有活动的日子，前端补零。
    # 纯统计，不调用 AI、不涉及配额。
    with connect() as conn:
        conn.execute("BEGIN")
        reviews, records, _ = day_counts(conn, user["id"], user["timezone"])
    return activity_summary(reviews, records, today_for(user), weeks)


@app.get("/api/stats/summary")
def get_stats_summary(days: Annotated[int, Query()] = 30, user=Depends(current_user)):
    # 总览「趋势」区：待复习 / 连续打卡 / 复习次数 / 真实保持率 + 未来 14 天预测。
    # 全部按用户本地日，SQL 分组，纯统计，不调用 AI、不占额度。
    if days not in ALLOWED_SUMMARY_DAYS:
        raise HTTPException(422, "days 只能是 7、30 或 90")
    with connect() as conn:
        conn.execute("BEGIN")
        return stats_summary(conn, user["id"], user["timezone"], today_for(user), days)


@app.get("/api/stats/mastery")
def get_mastery(
    weeks: Annotated[int, Query(ge=4, le=26)] = 12, user=Depends(current_user)
):
    # 掌握度趋势：按分区估算保持率的变化曲线；纯统计，不调用 AI、不占额度。
    with connect() as conn:
        conn.execute("BEGIN")
        return mastery_report(conn, user["id"], user["timezone"], today_for(user), weeks)


def window_total(counter, first, last):
    return sum(count for day, count in counter.items() if first <= day <= last)


@app.get("/api/overview")
def get_overview(user=Depends(current_user)):
    # 侧栏角标和"总览"页一次取齐，避免首页并发十几个请求。纯统计，不调用 AI。
    today = today_for(user)
    day = today.isoformat()
    with connect() as conn:
        conn.execute("BEGIN")
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(m.suspended_at IS NULL AND m.due_date <= :day), 0) AS due,
                   COALESCE(SUM(m.suspended_at IS NULL AND m.due_date < :day), 0) AS overdue
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = :user_id
            """,
            {"day": day, "user_id": user["id"]},
        ).fetchone()
        zone_rows = conn.execute(
            """
            SELECT p.zone, COUNT(*) AS total,
                   COALESCE(SUM(m.suspended_at IS NULL AND m.due_date <= ?), 0) AS due
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ?
            GROUP BY p.zone ORDER BY total DESC, p.zone
            """,
            (day, user["id"]),
        ).fetchall()
        preview_rows = conn.execute(
            MISTAKE_SELECT
            + " WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?"
            " ORDER BY m.due_date ASC, m.id ASC LIMIT 5",
            (user["id"], day),
        ).fetchall()
        review_days, _, mistake_days = day_counts(conn, user["id"], user["timezone"])
        weakness = weakness_analysis_state(conn, user["id"])
        hot = conn.execute(
            """
            SELECT p.id, p.title, p.created_at, u.username,
                   (SELECT COUNT(*) FROM post_comments c
                    WHERE c.post_id = p.id AND c.deleted_at IS NULL) AS comment_count
            FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.deleted_at IS NULL AND p.created_at >= ?
            ORDER BY comment_count DESC, p.created_at DESC, p.id DESC LIMIT 1
            """,
            ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds"),),
        ).fetchone()
    groups = list_groups(user)["groups"]
    groups.sort(key=lambda group: (-group["level"]["points"], group["id"]))
    top_pattern = None
    if weakness["status"] == "ready" and weakness["insight"]["content"].get("patterns"):
        pattern = weakness["insight"]["content"]["patterns"][0]
        top_pattern = {"title": pattern["title"], "confidence": pattern["confidence"]}
    return {
        "today": day,
        "due_count": counts["due"],
        "overdue_count": counts["overdue"],
        "total_mistakes": counts["total"],
        "streak_days": current_streak(set(review_days), today),
        "last7": [(today - timedelta(days=6 - offset)) in review_days for offset in range(7)],
        "zones": [dict(row) for row in zone_rows],
        # 与"本周战报"同一口径：含今天的最近 7 天 vs 紧邻的前 7 天；新增的是易错点条数。
        "week": {
            "reviews": window_total(review_days, today - timedelta(days=6), today),
            "prev_reviews": window_total(review_days, today - timedelta(days=13), today - timedelta(days=7)),
            "records": window_total(mistake_days, today - timedelta(days=6), today),
        },
        "due_preview": [
            {
                "id": row["id"],
                "problem_id": row["problem_id"],
                "title": row["title"],
                "zone": row["zone"],
                "description": row["description"],
                "due_date": row["due_date"],
                "overdue_days": max(0, (today - datetime.fromisoformat(row["due_date"]).date()).days),
                "repetitions": row["repetitions"],
            }
            for row in preview_rows
        ],
        "group_count": len(groups),
        "groups_preview": [
            {
                "id": group["id"],
                "name": group["name"],
                "member_count": group["member_count"],
                "member_limit": group["member_limit"],
                "level": group["level"],
            }
            for group in groups[:3]
        ],
        "weakness": {
            "status": weakness["status"],
            "mistake_count": weakness["mistake_count"],
            "minimum_mistakes": weakness["minimum_mistakes"],
            "top": top_pattern,
        },
        "hot_post": (
            {
                "id": hot["id"],
                "title": hot["title"],
                "comment_count": hot["comment_count"],
                "username": hot["username"],
                "created_at": hot["created_at"],
            }
            if hot is not None and hot["comment_count"] > 0 else None
        ),
    }


@app.get("/api/search")
def global_search(
    q: PostSearchQuery = "", limit: Annotated[int, Query(ge=1, le=20)] = 8,
    user=Depends(current_user),
):
    # Ctrl K 命令面板用：我自己的题目/错因 + 讨论区帖子；纯 LIKE 查询，不调用 AI、不占额度。
    with connect() as conn:
        conn.execute("BEGIN")
        return search_all(conn, user["id"], q, limit)


@app.post("/api/problems/photo")
async def recognize_problem_photo(user=Depends(current_user), file: UploadFile = File(...)):
    # 只识别、不落库：返回结构化字段供前端预填新增记录表单，用户确认后
    # 仍然走 create_problem 那条已有校验路径，这里不重复实现建档逻辑。
    content = await file.read(PHOTO_MAX_BYTES + 1)
    if len(content) > PHOTO_MAX_BYTES:
        raise HTTPException(413, f"图片太大，最多 {PHOTO_MAX_BYTES // (1024 * 1024)} MiB")
    if not content:
        raise HTTPException(400, "文件是空的")

    image = decode_uploaded_image(content)
    jpeg_bytes = await run_in_threadpool(resize_photo_for_recognition, image)

    # 配额检查和扣减跟生成练习题共用同一套逻辑：同一次 BEGIN IMMEDIATE 事务内
    # 原子扣减，调用失败也占用次数；AI 调用本身放到事务外面执行，不在网络
    # 请求期间持有数据库写锁。
    with ExitStack() as stack:
        with connect(write=True) as conn:
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
            day = today_for(user).isoformat()
            quota = ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO ai_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = ai_usage.attempts + 1
                WHERE ai_usage.attempts < ?
                """,
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")

        with track_call(user["id"], "photo"):
            return await run_in_threadpool(ai.recognize_photo, jpeg_bytes)


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
def list_mistakes(
    due_only: bool = True, zone: str | None = None, created_on: date | None = None,
    tag: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None,
    user=Depends(current_user),
):
    if zone is not None and zone not in PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    day = today_for(user).isoformat()
    sql = MISTAKE_SELECT + " WHERE p.user_id = ?"
    params = [user["id"]]
    if due_only:
        sql += " AND m.suspended_at IS NULL AND m.due_date <= ?"
        params.append(day)
    if zone is not None:
        sql += " AND p.zone = ?"
        params.append(zone)
    if tag:
        sql += " AND EXISTS (SELECT 1 FROM mistake_tags t WHERE t.mistake_id = m.id AND t.tag = ?)"
        params.append(tag)

    with connect() as conn:
        if created_on is not None:
            # 侧栏日历按用户本地日筛选"这一天新增的记录"：时区换算只能在 Python 里做。
            problem_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id, created_at FROM problems WHERE user_id = ?", (user["id"],)
                )
                if today_in_timezone(user["timezone"], datetime.fromisoformat(row["created_at"]))
                == created_on
            ]
            sql += " AND p.id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(problem_ids))
        sql += " ORDER BY m.due_date ASC, m.id ASC"
        rows = conn.execute(sql, params).fetchall()
        tags_by_id = tags_for_mistakes(conn, [row["id"] for row in rows])
    return {
        "today": day,
        "items": [{**dict(row), "tags": tags_by_id[row["id"]]} for row in rows],
    }


def rvb_review_queue(items, today, cap, done_today, ignore_cap=False):
    """对已过滤的到期候选排序/截断；不修改输入或调度状态。"""
    ordered = sorted(items, key=lambda item: (
        -max(0, (today - date.fromisoformat(item["due_date"])).days)
        / max(item["interval_days"], 1),
        item["due_date"], item["id"],
    ))
    remaining = None if cap is None or ignore_cap else max(0, cap - done_today)
    selected = ordered if remaining is None else ordered[:remaining]
    return {
        "today": today.isoformat(), "cap": cap, "done_today": done_today,
        "remaining_today": remaining, "total_due": len(ordered),
        "capped": len(ordered) > len(selected), "items": selected,
    }


@app.get("/api/review/queue")
def rvb_get_queue(
    ignore_cap: bool = False, zone: str | None = None,
    tag: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None,
    user=Depends(current_user),
):
    if zone is not None and zone not in PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    with connect() as conn:
        conn.execute("BEGIN")
        fresh = rvb_account(conn, user["id"])
        today = today_for(fresh)
        sql = MISTAKE_SELECT + (
            " WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?"
        )
        params = [user["id"], today.isoformat()]
        if zone is not None:
            sql += " AND p.zone = ?"
            params.append(zone)
        if tag:
            sql += " AND EXISTS (SELECT 1 FROM mistake_tags t WHERE t.mistake_id = m.id AND t.tag = ?)"
            params.append(tag)
        rows = conn.execute(sql, params).fetchall()
        tags_by_id = tags_for_mistakes(conn, [row["id"] for row in rows])
        items = [{**dict(row), "tags": tags_by_id[row["id"]]} for row in rows]
        review_days, _, _ = day_counts(conn, user["id"], fresh["timezone"])
        done_today = review_days.get(today, 0)
    return rvb_review_queue(items, today, fresh["daily_review_cap"], done_today, ignore_cap)


@app.get("/api/tags")
def list_tags(user=Depends(current_user)):
    with connect() as conn:
        counts = user_tag_counts(conn, user["id"])
    return {
        "tags": counts,
        "suggestions": list(SUGGESTED_TAGS),
        "limits": {"per_mistake": TAGS_PER_MISTAKE, "length": TAG_MAX_LENGTH},
    }


@app.put("/api/mistakes/{mistake_id}/tags")
def set_mistake_tags(mistake_id: int, data: MistakeTags, user=Depends(current_user)):
    try:
        tags = normalize_tags(data.tags)
    except TagError as error:
        raise HTTPException(422, str(error)) from None
    with connect(write=True) as conn:
        owned_mistake(conn, mistake_id, user["id"])
        try:
            replace_tags(conn, user["id"], mistake_id, tags)
        except TagError as error:
            raise HTTPException(422, str(error)) from None
    return {"tags": tags}


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
            redact_pending_answer(dict(row))
            for row in conn.execute(
                VARIANT_SELECT + " WHERE v.mistake_id = ? ORDER BY v.id DESC",
                (mistake_id,),
            )
        ]
        item["tags"] = tags_for_mistakes(conn, [mistake_id])[mistake_id]
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
        fresh = rvb_account(conn, user["id"])
        item = owned_mistake(conn, mistake_id, user["id"])
        day = today_for(fresh)

        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["suspended_at"] is not None:
            raise HTTPException(409, "这条易错点已暂停，请先恢复")
        if item["due_date"] > day.isoformat():
            raise HTTPException(409, "这条易错点尚未到期，今天不需要再次评分")

        overdue_days = max(0, (day - date.fromisoformat(item["due_date"])).days)
        state = schedule(
            repetitions=item["repetitions"],
            interval_days=item["interval_days"],
            ease_factor=item["ease_factor"],
            quality=data.quality,
            reviewed_on=day,
            overdue_days=overdue_days,
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
                mistake_id, quality, reviewed_at, next_due_date,
                elapsed_days, scheduled_days, ease_before, repetitions_before,
                due_before, last_reviewed_before, version_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mistake_id, data.quality, reviewed_at, state["due_date"],
                item["interval_days"] + overdue_days, item["interval_days"],
                item["ease_factor"], item["repetitions"],
                item["due_date"], item["last_reviewed_at"], item["version"] + 1,
            ),
        )

    return {**state, "version": item["version"] + 1}


@app.get("/api/mistakes/{mistake_id}/preview")
def rvb_preview(mistake_id: int, user=Depends(current_user)):
    with connect() as conn:
        conn.execute("BEGIN")
        fresh = rvb_account(conn, user["id"])
        item = owned_mistake(conn, mistake_id, user["id"])
        day = today_for(fresh)
        previews = preview_all(
            repetitions=item["repetitions"], interval_days=item["interval_days"],
            ease_factor=item["ease_factor"], reviewed_on=day,
            overdue_days=max(0, (day - date.fromisoformat(item["due_date"])).days),
        )
    return {
        "version": item["version"],
        "previews": {
            quality: {"interval_days": state["interval_days"], "due_date": state["due_date"]}
            for quality, state in previews.items()
        },
    }


@app.post("/api/mistakes/{mistake_id}/review/undo")
def rvb_undo_review(mistake_id: int, data: RvbVersionInput, user=Depends(current_user)):
    # connect(write=True) 持有 BEGIN IMMEDIATE；重读、恢复、删除日志一起提交。
    with connect(write=True) as conn:
        rvb_account(conn, user["id"])
        item = owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        review = conn.execute(
            "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id DESC LIMIT 1",
            (mistake_id,),
        ).fetchone()
        if review is None:
            raise HTTPException(409, "没有可以撤销的评分")
        if review["due_before"] is None:
            raise HTTPException(409, "这次评分太早，不能撤销")
        if datetime.fromisoformat(utc_now()) - datetime.fromisoformat(review["reviewed_at"]) > timedelta(minutes=30):
            raise HTTPException(409, "超过 30 分钟，不能撤销")
        if item["version"] != review["version_after"]:
            raise HTTPException(409, "这条记录之后又被修改过，不能撤销")
        restored = {
            "repetitions": review["repetitions_before"],
            "interval_days": review["scheduled_days"],
            "ease_factor": review["ease_before"],
            "due_date": review["due_before"],
            "last_reviewed_at": review["last_reviewed_before"],
            "version": item["version"] + 1,
        }
        conn.execute(
            """
            UPDATE mistakes SET repetitions = :repetitions, interval_days = :interval_days,
                ease_factor = :ease_factor, due_date = :due_date,
                last_reviewed_at = :last_reviewed_at, version = :version
            WHERE id = :id
            """,
            {**restored, "id": mistake_id},
        )
        conn.execute("DELETE FROM reviews WHERE id = ?", (review["id"],))
    return restored


@app.post("/api/mistakes/{mistake_id}/snooze")
def rvb_snooze(mistake_id: int, data: RvbSnoozeInput, user=Depends(current_user)):
    with connect(write=True) as conn:
        fresh = rvb_account(conn, user["id"])
        item = owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["suspended_at"] is not None:
            raise HTTPException(409, "这条易错点已暂停，请先恢复")
        today = today_for(fresh)
        if item["due_date"] > today.isoformat():
            raise HTTPException(409, "这条还没到期")
        due = (today + timedelta(days=data.days)).isoformat()
        conn.execute(
            "UPDATE mistakes SET due_date = ?, version = version + 1 WHERE id = ?",
            (due, mistake_id),
        )
    return {"due_date": due, "version": item["version"] + 1}


def rvb_set_suspension(mistake_id, data, user, suspended):
    with connect(write=True) as conn:
        rvb_account(conn, user["id"])
        item = owned_mistake(conn, mistake_id, user["id"])
        # 同一请求重试时携带的仍是旧版本；目标状态已达成就直接返回，不再写库。
        if (item["suspended_at"] is not None) == suspended:
            return {"suspended_at": item["suspended_at"], "version": item["version"]}
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        suspended_at = utc_now() if suspended else None
        conn.execute(
            "UPDATE mistakes SET suspended_at = ?, version = version + 1 WHERE id = ?",
            (suspended_at, mistake_id),
        )
    return {"suspended_at": suspended_at, "version": item["version"] + 1}


@app.post("/api/mistakes/{mistake_id}/suspend")
def rvb_suspend(mistake_id: int, data: RvbVersionInput, user=Depends(current_user)):
    return rvb_set_suspension(mistake_id, data, user, True)


@app.post("/api/mistakes/{mistake_id}/unsuspend")
def rvb_unsuspend(mistake_id: int, data: RvbVersionInput, user=Depends(current_user)):
    return rvb_set_suspension(mistake_id, data, user, False)


def mastery_signal(conn, mistake_id):
    # 只看最近几次复习：太久以前的表现代表性不够，用户可能早已改善或退步。
    # 只有 2 次以上评分一致偏低/偏高才给方向性提示；1 次或忽高忽低都太吵，
    # 保持不调整（None），这不是精确科学，宁可保守也不要被噪声带偏。
    rows = conn.execute(
        "SELECT quality FROM reviews WHERE mistake_id = ? "
        "ORDER BY reviewed_at DESC, id DESC LIMIT 3",
        (mistake_id,),
    ).fetchall()
    if len(rows) < 2:
        return None
    qualities = [row["quality"] for row in rows]
    if all(quality < 3 for quality in qualities):
        return "struggling"
    if all(quality >= 4 for quality in qualities):
        return "mastering"
    return None


@app.post("/api/mistakes/{mistake_id}/variants", status_code=201)
def create_variant(mistake_id: int, user=Depends(current_user)):
    # BEGIN IMMEDIATE 后重读套餐并扣额，与支付回调等写入串行执行。
    # 配额在短事务内原子扣除，网络请求期间不持有数据库写锁。
    with ExitStack() as stack:
        with connect(write=True) as conn:
            item = owned_mistake(conn, mistake_id, user["id"])
            # 完全没有复习记录时是 None，generate() 的提示词行为跟之前完全一样；
            # 有反复偏低/偏高的复习历史时才提示 AI 调整新题难度。
            item["mastery_signal"] = mastery_signal(conn, mistake_id)
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")

            day = today_for(user).isoformat()
            quota = ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(ai_slot())
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

        with track_call(user["id"], "variant"):
            generated = ai.generate(item)
        is_code_zone = item["zone"] in CODE_ZONES
        rows = []
        for question in generated["questions"]:
            if is_code_zone:
                description = f"{question['question']}\n\n【样例】\n{question['answer']}"
                expected_answer = ""
            else:
                description = question["question"]
                expected_answer = question["answer"]
            rows.append((description, expected_answer))

        with connect(write=True) as conn:
            # 重新读取当前错因：生成期间用户可能已经自己编辑过，不能用生成前的
            # 旧快照来判断是否需要回填，否则可能覆盖掉用户刚写的内容。
            current = owned_mistake(conn, mistake_id, user["id"])
            variants = []
            created_at = utc_now()
            for description, expected_answer in rows:
                cursor = conn.execute(
                    """
                    INSERT INTO variants(
                        mistake_id, description, model, created_at, expected_answer
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        mistake_id, description, generated["model"],
                        created_at, expected_answer,
                    ),
                )
                created_row = dict(conn.execute(
                    VARIANT_SELECT + " WHERE v.id = ?", (cursor.lastrowid,),
                ).fetchone())
                variants.append(redact_pending_answer(created_row))
            if not current["description"].strip():
                conn.execute(
                    "UPDATE mistakes SET description = ? WHERE id = ?",
                    (generated["mistake_summary"], mistake_id),
                )
                current["description"] = generated["mistake_summary"]
        return {"variants": variants, "mistake_description": current["description"]}


def normalize_math_answer(value: str) -> str:
    # 尽力而为的近似比较：只统一格式和数字项顺序，不处理分数化简、
    # 根号等数学等价形式，也不提供浮点误差容忍或符号运算。
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"[、，;；]", ",", normalized)
    try:
        numbers = sorted(float(part.strip()) for part in normalized.split(","))
    except ValueError:
        return "".join(normalized.split()).lower()
    # 正零与负零数值相同，拼接前统一表示；重复数字仍按原重数保留。
    return ",".join(str(0.0 if number == 0 else number) for number in numbers)


@app.put("/api/variants/{variant_id}/result")
def save_variant_result(
    variant_id: int,
    data: VariantResult,
    user=Depends(current_user),
):
    with connect(write=True) as conn:
        owned = conn.execute(
            """
            SELECT v.id, v.expected_answer
            FROM variants v
            JOIN mistakes m ON m.id = v.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE v.id = ? AND p.user_id = ?
            """,
            (variant_id, user["id"]),
        ).fetchone()
        if owned is None:
            raise HTTPException(404, "变体题不存在")

        result = data.result
        if owned["expected_answer"] and data.answer.strip():
            result = (
                "solved"
                if normalize_math_answer(data.answer)
                == normalize_math_answer(owned["expected_answer"])
                else "failed"
            )

        conn.execute(
            """
            UPDATE variants
            SET result = ?, answer_code = ?, answer = ?, result_updated_at = ?
            WHERE id = ?
            """,
            (
                result, data.answer_code, data.answer,
                utc_now(), variant_id,
            ),
        )
        result = conn.execute(
            VARIANT_SELECT + " WHERE v.id = ?",
            (variant_id,),
        ).fetchone()

    # 保存练习结果不会隐式修改原易错点的复习计划。
    return dict(result)


@app.get("/api/achievements")
def get_achievements(user=Depends(current_user)):
    with connect() as conn:
        # 多项统计共享只读快照，避免生成/删除记录时跨查询读到不同版本。
        conn.execute("BEGIN")
        metrics = learning_metrics(conn, user["id"], user["timezone"], today_for(user))
    return {"metrics": metrics, "achievements": evaluate_achievements(metrics)}


@app.get("/api/achievements/share-card")
def get_achievement_share_card(user=Depends(current_user)):
    today = today_for(user)
    with connect() as conn:
        # 与成就徽章一致，在同一只读快照内汇总所有指标。
        conn.execute("BEGIN")
        metrics = learning_metrics(conn, user["id"], user["timezone"], today)
    achievements = evaluate_achievements(metrics)
    png_bytes = render_achievement_card(user["username"], metrics, achievements, today)
    return Response(content=png_bytes, media_type="image/png")


LEADERBOARD_SIZE = 50


def review_streaks_by_user(users, review_rows):
    # 全站排行榜和小组共用：每个人的复习日期和今天都按自己的时区计算。
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
    return streaks


@app.get("/api/leaderboard")
def leaderboard(user=Depends(current_user)):
    with connect() as conn:
        users = conn.execute(
            "SELECT id, timezone, is_trial, public_rank_opt_out FROM users "
            "WHERE deleted_at IS NULL"
        ).fetchall()
        review_rows = conn.execute(
            """
            SELECT p.user_id AS user_id, r.reviewed_at
            FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            """
        ).fetchall()

    streaks = review_streaks_by_user(users, review_rows)

    # 体验账号不参与排行榜——跟体验账号能看 /api/plans 但不能真的下单是
    # 同一种"能看不能上榜"的模式；已排除的账号不占用前 LEADERBOARD_SIZE 名额。
    # 选择“不参与公开榜单”的用户同样不上榜，但仍能看到自己的连续天数。
    eligible = sorted(
        (
            row for row in users
            if not row["is_trial"] and not row["public_rank_opt_out"]
            and streaks[row["id"]] >= 1
        ),
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
    if not user["is_trial"] and not user["public_rank_opt_out"] and streaks[user["id"]] >= 1:
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


# ---- 榜单页：昨日之星、本周热门题目、今日一条（逻辑在 rank_board / hot_problems / rank_notice） ----


class PublicRankSetting(InputModel):
    participate: StrictBool


@app.put("/api/me/public-rank")
def update_public_rank(data: PublicRankSetting, user=Depends(current_user)):
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET public_rank_opt_out = ? WHERE id = ?",
            (0 if data.participate else 1, user["id"]),
        )
    rank_cache.invalidate()
    return {"participate": data.participate}


@app.get("/api/rank/yesterday")
def rank_yesterday(user=Depends(current_user)):
    return rank_board.yesterday_response(user)


@app.get("/api/rank/hot-problems")
def rank_hot_problems(user=Depends(current_user)):
    return hot_problems.hot_problems_response()


@app.get("/api/rank/notice")
def rank_notice_today(user=Depends(current_user)):
    with connect() as conn:
        return {"notice": rank_notice.current_notice(conn, rank_board.beijing_today())}


@app.get("/api/admin/daily-notices")
def admin_list_daily_notices(user=Depends(current_user)):
    require_admin(user)
    with connect() as conn:
        today = rank_board.beijing_today()
        return {"today": today.isoformat(), "notices": rank_notice.list_notices(conn, today)}


@app.post("/api/admin/daily-notices", status_code=201)
def admin_create_daily_notice(data: rank_notice.NoticeInput, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        notice_id = rank_notice.create_notice(conn, data, user["id"], utc_now())
    return {"id": notice_id}


@app.put("/api/admin/daily-notices/{notice_id}")
def admin_update_daily_notice(
    notice_id: int, data: rank_notice.NoticeInput, user=Depends(current_user)
):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_manual_account(conn, user["id"], admin=True)
        if rank_notice.update_notice(conn, notice_id, data, utc_now()) != 1:
            raise HTTPException(404, "这条记录不存在")
    return {"ok": True}


GROUP_MAX_MEMBERS = 10
GROUP_MAX_PER_USER = 5
GROUP_INVITE_CODE_LENGTH = 8
# 统一大写，省去容易混淆的 0/O、1/I/L。
GROUP_INVITE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_group_invite_code():
    return "".join(
        secrets.choice(GROUP_INVITE_CODE_ALPHABET)
        for _ in range(GROUP_INVITE_CODE_LENGTH)
    )


def require_group_capacity_for_user(conn, user_id):
    count = conn.execute(
        "SELECT COUNT(*) FROM study_group_members WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if count >= GROUP_MAX_PER_USER:
        raise HTTPException(403, f"每人最多同时加入 {GROUP_MAX_PER_USER} 个小组")


def member_group(conn, group_id, user_id):
    group = conn.execute(
        """
        SELECT g.* FROM study_groups g
        JOIN study_group_members m ON m.group_id = g.id
        WHERE g.id = ? AND m.user_id = ?
        """,
        (group_id, user_id),
    ).fetchone()
    if group is None:
        # 不区分不存在和未加入，避免枚举 ID 探测私人小组。
        raise HTTPException(404, "小组不存在")
    return group


def group_members_by_id(conn, group_ids):
    """Load all visible groups' members together, in preview order."""
    members = {group_id: [] for group_id in group_ids}
    if not members:
        return members
    placeholders = ",".join("?" for _ in group_ids)
    rows = conn.execute(
        f"""
        SELECT gm.group_id, u.id, u.username, u.timezone, u.avatar_version,
               gm.joined_at
        FROM study_group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id IN ({placeholders}) AND u.deleted_at IS NULL
        ORDER BY gm.joined_at ASC, u.id ASC
        """,
        tuple(group_ids),
    ).fetchall()
    for row in rows:
        members[row["group_id"]].append(row)
    return members


def group_points_by_id(conn, members_by_group, *, review_days=None):
    """Stream two activity queries over distinct members, regardless of groups.

    Parse each activity once, then apply each membership's inclusive joining
    instant and separate daily caps. SQL must not compare ISO strings: offsets
    and precision can differ for equal instants. Reviews of older problems count.
    """
    accumulator = GroupPointsAccumulator(members_by_group, review_days)
    user_ids = tuple(accumulator.memberships)
    if not user_ids:
        return accumulator.totals
    placeholders = ",".join("?" for _ in user_ids)
    review_rows = conn.execute(
        f"""
        SELECT p.user_id, r.reviewed_at
        FROM problems p
        JOIN mistakes m ON m.problem_id = p.id
        JOIN reviews r ON r.mistake_id = m.id
        WHERE p.user_id IN ({placeholders})
        """,
        user_ids,
    )
    for row in review_rows:
        accumulator.add_review(row)
    problem_rows = conn.execute(
        f"""
        SELECT p.user_id, p.created_at
        FROM problems p
        WHERE p.user_id IN ({placeholders})
        """,
        user_ids,
    )
    for row in problem_rows:
        accumulator.add_problem(row)
    return accumulator.totals


def group_member_avatar(member):
    return {
        "id": member["id"],
        "username": member["username"],
        "avatar_version": member["avatar_version"],
        "has_avatar": avatar_path(member["id"]).is_file(),
    }


def group_detail(conn, group_id, user_id):
    group = member_group(conn, group_id, user_id)
    members_by_group = group_members_by_id(conn, (group_id,))
    members = members_by_group[group_id]
    # Streaks retain their full-history meaning; growth counts only activity
    # since joining. Reuse the streamed reviews for both, including trial users.
    review_days = {}
    member_points = group_points_by_id(
        conn, members_by_group, review_days=review_days,
    )[group_id]
    streaks = {
        row["id"]: current_streak(review_days.get(row["id"], set()), today_for(row))
        for row in members
    }
    points = sum(member_points.values())
    ranked_members = sorted(
        members, key=lambda row: (-streaks[row["id"]], row["username"])
    )
    return {
        "id": group["id"],
        "name": group["name"],
        "invite_code": group["invite_code"],
        "created_at": group["created_at"],
        "is_creator": group["created_by"] == user_id,
        "member_limit": GROUP_MAX_MEMBERS,
        "level": level_summary(points),
        "points": points,
        "members": [
            {
                # 小组由熟人邀请，刻意展示真实用户名；全站排行榜仍匿名。
                **group_member_avatar(row),
                "current_streak_days": streaks[row["id"]],
                "points": member_points[row["id"]],
                "is_creator": group["created_by"] == row["id"],
            }
            for row in ranked_members
        ],
        "weakness_by_zone": weakness_by_zone(
            conn, {row["id"] for row in members}, min_cohort=GROUP_WEAKNESS_MIN_COHORT
        ),
    }


@app.post("/api/groups", status_code=201)
def create_group(data: NewGroup, user=Depends(current_user)):
    # 限额检查、建组和加入在同一写事务中，避免并发请求突破限额或留下空组。
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        require_group_capacity_for_user(conn, user["id"])
        created_at = utc_now()
        for _ in range(2):
            try:
                group_id = conn.execute(
                    """
                    INSERT INTO study_groups(name, invite_code, created_by, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (data.name, generate_group_invite_code(), user["id"], created_at),
                ).lastrowid
                break
            except sqlite3.IntegrityError as exc:
                if "study_groups.invite_code" not in str(exc):
                    raise
        else:
            raise HTTPException(503, "暂时无法生成小组邀请码，请稍后再试")
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user["id"], created_at),
        )
        return group_detail(conn, group_id, user["id"])


@app.get("/api/groups")
def list_groups(user=Depends(current_user)):
    with connect() as conn:
        conn.execute("BEGIN")
        groups = conn.execute(
            """
            SELECT g.id, g.name, g.created_by, g.created_at,
                   (SELECT COUNT(*) FROM study_group_members gm
                    JOIN users u ON u.id = gm.user_id
                    WHERE gm.group_id = g.id AND u.deleted_at IS NULL)
                   AS member_count
            FROM study_groups g
            JOIN study_group_members m ON m.group_id = g.id
            WHERE m.user_id = ?
            ORDER BY g.created_at DESC, g.id DESC
            """,
            (user["id"],),
        ).fetchall()
        members_by_group = group_members_by_id(
            conn, tuple(group["id"] for group in groups)
        )
        points_by_group = group_points_by_id(conn, members_by_group)
    return {"groups": [
        {
            "id": group["id"],
            "name": group["name"],
            "member_count": group["member_count"],
            "member_limit": GROUP_MAX_MEMBERS,
            "is_creator": group["created_by"] == user["id"],
            "created_at": group["created_at"],
            "level": level_summary(sum(points_by_group[group["id"]].values())),
            "members_preview": [
                group_member_avatar(member)
                for member in members_by_group[group["id"]][:5]
            ],
        }
        for group in groups
    ]}


@app.get("/api/group-levels")
def get_group_levels(user=Depends(current_user)):
    return {
        "member_limit": GROUP_MAX_MEMBERS,
        "levels": [dict(level) for level in LEVELS],
        "rules": [dict(rule) for rule in RULES],
    }


@app.post("/api/groups/join")
def join_group(data: JoinGroup, request: Request, user=Depends(current_user)):
    if rate_limited(
        f"group_join:{client_ip(request)}", GROUP_JOIN_LIMIT, GROUP_JOIN_WINDOW_SECONDS
    ):
        raise HTTPException(429, "加入尝试次数过多，请稍后再试")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        group = conn.execute(
            "SELECT id FROM study_groups WHERE invite_code = ?", (data.invite_code,)
        ).fetchone()
        if group is None:
            raise HTTPException(404, "邀请码对应的小组不存在")
        group_id = group["id"]
        if conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user["id"]),
        ).fetchone():
            raise HTTPException(409, "你已经加入了这个小组")
        member_count = conn.execute(
            "SELECT COUNT(*) FROM study_group_members WHERE group_id = ?", (group_id,)
        ).fetchone()[0]
        if member_count >= GROUP_MAX_MEMBERS:
            raise HTTPException(403, f"小组已达到 {GROUP_MAX_MEMBERS} 人上限")
        require_group_capacity_for_user(conn, user["id"])
        conn.execute(
            "INSERT INTO study_group_members(group_id, user_id, joined_at) VALUES (?, ?, ?)",
            (group_id, user["id"], utc_now()),
        )
        return group_detail(conn, group_id, user["id"])


@app.get("/api/groups/{group_id}")
def get_group(group_id: int, user=Depends(current_user)):
    with connect() as conn:
        # 权限、成员和统计共享只读快照，不混用退出/加入前后的成员集合。
        conn.execute("BEGIN")
        return group_detail(conn, group_id, user["id"])


@app.post("/api/groups/{group_id}/leave")
def leave_group(group_id: int, user=Depends(current_user)):
    with connect(write=True) as conn:
        member_group(conn, group_id, user["id"])
        conn.execute(
            "DELETE FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user["id"]),
        )
        if conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ?", (group_id,)
        ).fetchone() is None:
            conn.execute("DELETE FROM study_groups WHERE id = ?", (group_id,))
    return {"ok": True}


@app.delete("/api/groups/{group_id}/members/{user_id}")
def remove_group_member(group_id: int, user_id: int, user=Depends(current_user)):
    with connect(write=True) as conn:
        group = member_group(conn, group_id, user["id"])
        if group["created_by"] != user["id"]:
            raise HTTPException(403, "只有创建者可以移除成员")
        if user_id == user["id"]:
            raise HTTPException(400, "请使用退出小组或解散小组")
        if conn.execute(
            "SELECT 1 FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone() is None:
            raise HTTPException(404, "成员不存在")
        conn.execute(
            "DELETE FROM study_group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
    return {"ok": True}


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: int, user=Depends(current_user)):
    with connect(write=True) as conn:
        group = member_group(conn, group_id, user["id"])
        if group["created_by"] != user["id"]:
            raise HTTPException(403, "只有创建者可以解散小组")
        # 成员关系由外键 ON DELETE CASCADE 一并清除。
        conn.execute("DELETE FROM study_groups WHERE id = ?", (group_id,))
    return {"ok": True}


POST_LIST_DEFAULT_LIMIT = 20
POST_LIST_MAX_LIMIT = 50
POST_EXCERPT_LENGTH = 140
POST_HOT_SCORE = 5
POST_HOT_WINDOW = timedelta(days=7)
POST_PARTICIPANT_LIMIT = 4
POST_LIST_SORTS = {
    "activity": "last_activity_at DESC, id DESC",
    "new": "created_at DESC, id DESC",
    "hot": "hot_score DESC, last_activity_at DESC, id DESC",
}
# 筛选条件只来自这张白名单（值是写死的 SQL 片段，用户输入只作为绑定参数）。
POST_LIST_FILTERS = {
    "all": "1",
    "unanswered": "comment_count = 0",
    "solved": "solved = 1",
    "mine": "user_id = :me",
    "participated": (
        "(user_id = :me OR EXISTS(SELECT 1 FROM post_comments m WHERE m.post_id = board.id "
        "AND m.user_id = :me AND m.deleted_at IS NULL))"
    ),
}
# 每个帖子的统计在一次分组查询里取齐，列表翻页和各项统计共用。
POST_BOARD_CTE = """
WITH comment_stats AS (
    SELECT post_id, COUNT(*) AS comment_count, MAX(created_at) AS last_comment_at,
           SUM(created_at >= :since) AS recent_comments
    FROM post_comments WHERE deleted_at IS NULL GROUP BY post_id
), vote_stats AS (
    SELECT c.post_id, COUNT(*) AS helpful_total
    FROM comment_votes v JOIN post_comments c ON c.id = v.comment_id
    WHERE c.deleted_at IS NULL GROUP BY c.post_id
), board AS (
    SELECT p.id, p.title, p.body, p.created_at, p.user_id, p.zone,
           u.username, u.avatar_version,
           COALESCE(cs.comment_count, 0) AS comment_count,
           CASE WHEN cs.last_comment_at > p.created_at
                THEN cs.last_comment_at ELSE p.created_at END AS last_activity_at,
           EXISTS(
               SELECT 1 FROM post_comments accepted
               WHERE accepted.id = p.accepted_comment_id
                 AND accepted.post_id = p.id AND accepted.deleted_at IS NULL
           ) AS solved,
           COALESCE(vs.helpful_total, 0) AS helpful_total,
           COALESCE(cs.recent_comments, 0) + COALESCE(vs.helpful_total, 0) AS hot_score
    FROM posts p
    JOIN users u ON u.id = p.user_id
    LEFT JOIN comment_stats cs ON cs.post_id = p.id
    LEFT JOIN vote_stats vs ON vs.post_id = p.id
    WHERE p.deleted_at IS NULL
)
"""


def require_not_trial(user, action):
    if user["is_trial"]:
        raise HTTPException(403, f"体验账号不支持{action}")


def visible_post(conn, post_id):
    if not -(2**63) <= post_id <= 2**63 - 1:
        raise HTTPException(404, "帖子不存在")
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
    if not -(2**63) <= comment_id <= 2**63 - 1:
        raise HTTPException(404, "评论不存在")
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


ZERO_WIDTH_JOINER = "\u200d"


def _joins_previous_character(character):
    """肤色、变体选择符、零宽连接符、标签字符和各类组合符号，都和前一个字符是一个整体。"""
    code = ord(character)
    return (
        character in (ZERO_WIDTH_JOINER, "\ufe0e", "\ufe0f")
        or 0x1F3FB <= code <= 0x1F3FF
        or 0xE0020 <= code <= 0xE007F
        or unicodedata.category(character) in ("Mn", "Me", "Mc")
    )


def truncate_text(text, limit):
    """按字符数截断，但不把一个表情（肤色、👩‍💻 这样的连接序列、国旗、键帽）劈成两半。

    返回 (截断后的文本, 是否被截断)。前端 app.js 里有一份同样逻辑的 truncateExcerpt。
    """
    if len(text) <= limit:
        return text, False
    cut = limit
    while cut > 0 and (_joins_previous_character(text[cut]) or text[cut - 1] == ZERO_WIDTH_JOINER):
        cut -= 1
    regional = 0
    while regional < cut and 0x1F1E6 <= ord(text[cut - 1 - regional]) <= 0x1F1FF:
        regional += 1
    if regional % 2:
        cut -= 1  # 国旗由两个区域指示符组成，不能只留一个
    return text[:cut], True


def comment_reply_to(target, floor):
    if target is None:
        return None
    reply_to = {
        "id": target["id"],
        "floor": floor,
        "deleted": target["deleted_at"] is not None,
    }
    if not reply_to["deleted"]:
        shortened, was_cut = truncate_text(" ".join(target["body"].split()), 60)
        reply_to.update(
            username=target["username"],
            excerpt=shortened + ("…" if was_cut else ""),
        )
    return reply_to


def comment_response(row, post_author_id, floor, reply_to, votes=None):
    return {
        **dict(row),
        "has_avatar": avatar_path(row["user_id"]).is_file(),
        "floor": floor,
        "is_op": row["user_id"] == post_author_id,
        "reply_to": reply_to,
        "helpful_count": votes["helpful_count"] if votes is not None else 0,
        "viewer_helpful": bool(votes["viewer_helpful"]) if votes is not None else False,
    }


def serialize_comment(conn, row, post_author_id, viewer_id):
    context = conn.execute(
        """
        SELECT COUNT(*) AS floor,
               EXISTS(SELECT 1 FROM posts WHERE id = ? AND deleted_at IS NULL)
                   AS post_visible
        FROM post_comments WHERE post_id = ? AND id <= ?
        """,
        (row["post_id"], row["post_id"], row["id"]),
    ).fetchone()
    reply_to = None
    # Editing an old own comment is still allowed after its post is deleted,
    # but the response must not read another author's hidden reply target.
    if context["post_visible"] and row["reply_to_id"] is not None:
        target = conn.execute(
            """
            SELECT c.*, u.username,
                   (SELECT COUNT(*) FROM post_comments floors
                    WHERE floors.post_id = c.post_id AND floors.id <= c.id) AS floor
            FROM post_comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.id = ? AND c.post_id = ?
            """,
            (row["reply_to_id"], row["post_id"]),
        ).fetchone()
        if target is not None:
            reply_to = comment_reply_to(target, target["floor"])
    votes = conn.execute(
        "SELECT COUNT(*) AS helpful_count, "
        "COALESCE(MAX(user_id = ?), 0) AS viewer_helpful "
        "FROM comment_votes WHERE comment_id = ?",
        (viewer_id, row["id"]),
    ).fetchone()
    return comment_response(row, post_author_id, context["floor"], reply_to, votes)


def post_excerpt(body):
    """帖子摘要：去掉围栏代码块、折叠空白、按 140 字（表情安全）截断。

    围栏规则与 static/thread.js 的 renderBody 一致：以 ``` 开头的行开始，
    遇到只有 ``` 的行结束，未闭合则一直到结尾。
    """
    kept = []
    in_fence = False
    for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if in_fence:
            if re.fullmatch(r"```[ \t]*", line):
                in_fence = False
        elif line.startswith("```"):
            in_fence = True
            kept.append(" ")
        else:
            kept.append(line)
    text = " ".join(" ".join(kept).split())
    shortened, was_cut = truncate_text(text, POST_EXCERPT_LENGTH)
    return shortened + ("…" if was_cut else "")


def has_fenced_code(body):
    return re.search(r"^```", body, re.MULTILINE) is not None


@app.get("/api/posts")
def list_posts(
    q: PostSearchQuery = "",
    sort: Literal["activity", "new", "hot"] = "activity",
    filter: Literal["all", "unanswered", "solved", "mine", "participated"] = "all",
    zone: str | None = None,
    limit: Annotated[int, Query(ge=1, le=POST_LIST_MAX_LIMIT)] = POST_LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    user=Depends(current_user),
):
    if zone is not None and zone != "none" and zone not in PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    # 体验账号不能发帖也不能评论，“我的 / 参与过”恒为空，is_mine 恒为 false。
    me = None if user["is_trial"] else user["id"]
    since = (datetime.now(timezone.utc) - POST_HOT_WINDOW).isoformat(timespec="seconds")
    conditions = [POST_LIST_FILTERS[filter]]
    params = {"since": since, "me": me}
    if q:
        # Escape LIKE metacharacters (including the escape character itself).
        # SQLite LIKE is case-insensitive for ASCII letters by default.
        keyword = q.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        params["pattern"] = f"%{keyword}%"
        conditions.append("(title LIKE :pattern ESCAPE '!' OR body LIKE :pattern ESCAPE '!')")
    if zone == "none":
        conditions.append("zone IS NULL")
    elif zone is not None:
        params["zone"] = zone
        conditions.append("zone = :zone")
    where = " AND ".join(conditions)
    with connect() as conn:
        # 总数、读数和本页数据共用同一个只读快照。
        conn.execute("BEGIN")
        total = conn.execute(
            f"{POST_BOARD_CTE} SELECT COUNT(*) FROM board WHERE {where}", params
        ).fetchone()[0]
        counts = {"all": 0, "unanswered": 0, "solved": 0, "mine": 0}
        zone_counts = {}
        # counts / zone_counts 不受 q / filter / zone 影响：全站可见帖子的口径。
        for row in conn.execute(
            f"""{POST_BOARD_CTE}
            SELECT COALESCE(zone, 'none') AS zone_key, COUNT(*) AS total,
                   SUM(comment_count = 0) AS unanswered, SUM(solved) AS solved,
                   SUM(CASE WHEN user_id = :me THEN 1 ELSE 0 END) AS mine
            FROM board GROUP BY zone_key
            """,
            {"since": since, "me": me},
        ):
            zone_counts[row["zone_key"]] = row["total"]
            counts["all"] += row["total"]
            counts["unanswered"] += row["unanswered"]
            counts["solved"] += row["solved"]
            counts["mine"] += row["mine"]
        rows = conn.execute(
            f"""{POST_BOARD_CTE}
            SELECT * FROM board WHERE {where}
            ORDER BY {POST_LIST_SORTS[sort]} LIMIT :limit OFFSET :offset
            """,
            {**params, "limit": limit, "offset": offset},
        ).fetchall()
        participation = {}
        coded_posts = set()
        if rows:
            marks = ",".join("?" * len(rows))
            ids = [row["id"] for row in rows]
            # 参与者、最后评论者：整页一次分组查询取齐（key 用来比较“谁更晚”）。
            for item in conn.execute(
                f"""
                SELECT c.post_id, c.user_id, u.username, u.avatar_version,
                       MAX(c.created_at || '|' || printf('%020d', c.id)) AS last_key
                FROM post_comments c JOIN users u ON u.id = c.user_id
                WHERE c.deleted_at IS NULL AND c.post_id IN ({marks})
                GROUP BY c.post_id, c.user_id
                """,
                ids,
            ):
                participation.setdefault(item["post_id"], []).append(item)
            for item in conn.execute(
                f"""
                SELECT post_id, body FROM post_comments
                WHERE deleted_at IS NULL AND post_id IN ({marks}) AND body LIKE '%```%'
                """,
                ids,
            ):
                if has_fenced_code(item["body"]):
                    coded_posts.add(item["post_id"])

    avatars = {}

    def person(user_id, username, avatar_version):
        if user_id not in avatars:
            avatars[user_id] = avatar_path(user_id).is_file()
        return {"user_id": user_id, "username": username,
                "avatar_version": avatar_version, "has_avatar": avatars[user_id]}

    posts = []
    for row in rows:
        commenters = sorted(
            participation.get(row["id"], []), key=lambda item: item["last_key"], reverse=True
        )
        last_commenter = None
        if commenters:
            latest = commenters[0]
            last_commenter = person(latest["user_id"], latest["username"], latest["avatar_version"])
        people = [person(row["user_id"], row["username"], row["avatar_version"])]
        people += [
            person(item["user_id"], item["username"], item["avatar_version"])
            for item in commenters if item["user_id"] != row["user_id"]
        ]
        posts.append({
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "user_id": row["user_id"],
            "username": row["username"],
            "avatar_version": row["avatar_version"],
            "has_avatar": people[0]["has_avatar"],
            "zone": row["zone"],
            "excerpt": post_excerpt(row["body"]),
            "comment_count": row["comment_count"],
            "last_activity_at": row["last_activity_at"],
            "last_commenter": last_commenter,
            "participants": people[:POST_PARTICIPANT_LIMIT],
            "participant_count": len(people),
            "has_code": has_fenced_code(row["body"]) or row["id"] in coded_posts,
            "solved": bool(row["solved"]),
            "helpful_total": row["helpful_total"],
            "hot": row["hot_score"] >= POST_HOT_SCORE,
            "is_mine": me is not None and row["user_id"] == me,
        })
    return {
        "posts": posts,
        "total": total,
        "has_more": offset + len(posts) < total,
        "counts": counts,
        "zone_counts": zone_counts,
    }


def checked_post_zone(zone):
    if zone is not None and zone not in PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    return zone


@app.post("/api/posts", status_code=201)
def create_post(data: NewPost, user=Depends(current_user)):
    require_not_trial(user, "发帖")
    zone = checked_post_zone(data.zone)
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        cursor = conn.execute(
            "INSERT INTO posts(user_id, title, body, zone, created_at) VALUES (?, ?, ?, ?, ?)",
            (user["id"], data.title, data.body, zone, utc_now()),
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
    if not -(2**63) <= post_id <= 2**63 - 1:
        raise HTTPException(404, "帖子不存在")
    with connect() as conn:
        conn.execute("BEGIN")
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
        post = {**dict(row), "has_avatar": avatar_path(row["user_id"]).is_file()}
        rows = conn.execute(
            """
            SELECT c.*, u.username, u.avatar_version
            FROM post_comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.post_id = ?
            ORDER BY c.id ASC
            """,
            (post_id,),
        ).fetchall()
        # Tombstones keep their floor; reply lookups all use the same snapshot.
        by_id = {row["id"]: (row, floor) for floor, row in enumerate(rows, 1)}
        accepted, _ = by_id.get(post["accepted_comment_id"], (None, None))
        if accepted is None or accepted["deleted_at"] is not None:
            post["accepted_comment_id"] = None
        votes_by_id = {
            vote["comment_id"]: vote
            for vote in conn.execute(
                """
                SELECT v.comment_id, COUNT(*) AS helpful_count,
                       MAX(v.user_id = ?) AS viewer_helpful
                FROM comment_votes v
                JOIN post_comments c ON c.id = v.comment_id
                WHERE c.post_id = ? AND c.deleted_at IS NULL
                GROUP BY v.comment_id
                """,
                (user["id"], post_id),
            )
        }
        comments = sorted(
            (row for row in rows if row["deleted_at"] is None),
            key=lambda row: row["created_at"],
        )
        post["comments"] = []
        for row in comments:
            target, target_floor = by_id.get(row["reply_to_id"], (None, None))
            post["comments"].append(
                comment_response(
                    row, post["user_id"], by_id[row["id"]][1],
                    comment_reply_to(target, target_floor),
                    votes_by_id.get(row["id"]),
                )
            )
    return post


@app.put("/api/posts/{post_id}")
def edit_post(post_id: int, data: PostEdit, user=Depends(current_user)):
    require_not_trial(user, "发帖")
    # 省略 zone 表示不改；显式传 null 表示改回“未分区”。
    change_zone = "zone" in data.model_fields_set
    zone = checked_post_zone(data.zone)
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        post = owned_post(conn, post_id, user["id"])
        conn.execute(
            "UPDATE posts SET title = ?, body = ?, zone = ?, updated_at = ? WHERE id = ?",
            (data.title, data.body, zone if change_zone else post["zone"], utc_now(), post_id),
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
        recheck_account(conn, user["id"])
        owned_post(conn, post_id, user["id"])
        # 软删除：标记 deleted_at，不物理删除，也不级联标记这个帖子下的
        # 评论——帖子对所有人不可见之后，正常业务路径本来就到达不了
        # 这些评论，不需要逐条标记。
        conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ?",
            (utc_now(), post_id),
        )
        clear_deleted_thread_state(conn, post_id=post_id)
    return {"ok": True}


@app.post("/api/posts/{post_id}/comments", status_code=201)
def create_comment(post_id: int, data: NewComment, user=Depends(current_user)):
    require_not_trial(user, "评论")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        post = visible_post(conn, post_id)
        if data.reply_to_id is not None:
            # SQLite IDs are signed 64-bit integers; larger positive IDs are
            # nonexistent targets, rather than binding errors or invalid bodies.
            if data.reply_to_id > 2**63 - 1 or conn.execute(
                "SELECT 1 FROM post_comments "
                "WHERE id = ? AND post_id = ? AND deleted_at IS NULL",
                (data.reply_to_id, post_id),
            ).fetchone() is None:
                raise HTTPException(400, "被回复的评论不存在或已删除")
        cursor = conn.execute(
            "INSERT INTO post_comments(post_id, user_id, body, created_at, reply_to_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (post_id, user["id"], data.body, utc_now(), data.reply_to_id),
        )
        row = conn.execute(
            """
            SELECT c.*, u.username, u.avatar_version FROM post_comments c
            JOIN users u ON u.id = c.user_id WHERE c.id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        comment = serialize_comment(conn, row, post["user_id"], user["id"])
    return comment


def next_comment_update(previous):
    """Every edit changes the signature, including identical text in one clock tick."""
    current = datetime.now(timezone.utc)
    if previous:
        try:
            earlier = datetime.fromisoformat(previous)
            if earlier.tzinfo is None:
                earlier = earlier.replace(tzinfo=timezone.utc)
            if current <= earlier:
                current = earlier + timedelta(microseconds=1)
        except ValueError:
            pass
    return current.isoformat(timespec="microseconds")


@app.put("/api/comments/{comment_id}")
def edit_comment(comment_id: int, data: CommentEdit, user=Depends(current_user)):
    require_not_trial(user, "评论")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        owned = owned_comment(conn, comment_id, user["id"])
        conn.execute(
            "UPDATE post_comments SET body = ?, updated_at = ? WHERE id = ?",
            (data.body, next_comment_update(owned["updated_at"]), comment_id),
        )
        row = conn.execute(
            """
            SELECT c.*, u.username, u.avatar_version FROM post_comments c
            JOIN users u ON u.id = c.user_id WHERE c.id = ?
            """,
            (comment_id,),
        ).fetchone()
        post_author = conn.execute(
            "SELECT user_id FROM posts WHERE id = ?", (owned["post_id"],)
        ).fetchone()
        comment = serialize_comment(conn, row, post_author["user_id"], user["id"])
    return comment


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user=Depends(current_user)):
    require_not_trial(user, "评论")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        owned_comment(conn, comment_id, user["id"])
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?",
            (utc_now(), comment_id),
        )
        clear_deleted_thread_state(conn, comment_id=comment_id)
    return {"ok": True}


def clear_deleted_thread_state(conn, *, post_id=None, comment_id=None):
    """Clear private derived text and accepted references in the deletion transaction."""
    if comment_id is not None:
        conn.execute(
            "UPDATE posts SET accepted_comment_id = NULL WHERE accepted_comment_id = ?",
            (comment_id,),
        )
        conn.execute(
            "DELETE FROM post_summaries WHERE post_id = "
            "(SELECT post_id FROM post_comments WHERE id = ?)",
            (comment_id,),
        )
    else:
        conn.execute("UPDATE posts SET accepted_comment_id = NULL WHERE id = ?", (post_id,))
        conn.execute("DELETE FROM post_summaries WHERE post_id = ?", (post_id,))


@app.put("/api/posts/{post_id}/accepted")
def accept_comment(post_id: int, data: AcceptedComment, user=Depends(current_user)):
    require_not_trial(user, "采纳")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        owned_post(conn, post_id, user["id"])
        if not -(2**63) <= data.comment_id <= 2**63 - 1:
            raise HTTPException(400, "这条评论不存在或已删除")
        comment = conn.execute(
            "SELECT user_id FROM post_comments "
            "WHERE id = ? AND post_id = ? AND deleted_at IS NULL",
            (data.comment_id, post_id),
        ).fetchone()
        if comment is None:
            raise HTTPException(400, "这条评论不存在或已删除")
        if comment["user_id"] == user["id"]:
            raise HTTPException(400, "不能采纳自己的评论")
        conn.execute(
            "UPDATE posts SET accepted_comment_id = ? WHERE id = ?",
            (data.comment_id, post_id),
        )
    return {"accepted_comment_id": data.comment_id}


@app.exception_handler(RequestValidationError)
async def forum_action_validation_error(request: Request, exc: RequestValidationError):
    if re.fullmatch(
        r"/api/posts/[^/]+/(?:accepted|summary)|/api/comments/[^/]+/helpful"
        r"|/api/mistakes/[^/]+/(?:preview|review(?:/undo)?|snooze|suspend|unsuspend)"
        r"|/api/me/review-settings|/api/review/queue",
        request.url.path,
    ):
        return JSONResponse(
            status_code=422, content={"detail": "请求参数不正确，请检查后重试"},
        )
    return await request_validation_exception_handler(request, exc)


@app.delete("/api/posts/{post_id}/accepted")
def unaccept_comment(post_id: int, user=Depends(current_user)):
    require_not_trial(user, "采纳")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        owned_post(conn, post_id, user["id"])
        conn.execute("UPDATE posts SET accepted_comment_id = NULL WHERE id = ?", (post_id,))
    return {"accepted_comment_id": None}


def change_comment_helpful(comment_id, user, *, helpful):
    require_not_trial(user, "点有用")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        if not -(2**63) <= comment_id <= 2**63 - 1:
            raise HTTPException(404, "评论不存在")
        comment = conn.execute(
            "SELECT c.user_id FROM post_comments c JOIN posts p ON p.id = c.post_id "
            "WHERE c.id = ? AND c.deleted_at IS NULL AND p.deleted_at IS NULL",
            (comment_id,),
        ).fetchone()
        if comment is None:
            raise HTTPException(404, "评论不存在")
        if comment["user_id"] == user["id"]:
            raise HTTPException(400, "不能给自己的评论点有用")
        if rate_limited(f"helpful:{user['id']}", 60, 60):
            raise HTTPException(429, "操作过于频繁，请稍后再试")
        if helpful:
            conn.execute(
                "INSERT INTO comment_votes(comment_id, user_id, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(comment_id, user_id) DO NOTHING",
                (comment_id, user["id"], utc_now()),
            )
        else:
            conn.execute(
                "DELETE FROM comment_votes WHERE comment_id = ? AND user_id = ?",
                (comment_id, user["id"]),
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM comment_votes WHERE comment_id = ?", (comment_id,),
        ).fetchone()[0]
    return {"helpful_count": count, "viewer_helpful": helpful}


@app.put("/api/comments/{comment_id}/helpful")
def mark_comment_helpful(comment_id: int, user=Depends(current_user)):
    return change_comment_helpful(comment_id, user, helpful=True)


@app.delete("/api/comments/{comment_id}/helpful")
def unmark_comment_helpful(comment_id: int, user=Depends(current_user)):
    return change_comment_helpful(comment_id, user, helpful=False)


def thread_privacy_state(conn, post, comments):
    # Anonymous retention does not change text signatures. Detect account deletion
    # during an AI call too, so a completed old request cannot recreate cleared text.
    authors = {post["user_id"], *(comment["user_id"] for comment in comments)}
    placeholders = ",".join("?" for _ in authors)
    return tuple(
        (row["id"], row["deleted_at"])
        for row in conn.execute(
            f"SELECT id, deleted_at FROM users WHERE id IN ({placeholders}) ORDER BY id",
            tuple(authors),
        )
    )


@app.get("/api/posts/{post_id}/summary")
def get_thread_summary(post_id: int, user=Depends(current_user)):
    with connect() as conn:
        conn.execute("BEGIN")
        post, comments = thread_summary.load_thread(conn, post_id)
        row = conn.execute("SELECT * FROM post_summaries WHERE post_id = ?", (post_id,)).fetchone()
        if row is None:
            return {"summary": None}
        signature = thread_summary.thread_signature(post, comments)
        return {"summary": thread_summary.serialize_summary(row, signature)}


@app.post("/api/posts/{post_id}/summary")
def create_thread_summary(post_id: int, user=Depends(current_user)):
    require_not_trial(user, " AI 要点")
    with ExitStack() as stack:
        with connect(write=True) as conn:
            recheck_account(conn, user["id"])
            post, comments = thread_summary.load_thread(conn, post_id)
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
            if len(comments) < 2:
                raise HTTPException(400, "回复太少，暂时不需要提炼")
            if rate_limited(f"summary:{user['id']}", 10, 3600):
                raise HTTPException(429, "AI 要点请求太频繁，请稍后再试")
            signature = thread_summary.thread_signature(post, comments)
            row = conn.execute("SELECT * FROM post_summaries WHERE post_id = ?", (post_id,)).fetchone()
            if row is not None and row["signature"] == signature:
                return {"summary": thread_summary.serialize_summary(row, signature), "cached": True}
            reference = thread_summary.thread_reference(post, comments)
            privacy_state = thread_privacy_state(conn, post, comments)
            day = today_for(user).isoformat()
            quota = ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO ai_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = ai_usage.attempts + 1
                WHERE ai_usage.attempts < ?
                """,
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")

        try:
            with track_call(user["id"], "thread_summary"):
                content = thread_summary.validate_summary(
                    thread_summary.summarize_thread(reference), reference,
                )
        except HTTPException as exc:
            if exc.status_code in (502, 503, 504):
                with connect(write=True) as conn:
                    release_attempt(conn, user["id"], day)
            raise

        created_at = utc_now()
        with connect(write=True) as conn:
            recheck_account(conn, user["id"])
            current_post, current_comments = thread_summary.load_thread(conn, post_id)
            if (thread_summary.thread_signature(current_post, current_comments) != signature
                    or thread_privacy_state(conn, current_post, current_comments) != privacy_state):
                raise HTTPException(409, "讨论内容已变化，请重新提炼")
            conn.execute(
                """
                INSERT INTO post_summaries(post_id, signature, content, comment_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(post_id) DO UPDATE SET signature = excluded.signature,
                    content = excluded.content, comment_count = excluded.comment_count,
                    created_at = excluded.created_at
                """,
                (post_id, signature, json.dumps(content, ensure_ascii=False),
                 len(reference["comments"]), created_at),
            )
        return {"summary": {**content, "generated_at": created_at,
                            "comment_count": len(reference["comments"]), "stale": False},
                "cached": False}


def create_report(user, *, post_id=None, comment_id=None, reason):
    require_not_trial(user, "举报")
    if rate_limited(f"report:{user['id']}", REPORT_LIMIT, REPORT_WINDOW_SECONDS):
        raise HTTPException(429, "举报过于频繁，请稍后再试")
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
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
        recheck_account(conn, user["id"])
        target = conn.execute(
            "SELECT id FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
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


@app.get("/api/admin/dashboard")
def admin_dashboard(user=Depends(current_user)):
    require_admin(user)
    now = datetime.fromisoformat(utc_now()).astimezone(timezone.utc)
    # 运营看板统一用 UTC；时间戳按过去 N × 24 小时，AI 日汇总按含今天的 7 天。
    bounds = {
        "now": now.isoformat(),
        "since_7_days": (now - timedelta(days=7)).isoformat(),
        "since_30_days": (now - timedelta(days=30)).isoformat(),
        "today": now.date().isoformat(),
        "first_ai_day": (now.date() - timedelta(days=6)).isoformat(),
    }
    with connect() as conn:
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
        zone_counts = dict.fromkeys(PROBLEM_ZONES, 0)
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


@app.get("/api/admin/metrics")
def admin_metrics_overview(days: int = 7, user=Depends(current_user)):
    require_admin(user)
    if days not in PERIOD_CHOICES:
        raise HTTPException(422, "days 只能是 7 或 30")
    now = datetime.fromisoformat(utc_now()).astimezone(timezone.utc)
    with connect() as conn:
        return compute_admin_metrics(conn, days, now)


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
              AND reporter.deleted_at IS NULL AND owner.deleted_at IS NULL
            ORDER BY a.created_at ASC
            """
        ).fetchall()
    reports = [{"type": ("post" if row["post_id"] is not None else "comment"), **dict(row)} for row in rows]
    reports += [
        {
            "type": "avatar",
            **dict(row),
            "avatar_owner_has_avatar": avatar_path(row["avatar_owner_id"]).is_file(),
        }
        for row in avatar_rows
    ]
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
        recheck_account(conn, user["id"])
        visible_post(conn, post_id)
        conn.execute(
            "UPDATE posts SET deleted_at = ? WHERE id = ?", (utc_now(), post_id)
        )
        clear_deleted_thread_state(conn, post_id=post_id)
        resolve_reports_for(conn, post_id=post_id)
    return {"ok": True}


@app.delete("/api/admin/comments/{comment_id}")
def admin_delete_comment(comment_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        recheck_account(conn, user["id"])
        visible_comment(conn, comment_id)
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?",
            (utc_now(), comment_id),
        )
        clear_deleted_thread_state(conn, comment_id=comment_id)
        resolve_reports_for(conn, comment_id=comment_id)
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}/avatar")
def admin_clear_avatar(user_id: int, user=Depends(current_user)):
    require_admin(user)
    avatar_path(user_id).unlink(missing_ok=True)
    with connect(write=True) as conn:
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
            (utc_now(), user_id),
        )
    return {"ok": True, "has_avatar": False}


@app.post("/api/admin/users/{user_id}/ban")
def admin_ban_user(user_id: int, user=Depends(current_user)):
    require_admin(user)
    if user_id == user["id"]:
        raise HTTPException(400, "不能封禁自己")
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET is_banned = 1 WHERE id = ? AND deleted_at IS NULL", (user_id,)
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
    rank_cache.invalidate()
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/unban")
def admin_unban_user(user_id: int, user=Depends(current_user)):
    require_admin(user)
    with connect(write=True) as conn:
        cursor = conn.execute(
            "UPDATE users SET is_banned = 0 WHERE id = ? AND deleted_at IS NULL", (user_id,)
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "用户不存在")
    rank_cache.invalidate()
    return {"ok": True}
