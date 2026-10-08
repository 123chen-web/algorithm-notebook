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
            "users", "mistakes", "ai_usage", "ai_calls", "mistake_clusters",
            "comment_votes", "post_summaries",
            "redeem_codes", "app_settings", "manual_payment_claims", "goals", "review_ops",
            "mistake_scratch", "user_push", "email_changes", "problem_recommendations", "import_previews",
        } <= tables
        assert db.schema_version(conn) == 21
        accepted = next(
            row for row in conn.execute("PRAGMA table_info(posts)")
            if row["name"] == "accepted_comment_id"
        )
        assert accepted["type"] == "INTEGER"
        assert accepted["notnull"] == 0
        assert accepted["dflt_value"] is None
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(ai_calls)")
        }
        assert {"idx_ai_calls_created_at", "idx_ai_calls_user_created_at"} <= indexes
        assert "idx_comment_votes_user" in {
            row["name"] for row in conn.execute("PRAGMA index_list(comment_votes)")
        }

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


def initialize_before_accounts_migration(monkeypatch):
    migrations = [entry for entry in db.MIGRATIONS if entry[0] < 4]
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", migrations)
        patch.setattr(db, "SCHEMA_VERSION", migrations[-1][0])
        db.init_db()
    return migrations[-1][0]


def test_sec_existing_migrated_admin_survives_later_startups(database_path, monkeypatch):
    db.init_db()
    with db.connect(write=True) as conn:
        operator_id = conn.execute(
            "INSERT INTO users(username,password_hash,timezone,created_at,is_admin) "
            "VALUES ('legacyoperator','unchanged-hash','Asia/Shanghai',?,1)", (CREATED_AT,)
        ).lastrowid
        ordinary_id = conn.execute(
            "INSERT INTO users(username,password_hash,timezone,created_at) "
            "VALUES ('ordinary','unchanged-hash','Asia/Shanghai',?)", (CREATED_AT,)
        ).lastrowid
    monkeypatch.setenv("ADMIN_USERNAME", "legacyoperator")
    db.init_db()
    monkeypatch.setenv("ADMIN_USERNAME", "ordinary")
    db.init_db()
    with db.connect() as conn:
        roles = {row["id"]: row["is_admin"] for row in conn.execute("SELECT id,is_admin FROM users")}
        assert roles == {operator_id: 1, ordinary_id: 0}


def test_accounts_migration_preserves_old_user_and_adds_nullable_defaults(database_path, monkeypatch):
    initialize_before_accounts_migration(monkeypatch)
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, timezone, created_at, "
            "email, last_reminder_sent, avatar_version, is_banned) "
            "VALUES (7, 'legacy', 'unchanged-hash', 'Asia/Taipei', ?, "
            "'legacy@example.com', '2026-10-01', 5, 1)",
            (CREATED_AT,),
        )
        before = dict(conn.execute("SELECT * FROM users WHERE id = 7").fetchone())
        assert not {"deleted_at", "is_admin", "terms_accepted_at", "terms_version"} & before.keys()

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id = 7").fetchone())
        assert {key: user[key] for key in before} == before
        assert user["deleted_at"] is None
        assert user["is_admin"] == 0
        assert user["terms_accepted_at"] is None
        assert user["terms_version"] is None
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(users)")}
        assert columns["is_admin"]["type"] == "INTEGER"
        assert columns["is_admin"]["notnull"] == 1
        assert columns["is_admin"]["dflt_value"] == "0"
        for name in ("deleted_at", "terms_accepted_at", "terms_version"):
            assert columns[name]["type"] == "TEXT"
            assert columns[name]["notnull"] == 0
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_accounts_migration_defaults_on_fresh_database(database_path):
    db.init_db()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at) "
            "VALUES ('fresh', 'test-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        user = conn.execute("SELECT * FROM users WHERE username = 'fresh'").fetchone()
        assert user["deleted_at"] is None
        assert user["is_admin"] == 0
        assert user["terms_accepted_at"] is None
        assert user["terms_version"] is None


