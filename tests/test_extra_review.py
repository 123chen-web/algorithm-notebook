"""复习评分 / 撤销 / 队列的边界补充测试（HTTP 层）。"""
from datetime import date, datetime, timedelta, timezone

import pytest

import main
from db import connect
from test_app import client, new_problem, register


TODAY = date(2026, 9, 19)
NOW = datetime(2026, 9, 19, 4, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(main, "utc_now", lambda: NOW.isoformat())


def review(client, mistake_id, quality=4, version=0, **extra):
    payload = {"quality": quality, "version": version, **extra}
    return client.post(f"/api/mistakes/{mistake_id}/review", json=payload)


def due(mistake_id, value):
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = ? WHERE id = ?", (value, mistake_id))


def version_of(mistake_id):
    with connect() as conn:
        return conn.execute(
            "SELECT version, repetitions, interval_days, ease_factor, due_date "
            "FROM mistakes WHERE id = ?", (mistake_id,)
        ).fetchone()


# ---------------- 评分入参与版本冲突 ----------------

def test_review_rejects_quality_out_of_range(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = review(client, mistake_id, quality=9)
    assert response.status_code == 422


def test_review_rejects_non_integer_quality(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.post(
        f"/api/mistakes/{mistake_id}/review", json={"quality": "4", "version": 0}
    )
    assert response.status_code == 422


def test_review_requires_version_when_no_client_op_id(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.post(
        f"/api/mistakes/{mistake_id}/review", json={"quality": 4}
    )
    assert response.status_code == 422


def test_review_wrong_version_conflicts(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = review(client, mistake_id, quality=4, version=77)
    assert response.status_code == 409
    assert "已更新" in response.json()["detail"]


def test_review_before_due_is_rejected(client):
    register(client)
    mistake_id = new_problem(client)[0]
    due(mistake_id, (TODAY + timedelta(days=3)).isoformat())
    response = review(client, mistake_id, quality=4, version=0)
    assert response.status_code == 409
    assert "尚未到期" in response.json()["detail"]


def test_suspended_mistake_cannot_be_reviewed(client):
    register(client)
    mistake_id = new_problem(client)[0]
    suspended = client.post(
        f"/api/mistakes/{mistake_id}/suspend", json={"version": 0}
    )
    assert suspended.status_code == 200
    response = review(client, mistake_id, quality=4, version=1)
    assert response.status_code == 409
    assert "暂停" in response.json()["detail"]


# ---------------- 失败评分重置状态 ----------------

def test_failure_resets_repetitions_and_lowers_ease_floor(client):
    register(client)
    mistake_id = new_problem(client)[0]
    # 播种一个已经复习过几轮的状态：repetitions=5 / interval=40 / ease=2.5。
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET repetitions = 5, interval_days = 40, ease_factor = 2.5 "
            "WHERE id = ?", (mistake_id,)
        )
    fail = review(client, mistake_id, quality=0, version=0).json()
    assert fail["repetitions"] == 0
    assert fail["interval_days"] == 1
    assert fail["ease_factor"] == 2.3


def test_ease_factor_has_a_floor_of_1_3(client):
    register(client)
    mistake_id = new_problem(client)[0]
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET ease_factor = 1.31 WHERE id = ?", (mistake_id,)
        )
    result = review(client, mistake_id, quality=0, version=0).json()
    assert result["ease_factor"] == 1.3


# ---------------- 撤销 ----------------

def test_undo_restores_previous_schedule_state(client):
    register(client)
    mistake_id = new_problem(client)[0]
    before = version_of(mistake_id)
    rated = review(client, mistake_id, quality=4, version=0).json()
    assert rated["version"] == 1
    restored = client.post(
        f"/api/mistakes/{mistake_id}/review/undo", json={"version": 1}
    )
    assert restored.status_code == 200
    after = version_of(mistake_id)
    assert after["repetitions"] == before["repetitions"]
    assert after["interval_days"] == before["interval_days"]
    assert after["ease_factor"] == before["ease_factor"]
    assert after["due_date"] == before["due_date"]


def test_undo_without_review_log_is_rejected(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.post(
        f"/api/mistakes/{mistake_id}/review/undo", json={"version": 0}
    )
    assert response.status_code == 409
    assert "没有可以撤销" in response.json()["detail"]


def test_undo_with_stale_version_is_rejected(client):
    register(client)
    mistake_id = new_problem(client)[0]
    review(client, mistake_id, quality=4, version=0)
    response = client.post(
        f"/api/mistakes/{mistake_id}/review/undo", json={"version": 0}
    )
    assert response.status_code == 409


def test_undo_after_24h_window_is_rejected(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]
    review(client, mistake_id, quality=4, version=0)
    # 把"现在"拨到 25 小时之后。
    later = NOW + timedelta(hours=25)
    monkeypatch.setattr(main, "utc_now", lambda: later.isoformat())
    response = client.post(
        f"/api/mistakes/{mistake_id}/review/undo", json={"version": 1}
    )
    assert response.status_code == 409
    assert "24 小时" in response.json()["detail"]


# ---------------- snooze / queue ----------------

@pytest.mark.parametrize("days", [0, 2, 8])
def test_snooze_only_allows_1_3_7_days(client, days):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.post(
        f"/api/mistakes/{mistake_id}/snooze", json={"version": 0, "days": days}
    )
    assert response.status_code == 422


def test_snooze_before_due_is_rejected(client):
    register(client)
    mistake_id = new_problem(client)[0]
    due(mistake_id, (TODAY + timedelta(days=5)).isoformat())
    response = client.post(
        f"/api/mistakes/{mistake_id}/snooze", json={"version": 0, "days": 1}
    )
    assert response.status_code == 409
    assert "还没到期" in response.json()["detail"]


def test_queue_rejects_unknown_zone(client):
    register(client)
    response = client.get("/api/review/queue", params={"zone": "不存在的分区"})
    assert response.status_code == 400
    assert "分区不存在" in response.json()["detail"]


# ---------------- client_op_id 幂等 ----------------

def test_review_with_client_op_id_replays_first_response(client):
    register(client)
    mistake_id = new_problem(client)[0]
    first = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 4, "client_op_id": "op-abc-001"},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 0, "client_op_id": "op-abc-001"},
    )
    assert second.status_code == 200
    assert second.json() == first.json()
