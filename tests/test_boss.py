"""Boss 战（F5）的后端测试：池筛选、开场、胜负/毕业、结算。"""
import pytest

import main
import routers.boss
from db import connect
from test_app import client, new_problem, register


def add_reviews(mistake_id, qualities):
    with connect(write=True) as conn:
        for index, quality in enumerate(qualities):
            conn.execute(
                "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
                "VALUES (?, ?, ?, ?)",
                (
                    mistake_id,
                    quality,
                    f"2026-09-{10 + index:02d}T00:00:00+00:00",
                    f"2026-09-{11 + index:02d}T00:00:00+00:00",
                ),
            )


def start_session(client):
    response = client.post("/api/boss/session")
    assert response.status_code == 200
    return response.json()


def test_fail_count_below_threshold_not_in_pool(client):
    register(client)
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 1])  # 只失败 2 次，不够 3 次
    assert start_session(client) == {"empty": True}


def test_passing_reviews_do_not_count_as_fails(client):
    register(client)
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 1, 4, 5])  # 失败 2 次 + 答对 2 次
    assert start_session(client) == {"empty": True}


def test_fail_count_at_threshold_enters_pool(client):
    register(client)
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 1, 2])
    session = start_session(client)
    assert session["session_id"] > 0
    assert [r["mistake_id"] for r in session["rounds"]] == [mistake_id]
    assert session["rounds"][0]["title"] == "二分查找"


def test_pool_orders_by_due_date_and_caps_at_five(client):
    register(client)
    first, second = new_problem(client)
    add_reviews(first, [0, 0, 0])
    add_reviews(second, [1, 1, 1])
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = ? WHERE id = ?", ("2026-09-10", second))
        conn.execute("UPDATE mistakes SET due_date = ? WHERE id = ?", ("2026-09-18", first))
    session = start_session(client)
    # due 更早的排在前面。
    assert [r["mistake_id"] for r in session["rounds"]] == [second, first]


def test_pool_caps_at_five_rounds(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "多题",
            "zone": "算法",
            "language": "Python",
            "code": "pass",
            "thinking": "x",
            "mistakes": [f"错因{i}" for i in range(6)],
        },
    )
    assert response.status_code == 201
    for mistake_id in response.json()["mistake_ids"]:
        add_reviews(mistake_id, [0, 0, 0])
    session = start_session(client)
    assert len(session["rounds"]) == 5


def test_suspended_mistake_excluded_from_pool(client):
    register(client)
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 0, 0])
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET suspended_at = ? WHERE id = ?",
            (main.utc_now(), mistake_id),
        )
    assert start_session(client) == {"empty": True}


def test_other_users_mistakes_not_visible(client):
    register(client, "alice")
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 0, 0])
    client.post("/api/auth/logout")
    register(client, "bob")
    # bob 自己的池是空的，看不到 alice 的错题。
    assert start_session(client) == {"empty": True}
    # 往 alice 的场次提交也被挡掉。
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"}).status_code == 200
    session_id = start_session(client)["session_id"]
    client.post("/api/auth/logout")
    register(client, "carol")
    assert client.post(f"/api/boss/session/{session_id}/round", json={"mistake_id": mistake_id, "correct": True, "seconds": 10}).status_code == 404


def test_win_graduates_mistake(client):
    register(client)
    user_id = client.get("/api/me").json()["id"]
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 0, 0])
    session_id = start_session(client)["session_id"]

    response = client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": mistake_id, "correct": True, "seconds": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "win"
    assert body["graduated"] is True
    assert body["wins"] == 1 and body["losses"] == 0
    with connect() as conn:
        row = conn.execute(
            "SELECT graduated_at FROM boss_graduations WHERE user_id = ? AND mistake_id = ?",
            (user_id, mistake_id),
        ).fetchone()
        assert row is not None

    # 已毕业：新开一场不再出现。
    assert start_session(client) == {"empty": True}


def test_loss_does_not_graduate(client):
    register(client)
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 0, 0])
    session_id = start_session(client)["session_id"]

    response = client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": mistake_id, "correct": False, "seconds": 45},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "loss"
    assert body["graduated"] is False
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM boss_graduations").fetchone()[0] == 0

    # 没毕业：下次开场还在池里。
    session = start_session(client)
    assert [r["mistake_id"] for r in session["rounds"]] == [mistake_id]


def test_timeout_counts_as_loss(client):
    register(client)
    mistake_id = new_problem(client)[0]
    add_reviews(mistake_id, [0, 0, 0])
    session_id = start_session(client)["session_id"]

    response = client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": mistake_id, "correct": True, "seconds": 601},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "loss"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM boss_graduations").fetchone()[0] == 0


def test_round_validation(client):
    register(client)
    first, second = new_problem(client)
    add_reviews(first, [0, 0, 0])
    session_id = start_session(client)["session_id"]

    # 不在池里的题（失败次数不够）不能提交。
    assert client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": second, "correct": True, "seconds": 10},
    ).status_code == 422
    # 不存在的场次。
    assert client.post(
        "/api/boss/session/99999/round",
        json={"mistake_id": first, "correct": True, "seconds": 10},
    ).status_code == 404
    # 不存在/他人的错题。
    assert client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": 99999, "correct": True, "seconds": 10},
    ).status_code == 404
    # seconds 为负：422。
    assert client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": first, "correct": True, "seconds": -1},
    ).status_code == 422

    # 同一题重复提交：409。
    assert client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": first, "correct": True, "seconds": 10},
    ).status_code == 200
    assert client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": first, "correct": False, "seconds": 10},
    ).status_code == 409


def test_finish_settles_and_blocks_further_rounds(client):
    register(client)
    first, second = new_problem(client)
    add_reviews(first, [0, 0, 0])
    add_reviews(second, [1, 1, 1])
    session_id = start_session(client)["session_id"]

    client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": first, "correct": True, "seconds": 20},
    )
    client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": second, "correct": False, "seconds": 50},
    )
    response = client.post(f"/api/boss/session/{session_id}/finish")
    assert response.status_code == 200
    assert response.json() == {"wins": 1, "losses": 1}

    # 结算后不能再提交回合。
    assert client.post(
        f"/api/boss/session/{session_id}/round",
        json={"mistake_id": second, "correct": True, "seconds": 10},
    ).status_code == 409
    # 重复结算幂等，返回同样数字。
    again = client.post(f"/api/boss/session/{session_id}/finish")
    assert again.status_code == 200
    assert again.json() == {"wins": 1, "losses": 1}
    # 别人的场次不能结算。
    assert client.post("/api/boss/session/99999/finish").status_code == 404


def test_unauthenticated_requests_rejected(client):
    assert client.post("/api/boss/session").status_code == 401
