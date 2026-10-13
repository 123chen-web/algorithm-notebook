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
from models.base import InputModel

class PushSettingsInput(InputModel):
    # 微信提醒设置：渠道二选一；secret 为 null / 空串表示保持原密钥不变；
    # 密钥格式在接口里复用 push_channels.valid_key 校验。
    channel: Literal["serverchan", "pushplus"]
    secret: Annotated[str, StringConstraints(strict=True, max_length=128)] | None = None
    enabled: bool = Field(strict=True)

