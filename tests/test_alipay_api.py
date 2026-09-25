import json
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from db import connect
from payment_channels import MockChannel


CALLBACK_PATH = "/api/payments/alipay/callback"
WECHAT_CALLBACK_PATH = "/api/payments/wechat/callback"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "alipay-api.db"))
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "0")
    # 通知来自支付宝服务器，不带会话 Cookie 或浏览器的 CSRF 请求头。
    with TestClient(main.app) as instance:
        yield instance


def test_public_callback_passes_original_form_to_business_layer(
    client, monkeypatch
):
    raw_body = b"out_trade_no=order-1&subject=a%2Bb+c&sign=base64%2Bvalue%3D"
    received = []

    def handle_callback(channel, body, headers, *, user_id):
        received.append((channel, body, headers["content-type"], user_id))
        return {"status": "paid"}

    monkeypatch.setattr(main.payments, "handle_callback", handle_callback)
    response = client.post(
        CALLBACK_PATH,
        content=raw_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert received == [
        ("alipay", raw_body, "application/x-www-form-urlencoded", None)
    ]
    assert response.status_code == 200
    assert response.text == "success"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("status_code", [400, 404, 409, 503])
def test_callback_failure_returns_plain_text_without_acknowledging_payment(
    client, monkeypatch, status_code
):
    def reject(*args, **kwargs):
        raise HTTPException(status_code, "internal verification or order detail")

    monkeypatch.setattr(main.payments, "handle_callback", reject)
    response = client.post(CALLBACK_PATH, content=b"sign=invalid")
    assert response.status_code == status_code
    assert response.text == "failure"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_public_callback_is_disabled_in_mock_mode(client, monkeypatch):
    secret = "alipay-api-test-mock-secret"
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", secret)
    body = json.dumps({
        "order_id": "order-1",
        "channel": "alipay",
        "provider_trade_no": "mock-trade-1",
        "amount_cents": 990,
        "status": "paid",
    }).encode("utf-8")

    def must_not_handle(*args, **kwargs):
        pytest.fail("公开通知入口不能进入 mock 支付处理")

    monkeypatch.setattr(main.payments, "handle_callback", must_not_handle)
    response = client.post(
        CALLBACK_PATH,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Mock-Signature": MockChannel("alipay", secret).sign_callback(body),
        },
    )
    assert response.status_code == 404
    assert response.text == "failure"


@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", CALLBACK_PATH),
        ("PATCH", CALLBACK_PATH),
        ("DELETE", CALLBACK_PATH),
        ("POST", CALLBACK_PATH + "/"),
        ("POST", CALLBACK_PATH + "/other"),
        ("PUT", WECHAT_CALLBACK_PATH),
        ("PATCH", WECHAT_CALLBACK_PATH),
        ("DELETE", WECHAT_CALLBACK_PATH),
        ("POST", WECHAT_CALLBACK_PATH + "/"),
        ("POST", WECHAT_CALLBACK_PATH + "/other"),
        ("POST", "/api/payments/mock/alipay/callback"),
        ("POST", "/api/payments/mock/wechat/callback"),
        ("POST", "/api/orders"),
    ],
)
def test_csrf_exemption_is_only_exact_provider_callback_paths(client, method, path):
    response = client.request(method, path, content=b"test", follow_redirects=False)
    assert response.status_code == 403


@pytest.mark.parametrize("path", [CALLBACK_PATH, WECHAT_CALLBACK_PATH])
def test_csrf_exemption_lets_exact_provider_post_reach_the_route(client, path):
    # Config is incomplete in this fixture (only ALIPAY_* / no WECHAT_* env),
    # so both exempt paths reach channel_adapter() and fail there (503) rather
    # than being rejected by the CSRF gate itself (403).
    response = client.post(path, content=b"test", follow_redirects=False)
    assert response.status_code == 503


def test_mock_callback_still_requires_login(client, monkeypatch):
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    response = client.post(
        "/api/payments/mock/alipay/callback",
        content=b"test",
        headers={"X-CSRF-Protection": "1"},
    )
    assert response.status_code == 401


@pytest.fixture
def alipay_database(client, alipay_env, monkeypatch):
    now = "2026-09-21T10:00:00+00:00"
    monkeypatch.setattr(main.payments, "utc_now", lambda: now)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (1, 'alice', 'unused', 'Asia/Shanghai', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO plans(id, name, period_days, ai_daily_limit, "
            "price_cents, created_at) VALUES (1, '月套餐', 30, 20, 990, ?)",
            (now,),
        )


@pytest.fixture
def alipay_order(alipay_database, monkeypatch):
    from alipay import AliPay

    monkeypatch.setattr(
        AliPay,
        "api_alipay_trade_precreate",
        lambda self, **kwargs: {
            "code": "10000",
            "out_trade_no": kwargs["out_trade_no"],
            "qr_code": "https://qr.alipay.com/test-order",
        },
    )
    return main.payments.create_order(1, 1, "alipay")["order"]


@pytest.fixture
def authenticated_alipay_client(client, alipay_database):
    token = "alipay-api-user-session"
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, 1, ?)",
            (main.token_hash(token), int(time.time()) + 3600),
        )
    client.cookies.set("session", token)
    client.headers["X-CSRF-Protection"] = "1"
    return client


def notify_alipay(client, body):
    return client.post(
        CALLBACK_PATH,
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def stored_state(order):
    with connect() as conn:
        stored_order = dict(conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order["id"],)
        ).fetchone())
        subscription = dict(conn.execute(
            "SELECT plan_id, plan_expires_at FROM users WHERE id = 1"
        ).fetchone())
    return stored_order, subscription


