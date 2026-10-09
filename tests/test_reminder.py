"""F2 复习提醒：提醒开关 API、一键退订、每日提醒邮件的后端测试。

用 fake mailer 注入 reminders.send_daily_reminders，不发真实邮件。
"今天"通过注入 now（aware UTC datetime）冻结，避免依赖真实时钟。
"""
import secrets
from datetime import datetime, timezone

import pytest

import main
from db import connect
from reminders import send_daily_reminders
from routers import reminder as reminder_router
from test_app import client, new_problem, register  # noqa: F401  (client 是 fixture)



class FakeMailer:
    def __init__(self):
        self.sent = []

    def send_email(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})


def register_with(client, username, email, tz):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-test-password-123",
            "accept_terms": True,
            "invite_code": "test-invite",
            "email": email,
            "timezone": tz,
        },
    )
    assert response.status_code == 201
    return response.json()


def user_id(username):
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
    return row["id"]


def ensure_token(username):
    with connect() as conn:
        token = conn.execute("SELECT reminder_token FROM users WHERE username = ?", (username,)).fetchone()[0]
    assert token and token.startswith(f"{user_id(username)}.")
    return token



def make_due(client, username, due_date):
    """给当前登录用户建若干道错题，并把 due_date 固定到指定日期。"""
    new_problem(client)
    uid = user_id(username)
    with connect(write=True) as conn:
        conn.execute(
            """
            UPDATE mistakes SET due_date = ?
            WHERE id IN (
                SELECT m.id FROM mistakes m
                JOIN problems p ON p.id = m.problem_id
                WHERE p.user_id = ?
            )
            """,
            (due_date, uid),
        )
    return uid


def test_put_reminder_toggle(client):
    register(client, username="opt")
    response = client.put("/api/me/reminder", json={"opt_in": False})
    assert response.status_code == 200
    assert response.json() == {"opt_in": False}
    with connect() as conn:
        assert conn.execute(
            "SELECT reminder_opt_in FROM users WHERE username = 'opt'"
        ).fetchone()["reminder_opt_in"] == 0

    response = client.put("/api/me/reminder", json={"opt_in": True})
    assert response.json() == {"opt_in": True}
    with connect() as conn:
        assert conn.execute(
            "SELECT reminder_opt_in FROM users WHERE username = 'opt'"
        ).fetchone()["reminder_opt_in"] == 1


def test_put_reminder_rejects_non_bool(client):
    register(client, username="strict")
    assert client.put("/api/me/reminder", json={"opt_in": "yes"}).status_code == 422
    assert client.put("/api/me/reminder", json={}).status_code == 422


def test_opt_out_user_gets_no_mail(client):
    register(client, username="out", email="out@example.com")
    make_due(client, "out", "2026-09-20")
    client.put("/api/me/reminder", json={"opt_in": False})
    fake = FakeMailer()
    now = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    stats = send_daily_reminders(fake, now=now)
    assert stats["sent"] == 0
    assert fake.sent == []


def test_no_due_gets_no_mail(client):
    register(client, username="nodue", email="nodue@example.com")
    # 没有任何错题 → due 数为 0，不发。
    fake = FakeMailer()
    now = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    stats = send_daily_reminders(fake, now=now)
    assert stats["sent"] == 0
    assert fake.sent == []


def test_opt_in_user_gets_mail_with_unsubscribe_link(client):
    register(client, username="inz", email="inz@example.com")
    make_due(client, "inz", "2026-09-20")
    token = ensure_token("inz")
    fake = FakeMailer()
    now = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    stats = send_daily_reminders(fake, now=now)
    assert stats["sent"] == 1
    mail = fake.sent[0]
    assert mail["to"] == "inz@example.com"
    assert "今日有 2 道易错点待复习" in mail["subject"]
    assert "今日待复习 2 道（预计 4 分钟" in mail["body"]
    assert "   http://localhost:8000\n" in mail["body"]
    assert f"/api/reminder/unsubscribe?token={token}" in mail["body"]


def test_reminder_links_use_configured_homepage_without_an_unsupported_route(client):
    register(client, username="links", email="links@example.com")
    make_due(client, "links", "2026-09-20")
    fake = FakeMailer()
    send_daily_reminders(fake, now=datetime(2026, 9, 20, 1, tzinfo=timezone.utc),
                         base_url="https://example.com/notebook/")
    body = fake.sent[0]["body"]
    assert "   https://example.com/notebook\n" in body
    assert "localhost" not in body
    assert "#/today" not in body


def test_timezone_boundary(client):
    # 2026-09-20 16:30 UTC：上海已是 9-21，纽约还是 9-20。
    now = datetime(2026, 9, 20, 16, 30, tzinfo=timezone.utc)
    register_with(client, "sh", "sh@example.com", "Asia/Shanghai")
    register_with(client, "ny", "ny@example.com", "America/New_York")
    for name in ("sh", "ny"):
        uid = user_id(name)
        # 注意 register_with 登录的是最后一个用户；直接用 DB 建错题。
        with connect(write=True) as conn:
            pid = conn.execute(
                "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
                "VALUES (?, '时区题', '算法', 'Python', 'code', 'thinking', ?)",
                (uid, main.utc_now()),
            ).lastrowid
            conn.execute(
                "INSERT INTO mistakes(problem_id, description, due_date) "
                "VALUES (?, '时区错因', '2026-09-21')",
                (pid,),
            )
    fake = FakeMailer()
    stats = send_daily_reminders(fake, now=now)
    recipients = [mail["to"] for mail in fake.sent]
    # 上海"今天"是 9-21：due 9-21 已到期 → 发；纽约"今天"是 9-20：未到期 → 不发。
    assert stats["sent"] == 1
    assert recipients == ["sh@example.com"]