def test_accounts_migration_rolls_back_all_new_columns_on_failure(database_path, monkeypatch):
    before_version = initialize_before_accounts_migration(monkeypatch)
    accounts_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 4)

    def broken_accounts_migration(conn):
        accounts_apply(conn)
        raise ValueError("账号迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_accounts_migration if version == 4 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="账号迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == before_version
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        assert not {"deleted_at", "is_admin", "terms_accepted_at", "terms_version"} & columns


@pytest.mark.parametrize("from_version_2", [False, True], ids=["fresh", "version-2"])
def test_review_log_columns_preserve_old_reviews(database_path, monkeypatch, from_version_2):
    # 单独验证 2 → 3，避免后续迁移掩盖这一版的行为。
    migrations = [entry for entry in db.MIGRATIONS if entry[0] <= 3]
    monkeypatch.setattr(db, "MIGRATIONS", migrations)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 3)
    old_review = (9, 7, 4, CREATED_AT, "2026-09-27")
    before_state = None

    if from_version_2:
        with db.connect(write=True) as conn:
            for version, _name, apply in migrations:
                if version > 2:
                    break
                apply(conn)
                conn.execute(f"PRAGMA user_version = {version}")
            conn.execute(
                "INSERT INTO users(id, username, password_hash, timezone, created_at) "
                "VALUES (5, 'alice', 'original-hash', 'Asia/Shanghai', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO problems(id, user_id, title, language, code, thinking, created_at) "
                "VALUES (6, 5, '二分查找', 'Python', 'pass', '边界问题', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO mistakes(id, problem_id, description, repetitions, "
                "interval_days, ease_factor, due_date) "
                "VALUES (7, 6, '结束条件', 2, 6, 2.5, '2026-09-27')"
            )
            conn.execute(
                "INSERT INTO reviews(id, mistake_id, quality, reviewed_at, next_due_date) "
                "VALUES (?, ?, ?, ?, ?)",
                old_review,
            )
            before_state = dict(conn.execute("SELECT * FROM mistakes WHERE id = 7").fetchone())
            assert db.schema_version(conn) == 2

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(reviews)")
        }
        new_columns = {
            "elapsed_days": "INTEGER",
            "scheduled_days": "INTEGER",
            "ease_before": "REAL",
            "repetitions_before": "INTEGER",
        }
        for name, column_type in new_columns.items():
            assert columns[name]["type"] == column_type
            assert columns[name]["notnull"] == 0
            assert columns[name]["dflt_value"] is None

        if from_version_2:
            row = conn.execute("SELECT * FROM reviews WHERE id = 9").fetchone()
            assert tuple(row[name] for name in (
                "id", "mistake_id", "quality", "reviewed_at", "next_due_date"
            )) == old_review
            assert all(row[name] is None for name in new_columns)
            assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1
            assert dict(conn.execute("SELECT * FROM mistakes WHERE id = 7").fetchone()) == before_state
        else:
            assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("from_version", range(5), ids=["fresh", "v1", "v2", "v3", "v4"])
