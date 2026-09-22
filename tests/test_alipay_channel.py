import base64
from io import BytesIO
import json
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest

import payment_channels
from payment_channels import CallbackVerificationError, MockChannel, PaymentChannelError


FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}
ORDER = {"id": "order-123", "amount_cents": 990, "plan_id": 1}


@pytest.fixture
def channel(alipay_env):
    return payment_channels.get_channel("alipay")


def replace_form_field(body, name, value):
    fields = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
    fields[name] = value
    return urlencode(fields).encode("utf-8")


@pytest.mark.parametrize(
    "provider_status,expected_status",
    [("TRADE_SUCCESS", "paid"), ("TRADE_FINISHED", "paid"), ("TRADE_CLOSED", "closed")],
)
def test_rsa2_signed_form_verifies_with_real_sdk(
    channel, signed_alipay_callback, provider_status, expected_status
):
    import alipay

    assert isinstance(channel._client, alipay.AliPay)
    body = signed_alipay_callback(
        trade_status=provider_status, subject="算法 + 10% & = 测试", body=""
    )
    verified = channel.verify_callback(body, FORM_HEADERS)
    assert verified.model_dump() == {
        "order_id": "order-123",
        "channel": "alipay",
        "provider_trade_no": "alipay-trade-123",
        "amount_cents": 990,
        "status": expected_status,
    }


def test_content_type_header_is_case_insensitive(channel, signed_alipay_callback):
    verified = channel.verify_callback(
        signed_alipay_callback(),
        {"cOnTeNt-TyPe": "Application/X-WWW-Form-Urlencoded; charset=UTF-8"},
    )
    assert verified.amount_cents == 990


def test_signed_amount_tampering_is_rejected(channel, signed_alipay_callback):
    body = replace_form_field(signed_alipay_callback(), "total_amount", "0.01")
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, FORM_HEADERS)


def test_additional_provider_fields_are_covered_by_signature(channel, signed_alipay_callback):
    body = signed_alipay_callback(subject="original subject")
    body = replace_form_field(body, "subject", "altered subject")
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, FORM_HEADERS)


def test_merchant_key_cannot_sign_provider_notification(
    channel, signed_alipay_callback, alipay_keys
):
    body = signed_alipay_callback(sign_key=alipay_keys["merchant_private"])
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, FORM_HEADERS)


@pytest.mark.parametrize("signature", ["", "not base64!", "AAAA", "A" * 344])
def test_invalid_signature_is_a_callback_error(
    channel, signed_alipay_callback, signature
):
    body = replace_form_field(signed_alipay_callback(), "sign", signature)
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, FORM_HEADERS)


@pytest.mark.parametrize("sign_type", [None, "", "RSA", "rsa2"])
def test_signature_algorithm_cannot_be_downgraded(
    channel, signed_alipay_callback, sign_type
):
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(
            signed_alipay_callback(sign_type=sign_type), FORM_HEADERS
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"app_id": "other-app"},
        {"seller_id": "other-seller"},
        {"app_id": None},
        {"seller_id": None},
        {"out_trade_no": None},
        {"out_trade_no": " "},
        {"out_trade_no": "x" * 129},
        {"trade_no": None},
        {"trade_no": ""},
        {"trade_no": " "},
        {"trade_status": None},
        {"trade_status": "WAIT_BUYER_PAY"},
        {"trade_status": "UNKNOWN"},
        {"total_amount": None},
        {"charset": "gbk"},
    ],
)
def test_valid_signature_does_not_bypass_notification_validation(
    channel, signed_alipay_callback, overrides
):
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(signed_alipay_callback(**overrides), FORM_HEADERS)


@pytest.mark.parametrize(
    "amount,cents", [("9", 900), ("9.9", 990), ("9.90", 990), ("0.01", 1)]
)
def test_amount_is_converted_exactly_to_cents(
    channel, signed_alipay_callback, amount, cents
):
    verified = channel.verify_callback(
        signed_alipay_callback(total_amount=amount), FORM_HEADERS
    )
    assert verified.amount_cents == cents


