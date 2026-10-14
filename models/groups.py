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


class PublicRankSetting(InputModel):
    participate: StrictBool


class WeeklyGoalInput(InputModel):
    goal_type: Literal["review", "record"]
    target: Annotated[int, Field(ge=5, le=500)]


class GroupTodayVisibilityInput(InputModel):
    show: StrictBool


class SummaryPreference(InputModel):
    include_in_summary: StrictBool

