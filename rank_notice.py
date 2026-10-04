"""“今日一条”：管理员手写的一句话 + 可选链接 + 展示日期范围。

内容完全由管理员输入，不抓取任何第三方网站。日期是北京时间自然日，含首尾。
"""
import unicodedata
from datetime import date
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

NOTICE_MAX_CHARS = 80
LINK_MAX_CHARS = 500
MAX_SPAN_DAYS = 366
HISTORY_LIMIT = 100
# 双向文字控制符可以让文字显示成别的样子，一律拒绝。
_BIDI_CONTROLS = set("‪‫‬‭‮⁦⁧⁨⁩")


def clean_text(value):
    value = value.strip()
    if not value:
        raise ValueError("请填写一句话")
    if len(value) > NOTICE_MAX_CHARS:
        raise ValueError(f"最多 {NOTICE_MAX_CHARS} 个字")
    if any(unicodedata.category(char) == "Cc" or char in _BIDI_CONTROLS for char in value):
        raise ValueError("不能包含换行或控制字符")
    return value


def clean_link(value):
    """只允许 http/https、有主机名、不带账号密码的链接；空串视为没有链接。"""
    value = value.strip()
    if not value:
        return None
    if len(value) > LINK_MAX_CHARS:
        raise ValueError(f"链接最多 {LINK_MAX_CHARS} 个字符")
    if any(char.isspace() or unicodedata.category(char) == "Cc" or char == "\\" for char in value):
        raise ValueError("链接不能包含空白、控制字符或反斜杠")
    try:
        parts = urlsplit(value)
        parts.port  # 访问一次触发端口格式校验
    except ValueError:
        raise ValueError("链接格式不正确") from None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("链接必须以 http:// 或 https:// 开头")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("链接不能带账号或密码")
    return value


class NoticeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    link: Optional[str] = None
    start_date: date
    end_date: date
    is_active: bool = True

    @field_validator("text")
    @classmethod
    def _text(cls, value):
        return clean_text(value)

    @field_validator("link")
    @classmethod
    def _link(cls, value):
        return None if value is None else clean_link(value)

    @model_validator(mode="after")
    def _range(self):
        if self.end_date < self.start_date:
            raise ValueError("结束日期不能早于开始日期")
        if (self.end_date - self.start_date).days >= MAX_SPAN_DAYS:
            raise ValueError(f"展示范围最长 {MAX_SPAN_DAYS} 天")
        return self


def status_of(row, today):
    if not row["is_active"]:
        return "disabled"
    if row["end_date"] < today:
        return "expired"
    if row["start_date"] > today:
        return "scheduled"
    return "active"


def admin_item(row, today):
    return {
        "id": row["id"], "text": row["text"], "link": row["link"],
        "start_date": row["start_date"], "end_date": row["end_date"],
        "is_active": bool(row["is_active"]), "status": status_of(row, today),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def list_notices(conn, today):
    rows = conn.execute(
        "SELECT * FROM daily_notices ORDER BY id DESC LIMIT ?", (HISTORY_LIMIT,)
    ).fetchall()
    return [admin_item(row, today.isoformat()) for row in rows]


def current_notice(conn, today):
    """当天有效的那一条（启用且日期范围含今天）；多条重叠时取开始日期最近、最新建的。"""
    row = conn.execute(
        """
        SELECT text, link FROM daily_notices
        WHERE is_active = 1 AND start_date <= ? AND end_date >= ?
        ORDER BY start_date DESC, id DESC LIMIT 1
        """,
        (today.isoformat(), today.isoformat()),
    ).fetchone()
    return {"text": row["text"], "link": row["link"]} if row else None


def create_notice(conn, data, user_id, now):
    cursor = conn.execute(
        """
        INSERT INTO daily_notices(text, link, start_date, end_date, is_active,
                                  created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (data.text, data.link, data.start_date.isoformat(), data.end_date.isoformat(),
         int(data.is_active), user_id, now, now),
    )
    return cursor.lastrowid


def update_notice(conn, notice_id, data, now):
    cursor = conn.execute(
        """
        UPDATE daily_notices
        SET text = ?, link = ?, start_date = ?, end_date = ?, is_active = ?, updated_at = ?
        WHERE id = ?
        """,
        (data.text, data.link, data.start_date.isoformat(), data.end_date.isoformat(),
         int(data.is_active), now, notice_id),
    )
    return cursor.rowcount
