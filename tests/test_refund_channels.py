import base64
from io import BytesIO
import json
from urllib.parse import parse_qsl, urlsplit

import pytest

from payment_channels import MockChannel, PaymentChannelError, get_channel


ORDER = {
    "id": "order-123",
    "amount_cents": 990,
    "provider_trade_no": "alipay-trade-123",
}
REQUEST_NO = "refund-order-123"


@pytest.fixture
def channel(alipay_env):
    return get_channel("alipay")


# Response names/semantics were checked in installed python-alipay-sdk 3.4.0
# and Alipay's official models (the installed SDK returns untyped inner dicts):
# https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/response/AlipayTradeRefundResponse.java
# https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/response/AlipayTradeFastpayRefundQueryResponse.java
def refund_response(**overrides):
    return {
        "code": "10000", "out_trade_no": ORDER["id"],
        "trade_no": ORDER["provider_trade_no"], "refund_fee": "9.90",
        "fund_change": "Y", **overrides,
    }


def query_response(**overrides):
    return {
        "code": "10000", "out_trade_no": ORDER["id"],
        "trade_no": ORDER["provider_trade_no"], "out_request_no": REQUEST_NO,
        "refund_amount": "9.90", "refund_status": "REFUND_SUCCESS", **overrides,
    }


def fail_sdk(**kwargs):
    raise TimeoutError("private provider/transport details")


@pytest.mark.parametrize("name", ["alipay", "wechat"])
def test_mock_refund_is_synchronous_and_repeatable(name):
    adapter = MockChannel(name, "local-test-secret")
    expected = {
        "provider": "mock", "order_id": ORDER["id"],
        "refund_amount_cents": 990, "out_request_no": REQUEST_NO,
    }
    assert adapter.refund(ORDER) == expected
    assert adapter.refund(ORDER) == expected


@pytest.mark.parametrize("fund_change", ["Y", "N"])
@pytest.mark.parametrize(
    "amount,formatted,response_amount",
    [(1, "0.01", "0.01"), (990, "9.90", "9.9"), (100, "1.00", "1"),
     (123456789, "1234567.89", "1234567.89")],
)
def test_alipay_refund_uses_exact_amount_and_stable_request_number(
    channel, monkeypatch, fund_change, amount, formatted, response_amount
):
    calls = []

    def refund(**kwargs):
        calls.append(kwargs)
        return refund_response(refund_fee=response_amount, fund_change=fund_change)

    monkeypatch.setattr(channel._client, "api_alipay_trade_refund", refund)
    order = {**ORDER, "amount_cents": amount}
    for _ in range(2):
        assert channel.refund(order) == {
            "provider": "alipay", "order_id": ORDER["id"],
            "refund_amount_cents": amount, "out_request_no": REQUEST_NO,
        }
    assert calls == [
        {"out_trade_no": ORDER["id"], "refund_amount": formatted,
         "out_request_no": REQUEST_NO, "refund_reason": "用户自助申请全额退款"}
    ] * 2


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"code": "40004", "sub_msg": "private provider detail"},
     refund_response(code=10000), refund_response(out_trade_no="other-order"),
     refund_response(trade_no="other-trade"), refund_response(refund_fee="0.01"),
     refund_response(refund_fee=None), refund_response(refund_fee=9.90),
     refund_response(fund_change=None), refund_response(fund_change="unknown"),
     refund_response(fund_change=[])],
)
def test_invalid_refund_response_is_recovered_by_verified_query(
    channel, monkeypatch, response
):
    monkeypatch.setattr(channel._client, "api_alipay_trade_refund", lambda **kwargs: response)
    queries = []

    def query(**kwargs):
        queries.append(kwargs)
        return query_response()

    monkeypatch.setattr(channel._client, "api_alipay_trade_fastpay_refund_query", query)
    assert channel.refund(ORDER)["refund_amount_cents"] == 990
    assert queries == [{"out_request_no": REQUEST_NO, "out_trade_no": ORDER["id"]}]


