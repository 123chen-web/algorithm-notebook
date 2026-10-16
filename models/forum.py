"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""
from typing import Annotated

from pydantic import Field, StrictInt, StringConstraints

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

