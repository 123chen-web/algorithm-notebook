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

import tags


router = APIRouter()

TOKEN_PREFIX = "oyt_"
EXTENSION_SOURCES = ("leetcode", "leetcode-cn", "codeforces", "nowcoder")
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


class ExtensionImport(BaseModel):
    source: Literal["leetcode", "leetcode-cn", "codeforces", "nowcoder"]
    source_url: str = Field(max_length=SOURCE_URL_MAX_LENGTH)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=STATEMENT_MAX_LENGTH)
    difficulty: str = Field(default="", max_length=DIFFICULTY_MAX_LENGTH)
    tags: list[str] = Field(default_factory=list, max_length=TAGS_MAX_COUNT)
    language: str = Field(default="", max_length=40)
    zone: str = "算法"

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
    return {
        "id": problem_id,
        "title": data.title,
        "source": data.source,
        "source_url": data.source_url,
    }
