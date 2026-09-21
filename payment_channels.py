import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError


PaymentReference = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"\S")
]


class VerifiedCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: PaymentReference
    channel: Literal["alipay", "wechat"]
    provider_trade_no: PaymentReference
    amount_cents: int = Field(strict=True, gt=0)
    status: Literal["paid", "failed", "closed"]


class CallbackVerificationError(ValueError):
    pass


class PaymentChannelError(RuntimeError):
    pass


class PaymentChannel(Protocol):
    def create_payment(self, order: Mapping[str, object]) -> dict:
        ...

    def verify_callback(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> VerifiedCallback:
        ...


class MockChannel:
    def __init__(self, channel: str, secret: str):
        if channel not in ("alipay", "wechat"):
            raise ValueError("不支持的支付渠道")
        if not secret or not secret.strip():
            raise PaymentChannelError("Mock 支付尚未配置签名密钥")
        self.channel = channel
        self._secret = secret.encode("utf-8")

    def create_payment(self, order: Mapping[str, object]) -> dict:
        # 仅为本地联调占位，不向真实支付平台发起请求。
        return {
            "provider": "mock",
            "qr_code_url": f"mock://{self.channel}/{order['id']}",
            "redirect_url": None,
        }

    def sign_callback(self, raw_body: bytes) -> str:
        # 联调脚本用服务端密钥签名；密钥和签名能力不通过 API 返回。
        return hmac.new(self._secret, raw_body, hashlib.sha256).hexdigest()

    def verify_callback(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> VerifiedCallback:
        signatures = [
            value for key, value in headers.items()
            if key.lower() == "x-mock-signature"
        ]
        if len(signatures) != 1:
            raise CallbackVerificationError("Mock 回调签名无效")
        signature = signatures[0]
        if (
            not isinstance(signature, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", signature) is None
            or not hmac.compare_digest(self.sign_callback(raw_body), signature.lower())
        ):
            raise CallbackVerificationError("Mock 回调签名无效")

        # 对原始字节验签后再解析，拒绝缺失、额外字段和金额的隐式类型转换。
        try:
            callback = VerifiedCallback.model_validate_json(raw_body)
        except ValidationError:
            raise CallbackVerificationError("Mock 回调内容无效") from None
        if callback.channel != self.channel:
            raise CallbackVerificationError("Mock 回调支付渠道不匹配")
        return callback


def get_channel(channel: str) -> PaymentChannel:
    if channel not in ("alipay", "wechat"):
        raise ValueError("不支持的支付渠道")
    # 默认关闭；下一阶段在此接入真实适配器，订单生命周期不依赖 SDK。
    if os.getenv("PAYMENTS_MOCK_ENABLED") != "1":
        raise PaymentChannelError("Mock 支付尚未启用")
    return MockChannel(channel, os.getenv("PAYMENTS_MOCK_SECRET", ""))
