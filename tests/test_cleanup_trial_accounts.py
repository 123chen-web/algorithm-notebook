from datetime import datetime, timedelta, timezone

import pytest

import cleanup_trial_accounts
from db import connect, init_db


def iso(days_ago):
    return (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat(timespec="seconds")


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    init_db()

    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,email,timezone,"
            "created_at,is_trial) VALUES "
            "('trial_old','unused',NULL,'Asia/Shanghai',?,1)",
            (iso(10),),
        )
        conn.execute(
            "INSERT INTO users(username,password_hash,email,timezone,"
            "created_at,is_trial) VALUES "
            "('trial_recent','unused',NULL,'Asia/Shanghai',?,1)",
            (iso(1),),
        )
        conn.execute(
            "INSERT INTO users(username,password_hash,email,timezone,"
            "created_at,is_trial) VALUES "
            "('alice','unused','alice@example.com','Asia/Shanghai',?,0)",
            (iso(30),),
        )

        old_trial_id = conn.execute(
            "SELECT id FROM users WHERE username = 'trial_old'"
        ).fetchone()[0]
        problem = conn.execute(
            "INSERT INTO problems(user_id,title,language,code,thinking,"
            "created_at) VALUES (?, '题目', 'Python', 'pass', '思路', ?)",
            (old_trial_id, iso(10)),
        )
        conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) "
            "VALUES (?, '易错点', '2026-01-01')",
            (problem.lastrowid,),
        )

    return path


def usernames(conn):
    return {
        row["username"]
        for row in conn.execute("SELECT username FROM users").fetchall()
    }


def test_dry_run_reports_without_deleting(database, capsys):
    assert cleanup_trial_accounts.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "trial_old" in out
    assert "trial_recent" not in out  # 还没过期
    assert "alice" not in out  # 不是体验账号，年龄再老也不删

    with connect() as conn:
        assert usernames(conn) == {"trial_old", "trial_recent", "alice"}


def test_deletes_expired_trial_accounts_and_cascades(database):
    assert cleanup_trial_accounts.main([]) == 0

    with connect() as conn:
        assert usernames(conn) == {"trial_recent", "alice"}
        # 级联删除：过期体验账号名下的题目和易错点也一并消失。
        assert conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 0


def test_max_age_days_is_configurable(database):
    # 把过期天数调到 40 天，10 天前创建的体验账号还不算过期。
    assert cleanup_trial_accounts.main(["--max-age-days", "40"]) == 0

    with connect() as conn:
        assert usernames(conn) == {"trial_old", "trial_recent", "alice"}


def test_rejects_negative_max_age_days(database):
    with pytest.raises(SystemExit):
        cleanup_trial_accounts.main(["--max-age-days", "-1"])