@pytest.mark.parametrize(
    "amount",
    ["0", "0.00", "-1.00", "+9.90", "9.900", "9.999", "9e2", "NaN", "Infinity",
     " 9.90", "9.90 ", ".99", "9.", "９.９０"],
)
def test_invalid_amount_is_rejected_after_valid_signature(
    channel, signed_alipay_callback, amount
):
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(
            signed_alipay_callback(total_amount=amount), FORM_HEADERS
        )


@pytest.mark.parametrize(
    "suffix", [b"&out_trade_no=order-123", b"&sign_type=RSA2", b"&subject=x&subject=x"]
)
def test_duplicate_form_fields_are_rejected(
    channel, signed_alipay_callback, suffix
):
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(signed_alipay_callback() + suffix, FORM_HEADERS)


@pytest.mark.parametrize(
    "body", [b"\xff", b"field=%FF", b"field=%", b"field=%0", b"field=%GG", b"{}", b""]
)
def test_invalid_form_encoding_is_a_callback_error(channel, body):
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(body, FORM_HEADERS)


@pytest.mark.parametrize(
    "headers",
    [{}, {"Content-Type": "application/json"}, {"Content-Type": "text/plain"},
     {"Content-Type": "application/x-www-form-urlencoded; charset=gbk"},
     {"Content-Type": "application/x-www-form-urlencoded",
      "content-type": "application/x-www-form-urlencoded"}],
)
def test_invalid_content_type_is_rejected(channel, signed_alipay_callback, headers):
    with pytest.raises(CallbackVerificationError):
        channel.verify_callback(signed_alipay_callback(), headers)


def test_precreate_uses_decimal_yuan_and_returns_qr_code(channel, monkeypatch):
    calls = []

    def precreate(**kwargs):
        calls.append(kwargs)
        return {"code": "10000", "out_trade_no": ORDER["id"], "qr_code": "https://qr.alipay.com/test"}

    monkeypatch.setattr(channel._client, "api_alipay_trade_precreate", precreate)
    result = channel.create_payment(ORDER)
    assert result == {
        "provider": "alipay", "qr_code_url": "https://qr.alipay.com/test", "redirect_url": None
    }
    assert len(calls) == 1
    assert calls[0]["out_trade_no"] == ORDER["id"]
    assert calls[0]["total_amount"] == "9.90"
    assert calls[0]["notify_url"] == "https://example.com/api/payments/alipay/callback"
    assert isinstance(calls[0]["subject"], str) and calls[0]["subject"].strip()


