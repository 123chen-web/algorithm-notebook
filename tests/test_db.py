import sqlite3
from contextlib import closing

import pytest

from db import connect, init_db


CREATED_AT = "2026-09-21T10:00:00+00:00"


def insert_plan(conn, **overrides):
    values = {
        "name": "月度套餐",
        "period_days": 30,
        "ai_daily_limit": 10,
        "price_cents": 990,
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    return conn.execute(
        "INSERT INTO plans(name,period_days,ai_daily_limit,price_cents,"
        "created_at) VALUES (:name,:period_days,:ai_daily_limit,:price_cents,"
        ":created_at)",
        values,
    ).lastrowid


def insert_order(conn, order_id="test-order", **overrides):
    values = {
        "id": order_id,
        "user_id": 1,
        "plan_id": 1,
        "amount_cents": 990,
        "channel": "alipay",
        "provider_trade_no": None,
        "created_at": CREATED_AT,
    }
    values.update(overrides)
    conn.execute(
        "INSERT INTO orders(id,user_id,plan_id,amount_cents,channel,"
        "provider_trade_no,created_at) VALUES (:id,:user_id,:plan_id,"
        ":amount_cents,:channel,:provider_trade_no,:created_at)",
        values,
    )


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


@pytest.fixture
def database(database_path):
    init_db()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,timezone,created_at) "
            "VALUES ('alice','unused','Asia/Shanghai',?)",
            (CREATED_AT,),
        )
        insert_plan(conn)
    return database_path


def test_init_new_database_has_empty_payment_tables(database_path):
    init_db()
    init_db()

    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(users)")
        }
        assert columns["plan_id"]["type"] == "INTEGER"
        assert columns["plan_id"]["notnull"] == 0
        assert columns["plan_expires_at"]["type"] == "TEXT"
        assert columns["plan_expires_at"]["notnull"] == 0
        order_columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(orders)")
        }
        assert order_columns["refunded_at"]["type"] == "TEXT"
        assert order_columns["refunded_at"]["notnull"] == 0


@pytest.mark.parametrize("has_existing_migrations", [False, True])
def test_init_migrates_old_users_and_preserves_data(
    database_path, has_existing_migrations
):
    # 分别模拟最早的 users 表，以及已包含邮箱、提醒和体验标记的旧库。
    with closing(sqlite3.connect(database_path)) as conn:
        conn.execute(
            "CREATE TABLE users ("
            "id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, "
            "password_hash TEXT NOT NULL, timezone TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO users VALUES (7,'alice','original-hash',"
            "'Asia/Shanghai',?)",
            (CREATED_AT,),
        )
        if has_existing_migrations:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN last_reminder_sent TEXT")
            conn.execute(
                "ALTER TABLE users ADD COLUMN is_trial INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute(
                "UPDATE users SET email = 'alice@example.com', "
                "last_reminder_sent = '2026-09-20', is_trial = 1"
            )
        conn.commit()

    init_db()

    with connect(write=True) as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id = 7").fetchone())
        assert user == {
            "id": 7,
            "username": "alice",
            "password_hash": "original-hash",
            "timezone": "Asia/Shanghai",
            "created_at": CREATED_AT,
            "email": "alice@example.com" if has_existing_migrations else None,
            "last_reminder_sent": "2026-09-20" if has_existing_migrations else None,
            "is_trial": 1 if has_existing_migrations else 0,
            "plan_id": None,
            "plan_expires_at": None,
        }
        plan_id = insert_plan(conn)
        conn.execute(
            "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = 7",
            (plan_id, "2026-10-21T10:00:00+00:00"),
        )
        insert_order(conn, user_id=7, plan_id=plan_id)
        conn.execute(
            "UPDATE orders SET status = 'paid', provider_trade_no = 'trade-1', "
            "paid_at = ?",
            (CREATED_AT,),
        )
        before = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("users", "plans", "orders")
        }

    # 重复启动不能清空已迁移的订阅字段，也不能改变套餐和订单。
    init_db()
    init_db()
    with connect() as conn:
        after = {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
            for table in ("users", "plans", "orders")
        }
        assert after == before
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE users SET plan_id = 999 WHERE id = 7")


def test_payment_defaults_boundaries_and_amount_snapshot(database):
    with connect(write=True) as conn:
        plan_id = insert_plan(conn, period_days=1, ai_daily_limit=0, price_cents=1)
        insert_order(conn, plan_id=plan_id, amount_cents=2)
        user = conn.execute("SELECT * FROM users WHERE id = 1").fetchone()
        assert user["is_trial"] == 0
        assert user["plan_id"] is None
        assert user["plan_expires_at"] is None
        plan = conn.execute(
            "SELECT * FROM plans WHERE id = ?", (plan_id,)
        ).fetchone()
        assert plan["is_active"] == 1
        assert plan["ai_daily_limit"] == 0
        order = conn.execute("SELECT * FROM orders").fetchone()
        assert order["status"] == "pending"
        assert order["provider_trade_no"] is None
        assert order["paid_at"] is None
        assert order["closed_at"] is None
        assert order["refunded_at"] is None
        assert order["created_at"] == CREATED_AT

        # 成交金额独立保存，套餐价格和启用状态变化不能改写历史订单。
        conn.execute(
            "UPDATE plans SET price_cents = 3, is_active = 0 WHERE id = ?", (plan_id,)
        )
        assert conn.execute("SELECT amount_cents FROM orders").fetchone()[0] == 2


@pytest.mark.parametrize("column", ["period_days", "price_cents", "ai_daily_limit"])
@pytest.mark.parametrize("value", [-1, 1.5, "invalid", b"1", None])
def test_plan_rejects_invalid_integer_values(database, column, value):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_plan(conn, **{column: value})


