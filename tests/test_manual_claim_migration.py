"""Historical manual claims have unknown prices; migration must not invent them."""
import pytest

import db


SNAPSHOT_COLUMNS = (
    "amount_cents", "period_days", "plan_name_snapshot",
    "verified_amount_cents", "receipt_reference",
)


@pytest.fixture
def historical_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "claims-v21.db"))
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 21])
        patch.setattr(db, "SCHEMA_VERSION", 21)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO users(id, username, password_hash, timezone, created_at) "
                     "VALUES (7, 'old-payer', 'hash', 'Asia/Taipei', '2026-10-08')")
        conn.execute("INSERT INTO plans(id, name, period_days, ai_daily_limit, price_cents, "
                     "is_active, created_at) VALUES (3, '旧套餐', 30, 10, 990, 1, '2026-10-08')")
        conn.execute("INSERT INTO manual_payment_claims(id, user_id, plan_id, payer_note, "
                     "created_at) VALUES (11, 7, 3, '历史登记', '2026-10-08')")
        before = dict(conn.execute("SELECT * FROM manual_payment_claims WHERE id = 11").fetchone())
    return before


def test_upgrade_preserves_old_claim_and_leaves_historical_snapshot_unknown(historical_claim):
    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 28
        after = dict(conn.execute("SELECT * FROM manual_payment_claims WHERE id = 11").fetchone())
        for column in SNAPSHOT_COLUMNS:
            assert after.pop(column) is None
        assert after == historical_claim


@pytest.mark.parametrize("column,value", [
    ("amount_cents", 0), ("period_days", -1), ("verified_amount_cents", 1.25),
    ("plan_name_snapshot", ""), ("receipt_reference", ""), ("receipt_reference", "x" * 129),
])
def test_snapshot_constraints_reject_invalid_stored_values(historical_claim, column, value):
    db.init_db()
    with db.connect(write=True) as conn:
        with pytest.raises(db.sqlite3.IntegrityError):
            # Identifiers are selected only from the fixed cases above.
            conn.execute(f"UPDATE manual_payment_claims SET {column} = ? WHERE id = 11", (value,))


def test_receipt_index_allows_unknown_legacy_rows_but_rejects_duplicate_receipts(historical_claim):
    db.init_db()
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO manual_payment_claims(id, user_id, plan_id, payer_note, "
                     "created_at) VALUES (12, 7, 3, '另一历史登记', '2026-10-08')")
        assert conn.execute("SELECT COUNT(*) FROM manual_payment_claims "
                            "WHERE receipt_reference IS NULL").fetchone()[0] == 2
        conn.execute("UPDATE manual_payment_claims SET receipt_reference = ? WHERE id = 11",
                     ("alipay:receipt-one",))
        with pytest.raises(db.sqlite3.IntegrityError):
            conn.execute("UPDATE manual_payment_claims SET receipt_reference = ? WHERE id = 12",
                         ("alipay:receipt-one",))
