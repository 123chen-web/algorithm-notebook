"""plugin_api routes: 刷题伴侣浏览器插件的后端接口。

- POST /api/users/api-token：已登录用户签发/轮换长期 API token（明文只返回一次，
  数据库只存 SHA256，与 sessions.token_hash 同一做法）。
- POST /api/problems/import-from-extension：Bearer token 认证，一键建题
  （与 F1 的只预填不建题不同，插件做的是"看到题目 → 直接入库"）。

设计说明：
- token 长期有效，签发即轮换（旧 token 立即失效）；用户可在网页端重新签发。
- 导入的题目 code/thinking 留空（用户做题后在网页端补充）；不自动建错题，
  因为"想做的题"不是"做错的题"，不污染复习与热力图数据。
- tags 接受并校验（与错题标签同一套规范），但 v1 不落库：标签在本应用的数据
  模型里挂在错题（mistake_tags）上，题目级标签需要新的数据模型，留给后续版本。
- 扩展请求同样要带 X-CSRF-Protection: 1（全局中间件要求）。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

import re
import secrets
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

import tags


router = APIRouter()

TOKEN_PREFIX = "oyt_"
EXTENSION_SOURCES = ("leetcode", "leetcode-cn", "codeforces", "nowcoder", "luogu", "atcoder")
# 新增来源必须使用各自官方域名；已有来源保持原先只校验 http(s) 的宽松规则不变。
STRICT_SOURCE_HOSTS = {
    "luogu": ("luogu.com.cn",),
    "atcoder": ("atcoder.jp",),
}
REASON_MAX_LENGTH = 500
READ_LIMIT_PER_HOUR = 300
APPEND_LIMIT_PER_HOUR = 120
STATEMENT_MAX_LENGTH = 100_000
SOURCE_URL_MAX_LENGTH = 2000
DIFFICULTY_MAX_LENGTH = 40
TAGS_MAX_COUNT = 20


def _bearer_user(authorization: str | None = Header(default=None)):
    """用 Authorization: Bearer <api_token> 解析出用户；失败一律 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "API token 无效")
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(401, "API token 无效")
    with main.connect() as conn:
        row = conn.execute(
            """
            SELECT id, username, timezone, is_banned
            FROM users
            WHERE api_token_hash = ? AND deleted_at IS NULL
            """,
            (main.token_hash(token),),
        ).fetchone()
    if row is None or row["is_banned"]:
        raise HTTPException(401, "API token 无效")
    return {"id": row["id"], "api_token_hash": main.token_hash(token)}


@router.post("/api/users/api-token")
def issue_api_token(user=Depends(main.current_user)):
    """签发（或轮换）当前登录用户的插件 API token，明文只在本次返回。"""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET api_token_hash = ? WHERE id = ?",
            (main.token_hash(token), user["id"]),
        )
    return {"token": token}


