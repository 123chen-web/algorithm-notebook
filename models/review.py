"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StringConstraints, field_validator, model_validator

from models.base import InputModel
from scratch import _js_string_length
from core.config import CLIENT_OP_ID_PATTERN

class ScratchPut(InputModel):
    # 草稿演算区：code/fixed 为代码文本，table 为 {cols, rows} 或 null；
    # table 的结构校验在接口里按 static/trace-table.js 的 validate 规则做。
    version: int = Field(strict=True, ge=0)
    code: Annotated[str, StringConstraints(strict=True, max_length=100000)]
    fixed: Annotated[str, StringConstraints(strict=True, max_length=100000)]
    table: dict | None


class ReviewInput(InputModel):
    quality: int = Field(strict=True, ge=0, le=5)
    # 带 client_op_id 的离线补交可能拿不到版本号，此时省略 version 并跳过版本比较；
    # 不带 client_op_id 的普通评分 version 仍然必填。
    version: int | None = Field(default=None, strict=True, ge=0)
    client_op_id: str | None = Field(default=None, pattern=CLIENT_OP_ID_PATTERN)
    reviewed_at: datetime | None = None
    # 复习=重做：mode 只接受三个固定值；旧客户端不传时默认为 recall。
    mode: Literal["recall", "rewrite", "redo", "similar"] = "recall"
    # 小 JSON：{"suggested":4,"final":3,"similarity":0.82,"seconds":412}，
    # JS 码元长度 ≤ 500。空串表示不传（旧客户端兼容）。
    evidence: str = ""

    @field_validator("evidence")
    @classmethod
    def evidence_shape(cls, value):
        if not value:
            return value
        if _js_string_length(value) > 500:
            raise ValueError("evidence 长度不能超过 500 字符")
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("evidence 必须是 JSON 对象") from None
        if not isinstance(parsed, dict):
            raise ValueError("evidence 必须是 JSON 对象")
        allowed = {"suggested", "final", "similarity", "seconds", "result", "limit_seconds", "auto", "ref"}
        for key in parsed:
            if key not in allowed:
                raise ValueError(f"evidence 不支持的字段：{key}")
        suggested = parsed.get("suggested")
        final = parsed.get("final")
        for key, val in (("suggested", suggested), ("final", final)):
            if val is not None and not (isinstance(val, int) and 0 <= val <= 5):
                raise ValueError("evidence 的 suggested/final 必须是 0 到 5 的整数")
        similarity = parsed.get("similarity")
        if similarity is not None and not (
            isinstance(similarity, (int, float)) and 0 <= similarity <= 1
        ):
            raise ValueError("evidence 的 similarity 必须是 0 到 1 的数字")
        for key in ("seconds", "limit_seconds"):
            number = parsed.get(key)
            if number is not None and not (isinstance(number, int) and not isinstance(number, bool) and number >= 0):
                raise ValueError(f"evidence 的 {key} 必须是非负整数")
        result = parsed.get("result")
        if result is not None and result not in ("clean", "retry", "hinted", "failed"):
            raise ValueError("evidence 的 result 不合法")
        if "auto" in parsed and parsed["auto"] is not True:
            raise ValueError("evidence 的 auto 只能是 true")
        ref = parsed.get("ref")
        if ref is not None and not (isinstance(ref, str) and len(ref) <= 60):
            raise ValueError("evidence 的 ref 必须是不超过 60 字的文字")
        return value

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


class SpreadUndoItem(InputModel):
    id: StrictInt
    due_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    version: StrictInt


class SpreadUndoInput(InputModel):
    # 撤销“补欠账”：把摊出去的记录改回原来的到期日；只改没再动过的（版本号对得上）。
    items: list[SpreadUndoItem] = Field(max_length=5000)


class RvbSnoozeInput(RvbVersionInput):
    days: StrictInt

    @field_validator("days")
    @classmethod
    def allowed_days(cls, value):
        if value not in (1, 3, 7):
            raise ValueError("推迟天数只能是 1、3 或 7")
        return value


class RvbSettingsInput(InputModel):
    daily_review_cap: Annotated[StrictInt, Field(ge=5, le=200)] | None = None
    # 复习方式偏好：recall_only（只回忆，默认）/ redo_first（重做优先）。
    redo_preference: Literal["redo_first", "recall_only"] | None = None


class VariantResult(InputModel):
    result: Literal["unattempted", "solved", "partial", "failed"]
    answer_code: str = Field(default="", max_length=40000)
    answer: str = Field(default="", max_length=500)

