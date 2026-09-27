"""seed_plans.py：按名字幂等插入默认套餐，purchasable 默认关闭。"""
import pytest

import seed_plans
from db import connect, init_db


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "seed-plans-test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def test_seed_creates_two_plans_disabled_for_purchase(database_path):
    seed_plans.main([])
    with connect() as conn:
        rows = {
            row["name"]: dict(row)
            for row in conn.execute("SELECT * FROM plans ORDER BY id")
        }
    assert set(rows) == {"标准版", "进阶版"}
    for row in rows.values():
        assert row["purchasable"] == 0
        assert row["is_active"] == 1
    assert rows["标准版"]["price_cents"] == 990
    assert rows["标准版"]["ai_daily_limit"] == 30
    assert rows["进阶版"]["price_cents"] == 1990
    assert rows["进阶版"]["ai_daily_limit"] == 60


def test_seed_is_idempotent_and_never_overwrites_manual_price_changes(database_path):
    seed_plans.main([])
    init_db()
    with connect(write=True) as conn:
        # 管理员上线后手动调过价，重新跑脚本不能把它改回默认值。
        conn.execute("UPDATE plans SET price_cents = 1234 WHERE name = '标准版'")

    seed_plans.main([])
    with connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        price = conn.execute(
            "SELECT price_cents FROM plans WHERE name = '标准版'"
        ).fetchone()[0]
    assert rows == 2
    assert price == 1234


def test_dry_run_writes_nothing(database_path):
    seed_plans.main(["--dry-run"])
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 0


def test_enable_purchase_flips_existing_rows_without_creating_new_ones(database_path):
    seed_plans.main([])
    seed_plans.main(["--enable-purchase"])
    with connect() as conn:
        rows = conn.execute(
            "SELECT name, purchasable FROM plans ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert all(row["purchasable"] == 1 for row in rows)


def test_enable_purchase_dry_run_does_not_flip_anything(database_path):
    seed_plans.main([])
    seed_plans.main(["--enable-purchase", "--dry-run"])
    with connect() as conn:
        rows = conn.execute("SELECT purchasable FROM plans").fetchall()
    assert all(row["purchasable"] == 0 for row in rows)
