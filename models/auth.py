"""pydantic 输入/输出模型。纯数据定义，不依赖业务模块。"""

from pydantic import Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from models.base import InputModel
from core.security import normalize_email

class Credentials(InputModel):
    # 不再限制字符集（原来只认 ASCII 字母/数字/下划线，中文用户名会被拒绝），
    # 只保留最基本的边界：非空、长度封顶。保留原始输入供存量账号精确
    # 匹配，注册另行规范化；讨论区渲染用户名走 textContent，不走
    # innerHTML，这里放开字符集不会引入 XSS。
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=6, max_length=128)


class Registration(Credentials):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)
    accept_terms: bool = False
    invite_code: str = Field(min_length=1, max_length=256)
    email: str = Field(min_length=3, max_length=254)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        return normalize_timezone(value)


class TrialSignup(InputModel):
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        return normalize_timezone(value)


class EmailChangeConfirm(InputModel):
    token: str = Field(min_length=1, max_length=200)


class ForgotPassword(InputModel):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class ResetPassword(InputModel):
    # 空链接/空密码也由业务校验返回可供前端区分的 400 code。
    token: str = Field(max_length=512)
    password: str = Field(max_length=128)


class EmailUpdate(InputModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        return normalize_email(value)


class UsernameUpdate(InputModel):
    username: str = Field(min_length=1, max_length=32)


class PasswordChange(InputModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AccountDeletion(InputModel):
    password: str = Field(min_length=1, max_length=128)



def normalize_timezone(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("请填写有效时区，例如 Asia/Shanghai") from None
    return value