def test_refund_network_failure_can_be_recovered_by_query(channel, monkeypatch):
    monkeypatch.setattr(channel._client, "api_alipay_trade_refund", fail_sdk)
    monkeypatch.setattr(
        channel._client, "api_alipay_trade_fastpay_refund_query",
        lambda **kwargs: query_response(),
    )
    assert channel.refund(ORDER)["out_request_no"] == REQUEST_NO


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"code": "40004", "sub_msg": "private provider detail"},
     query_response(code=10000), query_response(out_trade_no="other-order"),
     query_response(trade_no="other-trade"), query_response(out_request_no="other-refund"),
     query_response(out_request_no=None), query_response(refund_status=None),
     query_response(refund_status="REFUND_PROCESSING"), query_response(refund_amount=None),
     query_response(refund_amount="0.01"), query_response(refund_amount=9.90),
     query_response(refund_amount="9.900"), query_response(refund_amount="9.9e0"),
     query_response(refund_amount=" 9.90"), query_response(refund_amount="9" * 33)],
)
def test_query_success_code_without_matching_successful_refund_is_rejected(
    channel, monkeypatch, response
):
    monkeypatch.setattr(channel._client, "api_alipay_trade_refund", fail_sdk)
    monkeypatch.setattr(
        channel._client, "api_alipay_trade_fastpay_refund_query", lambda **kwargs: response
    )
    with pytest.raises(PaymentChannelError) as error:
        channel.refund(ORDER)
    assert "private" not in str(error.value)


def test_sdk_errors_from_both_calls_are_sanitized(channel, monkeypatch):
    monkeypatch.setattr(channel._client, "api_alipay_trade_refund", fail_sdk)
    monkeypatch.setattr(channel._client, "api_alipay_trade_fastpay_refund_query", fail_sdk)
    with pytest.raises(PaymentChannelError) as error:
        channel.refund(ORDER)
    assert "private" not in str(error.value)


@pytest.mark.parametrize("amount", [True, 0, -1, "990", 9.90])
def test_invalid_local_refund_amount_never_calls_sdk(channel, amount):
    with pytest.raises(PaymentChannelError):
        channel.refund({**ORDER, "amount_cents": amount})


@pytest.mark.parametrize("trade_no", [None, "", " ", 123])
def test_invalid_local_trade_reference_never_calls_sdk(channel, trade_no):
    with pytest.raises(PaymentChannelError):
        channel.refund({**ORDER, "provider_trade_no": trade_no})


@pytest.mark.parametrize(
    "refund_mode,tamper_query",
    [("valid", False), ("timeout", False), ("tampered", False),
     ("timeout", True), ("tampered", True)],
)
def test_real_sdk_refund_and_query_sign_requests_and_verify_inner_responses(
    channel, monkeypatch, alipay_keys, refund_mode, tamper_query
):
    import alipay
    from Cryptodome.Hash import SHA256
    from Cryptodome.Signature import pkcs1_15

    methods = []

    def local_urlopen(request, data=None, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        request_body = data if isinstance(request, str) else request.data
        parts = [urlsplit(url).query]
        if request_body:
            parts.append(request_body.decode("utf-8"))
        fields = dict(parse_qsl("&".join(parts), keep_blank_values=True))
        signature = base64.b64decode(fields.pop("sign"))
        canonical = "&".join(
            f"{key}={value}" for key, value in sorted(fields.items()) if value != ""
        ).encode("utf-8")
        pkcs1_15.new(alipay_keys["merchant_private"].public_key()).verify(
            SHA256.new(canonical), signature
        )
        method = fields["method"]
        methods.append(method)
        content = json.loads(fields["biz_content"])
        assert content["out_trade_no"] == ORDER["id"]
        assert content["out_request_no"] == REQUEST_NO
        assert fields["sign_type"] == "RSA2"
        if method == "alipay.trade.refund":
            assert content["refund_amount"] == "9.90"
            assert content["refund_reason"] == "用户自助申请全额退款"
            if refund_mode == "timeout":
                raise TimeoutError("response lost after provider accepted refund")
            payload = refund_response()
            tampered = refund_mode == "tampered"
        else:
            assert method == "alipay.trade.fastpay.refund.query"
            payload = query_response()
            tampered = tamper_query
        response = json.dumps(payload, separators=(",", ":"))
        response_signature = base64.b64encode(
            pkcs1_15.new(alipay_keys["provider_private"]).sign(
                SHA256.new(response.encode("utf-8"))
            )
        ).decode("ascii")
        if tampered:
            response = response.replace("9.90", "0.01")
        response_type = method.replace(".", "_") + "_response"
        envelope = (
            "{" + json.dumps(response_type) + ":" + response
            + ',"sign":' + json.dumps(response_signature) + "}"
        )
        return BytesIO(envelope.encode("utf-8"))

    # Keep both installed SDK methods and their RSA2 verification intact.
    monkeypatch.setattr(alipay, "urlopen", local_urlopen)
    if tamper_query:
        with pytest.raises(PaymentChannelError):
            channel.refund(ORDER)
    else:
        assert channel.refund(ORDER)["refund_amount_cents"] == 990
    expected_methods = ["alipay.trade.refund"]
    if refund_mode != "valid":
        expected_methods.append("alipay.trade.fastpay.refund.query")
    assert methods == expected_methods
