import json
import sqlite3

import pytest
from fastapi import HTTPException

import payments
from db import connect, init_db
from payment_channels import MockChannel, PaymentChannelError


NOW = "2026-09-21T10:00:00+00:00"
SECRET = "business-test-payment-secret"


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("PAYMENTS_MOCK_ENABLED", "1")
    monkeypatch.setenv("PAYMENTS_MOCK_SECRET", SECRET)
    monkeypatch.setattr(payments, "utc_now", lambda: NOW)
    init_db()
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO users(id,username,password_hash,timezone,created_at) "
            "VALUES (?,?,'unused','Asia/Shanghai',?)",
            [(1, "alice", NOW), (2, "bob", NOW)],
        )
        conn.executemany(
            "INSERT INTO plans(id,name,period_days,ai_daily_limit,price_cents,"
            "created_at) VALUES (?,?,30,10,990,?)",
            [(1, "月度套餐", NOW), (2, "另一个套餐", NOW)],
        )


def callback_request(order, **overrides):
    data = {
        "order_id": order["id"],
        "channel": order["channel"],
        "provider_trade_no": f"trade-{order['id']}",
        "amount_cents": order["amount_cents"],
        "status": "paid",
    }
    data.update(overrides)
    body = json.dumps(data).encode("utf-8")
    signature = MockChannel(data["channel"], SECRET).sign_callback(body)
    return body, {"X-Mock-Signature": signature}


def deliver(order, **overrides):
    body, headers = callback_request(order, **overrides)
    return payments.handle_callback(order["channel"], body, headers, user_id=1)


def subscription():
    with connect() as conn:
        return dict(conn.execute(
            "SELECT plan_id,plan_expires_at,is_trial FROM users WHERE id = 1"
        ).fetchone())


def test_order_is_committed_before_calling_channel(database, monkeypatch):
    def create_payment(adapter, order):
        stored = payments.get_order(1, order["id"])
        assert stored["status"] == "pending"
        # 第二个连接能拿到写锁，说明调用渠道时没有挂着下单事务。
        with connect(write=True) as conn:
            conn.execute("UPDATE plans SET is_active = 0 WHERE id = 1")
        return {"provider": "test", "qr_code_url": None, "redirect_url": None}

    monkeypatch.setattr(MockChannel, "create_payment", create_payment)
    result = payments.create_order(1, 1, "alipay")
    assert result["order"]["amount_cents"] == 990
    assert result["payment"]["provider"] == "test"


def test_channel_error_preserves_pending_order_for_later_callback(
    database, monkeypatch
):
    def unavailable(adapter, order):
        raise PaymentChannelError("simulated response loss")

    monkeypatch.setattr(MockChannel, "create_payment", unavailable)
    with pytest.raises(HTTPException) as error:
        payments.create_order(1, 1, "alipay")
    assert error.value.status_code == 502
    order = payments.get_order(1, error.value.detail["order_id"])
    assert order["status"] == "pending"
    assert subscription()["plan_id"] is None


def test_order_ids_are_random_and_not_reused(database):
    orders = [payments.create_order(1, 1, "alipay")["order"] for _ in range(2)]
    assert orders[0]["id"] != orders[1]["id"]
    for order in orders:
        assert len(order["id"]) == 32
        assert len(bytes.fromhex(order["id"])) == 16


@pytest.mark.parametrize("plan_id", [True, 0, -1, "1", 1.5])
def test_business_layer_rejects_invalid_plan_id(database, plan_id):
    with pytest.raises(HTTPException) as error:
        payments.create_order(1, plan_id, "alipay")
    assert error.value.status_code == 400


def test_business_layer_rejects_unknown_channel_and_user(database):
    with pytest.raises(HTTPException) as error:
        payments.create_order(1, 1, "unknown")
    assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        payments.create_order(999, 1, "alipay")
    assert error.value.status_code == 404


def test_trial_accounts_cannot_place_orders(database):
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_trial = 1 WHERE id = 1")
    with pytest.raises(HTTPException) as error:
        payments.create_order(1, 1, "alipay")
    assert error.value.status_code == 403
    assert subscription()["plan_id"] is None


@pytest.mark.parametrize("status", ["failed", "closed"])
def test_failure_and_closure_are_idempotent_without_granting_subscription(
    database, status
):
    order = payments.create_order(1, 1, "alipay")["order"]
    first = deliver(order, status=status)
    second = deliver(order, status=status)
    assert first == second
    assert first["status"] == status
    assert first["paid_at"] is None
    assert first["closed_at"] == (NOW if status == "closed" else None)
    assert subscription()["plan_expires_at"] is None
    with pytest.raises(HTTPException) as error:
        deliver(order, status="paid")
    assert error.value.status_code == 409
    assert payments.get_order(1, order["id"]) == first


