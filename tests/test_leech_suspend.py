"""leech 自动暂停：连续失败 8 次（对标 Anki 默认）后复习接口自动暂停该易错点。"""

from datetime import date, datetime, timezone

import pytest

import main
from db import connect
from test_app import client, register


NOW = datetime(2026, 9, 19, 4, tzinfo=timezone.utc)
TODAY = date(2026, 9, 19)
LEECH_MESSAGE = "这道题连续失败 8 次，已自动暂停，建议换种方式学习，可随时恢复。"


@pytest.fixture(autouse=True)
def leech_clock(monkeypatch):
    monkeypatch.setattr(main, "utc_now", lambda: NOW.isoformat())


def seed(owner, **kwargs):
    with connect(write=True) as conn:
        problem = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, 'leech测试', '算法', 'Python', '', '', ?)",
            (owner, NOW.isoformat()),
        ).lastrowid
        mistake = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date, interval_days, "
            "repetitions, ease_factor, version, last_reviewed_at) VALUES (?, '边界遗漏', ?, 1, 0, 2.5, 0, ?)",
            (problem, kwargs.get("due", TODAY.isoformat()), NOW.isoformat()),
        ).lastrowid
    return mistake


def seed_failures(mistake, count, quality=0):
    """直接在 reviews 表里补失败记录（id 递增即时间先后）。"""
    with connect(write=True) as conn:
        for _ in range(count):
            conn.execute(
                "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
                "VALUES (?, ?, ?, ?)",
                (mistake, quality, NOW.isoformat(), TODAY.isoformat()),
            )


def stored(mistake):
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM mistakes WHERE id = ?", (mistake,)).fetchone())


def owner_id(client):
    with connect() as conn:
        return conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()["id"]


def rate(client, mistake, quality=0, version=0):
    return client.post(f"/api/mistakes/{mistake}/review", json={"quality": quality, "version": version})


def test_eighth_consecutive_failure_suspends_with_flag(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 7)  # 前 7 次失败，第 8 次走接口

    response = rate(client, mistake, quality=0, version=0)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["leech_suspended"] is True
    # 评分 version+1，暂停 version+1，共 +2。
    assert body["version"] == 2

    row = stored(mistake)
    assert row["suspended_at"] is not None
    assert row["version"] == 2
    # 答错折半：0 // 2 == 0，interval 回 1。
    assert body["repetitions"] == 0
    assert body["interval_days"] == 1


def test_seventh_failure_does_not_trigger(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 6)  # 第 7 次走接口

    body = rate(client, mistake, quality=0, version=0).json()
    assert "leech_suspended" not in body
    assert body["version"] == 1
    assert stored(mistake)["suspended_at"] is None


def test_non_consecutive_failures_do_not_trigger(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 7)
    seed_failures(mistake, 1, quality=4)  # 中间一次答对打断连续
    # 再失败一次：连续失败只有 1 次（接口这次）+ 0 = 不触发
    body = rate(client, mistake, quality=0, version=0).json()
    assert "leech_suspended" not in body
    assert stored(mistake)["suspended_at"] is None


def test_success_never_triggers(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 7)
    body = rate(client, mistake, quality=4, version=0).json()
    assert "leech_suspended" not in body
    assert stored(mistake)["suspended_at"] is None


def test_already_suspended_cannot_be_reviewed(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 7)
    assert rate(client, mistake, quality=0, version=0).status_code == 200
    # 已暂停：再次评分 409，不会重复触发。
    response = rate(client, mistake, quality=0, version=2)
    assert response.status_code == 409


def test_unsuspend_then_review_works_again(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 7)
    assert rate(client, mistake, quality=0, version=0).status_code == 200
    # 恢复（version=2 是暂停后的版本）。
    unsuspend = client.post(f"/api/mistakes/{mistake}/unsuspend", json={"version": 2})
    assert unsuspend.status_code == 200, unsuspend.text
    assert stored(mistake)["suspended_at"] is None
    # 恢复后可正常复习：due_date 是明天，改 due 到今天再评。
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = ? WHERE id = ?", (TODAY.isoformat(), mistake))
    response = rate(client, mistake, quality=4, version=3)
    assert response.status_code == 200, response.text
    assert "leech_suspended" not in response.json()


def test_leech_message_copy():
    # 前端提示文案与任务书一致。
    assert LEECH_MESSAGE == "这道题连续失败 8 次，已自动暂停，建议换种方式学习，可随时恢复。"


def test_auto_pause_score_can_be_undone_within_24_hours(client):
    register(client)
    mistake = seed(owner_id(client))
    seed_failures(mistake, 7)
    before = stored(mistake)
    response = rate(client, mistake).json()
    undo = client.post(f"/api/mistakes/{mistake}/review/undo", json={"version": response["version"]})
    assert undo.status_code == 200, undo.text
    after = stored(mistake)
    assert after["suspended_at"] is None
    for field in ("repetitions", "interval_days", "ease_factor", "due_date", "last_reviewed_at"):
        assert after[field] == before[field]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews WHERE mistake_id=?", (mistake,)).fetchone()[0] == 7