def test_forum_migration_to_v5_preserves_old_data_and_is_repeatable(
    database_path, monkeypatch, from_version,
):
    # 隔离本次论坛迁移；并行追加的后续迁移不应掩盖版本 5 的验证。
    monkeypatch.setattr(db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 5])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 5)
    before = None
    if from_version:
        old_migrations = [entry for entry in db.MIGRATIONS if entry[0] <= from_version]
        with monkeypatch.context() as patch:
            patch.setattr(db, "MIGRATIONS", old_migrations)
            patch.setattr(db, "SCHEMA_VERSION", from_version)
            db.init_db()
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, timezone, created_at) "
                "VALUES (7, 'legacy-forum', 'unchanged-hash', 'Asia/Taipei', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO posts (id, user_id, title, body, created_at, updated_at) "
                "VALUES (11, 7, '历史问题', '历史正文', ?, ?)",
                (CREATED_AT, CREATED_AT),
            )
            conn.execute(
                "INSERT INTO post_comments (id, post_id, user_id, body, created_at, deleted_at) "
                "VALUES (13, 11, 7, '历史评论', ?, ?)",
                (CREATED_AT, CREATED_AT),
            )
            before = {
                table: dict(conn.execute(f"SELECT * FROM {table}").fetchone())
                for table in ("users", "posts", "post_comments")
            }
            assert "accepted_comment_id" not in before["posts"]
            assert db.schema_version(conn) == from_version

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(posts)")}
        assert columns["accepted_comment_id"]["type"] == "INTEGER"
        assert columns["accepted_comment_id"]["notnull"] == 0
        assert columns["accepted_comment_id"]["dflt_value"] is None
        assert not any(
            row["from"] == "accepted_comment_id"
            for row in conn.execute("PRAGMA foreign_key_list(posts)")
        )
        if before:
            for table, saved in before.items():
                current = dict(conn.execute(f"SELECT * FROM {table}").fetchone())
                assert {key: current[key] for key in saved} == saved
            assert conn.execute("SELECT accepted_comment_id FROM posts WHERE id = 11").fetchone()[0] is None
        else:
            assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM post_summaries").fetchone()[0] == 0
        votes = {row["name"]: row for row in conn.execute("PRAGMA table_info(comment_votes)")}
        assert set(votes) == {"comment_id", "user_id", "created_at"}
        assert [(name, votes[name]["pk"]) for name in ("comment_id", "user_id")] == [
            ("comment_id", 1), ("user_id", 2),
        ]
        assert all(votes[name]["notnull"] == 1 for name in votes)
        assert {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in conn.execute("PRAGMA foreign_key_list(comment_votes)")
        } == {
            ("comment_id", "post_comments", "id", "CASCADE"),
            ("user_id", "users", "id", "CASCADE"),
        }
        assert {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in conn.execute("PRAGMA foreign_key_list(post_summaries)")
        } == {("post_id", "posts", "id", "CASCADE")}
        summaries = {row["name"]: row for row in conn.execute("PRAGMA table_info(post_summaries)")}
        assert set(summaries) == {"post_id", "signature", "content", "comment_count", "created_at"}
        assert summaries["post_id"]["type"] == "INTEGER"
        assert summaries["post_id"]["pk"] == 1
        for name, kind in (("signature", "TEXT"), ("content", "TEXT"),
                           ("comment_count", "INTEGER"), ("created_at", "TEXT")):
            assert summaries[name]["type"] == kind
            assert summaries[name]["notnull"] == 1
            assert summaries[name]["dflt_value"] is None
        assert [row["name"] for row in conn.execute("PRAGMA index_info(idx_comment_votes_user)")] == ["user_id"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_forum_migration_rolls_back_all_new_schema_on_failure(database_path, monkeypatch):
    with monkeypatch.context() as patch:
        old_migrations = [entry for entry in db.MIGRATIONS if entry[0] < 5]
        patch.setattr(db, "MIGRATIONS", old_migrations)
        patch.setattr(db, "SCHEMA_VERSION", 4)
        db.init_db()
    forum_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 5)

    def broken_forum_migration(conn):
        forum_apply(conn)
        raise ValueError("论坛迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_forum_migration if version == 5 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="论坛迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 4
        assert "accepted_comment_id" not in {
            row["name"] for row in conn.execute("PRAGMA table_info(posts)")
        }
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('comment_votes', 'idx_comment_votes_user', 'post_summaries')"
        ).fetchall() == []


def test_forum_migration_vote_uniqueness_and_foreign_key_cascades(database_path):
    db.init_db()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (7, 'op', 'hash', 'Asia/Taipei', ?), (8, 'member', 'hash', 'Asia/Taipei', ?)",
            (CREATED_AT, CREATED_AT),
        )
        conn.execute(
            "INSERT INTO posts(id, user_id, title, body, created_at) VALUES (11, 7, '问题', '正文', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO post_comments(id, post_id, user_id, body, created_at) "
            "VALUES (13, 11, 7, '评论', ?), (14, 11, 7, '另一评论', ?)",
            (CREATED_AT, CREATED_AT),
        )
        conn.execute("INSERT INTO comment_votes VALUES (13, 7, ?), (13, 8, ?), (14, 7, ?)",
                     (CREATED_AT, CREATED_AT, CREATED_AT))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO comment_votes VALUES (13, 8, ?)", (CREATED_AT,))
        conn.execute("DELETE FROM users WHERE id = 8")
        assert [tuple(row) for row in conn.execute("SELECT comment_id, user_id FROM comment_votes ORDER BY comment_id")] == [
            (13, 7), (14, 7),
        ]
        conn.execute("DELETE FROM post_comments WHERE id = 13")
        assert [tuple(row) for row in conn.execute("SELECT comment_id, user_id FROM comment_votes")] == [(14, 7)]
        conn.execute("INSERT INTO post_summaries VALUES (11, 'signature', '{}', 1, ?)", (CREATED_AT,))
        conn.execute("DELETE FROM posts WHERE id = 11")
        assert conn.execute("SELECT * FROM post_summaries").fetchall() == []
        assert conn.execute("SELECT * FROM comment_votes").fetchall() == []


def initialize_before_redeem_migration(monkeypatch):
    migrations = [entry for entry in db.MIGRATIONS if entry[0] < 6]
    assert migrations[-1][0] == 5
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", migrations)
        patch.setattr(db, "SCHEMA_VERSION", 5)
        db.init_db()


