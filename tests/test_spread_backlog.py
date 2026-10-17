"""补欠账：POST /api/review/spread 把今天上限放不下的逾期记录摊到之后几天；/undo 撤销。"""
from datetime import date, timedelta

import db
import review_lib
from core import time
from test_app import client, register  # noqa: F401


def make_overdue(client, count):
    """建 count 道题（各 1 条易错点），到期日依次是 count…1 天前（越前面越逾期）。"""
    ids = []
    for index in range(count):
        response = client.post("/api/problems", json={
            "title": f"题 {index}", "zone": "算法", "language": "Python", "code": "pass",
            "thinking": "想当然", "mistakes": ["边界没想清"],
        })
        assert response.status_code == 201, response.text
        ids.append(response.json()["mistake_ids"][0])
    today = date.fromisoformat(client.get("/api/review/queue").json()["today"])
    with db.connect() as conn:
        for offset, mistake_id in enumerate(ids):
            due = (today - timedelta(days=count - offset)).isoformat()
            conn.execute("UPDATE mistakes SET due_date = ?, interval_days = 1 WHERE id = ?", (due, mistake_id))
    return ids, today


def set_cap(client, cap):
    response = client.put("/api/me/review-settings", json={"daily_review_cap": cap})
    assert response.status_code == 200, response.text


def due_dates(ids):
    with db.connect() as conn:
        return {row["id"]: row["due_date"] for row in conn.execute(
            f"SELECT id, due_date FROM mistakes WHERE id IN ({','.join('?' * len(ids))})", ids)}


def test_spread_plan_fills_following_days_up_to_the_cap_around_existing_load():
    today = date(2026, 10, 17)
    excess = [{"id": i} for i in range(5)]
    plan = review_lib.spread_plan(excess, today, 2, {"2026-10-18": 1})
    assert [(item["id"], day) for item, day in plan] == [
        (0, "2026-10-18"), (1, "2026-10-19"), (2, "2026-10-19"), (3, "2026-10-20"), (4, "2026-10-20"),
    ]
    assert review_lib.spread_plan(excess, today, None, {}) == []


def test_spread_keeps_todays_share_and_moves_the_rest_most_overdue_first(client):
    register(client)
    ids, today = make_overdue(client, 12)
    set_cap(client, 5)
    response = client.post("/api/review/spread")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cap"] == 5 and body["days"] == 2
    moved = {entry["id"]: entry for entry in body["moved"]}
    assert set(moved) == set(ids[5:]), "最逾期的 5 条留在今天，其余 7 条摊出去"
    after = due_dates(ids)
    tomorrow, next_day = (today + timedelta(days=1)).isoformat(), (today + timedelta(days=2)).isoformat()
    assert [after[i] for i in ids[5:]] == [tomorrow] * 5 + [next_day] * 2
    assert all(after[i] < today.isoformat() for i in ids[:5])
    assert client.get("/api/review/queue").json()["total_due"] == 5, "今天只剩上限内的"


def test_spread_without_a_cap_is_409_and_moves_nothing(client):
    register(client)
    ids, _ = make_overdue(client, 3)
    set_cap(client, None)
    before = due_dates(ids)
    assert client.post("/api/review/spread").status_code == 409
    assert due_dates(ids) == before


def test_undo_restores_untouched_items_and_skips_ones_changed_since(client):
    register(client)
    ids, _ = make_overdue(client, 8)
    set_cap(client, 5)
    before = due_dates(ids)
    moved = client.post("/api/review/spread").json()["moved"]
    assert len(moved) == 3
    touched = moved[0]
    with db.connect() as conn:
        conn.execute("UPDATE mistakes SET version = version + 1 WHERE id = ?", (touched["id"],))
    items = [{"id": entry["id"], "due_date": entry["from"], "version": entry["version"]} for entry in moved]
    response = client.post("/api/review/spread/undo", json={"items": items})
    assert response.json() == {"restored": 2, "skipped": 1}
    after = due_dates(ids)
    assert after[touched["id"]] == touched["to"], "摊开后又改过的不动"
    for entry in moved[1:]:
        assert after[entry["id"]] == before[entry["id"]]


def test_undo_never_touches_another_users_records(client):
    register(client)
    ids, _ = make_overdue(client, 7)
    set_cap(client, 5)
    moved = client.post("/api/review/spread").json()["moved"]
    assert len(moved) == 2
    client.post("/api/auth/logout")
    register(client, username="bob")
    items = [{"id": entry["id"], "due_date": "2000-01-01", "version": entry["version"]} for entry in moved]
    assert client.post("/api/review/spread/undo", json={"items": items}).json()["restored"] == 0
    assert "2000-01-01" not in due_dates(ids).values()
    client.post("/api/auth/logout")
    assert client.post("/api/review/spread").status_code == 401
