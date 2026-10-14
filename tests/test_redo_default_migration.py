"""迁移 82：复习方式偏好默认改为只回忆。

1. 从 81 形状升级后，已有用户全部变成 recall_only，且列默认值变为 recall_only。
2. 迁移幂等：重复跑不报错，也不会把升级后用户主动选的 redo_first 重置掉。
3. 新注册账号默认 recall_only。
"""

import db
from db import connect


def _apply_81_only(monkeypatch, path):
    """把 MIGRATIONS 截到 81，模拟“还停留在 81”的旧库。"""
    monkeypatch.setenv("DATABASE_PATH", str(path))
    with monkeypatch.context() as old:
        old.setattr(
            db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 81]
        )
        db.init_db()


def _insert_user(conn, username, preference=None):
    if preference is None:
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES (?, 'x', 'Asia/Shanghai', '2026-09-01T00:00:00+00:00')",
            (username,),
        )
    else:
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at, redo_preference) "
            "VALUES (?, 'x', 'Asia/Shanghai', '2026-09-01T00:00:00+00:00', ?)",
            (username, preference),
        )
    return conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()[0]


def _column_default(conn):
    row = next(
        r for r in conn.execute("PRAGMA table_info(users)") if r[1] == "redo_preference"
    )
    return row[4]


def test_upgrade_from_81_sets_everyone_to_recall_only(tmp_path, monkeypatch):
    _apply_81_only(monkeypatch, tmp_path / "r82.db")
    with connect(write=True) as conn:
        _insert_user(conn, "u-default")  # 81 默认 redo_first
        _insert_user(conn, "u-explicit", "redo_first")
        _insert_user(conn, "u-recall", "recall_only")
    db.init_db()  # 跑 82
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        rows = {
            r[0]: r[1]
            for r in conn.execute("SELECT username, redo_preference FROM users")
        }
        assert rows == {
            "u-default": "recall_only",
            "u-explicit": "recall_only",
            "u-recall": "recall_only",
        }
        assert "recall_only" in (_column_default(conn) or "").lower()


def test_migration_82_is_idempotent_and_keeps_later_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "r82-idem.db"))
    db.init_db()
    db.init_db()  # 重复启动不报错
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
    # 用户在升级后主动改回 redo_first，再跑一次迁移不能被重置
    with connect(write=True) as conn:
        _insert_user(conn, "u-switcher")
        conn.execute(
            "UPDATE users SET redo_preference = 'redo_first' WHERE username = 'u-switcher'"
        )
    db.init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT redo_preference FROM users WHERE username = 'u-switcher'"
        ).fetchone()
        assert row[0] == "redo_first"
        assert "recall_only" in (_column_default(conn) or "").lower()


def test_migration_82_cleans_up_interrupted_tmp_column(tmp_path, monkeypatch):
    """模拟 82 中途崩溃：库停在 81 且留下了 redo_pref_tmp，再跑 init_db 应清理后正常升级。"""
    _apply_81_only(monkeypatch, tmp_path / "r82-tmp.db")
    with connect(write=True) as conn:
        conn.execute(
            "ALTER TABLE users ADD COLUMN redo_pref_tmp TEXT NOT NULL DEFAULT 'x'"
        )
    db.init_db()  # 跑 82：幂等路径应先清掉临时列再判断
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        columns = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "redo_pref_tmp" not in columns
        assert "redo_preference" in columns


def test_new_user_defaults_to_recall_only(tmp_path, monkeypatch):
    """新插入的用户（不显式指定偏好）拿到列默认值 recall_only。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "r82-new.db"))
    db.init_db()
    with connect(write=True) as conn:
        _insert_user(conn, "u-fresh")
    with connect() as conn:
        row = conn.execute(
            "SELECT redo_preference FROM users WHERE username = 'u-fresh'"
        ).fetchone()
        assert row[0] == "recall_only"
