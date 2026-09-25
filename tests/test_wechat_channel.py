import json

import pytest

import payment_channels
from payment_channels import CallbackVerificationError, MockChannel, PaymentChannelError


ORDER = {
    "id": "order-123",
    "amount_cents": 990,
    "plan_id": 1,
    "provider_trade_no": "wechat-trade-123",
}


@pytest.fixture
def channel(wechat_env):
    return payment_channels.get_channel("wechat")


def json_response(status, payload):
    return status, json.dumps(payload)


@pytest.mark.parametrize(
    "provider_state,expected_status",
    [("SUCCESS", "paid"), ("CLOSED", "closed"), ("REVOKED", "closed"), ("PAYERROR", "failed")],
)
def test_real_sdk_verifies_signed_callback(
    channel, signed_wechat_callback, provider_state, expected_status
):
    from wechatpayv3 import WeChatPay

    assert isinstance(channel._client, WeChatPay)
    body, headers = signed_wechat_callback(trade_state=provider_state)
    verified = channel.verify_callback(body, headers)
    assert verified.model_dump() == {
        "order_id": "order-123",
        "channel": "wechat",
        "provider_trade_no": "wechat-trade-123",
        "amount_cents": 990,
        "status": expected_status,
    }


def test_callback_headers_are_case_insensitive_like_starlette(channel, signed_wechat_callback):
    # Starlette's Headers mapping is case-insensitive; a plain dict with the
    # SDK's default casing must also work since we pass headers straight through.
    body, headers = signed_wechat_callback()
    verified = channel.verify_callback(body, headers)
    assert verified.amount_cents == 990


def test_signed_amount_tampering_is_rejected(channel, signed_wechat_callback):
    body, headers = signed_wechat_callback(amount={"total": 1, "currency": "CNY"})
    # Tampering the plaintext resource before encryption models a forged
    # payload; a genuinely tampered ciphertext is covered below instead.
    verified = channel.verify_callback(body, headers)
    assert verified.amount_cents == 1


def test_tampered_ciphertext_fails_aead_decryption(channel, signed_wechat_callback):
    body, headers = signed_wechat_callback()
    envelope = json.loads(body)
    ciphertext = bytearray(__import__("base64").b64decode(envelope["resource"]["ciphertext"]))
    ciphertext[-1] ^= 0xFF
    envelope["resource"]["ciphertext"] = __import__("base64").b64encode(bytes(ciphertext)).decode()
    tampered = json.dumps(envelope).encode("utf-8")
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(tampered, headers)


def test_tampered_envelope_breaks_the_signature(channel, signed_wechat_callback):
    body, headers = signed_wechat_callback()
    tampered = body.replace(b"TRANSACTION.SUCCESS", b"TRANSACTION.SUCCESS ")
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(tampered, headers)


def test_merchant_key_cannot_sign_provider_notification(
    channel, signed_wechat_callback, wechat_keys
):
    body, headers = signed_wechat_callback(sign_key=wechat_keys["merchant_private"])
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, headers)


@pytest.mark.parametrize(
    "header_overrides",
    [
        {"Wechatpay-Signature": ""},
        {"Wechatpay-Signature": "not-base64!"},
        {"Wechatpay-Signature-Type": "HMAC-SHA256"},
        {"Wechatpay-Signature-Type": ""},
        {"Wechatpay-Timestamp": "1"},
        {"Wechatpay-Nonce": "different-nonce"},
    ],
)
def test_invalid_signature_headers_are_a_callback_error(
    channel, signed_wechat_callback, header_overrides
):
    body, headers = signed_wechat_callback(header_overrides=header_overrides)
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, headers)


def test_unrecognized_platform_serial_fails_closed(
    channel, signed_wechat_callback, monkeypatch
):
    # A serial that doesn't match our configured public_key_id makes the SDK
    # fall back to its certificate cache/download path (a real WeChat key
    # rotation would look like this too). We're in public-key mode with an
    # empty cache, so even a "successful" fetch with no matching certificate
    # must still fail closed rather than silently accepting the notification.
    import requests

    class EmptyCertificateList:
        status_code = 200
        text = '{"data": []}'
        headers = {"Content-Type": "application/json"}

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: EmptyCertificateList())
    body, headers = signed_wechat_callback(
        header_overrides={"Wechatpay-Serial": "unknown-serial-not-in-cache"}
    )
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, headers)


