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
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)
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
import goal
import hot_problems
import mailer
import manual_claims
import payments
from payments import activate_plan
import push_channels
import rank_board
import rank_cache
import rank_notice
import thread_summary
from achievements import evaluate_achievements
from activity import activity_summary, day_counts
from admin_metrics import PERIOD_CHOICES, compute_metrics as compute_admin_metrics
from ai_limits import ai_slot, note_usage, release_attempt, track_call
from duck_prompt import MAX_USER_TURNS, build_messages, check_turns, validate_reply
from db import ROOT, connect, init_db, normalize_username, schema_version, sec_username_key
from legal import PRODUCT_NAME, TERMS_VERSION, render_legal_page
from group_levels import GroupPointsAccumulator, LEVELS, RULES, level_summary
from learning_stats import current_streak, learning_metrics
from stats_summary import ALLOWED_DAYS as ALLOWED_SUMMARY_DAYS, summary as stats_summary
from scheduler import preview_all, schedule, today_in_timezone
from mastery import (
    AT_RISK_BELOW as MASTERY_AT_RISK_BELOW,
    load_mistakes as mastery_load_mistakes,
    mastery_report,
    retention_on as mastery_retention_on,
)
from anki_export import build_anki_text
from search import search_all
from typical import typical_report
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
API_MAX_BODY_BYTES = 10 * 1024 * 1024
PHOTO_MAX_DIMENSION = 1600

# 简单校验即可：真正确认邮箱能收到信，靠的是密码找回时能不能收到邮件，
# 而不是注册时的格式检查，所以没有引入额外的邮箱校验依赖。
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(InputModel):
    # 不再限制字符集（原来只认 ASCII 字母/数字/下划线，中文用户名会被拒绝），
    # 只保留最基本的边界：非空、长度封顶。保留原始输入供存量账号精确
    # 匹配，注册另行规范化；讨论区渲染用户名走 textContent，不走
    # innerHTML，这里放开字符集不会引入 XSS。
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=6, max_length=128)


def normalize_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("请填写有效时区，例如 Asia/Shanghai") from None
    return value


class Registration(Credentials):
    username: str = Field(min_length=1, max_length=32)
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


class EmailChangeConfirm(InputModel):
    token: str = Field(min_length=1, max_length=200)


