"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import (
    ALLOWED_IMAGE_FORMATS,
    MAX_PATTERNS_PER_PROBLEM,
    PATTERN_STATUSES,
    PROBLEM_ZONES,
)
from models.base import InputModel, PostBody, PostTitle

PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

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