def test_rsa_signed_notification_commits_payment_and_replay_is_idempotent(
    client, alipay_order, signed_alipay_callback
):
    body = signed_alipay_callback(out_trade_no=alipay_order["id"])
    response = notify_alipay(client, body)
    assert response.status_code == 200
    assert response.text == "success"
    first_order, first_subscription = stored_state(alipay_order)
    assert first_order["status"] == "paid"
    assert first_order["provider_trade_no"] == "alipay-trade-123"
    assert first_subscription == {
        "plan_id": 1,
        "plan_expires_at": "2026-10-21T10:00:00+00:00",
    }

    duplicate = notify_alipay(client, body)
    assert duplicate.status_code == 200
    assert duplicate.text == "success"
    # 支付宝后续的 TRADE_FINISHED 也属于同一笔付款，不应再次延长套餐。
    finished = notify_alipay(client, signed_alipay_callback(
        out_trade_no=alipay_order["id"], trade_status="TRADE_FINISHED"
    ))
    assert finished.status_code == 200
    assert finished.text == "success"
    assert stored_state(alipay_order) == (first_order, first_subscription)


@pytest.mark.parametrize(
    "changes,expected_status",
    [
        ({"total_amount": "0.01"}, 400),
        ({"app_id": "another-app"}, 400),
        ({"seller_id": "another-seller"}, 400),
        ({"out_trade_no": "missing-order"}, 404),
    ],
)
def test_rsa_signed_invalid_notification_does_not_mutate_database(
    client, alipay_order, signed_alipay_callback, changes, expected_status
):
    initial = stored_state(alipay_order)
    payload = {"out_trade_no": alipay_order["id"], **changes}
    response = notify_alipay(client, signed_alipay_callback(**payload))
    assert response.status_code == expected_status
    assert response.text == "failure"
    assert stored_state(alipay_order) == initial


def test_notification_with_tampered_signature_does_not_mutate_database(
    client, alipay_order, signed_alipay_callback
):
    initial = stored_state(alipay_order)
    body = signed_alipay_callback(out_trade_no=alipay_order["id"])
    tampered = body.replace(b"total_amount=9.90", b"total_amount=9.91")
    assert tampered != body
    response = notify_alipay(client, tampered)
    assert response.status_code == 400
    assert response.text == "failure"
    assert stored_state(alipay_order) == initial


def test_alipay_notification_cannot_pay_a_wechat_order(
    client, alipay_order, signed_alipay_callback
):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE orders SET channel = 'wechat' WHERE id = ?",
            (alipay_order["id"],),
        )
    initial = stored_state(alipay_order)
    response = notify_alipay(client, signed_alipay_callback(
        out_trade_no=alipay_order["id"]
    ))
    assert response.status_code == 400
    assert response.text == "failure"
    assert stored_state(alipay_order) == initial


def test_rsa_signed_closed_notification_does_not_grant_subscription(
    client, alipay_order, signed_alipay_callback
):
    response = notify_alipay(client, signed_alipay_callback(
        out_trade_no=alipay_order["id"], trade_status="TRADE_CLOSED"
    ))
    assert response.status_code == 200
    assert response.text == "success"
    order, subscription = stored_state(alipay_order)
    assert order["status"] == "closed"
    assert order["paid_at"] is None
    assert subscription == {"plan_id": None, "plan_expires_at": None}


@pytest.mark.parametrize("private_key", [None, "invalid-private-key"])
def test_missing_or_invalid_configuration_rejects_order_before_inserting(
    authenticated_alipay_client, monkeypatch, private_key
):
    if private_key is None:
        monkeypatch.delenv("ALIPAY_PRIVATE_KEY")
    else:
        monkeypatch.setenv("ALIPAY_PRIVATE_KEY", private_key)
    response = authenticated_alipay_client.post(
        "/api/orders", json={"plan_id": 1, "channel": "alipay"}
    )
    assert response.status_code == 503
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_precreate_timeout_keeps_pending_order_for_later_signed_notification(
    authenticated_alipay_client, monkeypatch, signed_alipay_callback
):
    from alipay import AliPay

    def time_out(self, **kwargs):
        raise TimeoutError("simulated precreate response loss")

    monkeypatch.setattr(AliPay, "api_alipay_trade_precreate", time_out)
    response = authenticated_alipay_client.post(
        "/api/orders", json={"plan_id": 1, "channel": "alipay"}
    )
    assert response.status_code == 502
    order_id = response.json()["detail"]["order_id"]
    pending_order, initial_subscription = stored_state({"id": order_id})
    assert pending_order["status"] == "pending"
    assert initial_subscription == {"plan_id": None, "plan_expires_at": None}

    authenticated_alipay_client.cookies.clear()
    authenticated_alipay_client.headers.pop("X-CSRF-Protection")
    notification = notify_alipay(
        authenticated_alipay_client,
        signed_alipay_callback(out_trade_no=order_id),
    )
    assert notification.status_code == 200
    assert notification.text == "success"
    paid_order, subscription = stored_state(pending_order)
    assert paid_order["status"] == "paid"
    assert subscription == {
        "plan_id": 1,
        "plan_expires_at": "2026-10-21T10:00:00+00:00",
    }


def test_later_closed_notification_cannot_reverse_paid_order(
    client, alipay_order, signed_alipay_callback
):
    paid = notify_alipay(client, signed_alipay_callback(
        out_trade_no=alipay_order["id"]
    ))
    assert paid.status_code == 200
    first_state = stored_state(alipay_order)
    closed = notify_alipay(client, signed_alipay_callback(
        out_trade_no=alipay_order["id"], trade_status="TRADE_CLOSED"
    ))
    assert closed.status_code == 409
    assert closed.text == "failure"
    assert stored_state(alipay_order) == first_state
