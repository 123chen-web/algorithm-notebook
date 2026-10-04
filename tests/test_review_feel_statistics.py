"""暂停只改变复习工作量，保留历史活动、掌握度和学习成果。"""

from datetime import date, datetime, timedelta, timezone

import pytest

import mailer
import send_reminders
from activity import activity_summary, day_counts
from db import connect, init_db
from learning_stats import learning_metrics
from mastery import mastery_report
from stats_summary import summary
from weekly_recap import weekly_recap


TODAY = date(2026, 10, 4)
TIMEZONE = "Asia/Shanghai"
NOW = "2026-10-04T04:00:00+00:00"


@pytest.fixture
def statistics_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "review-statistics.db"))
    init_db()
    with connect(write=True) as conn:
        owner = conn.execute(
            "INSERT INTO users(username, password_hash, email, timezone, created_at) "
            "VALUES ('review-feel', 'unused', 'review-feel@example.com', ?, ?)",
            (TIMEZONE, NOW),
        ).lastrowid
    return owner


def add_mistake(conn, owner, *, due_in=0, created_ago=20, suspended=False, title="题"):
    created = TODAY - timedelta(days=created_ago)
    stamp = datetime(created.year, created.month, created.day, 4, tzinfo=timezone.utc).isoformat()
    problem = conn.execute(
        "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
        "VALUES (?, ?, '算法', 'Python', '', '', ?)",
        (owner, title, stamp),
    ).lastrowid
    return conn.execute(
        "INSERT INTO mistakes(problem_id, description, due_date, suspended_at) "
        "VALUES (?, '边界条件', ?, ?)",
        (problem, (TODAY + timedelta(days=due_in)).isoformat(), NOW if suspended else None),
    ).lastrowid


def add_review(conn, mistake, stamp, quality=4):
    conn.execute(
        "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)",
        (mistake, quality, stamp, (TODAY + timedelta(days=6)).isoformat()),
    )


def test_mastery_keeps_retention_and_total_but_excludes_suspended_workload(statistics_database):
    with connect(write=True) as conn:
        add_mistake(conn, statistics_database, due_in=-1)
        paused_today = add_mistake(conn, statistics_database)
        paused_overdue = add_mistake(conn, statistics_database, due_in=-10)
        before = mastery_report(conn, statistics_database, TIMEZONE, TODAY, 4)
        conn.execute(
            "UPDATE mistakes SET suspended_at = ? WHERE id IN (?, ?)",
            (NOW, paused_today, paused_overdue),
        )
        after = mastery_report(conn, statistics_database, TIMEZONE, TODAY, 4)
        entry = after["zones"][0]
        assert (entry["total"], entry["due"], entry["overdue"], entry["at_risk"]) == (3, 1, 1, 3)
        assert (before["zones"][0]["due"], before["zones"][0]["overdue"]) == (3, 2)
        for key in ("mastery", "change", "series", "total", "at_risk"):
            assert entry[key] == before["zones"][0][key]
        assert after["overall"] == before["overall"]
        assert (after["alert"]["due"], after["alert"]["overdue"]) == (1, 1)


def test_suspended_only_fading_zone_has_no_review_alert_and_returns_on_restore(statistics_database):
    with connect(write=True) as conn:
        mistake = add_mistake(conn, statistics_database, due_in=-20, suspended=True)
        paused = mastery_report(conn, statistics_database, TIMEZONE, TODAY, 4)
        entry = paused["zones"][0]
        assert (entry["total"], entry["due"], entry["overdue"], entry["at_risk"]) == (1, 0, 0, 1)
        assert paused["alert"] is None
        conn.execute("UPDATE mistakes SET suspended_at = NULL WHERE id = ?", (mistake,))
        restored = mastery_report(conn, statistics_database, TIMEZONE, TODAY, 4)
        assert (restored["zones"][0]["due"], restored["zones"][0]["overdue"]) == (1, 1)
        assert restored["alert"] is not None
        assert restored["overall"] == paused["overall"]