@pytest.mark.parametrize("tamper_response", [False, True])
def test_sdk_precreate_signs_request_and_verifies_signed_response(
    channel, monkeypatch, alipay_keys, tamper_response
):
    import alipay
    from Cryptodome.Hash import SHA256
    from Cryptodome.Signature import pkcs1_15

    requests = []

    def local_urlopen(request, data=None, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        request_body = data if isinstance(request, str) else request.data
        encoded_parts = [urlsplit(url).query]
        if request_body:
            encoded_parts.append(request_body.decode("utf-8"))
        fields = dict(parse_qsl("&".join(encoded_parts), keep_blank_values=True))
        requests.append(fields.copy())
        signature = base64.b64decode(fields.pop("sign"))
        # Request signatures include sign_type; notification signatures omit it.
        canonical = "&".join(
            f"{key}={value}" for key, value in sorted(fields.items()) if value != ""
        ).encode("utf-8")
        pkcs1_15.new(alipay_keys["merchant_private"].public_key()).verify(
            SHA256.new(canonical), signature
        )
        assert fields["app_id"] == "test-alipay-app"
        assert fields["method"] == "alipay.trade.precreate"
        assert fields["sign_type"] == "RSA2"
        assert fields["notify_url"] == "https://example.com/api/payments/alipay/callback"
        content = json.loads(fields["biz_content"])
        assert content["out_trade_no"] == ORDER["id"]
        assert content["total_amount"] == "9.90"

        response = json.dumps(
            {"code": "10000", "msg": "Success", "out_trade_no": ORDER["id"],
             "qr_code": "https://qr.alipay.com/sdk-test"},
            ensure_ascii=False, separators=(",", ":"),
        )
        response_signature = base64.b64encode(
            pkcs1_15.new(alipay_keys["provider_private"]).sign(
                SHA256.new(response.encode("utf-8"))
            )
        ).decode("ascii")
        if tamper_response:
            response = response.replace("sdk-test", "tampered")
        envelope = (
            '{"alipay_trade_precreate_response":' + response
            + ',"sign":' + json.dumps(response_signature) + "}"
        )
        return BytesIO(envelope.encode("utf-8"))

    # Keep api_alipay_trade_precreate and all SDK signing/verification intact.
    monkeypatch.setattr(alipay, "urlopen", local_urlopen)
    if tamper_response:
        with pytest.raises(PaymentChannelError):
            channel.create_payment(ORDER)
    else:
        assert channel.create_payment(ORDER) == {
            "provider": "alipay",
            "qr_code_url": "https://qr.alipay.com/sdk-test",
            "redirect_url": None,
        }
    assert len(requests) == 1


@pytest.mark.parametrize("amount,expected", [(1, "0.01"), (100, "1.00"), (123456789, "1234567.89")])
def test_precreate_amount_format_has_no_float_rounding(channel, monkeypatch, amount, expected):
    def precreate(**kwargs):
        assert kwargs["total_amount"] == expected
        return {"code": "10000", "out_trade_no": ORDER["id"], "qr_code": "qr-code"}

    monkeypatch.setattr(channel._client, "api_alipay_trade_precreate", precreate)
    channel.create_payment({**ORDER, "amount_cents": amount})


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"code": "40004", "sub_msg": "provider-private-detail"},
     {"code": 10000, "out_trade_no": "order-123", "qr_code": "qr"},
     {"code": "10000", "out_trade_no": "different-order", "qr_code": "qr"},
     {"code": "10000", "qr_code": "qr"},
     {"code": "10000", "out_trade_no": "order-123"},
     {"code": "10000", "out_trade_no": "order-123", "qr_code": ""},
     {"code": "10000", "out_trade_no": "order-123", "qr_code": " "},
     {"code": "10000", "out_trade_no": "order-123", "qr_code": 123}],
)
def test_precreate_rejects_provider_error_or_invalid_response(channel, monkeypatch, response):
    monkeypatch.setattr(channel._client, "api_alipay_trade_precreate", lambda **kwargs: response)
    with pytest.raises(PaymentChannelError) as error:
        channel.create_payment(ORDER)
    assert "provider-private-detail" not in str(error.value)


@pytest.mark.parametrize("exception", [TimeoutError("transport detail"), ValueError("invalid response signature")])
def test_precreate_translates_sdk_errors(channel, monkeypatch, exception):
    def fail(**kwargs):
        raise exception

    monkeypatch.setattr(channel._client, "api_alipay_trade_precreate", fail)
    with pytest.raises(PaymentChannelError) as error:
        channel.create_payment(ORDER)
    assert str(exception) not in str(error.value)


@pytest.mark.parametrize("amount", [True, 0, -1, "990", 9.90])
def test_invalid_local_amount_is_rejected_before_precreate(channel, monkeypatch, amount):
    def must_not_precreate(**kwargs):
        pytest.fail("Invalid local amounts must not reach Alipay")

    monkeypatch.setattr(channel._client, "api_alipay_trade_precreate", must_not_precreate)
    with pytest.raises(PaymentChannelError):
        channel.create_payment({**ORDER, "amount_cents": amount})