def test_unsubscribe_token(client):
    register(client, username="unsub", email="unsub@example.com")
    make_due(client, "unsub", "2026-09-20")
    token = ensure_token("unsub")

    bad = client.get("/api/reminder/unsubscribe", params={"token": "no-such-token"})
    assert bad.status_code == 200

    ok = client.get("/api/reminder/unsubscribe", params={"token": token})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}
    with connect() as conn:
        assert conn.execute(
            "SELECT reminder_opt_in FROM users WHERE username = 'unsub'"
        ).fetchone()["reminder_opt_in"] == 0

    # 退订后再跑每日提醒：不再发信。
    fake = FakeMailer()
    now = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    stats = send_daily_reminders(fake, now=now)
    assert stats["sent"] == 0
    assert fake.sent == []


def test_streak_at_risk_line(client):
    register(client, username="streak", email="streak@example.com")
    uid = make_due(client, "streak", "2026-09-20")
    with connect(write=True) as conn:
        mid = conn.execute(
            "SELECT m.id FROM mistakes m JOIN problems p ON p.id = m.problem_id "
            "WHERE p.user_id = ? LIMIT 1",
            (uid,),
        ).fetchone()["id"]
        # 连续三天打卡（9-18/19/20），今天（9-21）还没复习。
        for day in ("2026-09-18", "2026-09-19", "2026-09-20"):
            conn.execute(
                "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
                "VALUES (?, 4, ?, ?)",
                (mid, f"{day}T00:00:00+00:00", day),
            )
    fake = FakeMailer()
    now = datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc)  # 上海 9-21 09:00
    stats = send_daily_reminders(fake, now=now)
    assert stats["sent"] == 1
    assert "连续打卡 3 天" in fake.sent[0]["body"]

    # 今天也复习了 → 不再 at-risk。
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, 4, ?, ?)",
            (mid, "2026-09-21T01:30:00+00:00", "2026-09-21"),
        )
    fake = FakeMailer()
    stats = send_daily_reminders(fake, now=now)
    assert stats == {"sent": 0, "skipped": 1, "failed": 0}
    assert fake.sent == []
    # Keep the original streak assertion independently of the new daily send
    # deduplication: reviewing today eliminates the at-risk signal.
    from reminders import streak_at_risk
    stamps = [datetime.fromisoformat(f"{day}T01:00:00+00:00")
              for day in ("2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21")]
    assert streak_at_risk("Asia/Shanghai", now.date(), stamps) is None


def test_success_is_idempotent_and_failure_can_retry(client):
    register(client, username="daily", email="daily@example.com")
    uid = make_due(client, "daily", "2026-09-20")
    now = datetime(2026, 9, 20, 1, tzinfo=timezone.utc)
    class FailingMailer:
        def send_email(self, *args):
            raise RuntimeError("inert mail failure")
    assert send_daily_reminders(FailingMailer(), now=now) == {"sent": 0, "skipped": 0, "failed": 1}
    with connect() as conn:
        assert conn.execute("SELECT last_reminder_sent FROM users WHERE id=?", (uid,)).fetchone()[0] is None
    fake = FakeMailer()
    assert send_daily_reminders(fake, now=now)["sent"] == 1
    assert send_daily_reminders(fake, now=now) == {"sent": 0, "skipped": 1, "failed": 0}
    assert len(fake.sent) == 1
    tomorrow = datetime(2026, 9, 21, 1, tzinfo=timezone.utc)
    assert send_daily_reminders(fake, now=tomorrow)["sent"] == 1


def test_parallel_daily_reminders_send_once(client):
    from concurrent.futures import ThreadPoolExecutor
    register(client, username="parallel", email="parallel@example.com")
    make_due(client, "parallel", "2026-09-20")
    fake = FakeMailer()
    now = datetime(2026, 9, 20, 1, tzinfo=timezone.utc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: send_daily_reminders(fake, now=now), range(2)))
    assert sum(result["sent"] for result in results) == 1
    assert len(fake.sent) == 1


def test_unsubscribe_constant_time_and_unenumerable_response(client, monkeypatch):
    from routers import reminder
    register(client, username="constant", email="constant@example.com")
    token = ensure_token("constant")
    actual = reminder.secrets.compare_digest
    calls = []
    def spy(a, b):
        calls.append((a, b))
        return actual(a, b)
    monkeypatch.setattr(reminder.secrets, "compare_digest", spy)
    responses = [client.get("/api/reminder/unsubscribe", params={"token": value})
                 for value in ("garbage", token.replace(token.split('.')[1], "bad"), token)]
    assert len(calls) == 3
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)
    assert all(response.status_code == 200 and response.json() == {"ok": True} for response in responses)
