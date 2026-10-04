"""GET /api/stats/summary：趋势区的口径（用户本地日、真实保持率、预测含逾期）。"""

import time
from datetime import date, datetime, timedelta, timezone

import pytest

import ai
import stats_summary
from db import connect
from test_app import client, register  # noqa: F401  (client 是 fixture)
from test_overview_activity import seed_problem, user_id

TODAY = date(2026, 9, 19)  # 周六；test_app.client 冻结的用户本地日。
URL = "/api/stats/summary"


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    monkeypatch.setattr(ai, "generate", lambda *a, **k: pytest.fail("统计不能调用 AI"))


def utc(day, hour=4, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat()


def add_reviews(mistake_id, rows):
    """rows: [(时间戳, 评分)]，按给定顺序插入（id 先后即复习先后）。"""
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, '2026-09-20')",
            [(mistake_id, quality, stamp) for stamp, quality in rows],
        )


def summary(client, **params):
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def setup_user(client, name="alice"):
    register(client, name)
    return user_id(client)


def test_requires_login_and_validates_days(client):
    assert client.get(URL).status_code == 401
    setup_user(client)
    for good in (7, 30, 90):
        assert summary(client, days=good)["days"] == good
    assert summary(client)["days"] == 30
    for bad in (0, 1, 14, 31, 91, "abc"):
        assert client.get(URL, params={"days": bad}).status_code == 422


def test_empty_account_shape(client):
    setup_user(client)
    data = summary(client)
    assert data["timezone"] == "Asia/Shanghai" and data["today"] == "2026-09-19"
    assert data["due"] == {"today": 0, "overdue": 0}
    assert data["streak_days"] == 0
    assert data["reviews"] == {"current": 0, "previous": 0}
    assert data["retention"]["current"] == {"reviews": 0, "passed": 0, "rate": None}
    assert data["retention"]["previous"]["rate"] is None
    assert len(data["retention"]["weekly"]) == 8
    assert all(week["rate"] is None for week in data["retention"]["weekly"])
    assert len(data["forecast"]) == 14 and all(item["due"] == 0 for item in data["forecast"])
    assert len(data["daily_reviews"]) == 30 and all(item["count"] == 0 for item in data["daily_reviews"])
    assert "email" not in str(data)


def test_forecast_today_includes_overdue(client):
    uid = setup_user(client)
    seed_problem(uid, utc(TODAY), mistake_count=2, due_date="2026-09-10")  # 逾期
    seed_problem(uid, utc(TODAY), mistake_count=1, due_date="2026-09-19")  # 今天
    seed_problem(uid, utc(TODAY), mistake_count=3, due_date="2026-09-20")
    seed_problem(uid, utc(TODAY), mistake_count=1, due_date="2026-10-02")  # 第 14 天
    seed_problem(uid, utc(TODAY), mistake_count=5, due_date="2026-10-03")  # 超出
    data = summary(client)
    assert data["due"] == {"today": 3, "overdue": 2}
    forecast = {item["date"]: item["due"] for item in data["forecast"]}
    assert forecast["2026-09-19"] == 3
    assert forecast["2026-09-20"] == 3
    assert forecast["2026-10-02"] == 1
    assert "2026-10-03" not in forecast and len(forecast) == 14


def test_retention_ignores_first_review_and_quality_3_passes(client):
    uid = setup_user(client)
    a, b = seed_problem(uid, utc(TODAY), mistake_count=2)
    # a：首次 5（不计）、再 3（通过）、再 2（未通过）；b：首次 0（不计）。
    add_reviews(a, [(utc(TODAY, 1), 5), (utc(TODAY, 2), 3), (utc(TODAY, 3), 2)])
    add_reviews(b, [(utc(TODAY, 2), 0)])
    data = summary(client)
    assert data["reviews"]["current"] == 4
    assert data["retention"]["current"] == {"reviews": 2, "passed": 1, "rate": 0.5}


def test_retention_first_review_before_period_makes_later_one_count(client):
    uid = setup_user(client)
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    add_reviews(a, [(utc(TODAY - timedelta(days=200)), 4), (utc(TODAY), 4)])
    data = summary(client, days=7)
    assert data["retention"]["current"] == {"reviews": 1, "passed": 1, "rate": 1.0}
    assert data["reviews"] == {"current": 1, "previous": 0}


def test_period_boundaries_current_and_previous(client):
    uid = setup_user(client)
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    first_current = TODAY - timedelta(days=6)  # days=7
    stamps = [
        first_current - timedelta(days=8),   # 更早：两个周期都不计
        first_current - timedelta(days=7),   # 上一周期第一天
        first_current - timedelta(days=1),   # 上一周期最后一天
        first_current,                       # 本周期第一天
        TODAY,
    ]
    add_reviews(a, [(utc(day), 4) for day in stamps])
    data = summary(client, days=7)
    assert data["reviews"] == {"current": 2, "previous": 2}
    counts = {item["date"]: item["count"] for item in data["daily_reviews"]}
    assert len(counts) == 7 and counts[first_current.isoformat()] == 1 and counts[TODAY.isoformat()] == 1
    assert data["retention"]["previous"]["reviews"] == 2  # 两条都有更早的记录
    assert data["retention"]["current"]["reviews"] == 2


