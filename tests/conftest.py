import base64
import os
from urllib.parse import urlencode

import pytest


@pytest.fixture(autouse=True)
def isolate_alipay_configuration(monkeypatch):
    # A developer's real .env must not configure payment in unrelated tests.
    for name in tuple(os.environ):
        if name.startswith("ALIPAY_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def block_alipay_network(monkeypatch):
    """A missing SDK fails the relevant tests; a present SDK must never go online."""
    try:
        import alipay
    except ModuleNotFoundError as error:
        if error.name == "alipay":
            return
        raise

    def unexpected_network(*args, **kwargs):
        pytest.fail("Alipay network access must be mocked in tests")

    monkeypatch.setattr(alipay, "urlopen", unexpected_network)


@pytest.fixture(scope="session")
def alipay_keys():
    from Cryptodome.PublicKey import RSA

    # Separate keys model the merchant signing requests and Alipay signing notices.
    return {
        "merchant_private": RSA.generate(2048),
        "provider_private": RSA.generate(2048),
    }


@pytest.fixture
def alipay_env(monkeypatch, alipay_keys):
    values = {
        "ALIPAY_APP_ID": "test-alipay-app",
        "ALIPAY_SELLER_ID": "test-alipay-seller",
        "ALIPAY_PRIVATE_KEY": alipay_keys["merchant_private"].export_key().decode(),
        "ALIPAY_PUBLIC_KEY": (
            alipay_keys["provider_private"].public_key().export_key().decode()
        ),
        "ALIPAY_NOTIFY_URL": "https://example.com/api/payments/alipay/callback",
        "ALIPAY_SANDBOX": "0",
    }
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "0")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture
def signed_alipay_callback(alipay_keys):
    from Cryptodome.Hash import SHA256
    from Cryptodome.Signature import pkcs1_15

    def sign(*, sign_key=None, **overrides):
        data = {
            "app_id": "test-alipay-app",
            "seller_id": "test-alipay-seller",
            "out_trade_no": "order-123",
            "trade_no": "alipay-trade-123",
            "total_amount": "9.90",
            "trade_status": "TRADE_SUCCESS",
            "sign_type": "RSA2",
        }
        data.update(overrides)
        data = {key: value for key, value in data.items() if value is not None}
        # Independent implementation of Alipay's canonical signing string.
        # Signing decoded values also tests form encoding of +, %, &, and Unicode.
        canonical = "&".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
            if key not in ("sign", "sign_type") and value != ""
        ).encode("utf-8")
        key = sign_key if sign_key is not None else alipay_keys["provider_private"]
        data["sign"] = base64.b64encode(
            pkcs1_15.new(key).sign(SHA256.new(canonical))
        ).decode("ascii")
        return urlencode(data).encode("utf-8")

    return sign
