"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""
import re
import unicodedata
from urllib.parse import urlsplit
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
import error_taxonomy
from models.base import InputModel, MistakeText, ThinkingText, Title
from core.config import CODE_ZONES, QUICK_CODE_PLACEHOLDER, QUICK_THINKING_PLACEHOLDER

class ProblemFields(InputModel):
    title: Title
    zone: str
    # 数学类分区没有编程语言，允许留空；是否必填由下面的整体校验按分区判断。
    language: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=40)
    ] = ""
    code: str = Field(min_length=1, max_length=40000)
    thinking: ThinkingText
    # 可选：用户自己补的正确代码；网站不提供标准答案。
    correct_code: str = Field(default="", max_length=40000)
    # 可选：题目来源链接（粘贴链接识别时填）。新增时 None 当作空；编辑时 None = 保持不变，"" = 清除。
    source_url: str | None = Field(default=None, max_length=2000)
    # 判题类型：WA/TLE/MLE/RE/CE/NOANS 或空串（未填）。只在算法分区有意义，
    # 前端只在算法分区显示下拉；其他分区传了也接受（服务端不强制分区）。
    verdict: str = Field(default="")

    @field_validator("verdict")
    @classmethod
    def valid_verdict(cls, value):
        try:
            return error_taxonomy.validate_verdict(value)
        except error_taxonomy.TaxonomyError as error:
            raise ValueError(str(error)) from None

    @field_validator("source_url")
    @classmethod
    def valid_source_url(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            return ""
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise ValueError("题目链接格式不正确") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            raise ValueError("题目链接只支持 http(s) 地址")
        return value
    # 可选：这道错题属于哪些套路（必须在 static/data/patterns.json 白名单里，最多 3 个）。
    # 新增时不传/为空表示不关联；编辑时不传表示保持不变，传 [] 表示清空。
    pattern_ids: list[str] | None = Field(default=None, max_length=8)

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


class MistakeInput(InputModel):
    """新增记录时一条易错点的输入：描述 + 可选的最小反例。

    兼容旧客户端：mistakes 数组里仍可以直接放字符串，此时 counterexample 为空。
    """

    description: MistakeText = ""
    counterexample: str = Field(default="", max_length=1000)


class NewProblem(ProblemFields):
    mistakes: list[MistakeText | MistakeInput] = Field(default_factory=list, max_length=10)
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
    # 最小反例：纯文本 ≤1000 字，等宽显示。编辑时不传视为清空，前端总是带上当前值。
    counterexample: str = Field(default="", max_length=1000)


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