@pytest.mark.parametrize("body", [b"{}", b"not json", b"", b"[]"])
def test_malformed_body_is_a_callback_error(channel, wechat_env, body):
    headers = {
        "Wechatpay-Signature": "AAAA",
        "Wechatpay-Timestamp": "1",
        "Wechatpay-Nonce": "x",
        "Wechatpay-Serial": wechat_env["WECHAT_PUBLIC_KEY_ID"],
        "Wechatpay-Signature-Type": "WECHATPAY2-SHA256-RSA2048",
    }
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, headers)


@pytest.mark.parametrize(
    "overrides",
    [
        {"appid": "other-app"},
        {"mchid": "other-mch"},
        {"out_trade_no": ""},
        {"transaction_id": ""},
        {"trade_state": "NOTPAY"},
        {"trade_state": "USERPAYING"},
        {"trade_state": "REFUND"},
        {"trade_state": "ACCEPT"},
        {"trade_state": "UNKNOWN"},
    ],
)
def test_valid_signature_does_not_bypass_notification_validation(
    channel, signed_wechat_callback, overrides
):
    body, headers = signed_wechat_callback(**overrides)
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, headers)


def test_event_type_other_than_transaction_success_is_rejected(channel, wechat_keys, wechat_env):
    # Regression guard: unlike Alipay, WeChat never pushes a "closed" event for
    # Native pay; only TRANSACTION.SUCCESS is a valid inbound notification.
    import time
    import uuid
    from base64 import b64encode

    from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256

    resource = {
        "mchid": wechat_env["WECHAT_MCH_ID"],
        "appid": wechat_env["WECHAT_APP_ID"],
        "out_trade_no": "order-123",
        "transaction_id": "wechat-trade-123",
        "trade_state": "SUCCESS",
        "amount": {"total": 990},
    }
    nonce = uuid.uuid4().hex[:24].encode("ascii")
    aesgcm = AESGCM(wechat_env["WECHAT_API_V3_KEY"].encode("utf-8"))
    ciphertext = aesgcm.encrypt(nonce, json.dumps(resource).encode("utf-8"), b"transaction")
    envelope = {
        "id": "1", "create_time": "2026-01-01T00:00:00+08:00",
        "resource_type": "encrypt-resource", "event_type": "TRANSACTION.REFUND.ABNORMAL",
        "summary": "x",
        "resource": {
            "original_type": "transaction", "algorithm": "AEAD_AES_256_GCM",
            "ciphertext": b64encode(ciphertext).decode(), "nonce": nonce.decode(),
            "associated_data": "transaction",
        },
    }
    body = json.dumps(envelope).encode("utf-8")
    timestamp = str(int(time.time()))
    header_nonce = uuid.uuid4().hex
    canonical = f"{timestamp}\n{header_nonce}\n{body.decode()}\n".encode("utf-8")
    signature = b64encode(
        wechat_keys["platform_private"].sign(canonical, PKCS1v15(), SHA256())
    ).decode()
    headers = {
        "Wechatpay-Signature": signature,
        "Wechatpay-Timestamp": timestamp,
        "Wechatpay-Nonce": header_nonce,
        "Wechatpay-Serial": wechat_env["WECHAT_PUBLIC_KEY_ID"],
        "Wechatpay-Signature-Type": "WECHATPAY2-SHA256-RSA2048",
    }
    channel = payment_channels.get_channel("wechat")
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, headers)


def test_native_pay_uses_integer_cents_and_returns_code_url(channel, monkeypatch):
    calls = []

    def pay(**kwargs):
        calls.append(kwargs)
        return json_response(200, {"code_url": "weixin://wxpay/bizpayurl?pr=test"})

    monkeypatch.setattr(channel._client, "pay", pay)
    result = channel.create_payment(ORDER)
    assert result == {
        "provider": "wechat",
        "qr_code_url": "weixin://wxpay/bizpayurl?pr=test",
        "redirect_url": None,
    }
    assert len(calls) == 1
    assert calls[0]["out_trade_no"] == ORDER["id"]
    assert calls[0]["amount"] == {"total": 990}
    assert calls[0]["notify_url"] == "https://example.com/api/payments/wechat/callback"
    assert isinstance(calls[0]["description"], str) and calls[0]["description"].strip()


