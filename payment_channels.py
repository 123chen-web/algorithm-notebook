import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from email.message import Message
from typing import Annotated, Literal, Protocol
from urllib.parse import parse_qsl, urlsplit

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


class AlipayChannel:
    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        public_key: str,
        seller_id: str,
        notify_url: str,
        sandbox: bool = False,
    ):
        if not all(
            value and value.strip()
            for value in (app_id, private_key, public_key, seller_id, notify_url)
        ):
            raise PaymentChannelError("支付宝配置不完整")
        self.app_id = app_id.strip()
        self.seller_id = seller_id.strip()
        self.notify_url = notify_url.strip()
        try:
            url = urlsplit(self.notify_url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.fragment
            ):
                raise ValueError("invalid notify URL")
            # 延迟导入：未启用真实支付时不需要初始化 SDK 或密钥。
            from alipay import AliPay
            from alipay.utils import AliPayConfig

            self._client = AliPay(
                appid=self.app_id,
                app_notify_url=self.notify_url,
                app_private_key_string=private_key.strip().replace("\\n", "\n"),
                alipay_public_key_string=public_key.strip().replace("\\n", "\n"),
                sign_type="RSA2",
                debug=sandbox,
                config=AliPayConfig(timeout=15),
            )
            if (
                not self._client.app_private_key.has_private()
                or self._client.app_private_key.size_in_bits() < 2048
                or self._client.alipay_public_key.has_private()
                or self._client.alipay_public_key.size_in_bits() < 2048
            ):
                raise ValueError("expected RSA2 private/public keys")
        except (ImportError, ValueError, TypeError, IndexError):
            # 不把 SDK 的原始异常（可能包含密钥材料）暴露给调用方。
            raise PaymentChannelError("支付宝 SDK 或 RSA2 密钥配置无效") from None

    def create_payment(self, order: Mapping[str, object]) -> dict:
        amount_cents = order["amount_cents"]
        if type(amount_cents) is not int or amount_cents <= 0:
            raise PaymentChannelError("支付宝订单金额无效")
        # 全程整数换算，避免浮点数把分转换成元时产生舍入误差。
        total_amount = f"{amount_cents // 100}.{amount_cents % 100:02d}"
        try:
            result = self._client.api_alipay_trade_precreate(
                subject="算法错题本订阅",
                out_trade_no=order["id"],
                total_amount=total_amount,
                notify_url=self.notify_url,
            )
        except Exception:
            # SDK 的网络、响应解析及同步验签异常统一交给上层处理；保留 pending。
            raise PaymentChannelError("支付宝预下单失败") from None
        if (
            not isinstance(result, dict)
            or result.get("code") != "10000"
            or result.get("out_trade_no") != order["id"]
            or not isinstance(result.get("qr_code"), str)
            or not result["qr_code"].strip()
        ):
            raise PaymentChannelError("支付宝预下单响应无效")
        return {
            "provider": "alipay",
            "qr_code_url": result["qr_code"],
            "redirect_url": None,
        }

    def verify_callback(
        self, raw_body: bytes, headers: Mapping[str, str]
    ) -> VerifiedCallback:
        content_types = [
            value for key, value in headers.items() if key.lower() == "content-type"
        ]
        if (
            len(content_types) != 1
            or content_types[0].split(";", 1)[0].strip().lower()
            != "application/x-www-form-urlencoded"
            or len(raw_body) > 64 * 1024
        ):
            raise CallbackVerificationError("支付宝回调必须为 UTF-8 表单")
        content_type = Message()
        content_type["content-type"] = content_types[0]
        if content_type.get_content_charset("utf-8") != "utf-8":
            raise CallbackVerificationError("支付宝回调必须为 UTF-8 表单")
        try:
            encoded_form = raw_body.decode("utf-8")
            if re.search(r"%(?![0-9a-fA-F]{2})", encoded_form):
                raise ValueError("invalid percent encoding")
            fields = parse_qsl(
                encoded_form,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=128,
            )
            data = dict(fields)
            if len(data) != len(fields) or "" in data:
                raise ValueError("ambiguous form fields")
            signature = data.pop("sign")
            if not signature or data.get("sign_type") != "RSA2":
                raise ValueError("expected RSA2 signature")
        except (ValueError, KeyError):
            raise CallbackVerificationError("支付宝回调表单或签名无效") from None

        # 支付宝通知：URL 解码后按字段名排序；sign/sign_type 及空值不参与签名。
        # SDK 会移除 sign_type，故传副本；其他非空字段（包括新增字段）全部参与。
        signed_data = {key: value for key, value in data.items() if value != ""}
        try:
            verified = self._client.verify(signed_data, signature)
        except Exception:
            raise CallbackVerificationError("支付宝回调签名无效") from None
        if not verified:
            raise CallbackVerificationError("支付宝回调签名无效")

        try:
            if data["app_id"] != self.app_id or data["seller_id"] != self.seller_id:
                raise ValueError("unexpected app or seller")
            if data.get("charset", "utf-8").lower() != "utf-8":
                raise ValueError("unexpected charset")
            amount = data["total_amount"]
            if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amount) is None:
                raise ValueError("invalid total amount")
            yuan, _, cents = amount.partition(".")
            amount_cents = int(yuan) * 100 + int(cents.ljust(2, "0"))
            # WAIT_BUYER_PAY 不是终态；退款导致的 paid -> closed 仍由状态机拒绝。
            status = {
                "TRADE_SUCCESS": "paid",
                "TRADE_FINISHED": "paid",
                "TRADE_CLOSED": "closed",
            }[data["trade_status"]]
            return VerifiedCallback(
                order_id=data["out_trade_no"],
                channel="alipay",
                provider_trade_no=data["trade_no"],
                amount_cents=amount_cents,
                status=status,
            )
        except (ValueError, KeyError):
            raise CallbackVerificationError("支付宝回调内容无效") from None


def get_channel(channel: str) -> PaymentChannel:
    if channel not in ("alipay", "wechat"):
        raise ValueError("不支持的支付渠道")
    # Mock 是显式的本地模式，不因真实配置缺失而自动降级到 Mock。
    if os.getenv("PAYMENTS_MOCK_ENABLED") == "1":
        return MockChannel(channel, os.getenv("PAYMENTS_MOCK_SECRET", ""))
    if channel == "alipay":
        sandbox = os.getenv("ALIPAY_SANDBOX", "0")
        if sandbox not in ("0", "1"):
            raise PaymentChannelError("ALIPAY_SANDBOX 必须为 0 或 1")
        return AlipayChannel(
            app_id=os.getenv("ALIPAY_APP_ID", ""),
            private_key=os.getenv("ALIPAY_PRIVATE_KEY", ""),
            public_key=os.getenv("ALIPAY_PUBLIC_KEY", ""),
            seller_id=os.getenv("ALIPAY_SELLER_ID", ""),
            notify_url=os.getenv("ALIPAY_NOTIFY_URL", ""),
            sandbox=sandbox == "1",
        )
    raise PaymentChannelError("微信支付尚未接入")