@pytest.mark.parametrize("column", ["period_days", "price_cents"])
def test_plan_rejects_zero_period_or_price(database, column):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_plan(conn, **{column: 0})


@pytest.mark.parametrize("value", [0, -1, 1.5, "invalid", b"1", None])
def test_order_rejects_invalid_amount(database, value):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_order(conn, amount_cents=value)


def test_numeric_affinity_stores_convertible_values_as_integers(database):
    # SQLite 先执行 INTEGER affinity，再执行 CHECK；可无损转换的输入仍合法。
    with connect(write=True) as conn:
        plan_id = insert_plan(
            conn, period_days="30", ai_daily_limit=0.0, price_cents="990"
        )
        insert_order(conn, plan_id=plan_id, amount_cents=1.0)
        stored = conn.execute(
            "SELECT typeof(period_days),typeof(ai_daily_limit),typeof(price_cents) "
            "FROM plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        assert tuple(stored) == ("integer", "integer", "integer")
        amount_type = conn.execute(
            "SELECT typeof(amount_cents) FROM orders"
        ).fetchone()[0]
        assert amount_type == "integer"


@pytest.mark.parametrize("value", [-1, 2, 0.5, "invalid", None])
def test_plan_rejects_invalid_active_flag(database, value):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE plans SET is_active = ? WHERE id = 1", (value,))


@pytest.mark.parametrize("channel", ["alipay", "wechat"])
@pytest.mark.parametrize("status", ["pending", "paid", "failed", "closed", "refunded"])
def test_order_accepts_declared_channels_and_statuses(database, channel, status):
    with connect(write=True) as conn:
        insert_order(conn, channel=channel)
        conn.execute("UPDATE orders SET status = ?", (status,))
        row = conn.execute("SELECT channel,status FROM orders").fetchone()
        assert tuple(row) == (channel, status)


@pytest.mark.parametrize("column", ["channel", "status"])
@pytest.mark.parametrize("value", ["invalid", "", None])
def test_order_rejects_invalid_channel_or_status(database, column, value):
    with connect(write=True) as conn:
        insert_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"UPDATE orders SET {column} = ?", (value,))


def test_order_id_is_required_and_unique(database):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_order(conn, order_id=None)
        insert_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            insert_order(conn)


@pytest.mark.parametrize("column", ["user_id", "plan_id"])
@pytest.mark.parametrize("value", [999, None])
def test_order_requires_existing_user_and_plan(database, column, value):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            insert_order(conn, **{column: value})


@pytest.mark.parametrize("table", ["users", "plans"])
def test_order_prevents_deleting_user_or_plan(database, table):
    with connect(write=True) as conn:
        insert_order(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(f"DELETE FROM {table} WHERE id = 1")
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 1


def test_user_subscription_requires_existing_plan_and_restricts_delete(database):
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE users SET plan_id = 999 WHERE id = 1")
        conn.execute("UPDATE users SET plan_id = 1 WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM plans WHERE id = 1")
        conn.execute("UPDATE users SET plan_id = NULL WHERE id = 1")
        conn.execute("DELETE FROM plans WHERE id = 1")
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_provider_trade_number_is_unique_within_channel(database):
    with connect(write=True) as conn:
        for index, channel in enumerate(("alipay", "alipay", "wechat", "wechat")):
            insert_order(conn, order_id=f"pending-{index}", channel=channel)
        insert_order(conn, order_id="alipay-paid", provider_trade_no="same-trade")
        insert_order(
            conn,
            order_id="wechat-paid",
            channel="wechat",
            provider_trade_no="same-trade",
        )
        with pytest.raises(sqlite3.IntegrityError):
            insert_order(
                conn, order_id="duplicate-trade", provider_trade_no="same-trade"
            )
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 6


def test_init_migrates_old_orders_without_losing_orders_or_refund_times(database_path):
    with closing(sqlite3.connect(database_path)) as conn:
        # Freeze the pre-refund schema rather than deriving it from current SCHEMA.
        conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, timezone TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE plans (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                period_days INTEGER NOT NULL, ai_daily_limit INTEGER NOT NULL,
                price_cents INTEGER NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE orders (
                id TEXT NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                amount_cents INTEGER NOT NULL,
                channel TEXT NOT NULL CHECK(channel IN ('alipay', 'wechat')),
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'paid', 'failed', 'closed', 'refunded')),
                provider_trade_no TEXT,
                created_at TEXT NOT NULL, paid_at TEXT, closed_at TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO users VALUES (1, 'alice', 'original-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        insert_plan(conn)
        for status in ("pending", "paid", "refunded"):
            insert_order(conn, order_id=f"legacy-{status}")
            conn.execute(
                "UPDATE orders SET status = ?, paid_at = ? WHERE id = ?",
                (status, None if status == "pending" else CREATED_AT, f"legacy-{status}"),
            )
        conn.row_factory = sqlite3.Row
        before = [dict(row) for row in conn.execute("SELECT * FROM orders ORDER BY id")]
        conn.commit()

    init_db()
    init_db()

    with connect(write=True) as conn:
        after = [dict(row) for row in conn.execute("SELECT * FROM orders ORDER BY id")]
        assert after == [{**order, "refunded_at": None} for order in before]
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(orders)")
        }
        assert columns["refunded_at"]["type"] == "TEXT"
        assert columns["refunded_at"]["notnull"] == 0
        conn.execute(
            "UPDATE orders SET refunded_at = ? WHERE id = 'legacy-refunded'",
            (CREATED_AT,),
        )

    init_db()
    with connect() as conn:
        assert conn.execute(
            "SELECT refunded_at FROM orders WHERE id = 'legacy-refunded'"
        ).fetchone()[0] == CREATED_AT
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