@pytest.mark.parametrize("from_version_5", [False, True], ids=["fresh", "v5"])
def test_redeem_migration_schema_preserves_v5_data_and_repeated_startup(
    database_path, monkeypatch, from_version_5,
):
    # 隔离版本 6：之后追加的迁移（如 8 给 posts 加 zone 列）不应改变这里的“旧数据逐行相等”。
    monkeypatch.setattr(db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 6])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 6)
    before_data = before_schema = None
    if from_version_5:
        initialize_before_redeem_migration(monkeypatch)
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO plans(id, name, period_days, ai_daily_limit, price_cents, "
                "created_at) VALUES (3, '旧套餐', 30, 17, 990, ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO users(id, username, password_hash, timezone, created_at, "
                "email, is_admin, plan_id, plan_expires_at) "
                "VALUES (7, 'legacy-redeem', 'unchanged-hash', 'Asia/Taipei', ?, "
                "'legacy@example.com', 1, 3, '2026-11-01T10:00:00+00:00')",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO orders(id, user_id, plan_id, amount_cents, channel, "
                "status, provider_trade_no, created_at, paid_at) "
                "VALUES ('old-order', 7, 3, 990, 'alipay', 'paid', 'old-trade', ?, ?)",
                (CREATED_AT, CREATED_AT),
            )
            conn.execute(
                "INSERT INTO posts(id, user_id, title, body, created_at) "
                "VALUES (11, 7, '历史问题', '历史正文', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO post_comments(id, post_id, user_id, body, created_at) "
                "VALUES (13, 11, 7, '历史评论', ?)",
                (CREATED_AT,),
            )
            conn.execute("UPDATE posts SET accepted_comment_id = 13 WHERE id = 11")
            conn.execute("INSERT INTO comment_votes VALUES (13, 7, ?)", (CREATED_AT,))
            conn.execute(
                "INSERT INTO post_summaries VALUES (11, 'signature', '{}', 1, ?)",
                (CREATED_AT,),
            )
            tables = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            before_data = {
                table: [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
                for table in tables
            }
            before_schema = [
                tuple(row) for row in conn.execute(
                    "SELECT type, name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                )
            ]
            assert db.schema_version(conn) == 5

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION == 6
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(redeem_codes)")}
        assert set(columns) == {
            "id", "code_hash", "code_hint", "plan_id", "period_days", "note",
            "created_by", "created_at", "expires_at", "redeemed_by", "redeemed_at",
            "revoked_at",
        }
        for name in ("code_hash", "code_hint", "note", "created_at"):
            assert columns[name]["type"] == "TEXT"
            assert columns[name]["notnull"] == 1
        for name in ("plan_id", "period_days"):
            assert columns[name]["type"] == "INTEGER"
            assert columns[name]["notnull"] == 1
        assert columns["note"]["dflt_value"] == "''"
        for name in ("created_by", "expires_at", "redeemed_by", "redeemed_at", "revoked_at"):
            assert columns[name]["notnull"] == 0
            assert columns[name]["dflt_value"] is None
        assert columns["id"]["pk"] == 1
        assert "AUTOINCREMENT" in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'redeem_codes'"
        ).fetchone()[0]
        assert {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in conn.execute("PRAGMA foreign_key_list(redeem_codes)")
        } == {
            ("plan_id", "plans", "id", "NO ACTION"),
            ("created_by", "users", "id", "NO ACTION"),
            ("redeemed_by", "users", "id", "NO ACTION"),
        }
        indexes = list(conn.execute("PRAGMA index_list(redeem_codes)"))
        assert "idx_redeem_codes_redeemed_by" in {row["name"] for row in indexes}
        assert [row["name"] for row in conn.execute(
            "PRAGMA index_info(idx_redeem_codes_redeemed_by)"
        )] == ["redeemed_by"]
        unique_columns = {
            tuple(part["name"] for part in conn.execute(f'PRAGMA index_info("{row["name"]}")'))
            for row in indexes if row["unique"]
        }
        assert ("code_hash",) in unique_columns
        settings_columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(app_settings)")
        }
        assert set(settings_columns) == {"key", "value"}
        assert settings_columns["key"]["type"] == "TEXT"
        assert settings_columns["key"]["pk"] == 1
        assert settings_columns["value"]["type"] == "TEXT"
        assert settings_columns["value"]["notnull"] == 1
        assert dict(conn.execute("SELECT key, value FROM app_settings")) == {
            "manual_payment_enabled": "0", "manual_payment_contact": "",
        }
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0
        if before_data is not None:
            for table, saved in before_data.items():
                assert [tuple(row) for row in conn.execute(f'SELECT * FROM "{table}"')] == saved
            current_schema = {
                tuple(row) for row in conn.execute(
                    "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                )
            }
            assert set(before_schema) <= current_schema
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_redeem_migration_rolls_back_new_schema_and_settings_on_failure(
    database_path, monkeypatch,
):
    initialize_before_redeem_migration(monkeypatch)
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (7, 'legacy-redeem', 'unchanged-hash', 'Asia/Taipei', ?)",
            (CREATED_AT,),
        )
        before = dict(conn.execute("SELECT * FROM users WHERE id = 7").fetchone())
    redeem_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 6)

    def broken_redeem_migration(conn):
        redeem_apply(conn)
        raise ValueError("兑换码迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_redeem_migration if version == 6 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="兑换码迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 5
        assert dict(conn.execute("SELECT * FROM users WHERE id = 7").fetchone()) == before
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('redeem_codes', 'idx_redeem_codes_redeemed_by', 'app_settings')"
        ).fetchall() == []


