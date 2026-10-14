"""迁移 85：复习排期换成 FSRS。只加列，不改动任何已有排期。"""
import db
from db import connect


def _apply_84_only(monkeypatch, path):
    monkeypatch.setenv("DATABASE_PATH", str(path))
    with monkeypatch.context() as old:
        old.setattr(db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 84])
        db.init_db()


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_upgrade_adds_columns_and_keeps_existing_schedules(tmp_path, monkeypatch):
    _apply_84_only(monkeypatch, tmp_path / "r85.db")
    with connect(write=True) as conn:
        assert "stability" not in _columns(conn, "mistakes")
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES ('u', 'x', 'Asia/Shanghai', '2026-09-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO problems(user_id, title, language, code, thinking, created_at, zone) "
            "VALUES (1, 't', 'Python', '', '', '2026-09-01T00:00:00+00:00', '算法')"
        )
        conn.execute(
            "INSERT INTO mistakes(problem_id, description, repetitions, interval_days, ease_factor, "
            "due_date, last_reviewed_at, version) "
            "VALUES (1, 'd', 4, 21, 2.2, '2026-10-20', '2026-09-29T00:00:00+00:00', 3)"
        )

    db.init_db()

    with connect() as conn:
        assert {"stability", "difficulty"} <= _columns(conn, "mistakes")
        assert {"stability_before", "difficulty_before"} <= _columns(conn, "reviews")
        row = dict(conn.execute("SELECT * FROM mistakes WHERE id = 1").fetchone())
    assert row["stability"] is None and row["difficulty"] is None
    assert (row["repetitions"], row["interval_days"], row["ease_factor"], row["due_date"], row["version"]) == (
        4, 21, 2.2, "2026-10-20", 3,
    )


def test_migration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "again.db"))
    db.init_db()
    with connect(write=True) as conn:
        db._apply_fsrs_state(conn)
        db._apply_fsrs_state(conn)
    assert db.SCHEMA_VERSION >= 85
