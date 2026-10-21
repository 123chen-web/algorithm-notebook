"""前端错误上报的请求体：全部字段限长/限字符，多余字段一律拒绝。"""
from typing import Annotated, Optional

from pydantic import BeforeValidator, Field, StrictInt, StringConstraints

from models.base import InputModel


def _truncate(limit):
    # 过长的文本截断而不是拒绝：旧版前端或浏览器给的堆栈偶尔会超长。
    def apply(value):
        return value[:limit] if isinstance(value, str) else value
    return BeforeValidator(apply)


Message = Annotated[str, _truncate(300), StringConstraints(min_length=1, max_length=300)]
SourceName = Annotated[str, _truncate(100), StringConstraints(max_length=100, pattern=r"^[\w.\-]*$")]
StackText = Annotated[str, _truncate(1500), StringConstraints(max_length=1500)]
UserAgent = Annotated[str, _truncate(200), StringConstraints(max_length=200)]
RouteName = Annotated[str, StringConstraints(max_length=32, pattern=r"^(#/[A-Za-z][A-Za-z0-9_-]{0,29})?$")]
BuildTag = Annotated[str, StringConstraints(max_length=20, pattern=r"^[0-9A-Za-z._-]+$")]
LineNo = Annotated[StrictInt, Field(ge=0, le=10_000_000)]


class ClientErrorReport(InputModel):
    message: Message
    source: SourceName = ""
    line: LineNo = 0
    col: LineNo = 0
    stack: StackText = ""
    route: RouteName = ""
    ua: UserAgent = ""
    build: Optional[BuildTag] = None
