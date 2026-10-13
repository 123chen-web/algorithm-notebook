"""模型基类。"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
# 错因描述现在是可选的：留空时由 AI 在生成练习题时自动诊断并回填，
# 不再要求用户先自己说清楚错在哪。
MistakeText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000)
]
ThinkingText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

MistakeText = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=2000)
]
ThinkingText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

ThinkingText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

PostTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

PostBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]
PostSearchQuery = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200)
]
CommentBody = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
