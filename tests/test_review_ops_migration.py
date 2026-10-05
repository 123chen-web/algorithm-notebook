"""迁移 12：离线评分补交的幂等记录表 review_ops。"""

import sqlite3

import pytest

import db


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "review-ops.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def test_migration_number_is_the_next_one_and_unique():
    versions = [version for version, _name, _apply in db.MIGRATIONS]
    assert versions == sorted(set(versions))
    assert versions[-1] == 12 and versions[-2] == 11
    assert db.SCHEMA_VERSION == 12


def test_fresh_database_has_the_table_with_the_expected_shape(database_path):
    db.init_db()
    with db.connect() as conn:
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(review_ops)")}
        assert list(columns) == ["user_id", "client_op_id", "mistake_id", "response", "created_at"]
        assert all(column["notnull"] for column in columns.values())
        primary = sorted(
            (column["pk"], name) for name, column in columns.items() if column["pk"]
        )
        assert primary == [(1, "user_id"), (2, "client_op_id")]
        assert "idx_review_ops_created_at" in {
            row["name"] for row in conn.execute("PRAGMA index_list(review_ops)")
        }


def test_upgrade_from_version_11_keeps_data_and_is_repeatable(database_path, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(db, "MIGRATIONS", db.MIGRATIONS[:-1])
        db.init_db()
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == 11
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'review_ops'"
        ).fetchone() is None
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES ('old-user', 'x', 'Asia/Shanghai', '2026-09-01T00:00:00+00:00')"
        )
        user_id = conn.execute("SELECT id FROM users WHERE username = 'old-user'").fetchone()[0]

    db.init_db()
    db.init_db()  # 重复启动不报错
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == 12
        assert conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()[0] == "old-user"
        assert conn.execute("SELECT COUNT(*) FROM review_ops").fetchone()[0] == 0
        conn.execute(
            "INSERT INTO review_ops VALUES (?, 'op-00000001', 7, '{}', '2026-09-19T00:00:00+00:00')",
            (user_id,),
        )
        # 同一用户同一 id 不能有两条；不同 id 可以。
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO review_ops VALUES (?, 'op-00000001', 8, '{}', '2026-09-19T00:00:00+00:00')",
                (user_id,),
            )
        conn.execute(
            "INSERT INTO review_ops VALUES (?, 'op-00000002', 8, '{}', '2026-09-19T00:00:00+00:00')",
            (user_id,),
        )
        # 随用户行一起级联删除。
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        assert conn.execute("SELECT COUNT(*) FROM review_ops").fetchone()[0] == 0
