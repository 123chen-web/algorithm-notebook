import hashlib
import hmac
import json

import pytest

from payment_channels import (
    CallbackVerificationError,
    MockChannel,
    PaymentChannelError,
    get_channel,
)


SECRET = "local-test-payment-secret"


def callback_body(**overrides):
    values = {
        "order_id": "test-order",
        "channel": "alipay",
        "provider_trade_no": "test-trade",
        "amount_cents": 990,
        "status": "paid",
    }
    values.update(overrides)
    return json.dumps(values).encode("utf-8")


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_create_payment_returns_only_mock_payment_information(channel):
    adapter = MockChannel(channel, SECRET)
    result = adapter.create_payment({"id": "test-order"})
    assert result == {
        "provider": "mock",
        "qr_code_url": f"mock://{channel}/test-order",
        "redirect_url": None,
    }
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
@pytest.mark.parametrize("status", ["paid", "failed", "closed"])
@pytest.mark.parametrize("header", ["X-Mock-Signature", "x-mock-signature"])
def test_verify_callback_accepts_valid_signature(channel, status, header):
    adapter = MockChannel(channel, SECRET)
    body = callback_body(channel=channel, status=status)
    signature = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert adapter.sign_callback(body) == signature
    verified = adapter.verify_callback(body, {header: signature})
    assert verified.model_dump() == json.loads(body)


@pytest.mark.parametrize("signature", ["", "0" * 64, "签" * 64, "bad-signature"])
def test_verify_callback_rejects_bad_signature(signature):
    adapter = MockChannel("alipay", SECRET)
    with pytest.raises(CallbackVerificationError) as error:
        adapter.verify_callback(callback_body(), {"X-Mock-Signature": signature})
    assert SECRET not in str(error.value)


def test_verify_callback_rejects_missing_or_ambiguous_signature():
    adapter = MockChannel("alipay", SECRET)
    body = callback_body()
    signature = adapter.sign_callback(body)
    for headers in ({}, {"X-Mock-Signature": signature, "x-mock-signature": signature}):
        with pytest.raises(CallbackVerificationError):
            adapter.verify_callback(body, headers)


def test_verify_callback_rejects_body_tampering():
    adapter = MockChannel("alipay", SECRET)
    signature = adapter.sign_callback(callback_body())
    with pytest.raises(CallbackVerificationError):
        adapter.verify_callback(
            callback_body(amount_cents=1), {"X-Mock-Signature": signature}
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"order_id": ""},
        {"order_id": " "},
        {"order_id": "x" * 129},
        {"order_id": 42},
        {"provider_trade_no": ""},
        {"provider_trade_no": " "},
        {"provider_trade_no": None},
        {"amount_cents": 0},
        {"amount_cents": -1},
        {"amount_cents": "990"},
        {"amount_cents": 990.0},
        {"amount_cents": True},
        {"channel": "unsupported"},
        {"status": "pending"},
        {"status": "refunded"},
        {"extra_field": "ignored-by-accident"},
    ],
)
def test_verify_callback_rejects_invalid_fields(overrides):
    adapter = MockChannel("alipay", SECRET)
    body = callback_body(**overrides)
    with pytest.raises(CallbackVerificationError):
        adapter.verify_callback(body, {"X-Mock-Signature": adapter.sign_callback(body)})


@pytest.mark.parametrize(
    "field",
    ["order_id", "channel", "provider_trade_no", "amount_cents", "status"],
)
def test_verify_callback_rejects_missing_fields(field):
    adapter = MockChannel("alipay", SECRET)
    values = json.loads(callback_body())
    del values[field]
    body = json.dumps(values).encode("utf-8")
    with pytest.raises(CallbackVerificationError):
        adapter.verify_callback(body, {"X-Mock-Signature": adapter.sign_callback(body)})


@pytest.mark.parametrize("body", [b"not-json", b"\xff", b"null", b"[]"])
def test_verify_callback_rejects_invalid_body(body):
    adapter = MockChannel("alipay", SECRET)
    with pytest.raises(CallbackVerificationError):
        adapter.verify_callback(body, {"X-Mock-Signature": adapter.sign_callback(body)})


def test_verify_callback_rejects_other_channel():
    adapter = MockChannel("wechat", SECRET)
    body = callback_body(channel="alipay")
    with pytest.raises(CallbackVerificationError):
        adapter.verify_callback(body, {"X-Mock-Signature": adapter.sign_callback(body)})


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_get_channel_requires_explicit_enablement(channel, monkeypatch):
    monkeypatch.delenv("PAYMENTS_MOCK_ENABLED", raising=False)
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", SECRET)
    with pytest.raises(PaymentChannelError):
        get_channel(channel)


@pytest.mark.parametrize("enabled", ["", "0", "true"])
def test_get_channel_rejects_non_enabled_settings(enabled, monkeypatch):
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", enabled)
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", SECRET)
    with pytest.raises(PaymentChannelError):
        get_channel("alipay")


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_get_channel_requires_nonempty_secret(secret, monkeypatch):
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    if secret is None:
        monkeypatch.delenv("PAYMENTS_MOCK_SECRET", raising=False)
    else:
        monkeypatch.setenv("PAYMENTS_MOCK_SECRET", secret)
    with pytest.raises(PaymentChannelError):
        get_channel("alipay")


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_get_channel_returns_configured_mock(channel, monkeypatch):
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", SECRET)
    adapter = get_channel(channel)
    assert isinstance(adapter, MockChannel)
    assert adapter.channel == channel
    assert adapter.sign_callback(callback_body()) == MockChannel(
        channel, SECRET
    ).sign_callback(callback_body())


def test_get_channel_rejects_unknown_channel(monkeypatch):
    monkeypatch.delenv("PAYMENTS_MOCK_ENABLED", raising=False)
    with pytest.raises(ValueError, match="不支持的支付渠道"):
        get_channel("unsupported")
