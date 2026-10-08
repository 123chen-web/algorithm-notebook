"""seed_plans.py：按名字幂等插入默认套餐，purchasable 默认关闭。"""
import sqlite3

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
        assert row["period_days"] == 30
    assert rows["标准版"]["price_cents"] == 990
    assert rows["标准版"]["ai_daily_limit"] == 50
    assert rows["进阶版"]["price_cents"] == 1990
    assert rows["进阶版"]["ai_daily_limit"] == 120


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


def plan_rows():
    with connect(create=False) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM plans ORDER BY id")]


@pytest.fixture
def existing_plans(database_path):
    seed_plans.main([])
    with connect(write=True) as conn:
        conn.execute("UPDATE plans SET ai_daily_limit = 30, price_cents = 1234, "
                     "period_days = 45, purchasable = 1 WHERE name = '标准版'")
        conn.execute("UPDATE plans SET ai_daily_limit = 60, is_active = 0 "
                     "WHERE name = '进阶版'")
        conn.executemany(
            "INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, "
            "is_active, purchasable, created_at) VALUES (?, 365, 999, 1, 0, 0, ?)",
            [(name, "2026-01-01T00:00:00+00:00")
             for name in ("站长专属", "其他套餐", "标准版旧")],
        )
    return plan_rows()


@pytest.mark.parametrize("flags", [[], ["--dry-run"]])
def test_update_limits_defaults_to_preview_without_any_db_changes(
    database_path, existing_plans, monkeypatch, capsys, flags,
):
    before = database_path.read_bytes()
    monkeypatch.setattr(seed_plans, "init_db", lambda: pytest.fail("must not migrate"))
    assert seed_plans.main(["--update-limits", *flags]) == 0
    output = capsys.readouterr().out
    assert "标准版" in output and "30 -> 50" in output
    assert "进阶版" in output and "60 -> 120" in output
    assert "dry-run" in output
    assert database_path.read_bytes() == before
    assert plan_rows() == existing_plans


def test_update_limits_apply_changes_only_named_limits_and_is_idempotent(
    database_path, existing_plans, monkeypatch, capsys,
):
    monkeypatch.setattr(seed_plans, "init_db", lambda: pytest.fail("must not migrate"))
    assert seed_plans.main(["--update-limits", "--apply"]) == 0
    expected = [dict(row) for row in existing_plans]
    for row in expected:
        if row["name"] in ("标准版", "进阶版"):
            row["ai_daily_limit"] = {"标准版": 50, "进阶版": 120}[row["name"]]
    assert plan_rows() == expected  # price, duration, flags, hidden plans unchanged
    capsys.readouterr()
    assert seed_plans.main(["--update-limits", "--apply"]) == 0
    assert plan_rows() == expected
    assert "更新 0" in capsys.readouterr().out


@pytest.mark.parametrize("flags", [[], ["--apply"]])
def test_update_limits_does_not_create_missing_plans(database_path, capsys, flags):
    init_db()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, created_at) "
            "VALUES ('标准版', 30, 30, 990, '2026-01-01')"
        )
    assert seed_plans.main(["--update-limits", *flags]) == 0
    rows = plan_rows()
    assert len(rows) == 1
    assert rows[0]["ai_daily_limit"] == (50 if flags else 30)
    assert "进阶版」不存在" in capsys.readouterr().out


@pytest.mark.parametrize("flags", [[], ["--apply"]])
def test_update_limits_never_creates_a_missing_database(database_path, flags):
    with pytest.raises(sqlite3.OperationalError):
        seed_plans.main(["--update-limits", *flags])
    assert not database_path.exists()


@pytest.mark.parametrize("flags", [
    ["--apply"],
    ["--enable-purchase", "--apply"],
    ["--update-limits", "--enable-purchase"],
    ["--update-limits", "--apply", "--dry-run"],
])
def test_update_limits_rejects_ambiguous_flags_before_writing(database_path, flags):
    with pytest.raises(SystemExit) as error:
        seed_plans.main(flags)
    assert error.value.code == 2
    assert not database_path.exists()


def test_update_limits_rolls_back_both_plans_on_failure(database_path, existing_plans):
    with connect(write=True) as conn:
        conn.execute(
            "CREATE TRIGGER reject_advanced BEFORE UPDATE OF ai_daily_limit ON plans "
            "WHEN OLD.name = '进阶版' BEGIN SELECT RAISE(ABORT, 'quota update blocked'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="quota update blocked"):
        seed_plans.main(["--update-limits", "--apply"])
    assert plan_rows() == existing_plans


def test_regular_seed_still_preserves_existing_limits(database_path, existing_plans):
    seed_plans.main([])
    assert plan_rows() == existing_plans


def test_update_limits_lists_and_updates_each_exact_name_duplicate(database_path, capsys):
    init_db()
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, created_at) "
            "VALUES ('标准版', 30, ?, 990, '2026-01-01')",
            [(30,), (40,)],
        )
    before = plan_rows()
    seed_plans.main(["--update-limits"])
    output = capsys.readouterr().out
    assert "30 -> 50" in output and "40 -> 50" in output
    for row in before:
        assert f"id={row['id']}" in output
    assert plan_rows() == before
    seed_plans.main(["--update-limits", "--apply"])
    assert [row["ai_daily_limit"] for row in plan_rows()] == [50, 50]
