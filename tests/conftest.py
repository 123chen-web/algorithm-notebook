import base64
import os
from urllib.parse import urlencode

import pytest
from core import config


# 端到端冒烟测试（tests/e2e）需要 playwright 与真实浏览器，默认不收集；
# 需要时设置环境变量 OY_RUN_E2E=1，并按 tests/e2e/README.md 运行。
collect_ignore = [] if os.environ.get("OY_RUN_E2E") == "1" else ["e2e"]

# 线上 PBKDF2 是 60 万次迭代，单次约 0.4 秒；几千个用例每次注册/登录都真算一遍，
# 全量测试要近半小时（CI 超时是 30 分钟）。测试里默认降到 1000 次——哈希格式、
# 加盐、常量时间比较等逻辑完全不变，只是不再为安全强度付出时间。
# 需要验证真实迭代次数的用例加 @pytest.mark.real_password_hash。
TEST_PASSWORD_ITERATIONS = 1_000


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_password_hash: 使用线上的 PBKDF2 迭代次数，不做测试提速"
    )


@pytest.fixture(autouse=True)
def fast_password_hash(request, monkeypatch):
    if request.node.get_closest_marker("real_password_hash"):
        return
    from core import security
    monkeypatch.setattr(security, "PASSWORD_ITERATIONS", TEST_PASSWORD_ITERATIONS)


@pytest.fixture(autouse=True)
def first_review_same_day_by_default(monkeypatch):
    # 线上默认"刚记下的完整错题明天才第一次到期"；绝大多数既有测试是"记下就立刻复习"的流程，
    # 所以测试里默认当天到期，专门测试新行为的用例自己再把它改成 1（见 tests/test_first_review_delay.py）。
    import main
    monkeypatch.setattr(config, "FIRST_REVIEW_DELAY_DAYS", 0)


@pytest.fixture(autouse=True)
def isolate_code_runner(monkeypatch):
    import code_runner
    monkeypatch.delenv("CODE_RUNNER_URL", raising=False)
    monkeypatch.delenv("CODE_RUNNER_TOKEN", raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("Code execution services must be mocked in tests")
    monkeypatch.setattr(code_runner, "build_opener", forbidden)


@pytest.fixture(autouse=True)
def isolate_alipay_configuration(monkeypatch):
    # A developer's real .env must not configure payment in unrelated tests.
    for name in tuple(os.environ):
        if name.startswith("ALIPAY_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def isolate_mail_configuration(monkeypatch):
    # A developer's real .env must never make tests send real email
    # (bounces from fake addresses would land in the owner's inbox).
    for name in tuple(os.environ):
        if name.startswith("SMTP_"):
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


@pytest.fixture(autouse=True)
def isolate_wechat_configuration(monkeypatch):
    # A developer's real .env must not configure payment in unrelated tests.
    for name in tuple(os.environ):
        if name.startswith("WECHAT_"):
            monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def block_wechat_network(monkeypatch):
    """A missing SDK fails the relevant tests; a present SDK must never go online."""
    try:
        import requests
    except ModuleNotFoundError:
        return

    def unexpected_network(*args, **kwargs):
        pytest.fail("WeChat Pay network access must be mocked in tests")

    monkeypatch.setattr(requests, "get", unexpected_network)
    monkeypatch.setattr(requests, "post", unexpected_network)


@pytest.fixture(scope="session")
def wechat_keys():
    from cryptography.hazmat.primitives.asymmetric import rsa

    # Separate keys model the merchant signing requests and the WeChat Pay
    # platform signing notifications/responses (platform public key mode).
    return {
        "merchant_private": rsa.generate_private_key(public_exponent=65537, key_size=2048),
        "platform_private": rsa.generate_private_key(public_exponent=65537, key_size=2048),
    }


@pytest.fixture
def wechat_env(monkeypatch, wechat_keys):
    from cryptography.hazmat.primitives import serialization

    def pem(key):
        return key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()

    values = {
        "WECHAT_APP_ID": "test-wechat-app",
        "WECHAT_MCH_ID": "test-wechat-mch",
        "WECHAT_CERT_SERIAL_NO": "TEST0000000000000000000000000000000000",
        "WECHAT_PRIVATE_KEY": pem(wechat_keys["merchant_private"]),
        "WECHAT_PUBLIC_KEY": wechat_keys["platform_private"].public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode(),
        "WECHAT_PUBLIC_KEY_ID": "PUB_KEY_ID_TEST0000000000000000000000000000000000",
        "WECHAT_API_V3_KEY": "0123456789abcdef0123456789abcdef",
        "WECHAT_NOTIFY_URL": "https://example.com/api/payments/wechat/callback",
    }
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "0")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture
def signed_wechat_callback(wechat_keys, wechat_env):
    import json
    import time
    import uuid
    from base64 import b64encode

    from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.hashes import SHA256

    def sign(*, sign_key=None, associated_data="transaction", header_overrides=None, **overrides):
        resource = {
            "mchid": wechat_env["WECHAT_MCH_ID"],
            "appid": wechat_env["WECHAT_APP_ID"],
            "out_trade_no": "order-123",
            "transaction_id": "wechat-trade-123",
            "trade_type": "NATIVE",
            "trade_state": "SUCCESS",
            "trade_state_desc": "支付成功",
            "amount": {"total": 990, "payer_total": 990, "currency": "CNY", "payer_currency": "CNY"},
        }
        resource.update(overrides)
        resource = {key: value for key, value in resource.items() if value is not None}

        nonce = uuid.uuid4().hex[:24].encode("ascii")
        aesgcm = AESGCM(wechat_env["WECHAT_API_V3_KEY"].encode("utf-8"))
        plaintext = json.dumps(resource, ensure_ascii=False).encode("utf-8")
        assoc_bytes = associated_data.encode("utf-8") if associated_data else b""
        ciphertext = aesgcm.encrypt(nonce, plaintext, assoc_bytes)

        envelope = {
            "id": str(uuid.uuid4()),
            "create_time": "2026-01-01T00:00:00+08:00",
            "resource_type": "encrypt-resource",
            "event_type": "TRANSACTION.SUCCESS",
            "summary": "支付成功",
            "resource": {
                "original_type": "transaction",
                "algorithm": "AEAD_AES_256_GCM",
                "ciphertext": b64encode(ciphertext).decode("ascii"),
                "nonce": nonce.decode("ascii"),
                **({"associated_data": associated_data} if associated_data else {}),
            },
        }
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

        timestamp = str(int(time.time()))
        header_nonce = uuid.uuid4().hex
        canonical = f"{timestamp}\n{header_nonce}\n{body.decode('utf-8')}\n".encode("utf-8")
        key = sign_key if sign_key is not None else wechat_keys["platform_private"]
        signature = b64encode(key.sign(canonical, PKCS1v15(), SHA256())).decode("ascii")

        headers = {
            "Wechatpay-Signature": signature,
            "Wechatpay-Timestamp": timestamp,
            "Wechatpay-Nonce": header_nonce,
            "Wechatpay-Serial": wechat_env["WECHAT_PUBLIC_KEY_ID"],
            "Wechatpay-Signature-Type": "WECHATPAY2-SHA256-RSA2048",
        }
        if header_overrides:
            headers.update(header_overrides)
        return body, headers

    return sign


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
