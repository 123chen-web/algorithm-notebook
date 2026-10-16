"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""
from typing import Literal

from pydantic import Field

from models.base import InputModel
from duck_prompt import MAX_USER_TURNS

class DuckTurn(InputModel):
    role: Literal["user", "duck"]
    # 单条上限略宽于前端输入框（600 字），总长度仍由 check_turns 的 4000 字封顶。
    text: str = Field(min_length=1, max_length=2000)


class DuckInput(InputModel):
    # 容许多一条进入轮数校验，以便返回清楚的轮数上限提示。
    turns: list[DuckTurn] = Field(max_length=2 * MAX_USER_TURNS + 1)
    finish: bool = False
    # 卡住时要第几级提示（1 方向 / 2 小反例 / 3 指出哪一行）；0 是普通追问。
    hint: int = Field(0, ge=0, le=3)

