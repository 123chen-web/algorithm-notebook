"""刚记下的完整错题明天才第一次到期；速记（错因待补）仍然当天出现。"""
from datetime import date, timedelta

import pytest

import main
from test_app import client, register


@pytest.fixture(autouse=True)
def one_day_delay(monkeypatch):
    monkeypatch.setattr(main, "FIRST_REVIEW_DELAY_DAYS", 1)


def body(**extra):
    data = {"title": "二分", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "想法", "mistakes": ["漏了边界"]}
    data.update(extra)
    return data


def due_dates(problem_id):
    with main.connect() as conn:
        return [row["due_date"] for row in conn.execute(
            "SELECT due_date FROM mistakes WHERE problem_id = ? ORDER BY id", (problem_id,))]


def test_full_record_is_first_due_tomorrow_and_not_in_todays_queue(client):
    register(client)
    created = client.post("/api/problems", json=body())
    assert created.status_code == 201
    today = main.today_for({"timezone": "Asia/Shanghai"})
    assert due_dates(created.json()["id"]) == [(today + timedelta(days=1)).isoformat()]


def test_quick_record_with_pending_reason_still_shows_today(client):
    register(client)
    created = client.post("/api/problems", json={
        "title": "速记题", "zone": "算法", "language": "Python", "quick": True})
    assert created.status_code == 201
    today = main.today_for({"timezone": "Asia/Shanghai"}).isoformat()
    assert due_dates(created.json()["id"]) == [today]


def test_delay_zero_restores_same_day_review(client, monkeypatch):
    monkeypatch.setattr(main, "FIRST_REVIEW_DELAY_DAYS", 0)
    register(client)
    created = client.post("/api/problems", json=body())
    today = main.today_for({"timezone": "Asia/Shanghai"}).isoformat()
    assert due_dates(created.json()["id"]) == [today]