@pytest.mark.parametrize("from_version", [0, 1, 5, 6], ids=["fresh", "v1", "v5", "v6"])
def test_forum_zone_migration_to_v8_preserves_old_data_and_is_repeatable(
    database_path, monkeypatch, from_version,
):
    before = None
    if from_version:
        with monkeypatch.context() as patch:
            patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= from_version])
            patch.setattr(db, "SCHEMA_VERSION", from_version)
            db.init_db()
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, timezone, created_at) "
                "VALUES (7, 'legacy-zone', 'unchanged-hash', 'Asia/Taipei', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO posts (id, user_id, title, body, created_at) "
                "VALUES (11, 7, '历史问题', '历史正文', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO post_comments (id, post_id, user_id, body, created_at) "
                "VALUES (13, 11, 7, '历史评论', ?)",
                (CREATED_AT,),
            )
            before = {
                table: dict(conn.execute(f"SELECT * FROM {table}").fetchone())
                for table in ("users", "posts", "post_comments")
            }
            assert "zone" not in before["posts"]

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION >= 10
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(posts)")}
        assert columns["zone"]["type"] == "TEXT"
        assert columns["zone"]["notnull"] == 0
        assert columns["zone"]["dflt_value"] is None
        if before:
            for table, saved in before.items():
                current = dict(conn.execute(f"SELECT * FROM {table}").fetchone())
                assert {key: current[key] for key in saved} == saved
            assert conn.execute("SELECT zone FROM posts WHERE id = 11").fetchone()[0] is None
        indexes = {
            row["name"]: row for row in conn.execute("PRAGMA index_list(post_comments)")
        }
        assert [
            row["name"] for row in conn.execute("PRAGMA index_info(idx_post_comments_post_visible)")
        ] == ["post_id", "deleted_at", "created_at"]
        assert "idx_post_comments_post_visible" in indexes
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_forum_zone_migration_rolls_back_on_failure(database_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 6])
        patch.setattr(db, "SCHEMA_VERSION", 6)
        db.init_db()
    zone_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 8)

    def broken_zone_migration(conn):
        zone_apply(conn)
        raise ValueError("分区迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_zone_migration if version == 8 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="分区迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 6
        assert "zone" not in {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idx_post_comments_post_visible'"
        ).fetchone() is None


REVIEW_FEEL_COLUMNS = {
    "mistakes": {"suspended_at": "TEXT"},
    "reviews": {
        "due_before": "TEXT", "last_reviewed_before": "TEXT", "version_after": "INTEGER",
    },
    "users": {"daily_review_cap": "INTEGER"},
}


@pytest.mark.parametrize(
    "from_version", [0, 1, 2, 3, 4, 5, 6, 8, 9, 10],
    ids=["fresh", "v1", "v2", "v3", "v4", "v5", "v6", "v8", "v9", "v10"],
)
def test_review_feel_migration_to_v10_preserves_old_data_and_is_repeatable(
    database_path, monkeypatch, from_version,
):
    before = None
    if from_version:
        with monkeypatch.context() as patch:
            patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= from_version])
            patch.setattr(db, "SCHEMA_VERSION", from_version)
            db.init_db()
        with db.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, timezone, created_at) "
                "VALUES (7, 'legacy-review', 'unchanged-hash', 'Asia/Taipei', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO problems(id, user_id, title, language, code, thinking, created_at) "
                "VALUES (11, 7, '历史题目', 'Python', 'pass', '历史思路', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO mistakes(id, problem_id, description, repetitions, interval_days, "
                "ease_factor, due_date, last_reviewed_at, version) "
                "VALUES (13, 11, '历史易错点', 3, 15, 2.36, '2026-10-06', ?, 8)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO reviews(id, mistake_id, quality, reviewed_at, next_due_date) "
                "VALUES (17, 13, 3, ?, '2026-10-06')", (CREATED_AT,),
            )
            if from_version >= 3:
                conn.execute(
                    "UPDATE reviews SET elapsed_days = 6, scheduled_days = 6, "
                    "ease_before = 2.5, repetitions_before = 2 WHERE id = 17"
                )
            before = {
                table: dict(conn.execute(f"SELECT * FROM {table}").fetchone())
                for table in ("users", "problems", "mistakes", "reviews")
            }
            assert db.schema_version(conn) == from_version
            if from_version < 10:
                for table, new_columns in REVIEW_FEEL_COLUMNS.items():
                    assert not set(new_columns) & before[table].keys()

    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION >= 10
        for table, new_columns in REVIEW_FEEL_COLUMNS.items():
            columns = {row["name"]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, kind in new_columns.items():
                assert columns[name]["type"] == kind
                assert columns[name]["notnull"] == 0
                assert columns[name]["dflt_value"] is None
        if before:
            for table, saved in before.items():
                current = dict(conn.execute(f"SELECT * FROM {table}").fetchone())
                assert {key: current[key] for key in saved} == saved
            for table, new_columns in REVIEW_FEEL_COLUMNS.items():
                current = conn.execute(f"SELECT * FROM {table}").fetchone()
                assert all(current[name] is None for name in new_columns)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_review_feel_migration_rolls_back_all_columns_on_failure(database_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 9])
        patch.setattr(db, "SCHEMA_VERSION", 9)
        db.init_db()
    review_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 10)

    def broken_review_migration(conn):
        review_apply(conn)
        raise ValueError("复习手感迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_review_migration if version == 10 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="复习手感迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 9
        for table, new_columns in REVIEW_FEEL_COLUMNS.items():
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            assert not set(new_columns) & columns


def test_scratch_migration_to_v14_creates_table_and_preserves_old_data(
    database_path, monkeypatch,
):
    # 先在版本 13 的旧库上造好用户 / 题目 / 易错点，再升到最新，验证 14 号迁移。
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 13])
        patch.setattr(db, "SCHEMA_VERSION", 13)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (7, 'legacy-scratch', 'unchanged-hash', 'Asia/Taipei', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO problems(id, user_id, title, language, code, thinking, created_at) "
            "VALUES (11, 7, '历史题目', 'Python', 'pass', '历史思路', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO mistakes(id, problem_id, description, repetitions, "
            "interval_days, ease_factor, due_date, version) "
            "VALUES (13, 11, '历史易错点', 1, 2, 2.5, '2026-10-06', 3)",
        )
        assert db.schema_version(conn) == 13

    db.init_db()
    db.init_db()
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        columns = {row["name"]: row for row in conn.execute(
            "PRAGMA table_info(mistake_scratch)"
        )}
        assert set(columns) == {
            "mistake_id", "user_id", "version", "code", "fixed",
            "table_json", "updated_at",
        }
        assert columns["mistake_id"]["pk"] == 1
        assert columns["mistake_id"]["type"] == "INTEGER"
        assert columns["mistake_id"]["notnull"] == 0
        assert columns["user_id"]["type"] == "INTEGER"
        assert columns["user_id"]["notnull"] == 1
        assert columns["version"]["type"] == "INTEGER"
        assert columns["version"]["notnull"] == 1
        for name in ("code", "fixed"):
            assert columns[name]["type"] == "TEXT"
            assert columns[name]["notnull"] == 1
            assert columns[name]["dflt_value"] == "''"
        assert columns["table_json"]["type"] == "TEXT"
        assert columns["table_json"]["notnull"] == 0
        assert columns["updated_at"]["type"] == "TEXT"
        assert columns["updated_at"]["notnull"] == 1
        assert {
            (row["from"], row["table"], row["to"], row["on_delete"])
            for row in conn.execute("PRAGMA foreign_key_list(mistake_scratch)")
        } == {
            ("mistake_id", "mistakes", "id", "CASCADE"),
            ("user_id", "users", "id", "CASCADE"),
        }
        assert "idx_mistake_scratch_user" in {
            row["name"] for row in conn.execute("PRAGMA index_list(mistake_scratch)")
        }

        # 迁移后可以正常写入草稿。
        conn.execute(
            "INSERT INTO mistake_scratch"
            "(mistake_id, user_id, version, code, fixed, table_json, updated_at) "
            "VALUES (13, 7, 1, 'a', 'b', NULL, ?)",
            (CREATED_AT,),
        )
        # 删错题级联删草稿；旧数据原样保留。
        assert tuple(conn.execute(
            "SELECT description, version FROM mistakes WHERE id = 13"
        ).fetchone()) == ("历史易错点", 3)
        conn.execute("DELETE FROM mistakes WHERE id = 13")
        assert conn.execute(
            "SELECT COUNT(*) FROM mistake_scratch"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_scratch_migration_rolls_back_on_failure(database_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 13])
        patch.setattr(db, "SCHEMA_VERSION", 13)
        db.init_db()
    scratch_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 14)

    def broken_scratch_migration(conn):
        scratch_apply(conn)
        raise ValueError("草稿迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_scratch_migration if version == 14 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="草稿迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 13
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN "
            "('mistake_scratch', 'idx_mistake_scratch_user')"
        ).fetchall() == []