def test_user_timezone_day_boundary(client):
    uid = setup_user(client)  # Asia/Shanghai = UTC+8
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    add_reviews(a, [
        ("2026-09-18T15:59:59+00:00", 4),  # 上海 9/18 23:59:59
        ("2026-09-18T16:00:00+00:00", 4),  # 上海 9/19 00:00:00
        ("2026-09-19T15:59:59+00:00", 4),  # 上海 9/19 23:59:59
    ])
    data = summary(client, days=7)
    counts = {item["date"]: item["count"] for item in data["daily_reviews"]}
    assert counts["2026-09-18"] == 1 and counts["2026-09-19"] == 2
    assert data["streak_days"] == 2


def test_non_utc_offset_timestamps_are_normalised(client):
    uid = setup_user(client)
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    add_reviews(a, [("2026-09-19T08:00:00+08:00", 4)])  # = 00:00Z，上海 9/19 08:00
    counts = {item["date"]: item["count"] for item in summary(client, days=7)["daily_reviews"]}
    assert counts["2026-09-19"] == 1


def test_weekly_grouping_boundaries(client):
    uid = setup_user(client)
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    monday = date(2026, 9, 14)
    add_reviews(a, [
        (utc(date(2026, 9, 1)), 4),                       # 首次，不计
        (utc(monday - timedelta(days=1)), 5),             # 周日 -> 上周
        (utc(monday), 1),                                 # 周一 -> 本周，未通过
        (utc(monday + timedelta(days=5)), 3),             # 周六 -> 本周
    ])
    weekly = {week["week_start"]: week for week in summary(client)["retention"]["weekly"]}
    assert len(weekly) == 8
    assert weekly["2026-09-14"] == {"week_start": "2026-09-14", "reviews": 2, "passed": 1, "rate": 0.5}
    assert weekly["2026-09-07"]["reviews"] == 1 and weekly["2026-09-07"]["rate"] == 1.0
    assert weekly["2026-08-31"]["rate"] is None
    assert min(weekly) == "2026-07-27"


def test_daily_reviews_include_zero_days_and_streak(client):
    uid = setup_user(client)
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    add_reviews(a, [(utc(TODAY), 4), (utc(TODAY - timedelta(days=1)), 4), (utc(TODAY - timedelta(days=3)), 4)])
    data = summary(client, days=7)
    assert [item["count"] for item in data["daily_reviews"]] == [0, 0, 0, 1, 0, 1, 1]
    assert data["streak_days"] == 2


def test_other_users_are_not_mixed_in(client):
    mine = setup_user(client)
    (a,) = seed_problem(mine, utc(TODAY), mistake_count=1, due_date="2026-09-19")
    add_reviews(a, [(utc(TODAY), 4)])
    client.post("/api/auth/logout")
    other = setup_user(client, "bob")
    b, *_ = seed_problem(other, utc(TODAY), mistake_count=4, due_date="2026-09-01")
    add_reviews(b, [(utc(TODAY), 4)] * 5)
    data = summary(client)
    assert data["reviews"]["current"] == 5 and data["due"] == {"today": 4, "overdue": 4}
    assert data["retention"]["current"]["reviews"] == 4
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"})
    data = summary(client)
    assert data["reviews"]["current"] == 1 and data["due"] == {"today": 1, "overdue": 0}


def test_trial_account_gets_own_data(client):
    assert client.post("/api/auth/trial", json={"timezone": "America/New_York"}).status_code == 201
    data = summary(client)
    assert data["timezone"] == "America/New_York" and data["due"] == {"today": 0, "overdue": 0}


def test_half_hour_timezone(client):
    uid = setup_user(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = 'Asia/Kolkata' WHERE id = ?", (uid,))
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)
    add_reviews(a, [("2026-09-18T18:29:59+00:00", 4), ("2026-09-18T18:30:00+00:00", 4)])  # 本地 9/19 00:00 分界
    counts = {item["date"]: item["count"] for item in summary(client, days=7)["daily_reviews"]}
    assert counts["2026-09-18"] == 1 and counts["2026-09-19"] == 1


def test_query_count_does_not_grow_with_reviews(client):
    uid = setup_user(client)
    (a,) = seed_problem(uid, utc(TODAY), mistake_count=1)

    def run():
        statements = []
        with connect() as conn:
            conn.set_trace_callback(statements.append)
            stats_summary.stats_summary(conn, uid, "Asia/Shanghai", TODAY, 30)
        return len(statements)

    few = run()
    add_reviews(a, [(utc(TODAY - timedelta(days=i % 60), i % 20), 4) for i in range(500)])
    assert run() == few


def test_large_account_is_fast(client):
    uid = setup_user(client)
    mistakes = seed_problem(uid, utc(TODAY), mistake_count=300)
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    rows = [
        (mistakes[i % 300], 3 + i % 3, (base + timedelta(minutes=7 * i)).isoformat(), "2026-09-20")
        for i in range(60000)
    ]
    with connect(write=True) as conn:
        conn.executemany("INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)", rows)
    started = time.perf_counter()
    data = summary(client, days=90)
    elapsed = time.perf_counter() - started
    assert data["retention"]["weekly"][0]["reviews"] > 0 or data["reviews"]["previous"] > 0
    assert elapsed < 2.0, f"60k 条复习的汇总耗时 {elapsed:.2f}s"


def test_due_condition_is_the_single_definition():
    assert stats_summary.due_condition(":x") == "m.due_date <= :x"