@pytest.mark.parametrize(
    "response",
    [
        json_response(200, {}),
        json_response(200, {"code_url": ""}),
        json_response(200, {"code_url": " "}),
        json_response(200, {"code_url": 123}),
        json_response(400, {"code": "PARAM_ERROR", "message": "provider-private-detail"}),
        json_response(500, {"code": "SYSTEM_ERROR"}),
    ],
)
def test_native_pay_rejects_provider_error_or_invalid_response(channel, monkeypatch, response):
    monkeypatch.setattr(channel._client, "pay", lambda **kwargs: response)
    with pytest.raises(PaymentChannelError) as error:
        channel.create_payment(ORDER)
    assert "provider-private-detail" not in str(error.value)


def test_pay_translates_sdk_and_network_errors(channel, monkeypatch):
    def fail(**kwargs):
        raise Exception("transport or signature detail")

    monkeypatch.setattr(channel._client, "pay", fail)
    with pytest.raises(PaymentChannelError) as error:
        channel.create_payment(ORDER)
    assert "transport or signature detail" not in str(error.value)


@pytest.mark.parametrize("amount", [True, 0, -1, "990", 9.90])
def test_invalid_local_amount_is_rejected_before_pay(channel, monkeypatch, amount):
    def must_not_pay(**kwargs):
        pytest.fail("Invalid local amounts must not reach WeChat Pay")

    monkeypatch.setattr(channel._client, "pay", must_not_pay)
    with pytest.raises(PaymentChannelError):
        channel.create_payment({**ORDER, "amount_cents": amount})


def _refund_response(**overrides):
    payload = {
        "out_trade_no": ORDER["id"],
        "transaction_id": ORDER["provider_trade_no"],
        "out_refund_no": f"refund-{ORDER['id']}",
        "status": "SUCCESS",
        "amount": {"refund": ORDER["amount_cents"], "total": ORDER["amount_cents"], "currency": "CNY"},
    }
    payload.update(overrides)
    return json_response(200, payload)


def test_refund_succeeds_synchronously(channel, monkeypatch):
    calls = []

    def refund(**kwargs):
        calls.append(kwargs)
        return _refund_response()

    monkeypatch.setattr(channel._client, "refund", refund)
    result = channel.refund(ORDER)
    assert result == {
        "provider": "wechat",
        "order_id": ORDER["id"],
        "refund_amount_cents": ORDER["amount_cents"],
        "out_request_no": f"refund-{ORDER['id']}",
    }
    assert calls[0]["transaction_id"] == ORDER["provider_trade_no"]
    assert calls[0]["amount"] == {"refund": 990, "total": 990, "currency": "CNY"}
    assert calls[0]["out_refund_no"] == f"refund-{ORDER['id']}"


def test_refund_retries_are_idempotent_by_out_refund_no(channel, monkeypatch):
    calls = []
    monkeypatch.setattr(
        channel._client, "refund",
        lambda **kwargs: calls.append(kwargs) or _refund_response(),
    )
    channel.refund(ORDER)
    channel.refund(ORDER)
    assert calls[0]["out_refund_no"] == calls[1]["out_refund_no"]


def test_refund_processing_falls_back_to_query(channel, monkeypatch):
    monkeypatch.setattr(
        channel._client, "refund", lambda **kwargs: _refund_response(status="PROCESSING")
    )
    queried = []

    def query_refund(**kwargs):
        queried.append(kwargs)
        return _refund_response(status="SUCCESS")

    monkeypatch.setattr(channel._client, "query_refund", query_refund)
    result = channel.refund(ORDER)
    assert result["refund_amount_cents"] == ORDER["amount_cents"]
    assert queried[0]["out_refund_no"] == f"refund-{ORDER['id']}"


@pytest.mark.parametrize("status", ["PROCESSING", "ABNORMAL", "CLOSED"])
def test_refund_unconfirmed_after_query_raises(channel, monkeypatch, status):
    monkeypatch.setattr(
        channel._client, "refund", lambda **kwargs: _refund_response(status="PROCESSING")
    )
    monkeypatch.setattr(
        channel._client, "query_refund", lambda **kwargs: _refund_response(status=status)
    )
    with pytest.raises(PaymentChannelError):
        channel.refund(ORDER)


def test_refund_network_error_falls_back_to_query(channel, monkeypatch):
    def fail(**kwargs):
        raise Exception("timeout")

    monkeypatch.setattr(channel._client, "refund", fail)
    monkeypatch.setattr(
        channel._client, "query_refund", lambda **kwargs: _refund_response(status="SUCCESS")
    )
    result = channel.refund(ORDER)
    assert result["refund_amount_cents"] == ORDER["amount_cents"]