@router.delete("/api/users/api-token")
def revoke_api_token(user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        conn.execute("UPDATE users SET api_token_hash = NULL WHERE id = ?", (user["id"],))
    return {"ok": True}


def _host_matches(hostname, allowed):
    hostname = (hostname or "").lower().rstrip(".")
    return any(hostname == host or hostname.endswith("." + host) for host in allowed)


_PATH_RULES = {
    "luogu": re.compile(r"^/problem/([A-Za-z0-9_]+)/?$"),
    "leetcode": re.compile(r"^/problems/([A-Za-z0-9_-]+)(?:/.*)?$"),
    "leetcode-cn": re.compile(r"^/problems/([A-Za-z0-9_-]+)(?:/.*)?$"),
    "nowcoder": re.compile(r"^/(?:acm/)?problem/(\d+)/?$|^/practice/([0-9a-fA-F]+)/?$"),
    "atcoder": re.compile(r"^/contests/[A-Za-z0-9_-]+/tasks/([A-Za-z0-9_]+)/?$"),
}
_CF_RULES = (
    re.compile(r"^/problemset/problem/(\d+)/([A-Za-z0-9]+)/?$"),
    re.compile(r"^/(?:contest|gym)/(\d+)/problem/([A-Za-z0-9]+)/?$"),
)


def normalize_source_id(source, value):
    """查重用的统一写法：洛谷与 Codeforces 的题号一律大写，其余保持原样。"""
    value = (value or "").strip()
    if source in ("luogu", "codeforces"):
        return value.upper()
    return value


def problem_source_id(source, url):
    """从题目链接推出该来源下的题目标识；无法识别时返回 None。

    规则与扩展一致：洛谷题号大写、力扣 slug、牛客数字 id、Codeforces 为
    contestId+题号大写、AtCoder 为 task id。"""
    try:
        path = urlparse(url or "").path
    except ValueError:
        return None
    if source == "codeforces":
        for rule in _CF_RULES:
            match = rule.match(path)
            if match:
                return (match.group(1) + match.group(2)).upper()
        return None
    rule = _PATH_RULES.get(source)
    match = rule.match(path) if rule else None
    if not match:
        return None
    value = next((group for group in match.groups() if group), None)
    return normalize_source_id(source, value) if value else None


class ExtensionImport(BaseModel):
    source: Literal["leetcode", "leetcode-cn", "codeforces", "nowcoder", "luogu", "atcoder"]
    source_url: str = Field(max_length=SOURCE_URL_MAX_LENGTH)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=STATEMENT_MAX_LENGTH)
    difficulty: str = Field(default="", max_length=DIFFICULTY_MAX_LENGTH)
    tags: list[str] = Field(default_factory=list, max_length=TAGS_MAX_COUNT)
    language: str = Field(default="", max_length=40)
    zone: str = "算法"
    # 错因一句话（对应速记模式）：可选；有则建题后再建一条错题。
    pending_reason: str | None = Field(default=None, max_length=REASON_MAX_LENGTH)

    @field_validator("pending_reason")
    @classmethod
    def _strip_reason(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("错因不能为空")
        return value

    @model_validator(mode="after")
    def _strict_source_host(self):
        allowed = STRICT_SOURCE_HOSTS.get(self.source)
        if allowed and not _host_matches(urlparse(self.source_url).hostname, allowed):
            raise ValueError(f"source_url 必须是 {allowed[0]} 的链接")
        return self

    @field_validator("source_url")
    @classmethod
    def _valid_source_url(cls, value):
        value = value.strip()
        try:
            parsed = urlparse(value)
        except ValueError:
            raise ValueError("source_url 格式不正确")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("source_url 必须是 http(s) 链接")
        return value

    @field_validator("title", "content")
    @classmethod
    def _strip_nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("difficulty", "language")
    @classmethod
    def _strip(cls, value):
        return value.strip()

    @field_validator("zone")
    @classmethod
    def _valid_zone(cls, value):
        if value not in main.PROBLEM_ZONES:
            raise ValueError("请选择一个有效的题目分区")
        return value

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, value):
        # 与错题标签同一套规范；v1 不落库（见模块 docstring），只做校验，
        # 保证扩展端的合约稳定，后续题目级标签落地时无需改接口。
        try:
            return tags.normalize_tags(value)
        except tags.TagError as error:
            raise ValueError(str(error))


