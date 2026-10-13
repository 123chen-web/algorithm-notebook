"""刚记下的完整错题明天才第一次到期；速记（错因待补）仍然当天出现。"""
from datetime import date, timedelta

import pytest

import main
from test_app import client, register
from core import config
from core import time
import db


@pytest.fixture(autouse=True)
def one_day_delay(monkeypatch):
    monkeypatch.setattr(config, "FIRST_REVIEW_DELAY_DAYS", 1)


def body(**extra):
    data = {"title": "二分", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "想法", "mistakes": ["漏了边界"]}
    data.update(extra)
    return data


def due_dates(problem_id):
    with db.connect() as conn:
        return [row["due_date"] for row in conn.execute(
            "SELECT due_date FROM mistakes WHERE problem_id = ? ORDER BY id", (problem_id,))]


def test_full_record_is_first_due_tomorrow_and_not_in_todays_queue(client):
    register(client)
    created = client.post("/api/problems", json=body())
    assert created.status_code == 201
    today = time.today_for({"timezone": "Asia/Shanghai"})
    assert due_dates(created.json()["id"]) == [(today + timedelta(days=1)).isoformat()]


def test_quick_record_with_pending_reason_still_shows_today(client):
    register(client)
    created = client.post("/api/problems", json={
        "title": "速记题", "zone": "算法", "language": "Python", "quick": True})
    assert created.status_code == 201
    today = time.today_for({"timezone": "Asia/Shanghai"}).isoformat()
    assert due_dates(created.json()["id"]) == [today]


def test_delay_zero_restores_same_day_review(client, monkeypatch):
    monkeypatch.setattr(config, "FIRST_REVIEW_DELAY_DAYS", 0)
    register(client)
    created = client.post("/api/problems", json=body())
    today = time.today_for({"timezone": "Asia/Shanghai"}).isoformat()
    assert due_dates(created.json()["id"]) == [today]


def test_migration_76_shifts_only_recent_unreviewed_full_mistakes(client, monkeypatch):
    import sqlite3
    import db
    monkeypatch.setattr(config, "FIRST_REVIEW_DELAY_DAYS", 0)  # 模拟旧规则：新记当天就到期
    register(client)
    today = time.today_for({"timezone": "Asia/Shanghai"})
    recent = client.post("/api/problems", json=body()).json()["id"]
    quick = client.post("/api/problems", json={
        "title": "速记", "zone": "算法", "language": "Python", "quick": True}).json()["id"]
    reviewed = client.post("/api/problems", json=body(title="已复习")).json()["id"]
    old = client.post("/api/problems", json=body(title="旧题")).json()["id"]
    with db.connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET last_reviewed_at = '2026-10-01' WHERE problem_id = ?", (reviewed,))
        conn.execute("UPDATE problems SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (old,))
    before = {pid: due_dates(pid) for pid in (recent, quick, reviewed, old)}
    with db.connect(write=True) as conn:
        db._apply_shift_fresh_first_review(conn)
    shifted = lambda d: (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    assert due_dates(recent) == [shifted(before[recent][0])]
    assert due_dates(quick) == before[quick], "速记（待补错因）不顺延"
    assert due_dates(reviewed) == before[reviewed], "复习过的不动"
    assert due_dates(old) == before[old], "记录时间很早的（旧导入）不动"
