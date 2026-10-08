"""Muse growth integration regressions; all AI and mail are inert test doubles."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import threading

import pytest
from fastapi.testclient import TestClient

import main
from db import connect
from routers import explain as explain_routes
from test_app import client, register  # noqa: F401


NOW = "2026-09-19T04:00:00+00:00"
DAY = date(2026, 9, 19)
PASSWORD = "a-test-password-123"
GOOD_SCORE = {"score": 85, "missing_points": [], "follow_up": "怎样处理边界？"}


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


def start_boss(client):
    response = client.post("/api/boss/session")
    assert response.status_code == 200, response.text
    return response.json()


def round_boss(client, session_id, mistake_id, correct=False):
    return client.post(f"/api/boss/session/{session_id}/round", json={
        "mistake_id": mistake_id, "correct": correct, "seconds": 10,
    })


def test_boss_cannot_submit_a_replacement_never_issued_to_the_session(growth_client):
    register(growth_client)
    mids = make_problem(growth_client, count=6)["mistake_ids"]
    reviews(mids)
    session = start_boss(growth_client)
    assert [item["mistake_id"] for item in session["rounds"]] == mids[:5]
    assert round_boss(growth_client, session["session_id"], mids[0], correct=True).status_code == 200

    # The sixth eligible mistake enters the current global top five after the
    # first graduates, but was never issued as a round of this session.
    response = round_boss(growth_client, session["session_id"], mids[5], correct=True)
    assert response.status_code == 422, response.text
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM boss_rounds WHERE session_id=?",
                            (session["session_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM boss_graduations WHERE mistake_id=?",
                            (mids[5],)).fetchone()[0] == 0


def test_boss_issued_round_remains_valid_after_due_date_reordering(growth_client):
    register(growth_client)
    mids = make_problem(growth_client, count=6)["mistake_ids"]
    reviews(mids)
    session = start_boss(growth_client)
    assert [item["mistake_id"] for item in session["rounds"]] == mids[:5]
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date='2026-12-31' WHERE id=?", (mids[4],))
        conn.execute("UPDATE mistakes SET due_date='2026-08-01' WHERE id=?", (mids[5],))
    response = round_boss(growth_client, session["session_id"], mids[4])
    assert response.status_code == 200, response.text
    assert response.json()["losses"] == 1


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
    session_id = start_boss(client)["session_id"]
    assert round_boss(client, session_id, mid, correct=True).status_code == 200
    with connect(write=True) as conn:
        conn.execute("INSERT INTO explanations(user_id, mistake_id, explanation, score, "
                     "missing_points, created_at) VALUES (?, ?, '私人讲解', 85, '[]', ?)",
                     (owner, mid, NOW))
        for deleted in (None, NOW):
            conn.execute("INSERT INTO notes(user_id, title, content, problem_id, created_at, "
                         "updated_at, deleted_at) VALUES (?, '私人笔记', '含软删除', ?, ?, ?, ?)",
                         (owner, problem["id"], NOW, NOW, deleted))
        conn.execute("INSERT INTO import_screenshot_daily(user_id, day, attempts) VALUES (?, ?, 1)",
                     (owner, DAY.isoformat()))
        conn.execute("UPDATE users SET reminder_token=?, reminder_opt_in=1 WHERE id=?",
                     (f"inert-reminder-token-{owner}", owner))
    return session_id


def test_account_deletion_clears_all_new_growth_tables_and_keeps_other_users(growth_client):
    alice = register(growth_client)["id"]
    alice_session = seed_growth_data(growth_client, alice)
    assert growth_client.post("/api/auth/logout").status_code == 200
    bob = register(growth_client, username="bob")["id"]
    bob_session = seed_growth_data(growth_client, bob)
    assert growth_client.post("/api/auth/logout").status_code == 200
    assert growth_client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200
    response = growth_client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    with connect() as conn:
        for table in ("notes", "explanations", "boss_sessions", "boss_graduations", "import_screenshot_daily"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (alice,)).fetchone()[0] == 0, table
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (bob,)).fetchone()[0] > 0, table
        assert conn.execute("SELECT COUNT(*) FROM boss_rounds WHERE session_id=?", (alice_session,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM boss_rounds WHERE session_id=?", (bob_session,)).fetchone()[0] == 1
        user = conn.execute("SELECT deleted_at, reminder_token, reminder_opt_in FROM users WHERE id=?", (alice,)).fetchone()
        assert user["deleted_at"] is not None
        assert user["reminder_token"] is None and user["reminder_opt_in"] == 0


def explanation_count(owner):
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM explanations WHERE user_id=?", (owner,)).fetchone()[0]


def used_attempts(owner):
    with connect() as conn:
        row = conn.execute("SELECT attempts FROM ai_usage WHERE user_id=? AND day=?", (owner, DAY.isoformat())).fetchone()
        return row[0] if row else 0


def test_explain_same_user_mistake_concurrency_is_409_without_extra_charge(growth_client, monkeypatch):
    owner = register(growth_client)["id"]
    mid = make_problem(growth_client)["mistake_ids"][0]
    with connect(write=True) as conn:
        for _ in range(2):
            conn.execute("INSERT INTO explanations(user_id, mistake_id, explanation, score, "
                         "missing_points, created_at) VALUES (?, ?, '之前讲解', 85, '[]', ?)", (owner, mid, NOW))
        conn.execute("INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 2)", (owner, DAY.isoformat()))
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_ai(item, explanation):
        calls.append(item["id"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(10), "test did not release the inert AI response"
        return dict(GOOD_SCORE)

    monkeypatch.setattr(explain_routes, "explain_ai_score", blocked_ai)
    path = f"/api/mistakes/{mid}/explain"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(growth_client.post, path, json={"explanation": "第三次"})
        try:
            assert entered.wait(5), "first request never entered the fake AI"
            second = pool.submit(growth_client.post, path, json={"explanation": "并发第四次"})
            blocked = second.result(timeout=2)
            assert blocked.status_code == 409, blocked.text
            assert used_attempts(owner) == 3
            assert len(calls) == 1
        finally:
            release.set()
            completed = first.result(timeout=5)
    assert completed.status_code == 200, completed.text
    assert used_attempts(owner) == 3
    assert explanation_count(owner) == 3
    # The same-question guard must release on completion; the next ordinary
    # request sees the daily limit, rather than a leaked in-flight lock.
    exhausted = growth_client.post(path, json={"explanation": "已完成后第四次"})
    assert exhausted.status_code == 429, exhausted.text
    assert used_attempts(owner) == 3 and len(calls) == 1


def test_explain_problem_deleted_during_ai_returns_404_instead_of_500(growth_client, monkeypatch):
    owner = register(growth_client)["id"]
    problem = make_problem(growth_client)
    mid = problem["mistake_ids"][0]
    entered = threading.Event()
    release = threading.Event()

    def blocked_ai(item, explanation):
        entered.set()
        assert release.wait(10), "test did not release the inert AI response"
        return dict(GOOD_SCORE)

    monkeypatch.setattr(explain_routes, "explain_ai_score", blocked_ai)
    # Observe the HTTP status even if the old implementation raises a raw
    # sqlite foreign-key exception; this caller shares the mocked app/database.
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"},
                    cookies=growth_client.cookies, raise_server_exceptions=False) as caller:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(caller.post, f"/api/mistakes/{mid}/explain", json={"explanation": "途中删除题目"})
            try:
                assert entered.wait(5), "request never entered the fake AI"
                deleted = growth_client.delete(f"/api/problems/{problem['id']}")
                assert deleted.status_code == 200, deleted.text
            finally:
                release.set()
                response = pending.result(timeout=5)
    assert response.status_code == 404, response.text
    assert explanation_count(owner) == 0