def test_trade_number_cannot_be_used_for_another_order(database):
    first = payments.create_order(1, 1, "alipay")["order"]
    second = payments.create_order(1, 1, "alipay")["order"]
    deliver(first, status="closed", provider_trade_no="shared-trade")
    with pytest.raises(HTTPException) as error:
        deliver(second, provider_trade_no="shared-trade")
    assert error.value.status_code == 409
    stored = payments.get_order(1, second["id"])
    assert stored["status"] == "pending"
    assert stored["provider_trade_no"] is None
    assert subscription()["plan_id"] is None


def test_signed_callback_cannot_change_order_channel(database):
    order = payments.create_order(1, 1, "alipay")["order"]
    body, headers = callback_request(order, channel="wechat")
    with pytest.raises(HTTPException) as error:
        payments.handle_callback("wechat", body, headers, user_id=1)
    assert error.value.status_code == 400
    assert payments.get_order(1, order["id"])["status"] == "pending"


def test_signed_callback_for_missing_order_returns_not_found(database):
    order = payments.create_order(1, 1, "alipay")["order"]
    with pytest.raises(HTTPException) as error:
        deliver(order, order_id="missing")
    assert error.value.status_code == 404


def set_subscription(plan_id, plan_expires_at):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = 1",
            (plan_id, plan_expires_at),
        )


def test_paid_callback_starts_subscription_from_now_when_none_exists(database):
    order = payments.create_order(1, 1, "alipay")["order"]
    deliver(order)
    assert subscription() == {
        "plan_id": 1,
        "plan_expires_at": "2026-10-21T10:00:00+00:00",
        "is_trial": 0,
    }


def test_paid_callback_extends_from_current_expiry_when_not_expired(database):
    set_subscription(1, "2026-09-25T10:00:00+00:00")
    order = payments.create_order(1, 1, "alipay")["order"]
    deliver(order)
    # 未过期时从当前到期时间顺延，不从下单/支付的"现在"重新起算。
    assert subscription()["plan_expires_at"] == "2026-10-25T10:00:00+00:00"


def test_paid_callback_restarts_from_now_when_already_expired(database):
    set_subscription(1, "2026-09-10T10:00:00+00:00")
    order = payments.create_order(1, 1, "alipay")["order"]
    deliver(order)
    # 已过期不倒扣，从"现在"重新起算，而不是从已经过去的到期时间顺延。
    assert subscription()["plan_expires_at"] == "2026-10-21T10:00:00+00:00"


def test_duplicate_paid_callback_does_not_grant_subscription_twice(database):
    order = payments.create_order(1, 1, "alipay")["order"]
    deliver(order)
    first = subscription()
    deliver(order)
    assert subscription() == first


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
def test_refund_clears_subscription_preserves_usage_and_records_time(database, channel):
    order = payments.create_order(1, 1, channel)["order"]
    paid = deliver(order)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (1, ?, 7)",
            (NOW[:10],),
        )

    refunded = payments.refund_order(1, order["id"])

    assert refunded == payments.get_order(1, order["id"])
    assert refunded["status"] == "refunded"
    assert refunded["refunded_at"] == NOW
    assert refunded["paid_at"] == paid["paid_at"]
    assert refunded["provider_trade_no"] == paid["provider_trade_no"]
    assert refunded["amount_cents"] == 990
    assert subscription() == {
        "plan_id": None, "plan_expires_at": None, "is_trial": 0,
    }
    with connect() as conn:
        assert conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = 1 AND day = ?",
            (NOW[:10],),
        ).fetchone()[0] == 7


def test_refund_of_older_order_clears_entire_newer_subscription(database):
    older = payments.create_order(1, 1, "alipay")["order"]
    newer = payments.create_order(1, 2, "alipay")["order"]
    deliver(older)
    deliver(newer)
    assert subscription()["plan_id"] == 2
    assert subscription()["plan_expires_at"] == "2026-11-20T10:00:00+00:00"

    payments.refund_order(1, older["id"])

    assert subscription()["plan_id"] is None
    assert subscription()["plan_expires_at"] is None
    assert payments.get_order(1, newer["id"])["status"] == "paid"


@pytest.mark.parametrize("status", ["pending", "failed", "closed", "refunded"])
def test_refund_rejects_non_paid_orders_before_contacting_channel(
    database, monkeypatch, status
):
    order = payments.create_order(1, 1, "alipay")["order"]
    if status == "refunded":
        deliver(order)
        payments.refund_order(1, order["id"])
    elif status != "pending":
        deliver(order, status=status)
    before = payments.get_order(1, order["id"])
    before_subscription = subscription()

    def unexpected_refund(adapter, order):
        pytest.fail("Ineligible orders must not call the payment channel")

    monkeypatch.setattr(MockChannel, "refund", unexpected_refund)
    with pytest.raises(HTTPException) as error:
        payments.refund_order(1, order["id"])
    assert error.value.status_code == 409
    assert payments.get_order(1, order["id"]) == before
    assert subscription() == before_subscription