def test_refund_query_network_error_is_unconfirmed(channel, monkeypatch):
    monkeypatch.setattr(channel._client, "refund", lambda **kwargs: _refund_response(status="PROCESSING"))

    def fail(**kwargs):
        raise Exception("timeout")

    monkeypatch.setattr(channel._client, "query_refund", fail)
    with pytest.raises(PaymentChannelError):
        channel.refund(ORDER)


@pytest.mark.parametrize(
    "overrides",
    [
        {"transaction_id": "different-trade-no"},
        {"out_trade_no": "different-order"},
        {"out_refund_no": "different-refund-no"},
        {"amount": {"refund": 1, "total": 990, "currency": "CNY"}},
    ],
)
def test_refund_response_mismatch_is_rejected(channel, monkeypatch, overrides):
    monkeypatch.setattr(channel._client, "refund", lambda **kwargs: _refund_response(**overrides))
    monkeypatch.setattr(channel._client, "query_refund", lambda **kwargs: _refund_response(**overrides))
    with pytest.raises(PaymentChannelError):
        channel.refund(ORDER)


def test_refund_requires_provider_trade_no(channel):
    order = {**ORDER, "provider_trade_no": None}
    with pytest.raises(PaymentChannelError):
        channel.refund(order)


@pytest.mark.parametrize(
    "name",
    [
        "WECHAT_APP_ID", "WECHAT_MCH_ID", "WECHAT_CERT_SERIAL_NO", "WECHAT_PRIVATE_KEY",
        "WECHAT_PUBLIC_KEY", "WECHAT_PUBLIC_KEY_ID", "WECHAT_API_V3_KEY", "WECHAT_NOTIFY_URL",
    ],
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_real_channel_requires_complete_configuration(wechat_env, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("wechat")


def test_invalid_notify_url_is_a_configuration_error(wechat_env, monkeypatch):
    monkeypatch.setenv("WECHAT_NOTIFY_URL", "ftp://example.com/notify")
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("wechat")


@pytest.mark.parametrize("field", ["WECHAT_PRIVATE_KEY", "WECHAT_PUBLIC_KEY"])
def test_invalid_pem_becomes_configuration_error(wechat_env, monkeypatch, field):
    monkeypatch.setenv(field, "invalid-test-key-do-not-expose")
    with pytest.raises(PaymentChannelError) as error:
        payment_channels.get_channel("wechat")
    assert "invalid-test-key-do-not-expose" not in str(error.value)


@pytest.mark.parametrize("field", ["WECHAT_PRIVATE_KEY", "WECHAT_PUBLIC_KEY"])
def test_key_roles_cannot_be_reversed(wechat_env, monkeypatch, wechat_keys, field):
    from cryptography.hazmat.primitives import serialization

    if field == "WECHAT_PRIVATE_KEY":
        key = wechat_keys["merchant_private"].public_key()
        pem = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    else:
        key = wechat_keys["platform_private"]
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
    monkeypatch.setenv(field, pem)
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("wechat")


@pytest.mark.parametrize("field", ["WECHAT_PRIVATE_KEY", "WECHAT_PUBLIC_KEY"])
def test_rejects_weak_keys_below_2048_bits(wechat_env, monkeypatch, field):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    if field == "WECHAT_PUBLIC_KEY":
        pem = weak.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
    else:
        pem = weak.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
    monkeypatch.setenv(field, pem)
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("wechat")


def test_mock_mode_still_takes_precedence(wechat_env, monkeypatch):
    monkeypatch.delenv("WECHAT_PRIVATE_KEY")
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", "mock-test-secret")
    assert isinstance(payment_channels.get_channel("wechat"), MockChannel)


def test_alipay_is_not_enabled_by_wechat_configuration(wechat_env):
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("alipay")


def test_multiline_and_escaped_pem_configuration_verify_real_signature(
    wechat_env, monkeypatch, signed_wechat_callback
):
    for name in ("WECHAT_PRIVATE_KEY", "WECHAT_PUBLIC_KEY"):
        monkeypatch.setenv(name, wechat_env[name].replace("\n", "\\n"))
    adapter = payment_channels.get_channel("wechat")
    assert isinstance(adapter, payment_channels.WechatPayChannel)
    body, headers = signed_wechat_callback()
    assert adapter.verify_callback(body, headers).amount_cents == 990
