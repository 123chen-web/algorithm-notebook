import json
import time

import pytest
from fastapi.testclient import TestClient

import main
from db import connect
from payment_channels import MockChannel


MOCK_SECRET = "payment-api-test-secret"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "payments-api.db"))
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", MOCK_SECRET)
    monkeypatch.setenv("COOKIE_SECURE", "0")
    with TestClient(
        main.app,
        headers={"X-CSRF-Protection": "1"},
    ) as instance:
        with connect(write=True) as conn:
            for user_id, username in ((1, "alice"), (2, "bob")):
                conn.execute(
                    """
                    INSERT INTO users(
                        id, username, password_hash, timezone, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, username, "unused", "Asia/Shanghai", main.utc_now()),
                )
                conn.execute(
                    """
                    INSERT INTO sessions(token_hash, user_id, expires_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        main.token_hash(f"payment-session-{username}"),
                        user_id,
                        int(time.time()) + 3600,
                    ),
                )
            conn.executemany(
                """
                INSERT INTO plans(
                    id, name, period_days, ai_daily_limit,
                    price_cents, is_active, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, "月套餐", 30, 20, 990, 1, main.utc_now()),
                    (2, "已停用套餐", 60, 40, 1990, 0, main.utc_now()),
                ],
            )
        instance.cookies.set("session", "payment-session-alice")
        yield instance


def create_order(client, channel="alipay"):
    response = client.post(
        "/api/orders", json={"plan_id": 1, "channel": channel}
    )
    assert response.status_code == 201
    return response.json()["order"]


def send_callback(client, order, **changes):
    payload = {
        "order_id": order["id"],
        "channel": order["channel"],
        "provider_trade_no": f"mock-trade-{order['id']}",
        "amount_cents": order["amount_cents"],
        "status": "paid",
        **changes,
    }
    raw_body = json.dumps(payload).encode("utf-8")
    signature = MockChannel(order["channel"], MOCK_SECRET).sign_callback(raw_body)
    return client.post(
        f"/api/payments/mock/{order['channel']}/callback",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Mock-Signature": signature},
    )


def stored_order(order_id):
    with connect() as conn:
        return dict(
            conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        )


def subscription():
    with connect() as conn:
        return dict(
            conn.execute(
                "SELECT plan_id, plan_expires_at FROM users WHERE id = 1"
            ).fetchone()
        )


@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("GET", "/api/plans", None),
        ("POST", "/api/orders", {"plan_id": 1, "channel": "alipay"}),
        ("GET", "/api/orders", None),
        ("GET", "/api/orders/unknown", None),
        ("POST", "/api/orders/unknown/refund", None),
        ("POST", "/api/payments/mock/alipay/callback", {}),
    ],
)
def test_payment_routes_require_login(client, method, path, payload):
    client.cookies.clear()
    response = client.request(method, path, json=payload)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/orders", {"plan_id": 1, "channel": "alipay"}),
        ("/api/orders/unknown/refund", None),
        ("/api/payments/mock/alipay/callback", {}),
    ],
)
def test_payment_writes_require_csrf(client, path, payload):
    client.headers.pop("X-CSRF-Protection")
    response = client.post(path, json=payload)
    assert response.status_code == 403


def test_plan_list_excludes_inactive_plans(client):
    response = client.get("/api/plans")
    assert response.status_code == 200
    plans = response.json()["plans"]
    assert [plan["id"] for plan in plans] == [1]
    assert plans[0]["price_cents"] == 990


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_create_order_and_poll(client, channel):
    response = client.post(
        "/api/orders", json={"plan_id": 1, "channel": channel}
    )
    assert response.status_code == 201
    body = response.json()
    order = body["order"]
    assert order["plan_id"] == 1
    assert order["amount_cents"] == 990
    assert order["channel"] == channel
    assert order["status"] == "pending"
    assert body["payment"]["provider"] == "mock"
    assert body["payment"]["qr_code_url"].startswith(f"mock://{channel}/")
    assert MOCK_SECRET not in response.text

    polled = client.get(f"/api/orders/{order['id']}")
    assert polled.status_code == 200
    assert polled.json()["order"] == order


@pytest.mark.parametrize("plan_id", [2, 999])
def test_create_order_rejects_unavailable_plan(client, plan_id):
    response = client.post(
        "/api/orders", json={"plan_id": plan_id, "channel": "alipay"}
    )
    assert response.status_code == 404
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_create_order_rejects_trial_account(client):
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_trial = 1 WHERE id = 1")
    response = client.post(
        "/api/orders", json={"plan_id": 1, "channel": "alipay"}
    )
    assert response.status_code == 403
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"plan_id": 1, "channel": "alipay", "amount_cents": 1},
        {"plan_id": 1, "channel": "unsupported"},
        {"plan_id": "1", "channel": "alipay"},
        {"plan_id": 1.0, "channel": "alipay"},
        {"plan_id": True, "channel": "alipay"},
        {"plan_id": 0, "channel": "alipay"},
    ],
)
def test_create_order_rejects_invalid_input(client, payload):
    response = client.post("/api/orders", json=payload)
    assert response.status_code == 422


def test_order_and_callback_are_isolated_by_user(client):
    order = create_order(client)
    client.cookies.set("session", "payment-session-bob")
    assert client.get(f"/api/orders/{order['id']}").status_code == 404
    assert send_callback(client, order).status_code == 404
    assert stored_order(order["id"])["status"] == "pending"
    assert subscription() == {"plan_id": None, "plan_expires_at": None}


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_signed_callback_marks_order_paid_and_is_idempotent(client, channel):
    order = create_order(client, channel)
    response = send_callback(client, order)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["order"]["status"] == "paid"
    assert body["order"]["paid_at"] is not None
    first_subscription = subscription()
    assert first_subscription["plan_id"] == 1
    assert first_subscription["plan_expires_at"] is not None

    duplicate = send_callback(client, order)
    assert duplicate.status_code == 200
    assert duplicate.json() == body
    assert subscription() == first_subscription
    assert client.get(f"/api/orders/{order['id']}").json()["order"] == body["order"]


def test_mock_callback_and_order_creation_are_disabled_by_default(client, monkeypatch):
    order = create_order(client)
    monkeypatch.delenv("PAYMENTS_MOCK_ENABLED")
    assert send_callback(client, order).status_code == 404
    response = client.post(
        "/api/orders", json={"plan_id": 1, "channel": "alipay"}
    )
    assert response.status_code == 503
    assert stored_order(order["id"])["status"] == "pending"


def test_mock_callback_rejects_invalid_signature(client):
    order = create_order(client)
    response = client.post(
        "/api/payments/mock/alipay/callback",
        json={
            "order_id": order["id"],
            "channel": "alipay",
            "provider_trade_no": "mock-invalid-signature",
            "amount_cents": order["amount_cents"],
            "status": "paid",
        },
        headers={"X-Mock-Signature": "0" * 64},
    )
    assert response.status_code == 400
    assert stored_order(order["id"])["status"] == "pending"
    assert subscription() == {"plan_id": None, "plan_expires_at": None}


def test_mock_callback_rejects_signed_wrong_amount(client):
    order = create_order(client)
    response = send_callback(client, order, amount_cents=1)
    assert response.status_code == 400
    assert stored_order(order["id"])["status"] == "pending"
    assert subscription() == {"plan_id": None, "plan_expires_at": None}


def test_order_list_is_empty_before_purchase(client):
    response = client.get("/api/orders")
    assert response.status_code == 200
    assert response.json() == {"orders": []}


def test_order_list_is_private_newest_first_and_includes_inactive_plan(client):
    older, first_tied, second_tied = [create_order(client) for _ in range(3)]
    assert send_callback(client, first_tied).status_code == 200
    client.cookies.set("session", "payment-session-bob")
    bobs_order = create_order(client)
    client.cookies.set("session", "payment-session-alice")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE orders SET created_at = '2026-09-20T00:00:00+00:00' WHERE id = ?",
            (older["id"],),
        )
        conn.execute(
            "UPDATE orders SET created_at = '2026-09-21T00:00:00+00:00' "
            "WHERE id IN (?, ?)",
            (first_tied["id"], second_tied["id"]),
        )
        conn.execute("UPDATE plans SET is_active = 0, price_cents = 1990 WHERE id = 1")

    response = client.get("/api/orders")
    assert response.status_code == 200
    orders = response.json()["orders"]
    expected_ids = sorted([first_tied["id"], second_tied["id"]], reverse=True)
    assert [order["id"] for order in orders] == [*expected_ids, older["id"]]
    for order in orders:
        assert order["user_id"] == 1
        assert order["plan_name"] == "月套餐"
        assert order["amount_cents"] == 990
        assert {key: value for key, value in order.items() if key != "plan_name"} == (
            stored_order(order["id"])
        )

    client.cookies.set("session", "payment-session-bob")
    assert [order["id"] for order in client.get("/api/orders").json()["orders"]] == [
        bobs_order["id"],
    ]


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_refund_endpoint_updates_order_subscription_and_list(client, channel):
    order = create_order(client, channel)
    assert send_callback(client, order).status_code == 200

    response = client.post(f"/api/orders/{order['id']}/refund")
    assert response.status_code == 200
    refunded = response.json()["order"]
    assert refunded["status"] == "refunded"
    assert refunded["refunded_at"]
    assert refunded == stored_order(order["id"])
    assert subscription() == {"plan_id": None, "plan_expires_at": None}
    current_user = client.get("/api/me").json()
    assert current_user["plan_id"] is None
    assert current_user["plan_expires_at"] is None
    assert client.get("/api/orders").json()["orders"][0]["status"] == "refunded"

    repeat = client.post(f"/api/orders/{order['id']}/refund")
    assert repeat.status_code == 409
    assert stored_order(order["id"]) == refunded


def test_refund_endpoint_rejects_pending_and_other_users_orders(client):
    order = create_order(client)
    assert client.post(f"/api/orders/{order['id']}/refund").status_code == 409
    assert send_callback(client, order).status_code == 200
    before_subscription = subscription()

    client.cookies.set("session", "payment-session-bob")
    assert client.post(f"/api/orders/{order['id']}/refund").status_code == 404
    assert client.post("/api/orders/missing/refund").status_code == 404
    assert stored_order(order["id"])["status"] == "paid"
    assert subscription() == before_subscription