@router.post("/api/problems/import-from-extension", status_code=201)
def import_from_extension(data: ExtensionImport, user=Depends(_bearer_user)):
    """插件一键导入：在用户本题库直接建题，返回题目 id。"""
    with main.connect(write=True) as conn:
        # Revalidate under the write lock: revocation/rotation/ban may have
        # completed after the dependency read and must block this import too.
        fresh = conn.execute(
            "SELECT id FROM users WHERE id = ? AND api_token_hash = ? "
            "AND deleted_at IS NULL AND is_banned = 0",
            (user["id"], user["api_token_hash"]),
        ).fetchone()
        if fresh is None:
            raise HTTPException(401, "API token 无效")
        if main.rate_limited(f"extension-import:{user['id']}", 60, 3600):
            raise HTTPException(429, "每小时最多从扩展导入 60 道题，请稍后再试")
        cursor = conn.execute(
            """
            INSERT INTO problems(
                user_id, title, zone, language, code, thinking,
                source, source_url, statement, difficulty, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], data.title, data.zone, data.language,
                "", "",
                data.source, data.source_url, data.content,
                data.difficulty, main.utc_now(),
            ),
        )
        problem_id = cursor.lastrowid
        mistake_id = None
        if data.pending_reason:
            mistake_id = _insert_reason(conn, problem_id, data.pending_reason, user["id"])
    return {
        "id": problem_id,
        "title": data.title,
        "source": data.source,
        "source_url": data.source_url,
        "mistake_id": mistake_id,
    }


def _insert_reason(conn, problem_id, reason, user_id):
    """为题目建一条带错因的错题（今天到期），与新增记录里的错题同一写法。"""
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    day = main.today_for(dict(row)).isoformat()
    cursor = conn.execute(
        "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
        (problem_id, reason, day),
    )
    return cursor.lastrowid


def _fresh_user(conn, user):
    """在连接内再次确认 token 仍有效（撤销/轮换/封禁后立即失效）。"""
    row = conn.execute(
        "SELECT * FROM users WHERE id = ? AND api_token_hash = ? "
        "AND deleted_at IS NULL AND is_banned = 0",
        (user["id"], user["api_token_hash"]),
    ).fetchone()
    if row is None:
        raise HTTPException(401, "API token 无效")
    return dict(row)


@router.get("/api/problems/by-source")
def problem_by_source(source: str, source_id: str, user=Depends(_bearer_user)):
    """按 (来源, 题目标识) 查询本人是否已收录；只返回是否存在与题目 id。"""
    if source not in EXTENSION_SOURCES:
        raise HTTPException(422, "不支持的来源")
    wanted = normalize_source_id(source, source_id)
    if not wanted or len(wanted) > 200:
        raise HTTPException(422, "source_id 不合法")
    with main.connect() as conn:
        _fresh_user(conn, user)
        if main.rate_limited(f"extension-read:{user['id']}", READ_LIMIT_PER_HOUR, 3600):
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        rows = conn.execute(
            "SELECT id, source_url FROM problems WHERE user_id = ? AND source = ? ORDER BY id",
            (user["id"], source),
        ).fetchall()
    for row in rows:
        if problem_source_id(source, row["source_url"]) == wanted:
            return {"exists": True, "problem_id": row["id"]}
    return {"exists": False, "problem_id": None}


class AppendReason(BaseModel):
    reason: str = Field(min_length=1, max_length=REASON_MAX_LENGTH)
    quick: bool = True

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("错因不能为空")
        return value


@router.post("/api/problems/{problem_id}/mistakes", status_code=201)
def append_reason(problem_id: int, data: AppendReason, user=Depends(_bearer_user)):
    """给本人已有的题追加一条错因（速记）；不影响该题已有的复习计划。"""
    with main.connect(write=True) as conn:
        _fresh_user(conn, user)
        if main.rate_limited(f"extension-append:{user['id']}", APPEND_LIMIT_PER_HOUR, 3600):
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        owned = conn.execute(
            "SELECT id FROM problems WHERE id = ? AND user_id = ?", (problem_id, user["id"])
        ).fetchone()
        if owned is None:
            raise HTTPException(404, "题目不存在")
        mistake_id = _insert_reason(conn, problem_id, data.reason, user["id"])
    return {"id": mistake_id, "problem_id": problem_id, "status": "recorded"}


@router.get("/api/review/due-count")
def due_count(user=Depends(_bearer_user)):
    """今天到期且未暂停的易错点数量（只返回数字，不含每日上限折算）。"""
    with main.connect() as conn:
        account = _fresh_user(conn, user)
        if main.rate_limited(f"extension-read:{user['id']}", READ_LIMIT_PER_HOUR, 3600):
            raise HTTPException(429, "请求过于频繁，请稍后再试")
        today = main.today_for(account).isoformat()
        count = conn.execute(
            """
            SELECT COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?
            """,
            (user["id"], today),
        ).fetchone()[0]
    return {"due_count": count}