def test_push_migration_to_v15_creates_table_and_preserves_old_data(
    database_path, monkeypatch,
):
    # 先在版本 14 的旧库上造好用户与草稿，再升到最新，验证 15 号迁移。
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 14])
        patch.setattr(db, "SCHEMA_VERSION", 14)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (8, 'legacy-push', 'unchanged-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (9, 'legacy-push-2', 'unchanged-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        assert db.schema_version(conn) == 14

    db.init_db()
    db.init_db()
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION == 21
        columns = {row["name"]: row for row in conn.execute(
            "PRAGMA table_info(user_push)"
        )}
        assert set(columns) == {
            "user_id", "channel", "secret", "enabled",
            "fail_count", "last_ok_at", "updated_at",
        }
        assert columns["user_id"]["pk"] == 1
        assert columns["user_id"]["type"] == "INTEGER"
        assert columns["channel"]["type"] == "TEXT"
        assert columns["channel"]["notnull"] == 1
        assert columns["secret"]["type"] == "TEXT"
        assert columns["secret"]["notnull"] == 1
        assert columns["enabled"]["type"] == "INTEGER"
        assert columns["enabled"]["notnull"] == 1
        assert columns["enabled"]["dflt_value"] == "1"
        assert columns["fail_count"]["type"] == "INTEGER"
        assert columns["fail_count"]["notnull"] == 1
        assert columns["fail_count"]["dflt_value"] == "0"
        assert columns["last_ok_at"]["notnull"] == 0
        assert columns["updated_at"]["notnull"] == 1
        assert {
            (row["table"], row["to"], row["on_delete"])
            for row in conn.execute("PRAGMA foreign_key_list(user_push)")
        } == {("users", "id", "CASCADE")}

        # 迁移后可以正常写入推送配置。
        conn.execute(
            "INSERT INTO user_push"
            "(user_id, channel, secret, enabled, fail_count, last_ok_at, updated_at) "
            "VALUES (8, 'serverchan', ?, 1, 0, NULL, ?)",
            ("SCT" + "a1B2" * 10, CREATED_AT),
        )
        # CHECK 约束拒绝非法渠道 / 开关值 / 负数失败计数。
        for values in (
            "(9, 'wechat', 'abcdefgh', 1, 0, NULL, ?)",
            "(9, 'serverchan', 'abcdefgh', 2, 0, NULL, ?)",
            "(9, 'serverchan', 'abcdefgh', 1, -1, NULL, ?)",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO user_push"
                    "(user_id, channel, secret, enabled, fail_count, last_ok_at, updated_at) "
                    f"VALUES {values}",
                    (CREATED_AT,),
                )
        # 旧数据原样保留（14 号迁移的表与用户都在）。
        assert conn.execute(
            "SELECT password_hash FROM users WHERE id = 8"
        ).fetchone()[0] == "unchanged-hash"
        assert "mistake_scratch" in {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        # 删用户级联删推送配置。
        conn.execute("DELETE FROM users WHERE id = 8")
        assert conn.execute("SELECT COUNT(*) FROM user_push").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_push_migration_rolls_back_on_failure(database_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 14])
        patch.setattr(db, "SCHEMA_VERSION", 14)
        db.init_db()
    push_apply = next(apply for version, _name, apply in db.MIGRATIONS if version == 15)

    def broken_push_migration(conn):
        push_apply(conn)
        raise ValueError("推送迁移最后一步失败")

    monkeypatch.setattr(
        db, "MIGRATIONS",
        [
            (version, name, broken_push_migration if version == 15 else apply)
            for version, name, apply in db.MIGRATIONS
        ],
    )
    with pytest.raises(ValueError, match="推送迁移最后一步失败"):
        db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 14
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'user_push'"
        ).fetchall() == []