@pytest.mark.parametrize("order_exists", [False, True])
def test_refund_rejects_missing_or_other_users_order_before_channel(
    database, monkeypatch, order_exists
):
    order = payments.create_order(1, 1, "alipay")["order"]
    paid = deliver(order)
    before_subscription = subscription()

    def unexpected_refund(adapter, order):
        pytest.fail("Unauthorized refunds must not call the payment channel")

    monkeypatch.setattr(MockChannel, "refund", unexpected_refund)
    with pytest.raises(HTTPException) as error:
        payments.refund_order(2, order["id"] if order_exists else "missing")
    assert error.value.status_code == 404
    assert payments.get_order(1, order["id"]) == paid
    assert subscription() == before_subscription


def test_uncertain_channel_refund_retains_paid_order_and_allows_retry(
    database, monkeypatch
):
    order = payments.create_order(1, 1, "alipay")["order"]
    paid = deliver(order)
    before_subscription = subscription()
    attempts = []
    original_refund = MockChannel.refund

    def lost_response(adapter, order):
        attempts.append(dict(order))
        if len(attempts) == 1:
            raise PaymentChannelError("simulated response loss after acceptance")
        return original_refund(adapter, order)

    monkeypatch.setattr(MockChannel, "refund", lost_response)
    with pytest.raises(HTTPException) as error:
        payments.refund_order(1, order["id"])
    assert error.value.status_code == 502
    assert error.value.detail["order_id"] == order["id"]
    assert error.value.detail["message"]
    assert payments.get_order(1, order["id"]) == paid
    assert subscription() == before_subscription

    assert payments.refund_order(1, order["id"])["status"] == "refunded"
    assert attempts == [paid, paid]
    assert subscription()["plan_id"] is None


def test_refund_transaction_rolls_back_order_when_subscription_update_fails(database):
    order = payments.create_order(1, 1, "alipay")["order"]
    paid = deliver(order)
    before_subscription = subscription()
    with connect(write=True) as conn:
        conn.execute(
            "CREATE TRIGGER fail_subscription_clear "
            "BEFORE UPDATE OF plan_id ON users WHEN NEW.plan_id IS NULL "
            "BEGIN SELECT RAISE(ABORT, 'simulated subscription failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="simulated subscription failure"):
        payments.refund_order(1, order["id"])
    assert payments.get_order(1, order["id"]) == paid
    assert subscription() == before_subscription

    with connect(write=True) as conn:
        conn.execute("DROP TRIGGER fail_subscription_clear")
    assert payments.refund_order(1, order["id"])["status"] == "refunded"
    assert subscription()["plan_id"] is None


def test_interleaved_refund_loser_does_not_revoke_new_purchase(database, monkeypatch):
    order = payments.create_order(1, 1, "alipay")["order"]
    deliver(order)
    original_refund = MockChannel.refund
    channel_calls = []
    winner = {}
    new_purchase = {}

    def interleave(adapter, snapshot):
        channel_calls.append(dict(snapshot))
        if len(channel_calls) == 1:
            # Both requests already read paid; the nested request commits first.
            # It also needs a write lock, proving no lock spans the channel call.
            winner.update(payments.refund_order(1, order["id"]))
            purchase = payments.create_order(1, 2, "alipay")["order"]
            deliver(purchase)
            new_purchase.update(subscription())
        return original_refund(adapter, snapshot)

    monkeypatch.setattr(MockChannel, "refund", interleave)
    with pytest.raises(HTTPException) as error:
        payments.refund_order(1, order["id"])

    assert error.value.status_code == 409
    assert len(channel_calls) == 2
    assert all(snapshot["status"] == "paid" for snapshot in channel_calls)
    assert payments.get_order(1, order["id"]) == winner
    assert winner["status"] == "refunded"
    assert subscription() == new_purchase
    assert new_purchase["plan_id"] == 2


def test_paid_callback_after_refund_cannot_restore_subscription(database):
    order = payments.create_order(1, 1, "alipay")["order"]
    deliver(order)
    refunded = payments.refund_order(1, order["id"])

    with pytest.raises(HTTPException) as error:
        deliver(order)
    assert error.value.status_code == 409
    assert payments.get_order(1, order["id"]) == refunded
    assert subscription()["plan_id"] is None