def test_suspension_keeps_historical_activity_weekly_recap_and_learning_metrics(statistics_database):
    with connect(write=True) as conn:
        mistake = add_mistake(conn, statistics_database, created_ago=2)
        # 上海本地 10-03 23:59:59 与 10-04 00:00:00，连续两天都有评分。
        add_review(conn, mistake, "2026-10-03T15:59:59+00:00")
        add_review(conn, mistake, "2026-10-03T16:00:00+00:00")
        before_days = day_counts(conn, statistics_database, TIMEZONE)
        before_week = weekly_recap(conn, statistics_database, TIMEZONE, TODAY)
        before_learning = learning_metrics(conn, statistics_database, TIMEZONE, TODAY)
        conn.execute("UPDATE mistakes SET suspended_at = ? WHERE id = ?", (NOW, mistake))
        after_days = day_counts(conn, statistics_database, TIMEZONE)
        after_week = weekly_recap(conn, statistics_database, TIMEZONE, TODAY)
        after_learning = learning_metrics(conn, statistics_database, TIMEZONE, TODAY)
        assert after_days == before_days
        assert after_week == before_week
        assert after_learning == before_learning
        assert after_week["reviews_completed"] == 2
        assert after_learning["mistake_count"] == 1
        assert after_learning["current_streak_days"] == 2
        heatmap = activity_summary(after_days[0], after_days[1], TODAY, 4)
        assert heatmap["totals"]["reviews"] == 2
        assert heatmap["streak_days"] == 2


def test_summary_excludes_suspended_due_and_forecast_but_keeps_historical_reviews(statistics_database):
    with connect(write=True) as conn:
        active_due = add_mistake(conn, statistics_database, due_in=-1)
        add_mistake(conn, statistics_database, due_in=3)
        paused_due = add_mistake(conn, statistics_database, due_in=-10, suspended=True)
        add_mistake(conn, statistics_database, due_in=4, suspended=True)
        add_review(conn, active_due, "2026-10-03T04:00:00+00:00")
        add_review(conn, paused_due, "2026-10-03T15:59:59+00:00", quality=0)
        add_review(conn, paused_due, "2026-10-03T16:00:00+00:00", quality=4)
        report = summary(conn, statistics_database, TIMEZONE, TODAY, 7)
        assert report["due"] == {"today": 1, "overdue": 1}
        assert report["forecast"][0] == {"date": TODAY.isoformat(), "due": 1}
        assert report["forecast"][3]["due"] == 1
        assert report["forecast"][4]["due"] == 0
        assert sum(day["due"] for day in report["forecast"]) == 2
        assert report["reviews"] == {"current": 3, "previous": 0}
        assert report["retention"]["current"] == {"reviews": 1, "passed": 1, "rate": 1.0}
        assert report["streak_days"] == 2
        assert report["daily_reviews"][-1]["count"] == 1


def test_reminder_counts_and_list_exclude_suspended_future_and_other_users(statistics_database):
    with connect(write=True) as conn:
        add_mistake(conn, statistics_database, due_in=-1, title="有效逾期")
        add_mistake(conn, statistics_database, title="有效今日")
        add_mistake(conn, statistics_database, due_in=-20, suspended=True, title="暂停逾期")
        add_mistake(conn, statistics_database, suspended=True, title="暂停今日")
        add_mistake(conn, statistics_database, due_in=1, title="未来记录")
        other = conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES ('another', 'unused', ?, ?)", (TIMEZONE, NOW),
        ).lastrowid
        add_mistake(conn, other, due_in=-30, title="另一用户")
        assert send_reminders.due_count(conn, statistics_database, TODAY.isoformat()) == 2
        listed = send_reminders.due_mistakes(conn, statistics_database, TODAY.isoformat())
        assert [row["title"] for row in listed] == ["有效逾期", "有效今日"]


@pytest.mark.parametrize("dry_run", [False, True], ids=["send", "dry-run"])
def test_reminder_script_skips_suspended_only_account(statistics_database, monkeypatch, capsys, dry_run):
    with connect(write=True) as conn:
        add_mistake(conn, statistics_database, due_in=-20, suspended=True)
    sent = []
    monkeypatch.setattr(send_reminders, "today_in_timezone", lambda name: TODAY)
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))
    assert send_reminders.main(["--dry-run"] if dry_run else []) == 0
    assert sent == []
    assert "review-feel@example.com" not in capsys.readouterr().out
    with connect() as conn:
        assert conn.execute(
            "SELECT last_reminder_sent FROM users WHERE id = ?", (statistics_database,),
        ).fetchone()["last_reminder_sent"] is None