@pytest.mark.parametrize("from_version", [16, 17])
def test_recommend_and_pending_reason_upgrade_preserves_data_and_constraints(
    database_path, monkeypatch, from_version,
):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= from_version])
        patch.setattr(db, "SCHEMA_VERSION", from_version)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (8, 'legacy-quick', 'unchanged-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO problems(id, user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (9, 8, '旧题', '算法', 'Python', 'pass', '旧思路', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO mistakes(id, problem_id, description, repetitions, interval_days, "
            "ease_factor, due_date, last_reviewed_at, version, suspended_at) "
            "VALUES (10, 9, '旧原因', 3, 15, 2.7, '2026-10-12', ?, 7, ?)",
            (CREATED_AT, CREATED_AT),
        )
        before = dict(conn.execute("SELECT * FROM mistakes WHERE id = 10").fetchone())
        assert "pending_reason" not in before
        if from_version == 17:
            conn.execute(
                "INSERT INTO problem_recommendations "
                "(user_id, contest_id, idx, recommended_at, state) VALUES (8, 4, 'A', ?, 'done')",
                (CREATED_AT,),
            )
    db.init_db()
    db.init_db()
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION == 21
        after = dict(conn.execute("SELECT * FROM mistakes WHERE id = 10").fetchone())
        assert after.pop("pending_reason") == 0
        assert after == before
        pending = next(row for row in conn.execute("PRAGMA table_info(mistakes)")
                       if row["name"] == "pending_reason")
        assert pending["type"] == "INTEGER"
        assert pending["notnull"] == 1
        assert pending["dflt_value"] == "0"
        for invalid in (-1, 2, None):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE mistakes SET pending_reason = ? WHERE id = 10", (invalid,))
        if from_version == 17:
            assert conn.execute(
                "SELECT state FROM problem_recommendations WHERE user_id = 8 AND contest_id = 4 AND idx = 'A'"
            ).fetchone()[0] == "done"
        else:
            assert conn.execute("SELECT COUNT(*) FROM problem_recommendations").fetchone()[0] == 0
        conn.execute(
            "INSERT INTO problem_recommendations (user_id, contest_id, idx, recommended_at) "
            "VALUES (8, 5, 'B', ?)", (CREATED_AT,),
        )
        assert conn.execute(
            "SELECT state FROM problem_recommendations WHERE user_id = 8 AND contest_id = 5 AND idx = 'B'"
        ).fetchone()[0] == "new"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO problem_recommendations (user_id, contest_id, idx, recommended_at) "
                "VALUES (8, 5, 'B', ?)", (CREATED_AT,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE problem_recommendations SET state = 'invalid' WHERE user_id = 8")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO problem_recommendations (user_id, contest_id, idx, recommended_at) "
                "VALUES (999, 5, 'B', ?)", (CREATED_AT,),
            )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_import_preview_upgrade_from_v18_preserves_notes_and_cascades(database_path, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 18])
        patch.setattr(db, "SCHEMA_VERSION", 18)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO users(id,username,password_hash,timezone,created_at) "
                     "VALUES (8,'import-upgrade','hash','Asia/Shanghai',?)", (CREATED_AT,))
        conn.execute("INSERT INTO problems(user_id,title,language,code,thinking,created_at) "
                     "VALUES (8,'保留题','Python','pass','旧思路',?)", (CREATED_AT,))
    db.init_db()
    db.init_db()
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION == 21
        assert conn.execute("SELECT title FROM problems").fetchone()[0] == "保留题"
        conn.execute("INSERT INTO import_previews(token,user_id,zone,records,expires_at) "
                     "VALUES ('token',8,'算法','[]',123)")
        conn.execute("DELETE FROM users WHERE id = 8")
        assert conn.execute("SELECT COUNT(*) FROM import_previews").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
