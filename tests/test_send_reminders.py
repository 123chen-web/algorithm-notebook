from datetime import date

import pytest

import mailer
import send_reminders
from db import connect, init_db


def add_mistake(conn, username, title, zone, description, due_date):
    user_id = conn.execute(
        "SELECT id FROM users WHERE username = ?", (username,)
    ).fetchone()["id"]
    problem = conn.execute(
        "INSERT INTO problems(user_id,title,zone,language,code,thinking,created_at) "
        "VALUES (?, ?, ?, 'Python', 'pass', '思路', '2026-01-01')",
        (user_id, title, zone),
    )
    conn.execute(
        "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
        (problem.lastrowid, description, due_date),
    )
    return problem.lastrowid


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
        # alice 有一条今天到期的易错点。
        add_mistake(
            conn, "alice", "二分查找边界", "算法", "区间右端点需要减一", "2026-09-20"
        )

        # bob 即使有到期记录，也因为没有邮箱而跳过。
        add_mistake(
            conn, "bob", "无邮箱用户的题目", "后端", "检查事务边界", "2026-09-01"
        )

        # carol 有邮箱，但易错点还没到期。
        add_mistake(
            conn, "carol", "尚未到期的题目", "前端", "清理事件监听", "2026-12-31"
        )

    return path


@pytest.fixture
def sent_emails(monkeypatch):
    messages = []
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: messages.append((to, subject, body))
    )
    return messages


def test_body_includes_problem_details_and_review_prompt(database, sent_emails):
    assert send_reminders.main([]) == 0
    assert len(sent_emails) == 1
    to, subject, body = sent_emails[0]
    assert to == "alice@example.com"
    assert subject == send_reminders.REMINDER_SUBJECT
    assert "今天有 1 条" in body
    assert "[算法] 二分查找边界" in body
    assert "区间右端点需要减一" in body
    assert "2026-09-20" in body
    assert "今日复习" in body


@pytest.mark.parametrize("remaining", [0, 3], ids=["exact-limit", "over-limit"])
def test_body_limits_items_and_reports_remaining(database, sent_emails, remaining):
    limit = send_reminders.REMINDER_ITEM_LIMIT
    titles = [f"展示题目 {i:02d}" for i in range(1, limit + remaining)]
    with connect(write=True) as conn:
        for title in titles:
            add_mistake(conn, "alice", title, "算法", "检查边界", "2026-09-20")

    assert send_reminders.main([]) == 0
    body = sent_emails[0][2]
    assert f"今天有 {limit + remaining} 条" in body
    assert "[算法] 二分查找边界" in body
    for title in titles[:limit - 1]:
        assert f"[算法] {title}" in body
    for title in titles[limit - 1:]:
        assert title not in body
    if remaining:
        assert f"还有 {remaining} 条" in body
    else:
        assert "还有" not in body


def test_body_orders_oldest_due_first_and_same_day_by_id(database, sent_emails):
    with connect(write=True) as conn:
        add_mistake(conn, "alice", "昨天到期", "算法", "检查结束条件", "2026-09-19")
        add_mistake(conn, "alice", "最早到期先添加", "后端", "检查重试", "2026-09-01")
        add_mistake(conn, "alice", "最早到期后添加", "前端", "检查依赖", "2026-09-01")

    assert send_reminders.main([]) == 0
    body = sent_emails[0][2]
    assert (
        body.index("[后端] 最早到期先添加")
        < body.index("[前端] 最早到期后添加")
        < body.index("[算法] 昨天到期")
        < body.index("[算法] 二分查找边界")
    )


def test_body_isolates_users_and_excludes_future_mistakes(database, sent_emails):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "SELECT p.id FROM problems p JOIN users u ON u.id = p.user_id "
            "WHERE u.username = 'alice'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
            (problem_id, "同一道题还要检查空数组", "2026-09-20"),
        )
        add_mistake(conn, "alice", "明天才复习", "算法", "未到期的描述", "2026-09-21")
        add_mistake(conn, "carol", "另一用户的到期题", "前端", "检查响应式更新", "2026-09-19")

    assert send_reminders.main([]) == 0
    bodies = {to: body for to, _, body in sent_emails}
    assert set(bodies) == {"alice@example.com", "carol@example.com"}
    alice_body = bodies["alice@example.com"]
    assert "今天有 2 条" in alice_body
    assert alice_body.count("[算法] 二分查找边界") == 2
    assert "区间右端点需要减一" in alice_body
    assert "同一道题还要检查空数组" in alice_body
    assert "明天才复习" not in alice_body
    assert "未到期的描述" not in alice_body
    assert "另一用户的到期题" not in alice_body
    assert "无邮箱用户的题目" not in alice_body
    carol_body = bodies["carol@example.com"]
    assert "今天有 1 条" in carol_body
    assert "[前端] 另一用户的到期题" in carol_body
    assert "二分查找边界" not in carol_body
    assert "尚未到期的题目" not in carol_body


def test_body_keeps_description_preview_on_one_short_line(database, sent_emails):
    description = "检查\n  边界\t" + "长" * 100
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET description = ? WHERE problem_id = "
            "(SELECT p.id FROM problems p JOIN users u ON u.id = p.user_id "
            "WHERE u.username = 'alice')",
            (description,),
        )

    assert send_reminders.main([]) == 0
    body = sent_emails[0][2]
    assert "检查 边界 " + "长" * 73 + "…" in body
    assert description not in body
    assert "长" * 74 not in body


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