@pytest.mark.parametrize(
    "name",
    ["ALIPAY_APP_ID", "ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY", "ALIPAY_SELLER_ID", "ALIPAY_NOTIFY_URL"],
)
@pytest.mark.parametrize("value", [None, "", "   "])
def test_real_channel_requires_complete_configuration(alipay_env, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("alipay")


@pytest.mark.parametrize("escaped", [False, True])
def test_multiline_and_escaped_pem_configuration_verify_real_signature(
    alipay_env, monkeypatch, signed_alipay_callback, escaped
):
    if escaped:
        for name in ("ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY"):
            monkeypatch.setenv(name, alipay_env[name].replace("\n", "\\n"))
    adapter = payment_channels.get_channel("alipay")
    assert isinstance(adapter, payment_channels.AlipayChannel)
    assert adapter.verify_callback(signed_alipay_callback(), FORM_HEADERS).amount_cents == 990


@pytest.mark.parametrize("name", ["ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY"])
def test_invalid_pem_becomes_configuration_error(alipay_env, monkeypatch, name):
    monkeypatch.setenv(name, "invalid-test-key-do-not-expose")
    with pytest.raises(PaymentChannelError) as error:
        payment_channels.get_channel("alipay")
    assert "invalid-test-key-do-not-expose" not in str(error.value)


def test_pkcs8_private_key_is_supported(
    alipay_env, monkeypatch, alipay_keys, signed_alipay_callback
):
    key = alipay_keys["merchant_private"].export_key(pkcs=8).decode()
    assert key.startswith("-----BEGIN PRIVATE KEY-----")
    monkeypatch.setenv("ALIPAY_PRIVATE_KEY", key)
    adapter = payment_channels.get_channel("alipay")
    assert adapter.verify_callback(signed_alipay_callback(), FORM_HEADERS).status == "paid"


@pytest.mark.parametrize("field", ["ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY"])
def test_key_roles_cannot_be_reversed(alipay_env, monkeypatch, alipay_keys, field):
    if field == "ALIPAY_PRIVATE_KEY":
        key = alipay_keys["merchant_private"].public_key()
    else:
        key = alipay_keys["provider_private"]
    monkeypatch.setenv(field, key.export_key().decode())
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("alipay")


@pytest.mark.parametrize("field", ["ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY"])
def test_rsa2_configuration_rejects_1024_bit_keys(alipay_env, monkeypatch, field):
    from Cryptodome.PublicKey import RSA

    key = RSA.generate(1024)
    if field == "ALIPAY_PUBLIC_KEY":
        key = key.public_key()
    monkeypatch.setenv(field, key.export_key().decode())
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("alipay")


@pytest.mark.parametrize(
    "notify_url", ["/api/payments/alipay/callback", "ftp://example.com/notify",
                   "https://", "https://user:secret@example.com/notify",
                   "https://example.com/notify#fragment"],
)
def test_invalid_notify_url_is_a_configuration_error(alipay_env, monkeypatch, notify_url):
    monkeypatch.setenv("ALIPAY_NOTIFY_URL", notify_url)
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("alipay")


@pytest.mark.parametrize("sandbox", ["true", "false", "yes", "2", ""])
def test_invalid_sandbox_setting_is_rejected(alipay_env, monkeypatch, sandbox):
    monkeypatch.setenv("ALIPAY_SANDBOX", sandbox)
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("alipay")


@pytest.mark.parametrize("sandbox", [None, "0", "1"])
def test_valid_sandbox_configuration_constructs_sdk(alipay_env, monkeypatch, sandbox):
    if sandbox is None:
        monkeypatch.delenv("ALIPAY_SANDBOX")
    else:
        monkeypatch.setenv("ALIPAY_SANDBOX", sandbox)
    assert isinstance(payment_channels.get_channel("alipay"), payment_channels.AlipayChannel)


@pytest.mark.parametrize("name", ["ALIPAY_PRIVATE_KEY", "ALIPAY_APP_ID"])
def test_mock_mode_still_takes_precedence(alipay_env, monkeypatch, name):
    monkeypatch.delenv(name)
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", "mock-test-secret")
    assert isinstance(payment_channels.get_channel("alipay"), MockChannel)


def test_wechat_is_not_enabled_by_alipay_configuration(alipay_env):
    with pytest.raises(PaymentChannelError):
        payment_channels.get_channel("wechat")
