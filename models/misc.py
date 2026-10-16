"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from models.base import InputModel

class PushSettingsInput(InputModel):
    # 微信提醒设置：渠道二选一；secret 为 null / 空串表示保持原密钥不变；
    # 密钥格式在接口里复用 push_channels.valid_key 校验。
    channel: Literal["serverchan", "pushplus"]
    secret: Annotated[str, StringConstraints(strict=True, max_length=128)] | None = None
    enabled: bool = Field(strict=True)

