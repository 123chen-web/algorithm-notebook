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


class NewManualClaim(InputModel):
    plan_id: int = Field(strict=True, gt=0)
    actual_paid_cents: int = Field(strict=True, gt=0, lt=2**53)
    payer_receipt: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=200)] = ''
    payer_note: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=60)]
    contact: Annotated[str, StringConstraints(
        strict=True, strip_whitespace=True, max_length=60)] = ""

    @field_validator("payer_note", "contact", "payer_receipt")
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



def _plain_text(value):
    # 不含换行、制表符等控制/格式字符，登记内容会原样展示给站长。
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("内容不能包含控制字符")
    return value
