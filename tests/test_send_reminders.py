from datetime import date

import pytest

import mailer
import send_reminders
from db import connect, init_db


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setattr(
        send_reminders, "today_in_timezone", lambda tz: date(2026, 9, 20)
    )
    init_db()

    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,email,timezone,created_at) "
            "VALUES ('alice','unused','alice@example.com','Asia/Shanghai','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users(username,password_hash,email,timezone,created_at) "
            "VALUES ('bob','unused',NULL,'Asia/Shanghai','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO users(username,password_hash,email,timezone,created_at) "
            "VALUES ('carol','unused','carol@example.com','Asia/Shanghai','2026-01-01')"
        )
        alice_id = conn.execute(
            "SELECT id FROM users WHERE username = 'alice'"
        ).fetchone()[0]
        carol_id = conn.execute(
            "SELECT id FROM users WHERE username = 'carol'"
        ).fetchone()[0]

        # alice 有一条今天到期的易错点。
        problem = conn.execute(
            "INSERT INTO problems(user_id,title,language,code,thinking,created_at) "
            "VALUES (?, '题目', 'Python', 'pass', '思路', '2026-01-01')",
            (alice_id,),
        )
        conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) "
            "VALUES (?, '易错点', '2026-09-20')",
            (problem.lastrowid,),
        )

        # carol 有邮箱，但易错点还没到期。
        problem2 = conn.execute(
            "INSERT INTO problems(user_id,title,language,code,thinking,created_at) "
            "VALUES (?, '题目二', 'Python', 'pass', '思路', '2026-01-01')",
            (carol_id,),
        )
        conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) "
            "VALUES (?, '易错点二', '2026-12-31')",
            (problem2.lastrowid,),
        )

    return path


def test_dry_run_reports_without_sending_or_writing(database, capsys, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))

    assert send_reminders.main(["--dry-run"]) == 0
    assert sent == []

    out = capsys.readouterr().out
    assert "alice" in out
    assert "carol" not in out  # 没有到期记录，不出现在名单里
    assert "bob" not in out  # 没有邮箱，直接跳过

    with connect() as conn:
        row = conn.execute(
            "SELECT last_reminder_sent FROM users WHERE username = 'alice'"
        ).fetchone()
        assert row["last_reminder_sent"] is None


def test_sends_once_per_day_and_dedupes(database, monkeypatch):
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    sent = []
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: sent.append(to)
    )

    assert send_reminders.main([]) == 0
    assert sent == ["alice@example.com"]  # bob 没邮箱，carol 没到期记录

    with connect() as conn:
        row = conn.execute(
            "SELECT last_reminder_sent FROM users WHERE username = 'alice'"
        ).fetchone()
        assert row["last_reminder_sent"] == "2026-09-20"

    # 同一天再跑一次不会重复发送。
    assert send_reminders.main([]) == 0
    assert sent == ["alice@example.com"]


def test_send_failure_does_not_mark_as_sent(database, monkeypatch):
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)

    def failing_send(to, subject, body):
        raise RuntimeError("模拟 SMTP 故障")

    monkeypatch.setattr(mailer, "send_email", failing_send)
    assert send_reminders.main([]) == 0

    with connect() as conn:
        row = conn.execute(
            "SELECT last_reminder_sent FROM users WHERE username = 'alice'"
        ).fetchone()
        # 发信失败不应该标记成已发送，否则今天就再也补发不了了。
        assert row["last_reminder_sent"] is None


def test_refuses_to_run_without_smtp_configured(database, monkeypatch):
    monkeypatch.setattr(mailer, "smtp_configured", lambda: False)
    assert send_reminders.main([]) == 1
