"""举一反三（GET /api/review/similar）的后端测试。

router 挂在独立 FastAPI() 上测，不经过 main.app：Task C 的推荐查询用
monkeypatch  mock 掉；DB 用 tmp_path 里的 sqlite（跑测试时 TMPDIR 必须在仓库外，
例如 TMPDIR=~/workspace/pytest-tmp）；DB 隔离方式仿照 tests/test_quick_record.py。
"""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main
import routers.mistakes
import routers.problems
from db import init_db
from routers import similar

TODAY = date(2026, 9, 19)

# POST /api/problems 允许的字段（InputModel extra="forbid"）。
PROBLEM_FIELDS = ("title", "zone", "language", "code", "thinking", "mistakes", "quick")


def _make_user(conn, uid, username):
    conn.execute(
        "INSERT INTO users(id, username, password_hash, timezone, created_at)"
        " VALUES (?,?,?,?,?)",
        (uid, username, "x", "Asia/Shanghai", "2026-09-19T00:00:00+00:00"),
    )
    conn.commit()
    return {"id": uid, "username": username, "timezone": "Asia/Shanghai"}


def _make_problem(conn, user_id, title, tags=()):
    cursor = conn.execute(
        "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (user_id, title, "算法", "Python", "code", "thinking", "2026-09-19T00:00:00+00:00"),
    )
    problem_id = cursor.lastrowid
    cursor = conn.execute(
        "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?,?,?)",
        (problem_id, "错因", "2026-09-19"),
    )
    mistake_id = cursor.lastrowid
    for tag in tags:
        conn.execute(
            "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) VALUES (?,?,?,?)",
            (mistake_id, user_id, tag, "2026-09-19T00:00:00+00:00"),
        )
    conn.commit()
    return problem_id, mistake_id


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "similar.db"))
    monkeypatch.setattr(main, "today_for", lambda user: TODAY)
    main.reset_rate_limits()
    init_db()

    app = FastAPI()
    app.include_router(similar.router)
    app.include_router(routers.problems.router)
    app.include_router(routers.mistakes.router)
    state = {"current": None}
    app.dependency_overrides[main.current_user] = lambda: state["current"]
    with TestClient(app) as client:
        yield client, state


def _cf_item(name, tags, contest_id=1000, idx="A"):
    return {
        "id": f"{contest_id}{idx}",
        "contest_id": contest_id,
        "idx": idx,
        "name": name,
        "rating": 1000,
        "tags": list(tags),
        "url": f"https://codeforces.com/problemset/problem/{contest_id}/{idx}",
        "reason": "理由",
        "state": "new",
    }


def test_other_user_mistake_404(app_client, monkeypatch):
    client, state = app_client
    monkeypatch.setattr(
        similar.recommend_logic, "recommend_for_today", lambda conn, user, today: ([], "")
    )
    with main.connect() as conn:
        alice = _make_user(conn, 1, "alice")
        _make_user(conn, 2, "bob")
        _, bob_mid = _make_problem(conn, 2, "bob 的题", tags=("动态规划",))
    state["current"] = alice

    # 他人的错题：404。
    response = client.get(f"/api/review/similar?mistake_id={bob_mid}")
    assert response.status_code == 404
    # 不存在的错题：同样 404，不区分。
    response = client.get("/api/review/similar?mistake_id=99999")
    assert response.status_code == 404


def test_site_fillers_when_cf_short(app_client, monkeypatch):
    client, state = app_client
    # CF 推荐只给 1 道（同标签），剩 2 道必须用站内同标签题补齐。
    monkeypatch.setattr(
        similar.recommend_logic,
        "recommend_for_today",
        lambda conn, user, today: ([_cf_item("CF DP 题", ["dp"])], ""),
    )
    with main.connect() as conn:
        alice = _make_user(conn, 1, "alice")
        _, my_mid = _make_problem(conn, 1, "我的 DP 题", tags=("动态规划",))
        _make_problem(conn, 1, "站内 DP 题 1", tags=("动态规划",))
        _make_problem(conn, 1, "站内 DP 题 2", tags=("动态规划",))
        _make_problem(conn, 1, "站内图论题", tags=("图论",))
    state["current"] = alice

    response = client.get(f"/api/review/similar?mistake_id={my_mid}")
    assert response.status_code == 200
    body = response.json()
    assert body["tags"] == ["动态规划"]
    items = body["items"]
    assert len(items) == 3
    # CF 优先排前。
    assert items[0]["source"] == "codeforces"
    assert items[0]["title"] == "CF DP 题"
    assert items[0]["difficulty"] == 1000
    # 站内补齐：同标签、不含刚复习的题目、不含其他标签题。
    assert [item["source"] for item in items[1:]] == ["站内", "站内"]
    assert {item["title"] for item in items[1:]} == {"站内 DP 题 1", "站内 DP 题 2"}
    # 响应结构与一键加入所需的预填字段。
    for item in items:
        assert set(item) >= {"title", "source", "difficulty", "tags", "add_payload"}
        payload = item["add_payload"]
        assert payload["quick"] is True
        assert payload["source_tag"] == "举一反三"


def test_quick_add_end_to_end(app_client, monkeypatch):
    # CF 一道没有：全部由站内补齐；前端走 add_payload 一键建题成功。
    client, state = app_client
    monkeypatch.setattr(
        similar.recommend_logic, "recommend_for_today", lambda conn, user, today: ([], "")
    )
    with main.connect() as conn:
        alice = _make_user(conn, 1, "alice")
        _, my_mid = _make_problem(conn, 1, "我的 DP 题", tags=("动态规划",))
        _make_problem(conn, 1, "站内 DP 题", tags=("动态规划",))
    state["current"] = alice

    response = client.get(f"/api/review/similar?mistake_id={my_mid}")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["source"] == "站内"

    # 前端 quickAdd：剥掉 NewProblem 不认的字段（extra="forbid"），language 按偏好补齐。
    payload = items[0]["add_payload"]
    body = {key: payload[key] for key in PROBLEM_FIELDS if key in payload}
    body["language"] = "Python"
    response = client.post("/api/problems", json=body)
    assert response.status_code == 201
    new_mid = response.json()["mistake_ids"][0]

    # NewProblem 没有 source 字段：来源标记打在新建易错点的标签上。
    response = client.put(f"/api/mistakes/{new_mid}/tags", json={"tags": ["举一反三"]})
    assert response.status_code == 200
    assert response.json()["tags"] == ["举一反三"]


def test_no_tags_no_fillers(app_client, monkeypatch):
    # 错题没有标签：CF 查询返回空时也补不出站内题，返回空列表不报错。
    client, state = app_client
    monkeypatch.setattr(
        similar.recommend_logic, "recommend_for_today", lambda conn, user, today: ([], "")
    )
    with main.connect() as conn:
        alice = _make_user(conn, 1, "alice")
        _, my_mid = _make_problem(conn, 1, "无标签题")
    state["current"] = alice

    response = client.get(f"/api/review/similar?mistake_id={my_mid}")
    assert response.status_code == 200
    assert response.json()["items"] == []
