import sqlite3
from contextlib import closing

import pytest

import db


CREATED_AT = "2026-09-21T10:00:00+00:00"


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "migrations.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def test_fresh_database_and_repeated_startup(database_path, monkeypatch):
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "users", "mistakes", "ai_usage", "ai_calls", "mistake_clusters"
        } <= tables
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(ai_calls)")
        }
        assert {"idx_ai_calls_created_at", "idx_ai_calls_user_created_at"} <= indexes

    def must_not_run(conn):
        pytest.fail("完整数据库重复启动时不应重新执行迁移")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [(version, name, must_not_run) for version, name, _apply in db.MIGRATIONS],
    )
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION


@pytest.mark.parametrize("has_email", [False, True])
def test_legacy_database_backfills_columns_and_preserves_data(database_path, has_email):
    # 冻结早期结构，不能从当前 SCHEMA 生成待迁移的旧库。
    with closing(sqlite3.connect(database_path)) as conn:
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
                amount_cents INTEGER NOT NULL, channel TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', provider_trade_no TEXT,
                created_at TEXT NOT NULL, paid_at TEXT, closed_at TEXT
            );
            CREATE TABLE problems (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL, language TEXT NOT NULL,
                code TEXT NOT NULL, thinking TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO users VALUES (7, 'alice', 'original-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        if has_email:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
            conn.execute("UPDATE users SET email = 'alice@example.com'")
        conn.execute(
            "INSERT INTO plans VALUES (3, '旧套餐', 30, 10, 990, 1, ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO orders VALUES ('old-order', 7, 3, 990, 'alipay', "
            "'paid', 'old-trade', ?, ?, NULL)",
            (CREATED_AT, CREATED_AT),
        )
        conn.execute(
            "INSERT INTO problems VALUES (5, 7, '二分查找', 'Python', "
            "'pass', '边界问题', ?)",
            (CREATED_AT,),
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        conn.commit()

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        user = conn.execute("SELECT * FROM users WHERE id = 7").fetchone()
        assert user["username"] == "alice"
        assert user["password_hash"] == "original-hash"
        assert user["email"] == ("alice@example.com" if has_email else None)
        assert user["plan_id"] is None
        assert user["is_trial"] == 0
        assert user["is_banned"] == 0
        assert user["avatar_version"] == 0
        assert {"plan_expires_at", "last_reminder_sent"} <= set(user.keys())
        problem = conn.execute("SELECT * FROM problems WHERE id = 5").fetchone()
        assert problem["zone"] == "算法"
        assert problem["thinking"] == "边界问题"
        assert conn.execute("SELECT purchasable FROM plans WHERE id = 3").fetchone()[0] == 1
        order = conn.execute("SELECT * FROM orders WHERE id = 'old-order'").fetchone()
        assert order["status"] == "paid"
        assert order["provider_trade_no"] == "old-trade"
        assert order["paid_at"] == CREATED_AT
        assert order["refunded_at"] is None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_migration_rolls_back_schema_data_and_version(database_path, monkeypatch):
    db.init_db()
    before_version = db.SCHEMA_VERSION

    def broken_migration(conn):
        conn.execute("ALTER TABLE users ADD COLUMN unfinished TEXT")
        conn.execute("CREATE TABLE unfinished_table (value TEXT)")
        conn.execute("INSERT INTO unfinished_table VALUES ('半成品')")
        raise ValueError("模拟迁移失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        db.MIGRATIONS + [(before_version + 1, "故障迁移", broken_migration)],
    )
    monkeypatch.setattr(db, "SCHEMA_VERSION", before_version + 1)
    with pytest.raises(ValueError, match="模拟迁移失败"):
        db.init_db()

    with db.connect() as conn:
        assert db.schema_version(conn) == before_version
        assert "unfinished" not in {
            row["name"] for row in conn.execute("PRAGMA table_info(users)")
        }
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'unfinished_table'"
        ).fetchone() is None


def test_baseline_is_also_transactional(database_path, monkeypatch):
    def broken_baseline(conn):
        db._apply_baseline(conn)
        raise ValueError("基线最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [(1, "故障基线", broken_baseline), *db.MIGRATIONS[1:]],
    )
    with pytest.raises(ValueError, match="基线最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 0
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall() == []


def test_newer_database_is_rejected_without_changing_contents(database_path):
    future_version = db.SCHEMA_VERSION + 1
    with closing(sqlite3.connect(database_path)) as conn:
        conn.execute("CREATE TABLE future_data (value TEXT)")
        conn.execute("INSERT INTO future_data VALUES ('保留未来数据库内容')")
        conn.execute(f"PRAGMA user_version = {future_version}")
        conn.commit()
        before = list(conn.iterdump())

    with pytest.raises(RuntimeError) as exc_info:
        db.init_db()
    assert f"数据库版本 {future_version}" in str(exc_info.value)
    assert f"当前程序支持的 {db.SCHEMA_VERSION}" in str(exc_info.value)
    with closing(sqlite3.connect(database_path)) as conn:
        assert list(conn.iterdump()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == future_version


def test_complete_database_from_a_newer_version_is_rejected_too(database_path):
    # 真实场景：新版程序建好的库表齐全，只有版本号更大；只有入口处的版本检查能拦住它
    # （表不全的库会先走进基线自修复，那里另有一道检查，所以上一个测试测不出入口检查被删）。
    db.init_db()
    future_version = db.SCHEMA_VERSION + 1
    with closing(sqlite3.connect(database_path)) as conn:
        conn.execute(f"PRAGMA user_version = {future_version}")
        conn.commit()
        before = list(conn.iterdump())

    with pytest.raises(RuntimeError) as exc_info:
        db.init_db()
    assert f"数据库版本 {future_version}" in str(exc_info.value)
    with closing(sqlite3.connect(database_path)) as conn:
        assert list(conn.iterdump()) == before
        assert conn.execute("PRAGMA user_version").fetchone()[0] == future_version


def test_migration_versions_are_strictly_increasing():
    versions = [version for version, _name, _apply in db.MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions[0] == 1
    assert versions[-1] == db.SCHEMA_VERSION


def test_current_database_keeps_legacy_self_repair(database_path):
    db.init_db()
    with db.connect(write=True) as conn:
        conn.execute("DROP TABLE mistake_clusters")
        conn.execute("DROP INDEX idx_users_email")
        conn.execute("DROP TABLE variants")
        conn.execute(
            """
            CREATE TABLE variants (
                id INTEGER PRIMARY KEY,
                mistake_id INTEGER NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
                description TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT 'unattempted',
                answer_code TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                result_updated_at TEXT
            )
            """
        )

    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM mistake_clusters").fetchone()[0] == 0
        assert {"answer", "expected_answer"} <= {
            row["name"] for row in conn.execute("PRAGMA table_info(variants)")
        }
        assert "idx_users_email" in {
            row["name"] for row in conn.execute("PRAGMA index_list(users)")
        }


def test_connect_without_create_does_not_make_database_or_parent(database_path):
    missing_path = database_path.parent / "missing-parent" / "missing.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_PATH", str(missing_path))
        with pytest.raises(sqlite3.OperationalError):
            with db.connect(create=False):
                pytest.fail("缺失数据库不能打开成功")
    assert not missing_path.parent.exists()
