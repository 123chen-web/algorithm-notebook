"""支付 fail-closed：无商户配置时自动支付入口一律 503，绝不调 SDK。"""

import json

import pytest
from fastapi.testclient import TestClient

import main
import payment_channels
import payments
from db import connect
from payment_channels import payments_configured


PAYMENT_ENV_VARS = (
    "PAYMENTS_MOCK_ENABLED",
    "PAYMENTS_MOCK_SECRET",
    "ALIPAY_SANDBOX",
    "ALIPAY_APP_ID",
    "ALIPAY_PRIVATE_KEY",
    "ALIPAY_PUBLIC_KEY",
    "ALIPAY_SELLER_ID",
    "ALIPAY_NOTIFY_URL",
    "WECHAT_APP_ID",
    "WECHAT_MCH_ID",
    "WECHAT_API_V3_KEY",
    "WECHAT_CERT_SERIAL_NO",
    "WECHAT_PRIVATE_KEY",
    "WECHAT_PUBLIC_KEY",
    "WECHAT_PUBLIC_KEY_ID",
    "WECHAT_NOTIFY_URL",
)

EXPECTED_DETAIL = "支付通道未配置，请使用手动收款"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "payments-failclosed.db"))
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", "failclosed-test-secret")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    with TestClient(
        main.app,
        headers={"X-CSRF-Protection": "1"},
    ) as instance:
        with connect(write=True) as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, timezone, created_at) "
                "VALUES (1, 'alice', 'unused', 'Asia/Shanghai', ?)",
                (main.utc_now(),),
            )
            conn.execute(
                "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, 1, ?)",
                (main.token_hash("failclosed-session"), 9999999999),
            )
            conn.execute(
                "INSERT INTO plans(id, name, period_days, ai_daily_limit, price_cents, "
                "is_active, created_at) VALUES (1, '月套餐', 30, 20, 990, 1, ?)",
                (main.utc_now(),),
            )
        instance.cookies.set("session", "failclosed-session")
        yield instance


def clear_payment_env(monkeypatch):
    for name in PAYMENT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def order_count():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


def test_payments_configured_unit(monkeypatch):
    clear_payment_env(monkeypatch)
    assert payments_configured("alipay") is False
    assert payments_configured("wechat") is False
    assert payments_configured("unknown") is False
    # 显式本地联调模式视为已配置。
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    assert payments_configured("alipay") is True
    assert payments_configured("wechat") is True
    assert payments_configured("unknown") is False
    # 完整商户配置视为已配置。
    monkeypatch.delenv("PAYMENTS_MOCK_ENABLED")
    monkeypatch.setenv("ALIPAY_SANDBOX", "1")
    for name in (
        "ALIPAY_APP_ID", "ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY",
        "ALIPAY_SELLER_ID", "ALIPAY_NOTIFY_URL",
    ):
        monkeypatch.setenv(name, "x")
    assert payments_configured("alipay") is True
    assert payments_configured("wechat") is False
    # 缺一项即视为未配置。
    monkeypatch.delenv("ALIPAY_APP_ID")
    assert payments_configured("alipay") is False


def test_create_order_without_config_returns_503_and_creates_no_order(client, monkeypatch):
    clear_payment_env(monkeypatch)
    before = order_count()
    response = client.post("/api/orders", json={"plan_id": 1, "channel": "alipay"})
    assert response.status_code == 503
    assert response.json()["detail"] == EXPECTED_DETAIL
    # fail-closed：订单不落库。
    assert order_count() == before


def test_create_order_never_touches_sdk_without_config(client, monkeypatch):
    clear_payment_env(monkeypatch)

    def boom(channel):
        raise AssertionError("SDK/渠道实例化不应被调用")

    monkeypatch.setattr(payments, "get_channel", boom)
    response = client.post("/api/orders", json={"plan_id": 1, "channel": "wechat"})
    assert response.status_code == 503
    assert response.json()["detail"] == EXPECTED_DETAIL


def test_refund_without_config_returns_503(client, monkeypatch):
    # 先在 Mock 模式下建单并支付。
    order = client.post("/api/orders", json={"plan_id": 1, "channel": "alipay"}).json()["order"]
    payload = {
        "order_id": order["id"], "channel": "alipay",
        "provider_trade_no": "mock-trade-failclosed", "amount_cents": 990, "status": "paid",
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = payment_channels.MockChannel("alipay", "failclosed-test-secret").sign_callback(raw_body)
    callback = client.post(
        "/api/payments/mock/alipay/callback", content=raw_body,
        headers={"Content-Type": "application/json", "X-Mock-Signature": signature},
    )
    assert callback.status_code == 200

    clear_payment_env(monkeypatch)
    response = client.post(f"/api/orders/{order['id']}/refund")
    assert response.status_code == 503
    assert response.json()["detail"] == EXPECTED_DETAIL
    # 订单保持 paid，未被退款。
    with connect() as conn:
        status = conn.execute(
            "SELECT status FROM orders WHERE id = ?", (order["id"],)
        ).fetchone()["status"]
    assert status == "paid"


def test_public_callback_without_config_is_rejected(client, monkeypatch):
    clear_payment_env(monkeypatch)
    response = client.post("/api/payments/alipay/callback", content=b"{}")
    assert response.status_code == 503
    response = client.post("/api/payments/wechat/callback", content=b"{}")
    assert response.status_code == 503


def test_mock_mode_still_works_when_explicitly_enabled(client):
    # 显式本地联调模式不受 fail-closed 影响。
    response = client.post("/api/orders", json={"plan_id": 1, "channel": "alipay"})
    assert response.status_code == 201