class ForgotPassword(InputModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class ResetPassword(InputModel):
    # 空链接/空密码也由业务校验返回可供前端区分的 400 code。
    token: str = Field(max_length=512)
    password: str = Field(max_length=128)


class EmailUpdate(InputModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class UsernameUpdate(InputModel):
    username: str = Field(min_length=1, max_length=32)


class PasswordChange(InputModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AccountDeletion(InputModel):
    password: str = Field(min_length=1, max_length=128)


class GoalSetting(InputModel):
    # 这里只设物理上限；「名称 ≤ 30 字、日期是未来 1–365 天」由 goal.save 校验，
    # 这样 422 的 detail 是给用户看的整句，而不是 pydantic 的字段错误列表。
    name: str = Field(max_length=600)
    goal_date: str = Field(max_length=32)


class NewGroup(InputModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ]


class JoinGroup(InputModel):
    invite_code: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=64)
    ]


# 速记模式的占位文案：只留证据、原因以后补时，服务端填充的标记文本。
QUICK_CODE_PLACEHOLDER = "（速记：代码待补）"
QUICK_THINKING_PLACEHOLDER = "（速记：思路待补）"
QUICK_MISTAKE_PLACEHOLDER = "（待补：为什么错）"
# 对外展示"待补"状态时的统一文案（Anki 导出等），不泄露内部占位原文。
PENDING_REASON_DISPLAY = "（待补）"


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
    mistakes: list[MistakeText] = Field(default_factory=list, max_length=10)
    # 速记模式：只要求 title 和 zone；code/thinking/mistakes 可省略或为空，
    # 服务端补占位。quick=false 时行为与原来完全一致。
    quick: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def _fill_quick_placeholders(cls, data):
        if isinstance(data, dict) and data.get("quick") is True:
            data = dict(data)
            for field, placeholder in (
                ("code", QUICK_CODE_PLACEHOLDER),
                ("thinking", QUICK_THINKING_PLACEHOLDER),
            ):
                value = data.get(field)
                # 在字段校验前只识别缺失/空白文本；其他类型仍由 Pydantic 拒绝。
                if value is None or (isinstance(value, str) and not value.strip()):
                    data[field] = placeholder
        return data

    @model_validator(mode="after")
    def _require_mistakes_unless_quick(self):
        if not self.quick and not self.mistakes:
            raise ValueError("请至少添加 1 条易错点")
        return self


class ProblemEdit(ProblemFields):
    pass


class MistakeEdit(InputModel):
    description: MistakeText
    version: int = Field(strict=True, ge=0)


class MistakeTags(InputModel):
    # 单个标签的字数和每条的个数上限在 tags.normalize_tags 里统一校验并给出中文提示；
    # 这里只挡掉明显离谱的请求体大小。
    tags: list[str] = Field(max_length=50)


class MistakeReasonInput(InputModel):
    # 创建/普通编辑允许留空；消除待补标记则必须真有一句原因。
    # 标签个数上限 3 在接口里单独校验（normalize_tags 的上限是 8）。
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    tags: list[str] = Field(max_length=50)
    version: int = Field(strict=True, ge=0)


class ScratchPut(InputModel):
    # 草稿演算区：code/fixed 为代码文本，table 为 {cols, rows} 或 null；
    # table 的结构校验在接口里按 static/trace-table.js 的 validate 规则做。
    version: int = Field(strict=True, ge=0)
    code: Annotated[str, StringConstraints(strict=True, max_length=100000)]
    fixed: Annotated[str, StringConstraints(strict=True, max_length=100000)]
    table: dict | None


class PushSettingsInput(InputModel):
    # 微信提醒设置：渠道二选一；secret 为 null / 空串表示保持原密钥不变；
    # 密钥格式在接口里复用 push_channels.valid_key 校验。
    channel: Literal["serverchan", "pushplus"]
    secret: Annotated[str, StringConstraints(strict=True, max_length=128)] | None = None
    enabled: bool = Field(strict=True)


CLIENT_OP_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class ReviewInput(InputModel):
    quality: int = Field(strict=True, ge=0, le=5)
    # 带 client_op_id 的离线补交可能拿不到版本号，此时省略 version 并跳过版本比较；
    # 不带 client_op_id 的普通评分 version 仍然必填。
    version: int | None = Field(default=None, strict=True, ge=0)
    client_op_id: str | None = Field(default=None, pattern=CLIENT_OP_ID_PATTERN)
    reviewed_at: datetime | None = None

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def aware_iso_time(cls, value):
        # 只接受带时区的 ISO 字符串：数字时间戳和没有时区的时间都有歧义。
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError("reviewed_at 必须是带时区的 ISO 时间")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("reviewed_at 必须是带时区的 ISO 时间") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("reviewed_at 必须带时区")
        return parsed

    @model_validator(mode="after")
    def version_required_without_op_id(self):
        if self.version is None and self.client_op_id is None:
            raise ValueError("缺少 version")
        return self


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
    return max(1, int(os.getenv("AI_DAILY_LIMIT", "20")))


def trial_ai_limit():
    # 体验账号任何人都能开，额度要远低于正式账号，否则等于把
    # AI_DAILY_LIMIT 变成"任何人每天可用次数 × 无限个体验账号"。
    return max(0, int(os.getenv("TRIAL_AI_DAILY_LIMIT", "4")))


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


def sec_has_avatar(user_id):
    with connect() as conn:
        author = conn.execute(
            "SELECT 1 FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
        ).fetchone()
    return author is not None and avatar_path(user_id).is_file()


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
    return sec_username_key(value)


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


def cookie_secure(request):
    """COOKIE_SECURE 显式设置时按显式值；未设置时按请求是否为 https 自动决定。"""
    explicit = os.getenv("COOKIE_SECURE")
    if explicit is not None and explicit.strip() != "":
        return explicit.strip() == "1"
    if os.getenv("TRUST_PROXY") == "1":
        proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
        if proto:
            return proto == "https"
    return request.url.scheme == "https"


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


def mistake_public(item):
    """对外 JSON 的易错点通用字段：pending_reason 转为布尔值。

    数据库里存的是 0/1 整数；对外一律给 true/false，保持字段加法兼容。
    """
    item["pending_reason"] = bool(item.get("pending_reason", 0))
    return item


def owned_mistake(conn, mistake_id, user_id):
    row = conn.execute(
        MISTAKE_SELECT + " WHERE m.id = ? AND p.user_id = ?",
        (mistake_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "记录不存在")
    return mistake_public(dict(row))


def owned_problem(conn, problem_id, user_id):
    row = conn.execute(
        "SELECT * FROM problems WHERE id = ? AND user_id = ?",
        (problem_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "题目不存在")
    return dict(row)


# ---- 草稿演算区（/api/mistakes/{id}/scratch）----
# 与 static/scratch.js、static/trace-table.js 的前端常量保持一致。
SCRATCH_MAX_LINES = 2000
SCRATCH_SIZE_LIMIT = 40000
TRACE_TABLE_MAX_COLS = 20
TRACE_TABLE_MAX_ROWS = 60
TRACE_TABLE_MAX_HEADER_LEN = 20
TRACE_TABLE_MAX_CELL_LEN = 60
TRACE_TABLE_MAX_SERIALIZED = 20000
_SCRATCH_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


def _js_string_length(value):
    # JavaScript 的 string.length 与 JSON.stringify().length 都按 UTF-16
    # 码元计数；Python 的 len() 按码点计数，emoji 等增补平面字符会少算。
    return len(value.encode("utf-16-le")) // 2


def _scratch_line_count(value):
    # 与 scratch.js 的 countLines 完全一致：空串算 1 行，否则按 \n 切。
    return 1 if value == "" else value.count("\n") + 1


def _scratch_sanitize_cell(value, max_len):
    # 与 trace-table.js 的 sanitize 一致：控制字符（含换行、制表符、DEL）
    # 换成普通空格，再截断到 max_len。
    return _SCRATCH_CONTROL_CHAR.sub(" ", value)[:max_len]


def normalize_scratch_table(value):
    """按 trace-table.js validate 的规则归一化演算表；结构非法返回 None。

    超出上限的行列丢弃；表头/单元格做控制字符替换与截断；短行补空字符串；
    归一化后序列化超过 20000 字符同样判为非法。
    """
    if not isinstance(value, dict):
        return None
    cols = value.get("cols")
    rows = value.get("rows")
    if not isinstance(cols, list) or not isinstance(rows, list):
        return None
    kept_cols = cols[:TRACE_TABLE_MAX_COLS]
    kept_rows = rows[:TRACE_TABLE_MAX_ROWS]
    if len(kept_cols) < 1 or len(kept_rows) < 1:
        return None
    norm_cols = []
    for col in kept_cols:
        if not isinstance(col, str):
            return None
        norm_cols.append(_scratch_sanitize_cell(col, TRACE_TABLE_MAX_HEADER_LEN))
    norm_rows = []
    for row in kept_rows:
        if not isinstance(row, list):
            return None
        norm_row = []
        for index in range(len(norm_cols)):
            cell = row[index] if index < len(row) else ""
            if not isinstance(cell, str):
                return None
            norm_row.append(_scratch_sanitize_cell(cell, TRACE_TABLE_MAX_CELL_LEN))
        norm_rows.append(norm_row)
    model = {"cols": norm_cols, "rows": norm_rows}
    if _js_string_length(
        json.dumps(model, ensure_ascii=False, separators=(",", ":"))
    ) > TRACE_TABLE_MAX_SERIALIZED:
        return None
    return model


def scratch_serialized_size(code, fixed, table):
    # 与 scratch.js 的 serializedSize 一致：
    # code.length + fixed.length + (table ? JSON.stringify(table).length : 0)。
    total = _js_string_length(code) + _js_string_length(fixed)
    if table is not None:
        total += _js_string_length(
            json.dumps(table, ensure_ascii=False, separators=(",", ":"))
        )
    return total


def scratch_state(conn, mistake_id):
    """组装 GET / 409 current 用的草稿状态（调用方已确认属主）。"""
    row = conn.execute(
        "SELECT version, code, fixed, table_json, updated_at "
        "FROM mistake_scratch WHERE mistake_id = ?",
        (mistake_id,),
    ).fetchone()
    if row is None:
        return {"version": 0, "code": "", "fixed": "", "table": None,
                "updated_at": None}
    return {
        "version": row["version"],
        "code": row["code"],
        "fixed": row["fixed"],
        "table": json.loads(row["table_json"]) if row["table_json"] else None,
        "updated_at": row["updated_at"],
    }


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
# 按账号叠加的失败次数上限：比 IP 上限宽松，只统计失败，防止分散 IP 撞同一个账号。
LOGIN_ACCOUNT_LIMIT = 30
FORGOT_PASSWORD_LIMIT = 5
FORGOT_PASSWORD_WINDOW_SECONDS = 15 * 60
FORGOT_PASSWORD_EMAIL_LIMIT = 3
FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS = 60 * 60
RESET_PASSWORD_LIMIT = 10
RESET_PASSWORD_WINDOW_SECONDS = 15 * 60
RESET_TOKEN_SECONDS = 30 * 60
EMAIL_CHANGE_SECONDS = 30 * 60
# 举报是登录后的操作，按 user_id 限流比按 IP 更准（不会误伤同一 IP 下的其他人）。
REPORT_LIMIT = 10
REPORT_WINDOW_SECONDS = 60 * 60
# Anki 导出是一次性带走整份学习内容的操作，按用户每小时限流；体验账号直接拒绝。
ANKI_EXPORT_LIMIT = 10
ANKI_EXPORT_WINDOW_SECONDS = 60 * 60
ANKI_MAX_RECORDS = 5000
# 整本 JSON 导出：每用户每小时次数与题目记录数上限。
EXPORT_LIMIT = 10
EXPORT_WINDOW_SECONDS = 60 * 60
EXPORT_MAX_RECORDS = 5000
ANKI_SCOPES = ("all", "zone", "weak", "mastered")

_rate_lock = threading.Lock()
_rate_buckets = defaultdict(deque)
_sec_password_locks = defaultdict(threading.Lock)


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


def rate_peek(key, limit, window_seconds):
    """只查看桶是否已满，不记录本次调用。"""
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        return len(bucket) >= limit


def rate_note(key, window_seconds):
    """记录一次事件（例如一次登录失败），不判断是否超限。"""
    now = time.time()
    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        bucket.append(now)


def reset_rate_limits():
    with _rate_lock:
        _rate_buckets.clear()


def client_ip(request: Request):
    # 部署在 Caddy 之类的反向代理后面时，socket 地址永远是代理的内网 IP，
    # 限流会变成全站共用一个桶；设置 TRUST_PROXY=1（且应用端口不对公网开放）后，
    # 取代理追加在 X-Forwarded-For 最右侧的那一项（客户端自己伪造的项都在它左边）。
    if os.getenv("TRUST_PROXY") == "1":
        forwarded = request.headers.get("X-Forwarded-For", "")
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
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

    if request.url.path.startswith("/api/"):
        declared = request.headers.get("content-length", "")
        # 全局上限留足照片识别（8 MiB + multipart 开销）的余量；头像等各自的上限仍在接口内。
        if declared.isdigit() and int(declared) > API_MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "请求内容太大"},
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


# 与 static/capture.js 的 renderThinking 同一套识别规则：只认真正以
# “题目链接：”开头的行，取行内第一个 http(s) 链接，去掉句尾标点。
_ANKI_LINK_PREFIX = "题目链接："
_ANKI_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_ANKI_URL_TAIL_RE = re.compile(r"[.,;:!?)\u3001\u3002\uff0c\uff1b\uff01\uff1f\uff09]+$")


def anki_url_from_thinking(thinking):
    """从思路文字的“题目链接：”行提取第一个安全 http(s) 链接；没有则返回空串。"""
    for line in str(thinking or "").split("\n"):
        if not line.startswith(_ANKI_LINK_PREFIX):
            continue
        for match in _ANKI_URL_RE.finditer(line):
            candidate = _ANKI_URL_TAIL_RE.sub("", match.group(0))
            try:
                parsed = urlsplit(candidate)
            except ValueError:
                continue
            if parsed.scheme in ("http", "https") and parsed.netloc and not parsed.username:
                return candidate
    return ""


def anki_mastery_sets(conn, user_id, timezone_name, today):
    """按 mastery 的单条保持率把易错点分成 (薄弱 id 集合, 已掌握 id 集合)。

    判定与 mastery.py 完全一致：retention_on(item, today) 低于 AT_RISK_BELOW
    算还没掌握（薄弱），否则算已掌握；当天还不存在的记录不参与。
    """
    weak, mastered = set(), set()
    items = mastery_load_mistakes(conn, user_id, timezone_name)
    for mistake_id, item in items.items():
        retention = mastery_retention_on(item, today)
        if retention is None:
            continue
        (weak if retention < MASTERY_AT_RISK_BELOW else mastered).add(mistake_id)
    return weak, mastered


def anki_export_records(conn, user_id, timezone_name, today, scope, zone=None):
    """按 scope 取该用户未删除的易错点，组装成 build_anki_text 需要的 record 字典。"""
    wanted_ids = None
    if scope in ("weak", "mastered"):
        weak, mastered = anki_mastery_sets(conn, user_id, timezone_name, today)
        wanted_ids = weak if scope == "weak" else mastered

    sql = [
        """
        SELECT m.id, p.title, p.zone, p.thinking, p.code, m.description AS cause,
               m.pending_reason,
               (
                   SELECT v.answer_code FROM variants v
                   WHERE v.mistake_id = m.id AND v.result = 'solved'
                     AND v.answer_code != '' AND v.result_updated_at IS NOT NULL
                   ORDER BY v.result_updated_at DESC, v.id DESC LIMIT 1
               ) AS fixed_code
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """
    ]
    params = [user_id]
    if scope == "zone":
        sql.append("AND p.zone = ?")
        params.append(zone)
    if wanted_ids is not None:
        sql.append("AND m.id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(sorted(wanted_ids)))
    sql.append("ORDER BY m.id")
    rows = conn.execute(" ".join(sql), params).fetchall()
    tags_by_id = tags_for_mistakes(conn, [row["id"] for row in rows])
    records = []
    for row in rows:
        records.append({
            "id": row["id"],
            "title": row["title"],
            "zone": row["zone"],
            "url": anki_url_from_thinking(row["thinking"]),
            # 待补原因的卡片：背面原因处只显示"（待补）"，不泄露内部占位文案。
            "cause": PENDING_REASON_DISPLAY if row["pending_reason"] else row["cause"],
            "notes": row["thinking"],
            "code": row["code"],
            "fixed_code": row["fixed_code"] or "",
            "tags": tags_by_id[row["id"]],
        })
    return records


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


# ==================== 微信提醒（Server酱 / PushPlus） ====================

PUSH_TEST_LIMIT = 5
PUSH_TEST_WINDOW_SECONDS = 3600
PUSH_TEST_TITLE = f"{PRODUCT_NAME}：微信提醒测试"
PUSH_TEST_BODY = (
    "这是一条测试消息。能在微信里收到它，说明每日复习提醒的推送配置正确。\n"
    "（该消息由账号设置页的「发送测试消息」触发。）"
)
PUSH_RESULT_MESSAGES = {
    "ok": "测试消息已发送，请在微信里确认收到。",
    "network": "无法连接推送服务商，请检查服务器网络后稍后重试。",
    "auth": "服务商拒绝了密钥（认证失败），请核对 SendKey/token 后重新保存。",
    "rate_limited": "推送被服务商限流，请稍后再试。",
    "provider": "推送服务商暂时不可用，请稍后重试或更换渠道。",
}


def push_settings_payload(conn, user_id):
    """组装对外的设置状态：密钥任何时候都只回尾号 4 位，不回原文。"""
    row = conn.execute(
        "SELECT channel, secret, enabled, fail_count FROM user_push WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return {
            "channel": None, "enabled": False, "configured": False,
            "tail": "", "fail_count": 0,
        }
    secret = row["secret"]
    return {
        "channel": row["channel"],
        "enabled": bool(row["enabled"]),
        "configured": bool(secret),
        "tail": secret[-4:] if secret else "",
        "fail_count": row["fail_count"],
    }


def sec_recheck_session(conn, user_id, request):
    token = request.cookies.get("session", "")
    row = conn.execute(
        "SELECT 1 FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.user_id = ? AND s.expires_at > ? "
        "AND u.deleted_at IS NULL AND u.is_banned = 0",
        (token_hash(token), user_id, int(time.time())),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")


def revoke_other_sessions(conn, user_id, request):
    current_token = token_hash(request.cookies.get("session", ""))
    return conn.execute(
        "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
        (user_id, current_token),
    ).rowcount


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

    for table in ("sessions", "password_resets", "user_push", "mistake_scratch", "problems",
                  "mistake_tags", "weakness_insights", "mistake_clusters",
                  "ai_usage", "comment_votes",
                  "manual_payment_claims", "goals", "review_ops",
                  "problem_recommendations", "import_previews"):
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
    sec_anonymous_name = f"已注销用户 #{user_id}"
    sec_suffix = 2
    while conn.execute(
        "SELECT 1 FROM users WHERE username = ? AND id != ?",
        (sec_anonymous_name, user_id),
    ).fetchone() is not None:
        sec_anonymous_name = f"已注销用户 #{user_id}-{sec_suffix}"
        sec_suffix += 1
    conn.execute(
        """
        UPDATE users SET username = ?, email = NULL, password_hash = ?,
            avatar_version = 0, bio = '', lifetime_problem_count = 0,
            last_reminder_sent = NULL, is_admin = 0, deleted_at = ?
        WHERE id = ?
        """,
        (sec_anonymous_name, DUMMY_PASSWORD, deleted_at, user_id),
    )


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


def claim_http_error(error):
    return HTTPException(error.status_code, error.detail)


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


def window_total(counter, first, last):
    return sum(count for day, count in counter.items() if first <= day <= last)


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


REVIEW_OP_RETENTION = timedelta(days=30)
REVIEW_FUTURE_SLACK = timedelta(seconds=60)
REVIEW_BACKFILL_WINDOW = timedelta(days=7)


def pwa_review_op_replay(conn, user_id, mistake_id, client_op_id, now):
    """离线补交的幂等入口：清理 30 天前的记录，再查是否已处理过这个 client_op_id。

    必须在写事务（BEGIN IMMEDIATE）里调用：查和后面的写同属一个事务，
    并发的重复提交会在写锁上排队，后到的一定能看到先到的记录。
    命中时返回第一次保存的响应（不再评分），未命中返回 None。
    """
    cutoff = (datetime.fromisoformat(now) - REVIEW_OP_RETENTION).isoformat(timespec="seconds")
    conn.execute("DELETE FROM review_ops WHERE created_at < ?", (cutoff,))
    saved = conn.execute(
        "SELECT mistake_id, response FROM review_ops WHERE user_id = ? AND client_op_id = ?",
        (user_id, client_op_id),
    ).fetchone()
    if saved is None:
        return None
    if saved["mistake_id"] != mistake_id:
        raise HTTPException(422, "这个 client_op_id 已用于另一道题")
    return json.loads(saved["response"])


def pwa_reviewed_at(data, item, now):
    """校验并规范化客户端给的评分时间；返回 UTC、秒精度的 datetime。"""
    server_now = datetime.fromisoformat(now)
    given = data.reviewed_at.astimezone(timezone.utc)
    if given > server_now + REVIEW_FUTURE_SLACK:
        raise HTTPException(422, "评分时间不能晚于当前时间")
    if given < server_now - REVIEW_BACKFILL_WINDOW:
        raise HTTPException(422, "评分时间不能早于 7 天前")
    given = given.replace(microsecond=0)
    last = item["last_reviewed_at"]
    if last is not None and given < datetime.fromisoformat(last):
        raise HTTPException(409, "这道题之后已经有更新的评分")
    return given


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


class DuckTurn(InputModel):
    role: Literal["user", "duck"]
    # 单条上限略宽于前端输入框（600 字），总长度仍由 check_turns 的 4000 字封顶。
    text: str = Field(min_length=1, max_length=2000)


class DuckInput(InputModel):
    # 容许多一条进入轮数校验，以便返回清楚的轮数上限提示。
    turns: list[DuckTurn] = Field(max_length=2 * MAX_USER_TURNS + 1)
    finish: bool = False


DUCK_RETRY_HINT = (
    "刚才的回答不合格（{reason}）。请重新回答：只能提问或简短肯定，"
    "不要给出答案、解法、代码或结论，不要使用 Markdown。"
)
DUCK_BAD_REPLY = "小黄鸭这次没答好，请再试一次"


def duck_ai_reply(item, turns, finish):
    """一次请求内的橡皮鸭对话：回复不合格时带上原因提示重试一次，仍不合格抛 502。

    item 是 owned_mistake() 读出的当前用户记录；turns 已通过 check_turns 校验。
    回复校验与清理全部复用 duck_prompt.validate_reply，不另起一套。
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    notes = (item["thinking"] or "").strip()
    if item["description"].strip():
        notes = (notes + "\n当时的错因记录：" + item["description"].strip()).strip()
    problem = {
        "title": item["title"],
        "zone": item["zone"],
        "notes": notes,
        "code": item["code"],
    }
    messages = build_messages(problem, turns, finish)
    try:
        # 与其他 AI 入口一致：禁止 SDK 自动重试（重试由这里的校验逻辑显式控制）。
        with OpenAI(api_key=api_key, base_url=base_url, timeout=90.0, max_retries=0) as client:
            for attempt in range(2):
                note_usage(model, None)
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    # 回复上限 120 字；带隐藏推理过程的模型会把推理 token 也算进去，多留余量。
                    max_tokens=4000,
                )
                note_usage(model, response)
                try:
                    choice = response.choices[0]
                    content = choice.message.content
                    complete = choice.finish_reason == "stop"
                except (AttributeError, IndexError, TypeError):
                    content, complete = None, False
                text = content.strip() if complete and isinstance(content, str) else ""
                ok, result = validate_reply(text, finish)
                if ok:
                    return result
                if attempt == 0:
                    messages = messages + [
                        {"role": "assistant", "content": text or "（空回复）"},
                        {"role": "user", "content": DUCK_RETRY_HINT.format(reason=result)},
                    ]
    except APITimeoutError:
        raise HTTPException(504, "小黄鸭回复超时，请稍后重试") from None
    except RateLimitError:
        raise HTTPException(503, "AI 服务暂时不可用，请检查额度或稍后重试") from None
    except APIConnectionError:
        raise HTTPException(502, "暂时无法连接 AI 服务") from None
    except APIStatusError:
        raise HTTPException(502, "AI 请求失败，请管理员检查模型和 API 配置") from None
    raise HTTPException(502, DUCK_BAD_REPLY)


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


# ---- 榜单页：昨日之星、本周热门题目、今日一条（逻辑在 rank_board / hot_problems / rank_notice） ----


class PublicRankSetting(InputModel):
    participate: StrictBool


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


POST_LIST_DEFAULT_LIMIT = 20
POST_LIST_MAX_OFFSET = 10000
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


def checked_post_zone(zone):
    if zone is not None and zone not in PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    return zone


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


@app.exception_handler(RequestValidationError)
async def forum_action_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/me/bio" and request.method == "PUT":
        # Validation errors must not echo malformed Unicode input into UTF-8 JSON.
        return JSONResponse(
            status_code=422, content={"detail": "简介内容无效，请填写最多 200 字的文字"},
        )
    if re.fullmatch(
        r"/api/posts/[^/]+/(?:accepted|summary)|/api/comments/[^/]+/helpful"
        r"|/api/mistakes/[^/]+/(?:preview|review(?:/undo)?|snooze|suspend|unsuspend|run)"
        r"|/api/me/review-settings|/api/review/queue|/api/import/confirm"
        r"|/api/admin/manual-claims/[^/]+/confirm",
        request.url.path,
    ):
        return JSONResponse(
            status_code=422, content={"detail": "请求参数不正确，请检查后重试"},
        )
    return await request_validation_exception_handler(request, exc)


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


# ===== [generated] routers 挂载（main.py 拆分脚本生成，请勿手改） =====
# 路由 handlers 已按领域拆分到 routers/；此处按原有相对顺序挂载。
# 共享依赖保留在 main 命名空间，routers 内以 main.xxx 引用，
# 以保证测试中的 monkeypatch.setattr(main, ...) 继续生效。
import routers.pages
import routers.auth
import routers.account
import routers.payments
import routers.stats
import routers.problems
import routers.mistakes
import routers.review
import routers.rank
import routers.groups
import routers.forum
import routers.admin
import routers.recommend
import routers.profile
import routers.import_wizard

app.include_router(routers.pages.router)
app.include_router(routers.auth.router)
app.include_router(routers.account.router)
app.include_router(routers.payments.router)
app.include_router(routers.stats.router)
app.include_router(routers.problems.router)
app.include_router(routers.mistakes.router)
app.include_router(routers.review.router)
app.include_router(routers.rank.router)
app.include_router(routers.groups.router)
app.include_router(routers.forum.router)
app.include_router(routers.admin.router)
app.include_router(routers.recommend.router)
app.include_router(routers.profile.router)
app.include_router(routers.import_wizard.router)

# 以下名字被测试或其它 routers 以 main.<name> 引用，在此重新导出：
from routers.account import revoke_sessions  # noqa: F401
from routers.stats import create_weakness_analysis  # noqa: F401
from routers.problems import recognize_problem_photo  # noqa: F401
from routers.review import rvb_undo_review  # noqa: F401
from routers.groups import list_groups  # noqa: F401
from routers.forum import accept_comment, create_thread_summary, delete_comment, delete_post, unaccept_comment  # noqa: F401
# ===== [generated] end =====

# Optional isolated code execution is registered outside the generated block.
import routers.code_run
app.include_router(routers.code_run.router)
