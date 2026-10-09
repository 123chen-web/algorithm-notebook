"""Muse growth integration regressions; all AI and mail are inert test doubles."""
from datetime import date

import pytest

import main
from db import connect
from test_app import client, register  # noqa: F401


NOW = "2026-09-19T04:00:00+00:00"
DAY = date(2026, 9, 19)
PASSWORD = "a-test-password-123"


@pytest.fixture
def growth_client(client, monkeypatch):
    monkeypatch.setenv("AI_DAILY_LIMIT", "20")
    monkeypatch.setattr(main, "utc_now", lambda: NOW)
    monkeypatch.setattr(main, "today_for", lambda user: DAY)
    for name in list(main.os.environ):
        if name.startswith("SMTP_"):
            monkeypatch.delenv(name, raising=False)

    def forbidden_ai(*args, **kwargs):
        pytest.fail("Growth integration tests must never initialize a real AI client")

    monkeypatch.setattr(main, "OpenAI", forbidden_ai)
    return client


def make_problem(client, title="集成回归", count=1):
    response = client.post("/api/problems", json={
        "title": title, "zone": "算法", "language": "Python", "code": "pass",
        "thinking": "边界条件", "mistakes": [f"错因 {index}" for index in range(count)],
    })
    assert response.status_code == 201, response.text
    return response.json()


def reviews(mistake_ids, qualities=(0, 0, 0)):
    with connect(write=True) as conn:
        for mid in mistake_ids:
            for quality in qualities:
                conn.execute(
                    "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
                    "VALUES (?, ?, ?, ?)", (mid, quality, NOW, DAY.isoformat()),
                )


@pytest.mark.parametrize("endpoint", ["heatmap", "focus"])
def test_mastery_same_second_reviews_use_latest_inserted_score(growth_client, endpoint):
    owner = register(growth_client)["id"]
    mids = make_problem(growth_client, count=5)["mistake_ids"]
    with connect(write=True) as conn:
        for mid in mids:
            conn.execute("INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) "
                         "VALUES (?, ?, '同秒评分', ?)", (mid, owner, NOW))
    # All timestamps are identical: the newest (successful) row must receive
    # weight 0.5, rather than the oldest-row ordering giving it weight 0.2.
    reviews(mids, qualities=(0, 0, 4))
    response = growth_client.get(f"/api/mastery/{endpoint}")
    assert response.status_code == 200, response.text
    if endpoint == "heatmap":
        assert response.json()["cells"]["同秒评分"][-1] == 0.5
    else:
        assert response.json()["tag"] == "同秒评分"
        assert response.json()["score"] == 0.5


def seed_growth_data(client, owner):
    problem = make_problem(client)
    mid = problem["mistake_ids"][0]
    reviews([mid])
    with connect(write=True) as conn:
        for deleted in (None, NOW):
            conn.execute("INSERT INTO notes(user_id, title, content, problem_id, created_at, "
                         "updated_at, deleted_at) VALUES (?, '私人笔记', '含软删除', ?, ?, ?, ?)",
                         (owner, problem["id"], NOW, NOW, deleted))
        conn.execute("UPDATE users SET reminder_token=?, reminder_opt_in=1 WHERE id=?",
                     (f"inert-reminder-token-{owner}", owner))


def test_account_deletion_clears_all_new_growth_tables_and_keeps_other_users(growth_client):
    alice = register(growth_client)["id"]
    seed_growth_data(growth_client, alice)
    assert growth_client.post("/api/auth/logout").status_code == 200
    bob = register(growth_client, username="bob")["id"]
    seed_growth_data(growth_client, bob)
    assert growth_client.post("/api/auth/logout").status_code == 200
    assert growth_client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    response = growth_client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    with connect() as conn:
        for table in ("notes",):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (alice,)).fetchone()[0] == 0, table
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (bob,)).fetchone()[0] > 0, table
        user = conn.execute("SELECT deleted_at, reminder_token, reminder_opt_in FROM users WHERE id=?", (alice,)).fetchone()
        assert user["deleted_at"] is not None
        assert user["reminder_token"] is None and user["reminder_opt_in"] == 0
