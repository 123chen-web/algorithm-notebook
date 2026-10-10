"""题目的可选"正确代码"：新增、编辑、返回、迁移旧导入格式。"""
import sqlite3
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import db
import main
import routers.problems as problems_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 10, 10))
    main.init_db()
    with main.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at)"
            " VALUES ('alice', 'x', 'Asia/Shanghai', '2026-10-10T00:00:00+00:00')"
        )
    app = FastAPI()
    app.include_router(problems_router.router)
    app.dependency_overrides[main.current_user] = lambda: {
        "id": 1, "username": "alice", "timezone": "Asia/Shanghai", "is_admin": 0,
    }
    return TestClient(app)


def _body(**extra):
    body = {"title": "两数之和", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "想法", "mistakes": ["漏了边界"]}
    body.update(extra)
    return body


def test_create_and_edit_keep_correct_code(client):
    created = client.post("/api/problems", json=_body(correct_code="print(2)\n"))
    assert created.status_code == 201
    pid = created.json()["id"]
    with main.connect() as conn:
        row = conn.execute("SELECT correct_code FROM problems WHERE id = ?", (pid,)).fetchone()
    assert row["correct_code"] == "print(2)\n"

    edited = client.put(f"/api/problems/{pid}", json={
        k: v for k, v in _body(correct_code="print(3)").items() if k != "mistakes"})
    assert edited.status_code == 200
    assert edited.json()["correct_code"] == "print(3)"


def test_correct_code_is_optional_and_capped(client):
    created = client.post("/api/problems", json=_body())
    assert created.status_code == 201
    with main.connect() as conn:
        assert conn.execute("SELECT correct_code FROM problems").fetchone()[0] == ""
    too_long = client.post("/api/problems", json=_body(correct_code="x" * 40001))
    assert too_long.status_code == 422


def test_migration_moves_imported_correct_code_out_of_thinking(tmp_path):
    conn = sqlite3.connect(tmp_path / "m.db")
    conn.execute("CREATE TABLE problems (id INTEGER PRIMARY KEY, thinking TEXT NOT NULL)")
    conn.executemany("INSERT INTO problems VALUES (?, ?)", [
        (1, "【错误原因】\n忘了判界\n\n【正确代码】\n```cpp\nint main(){}\n```"),
        (2, "我写的【正确代码】只是一句话"),
        (3, "没有这个标记"),
    ])
    db._apply_correct_code(conn)
    rows = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, thinking, correct_code FROM problems")}
    assert rows[1] == ("【错误原因】\n忘了判界", "int main(){}\n")
    assert rows[2] == ("我写的【正确代码】只是一句话", "")
    assert rows[3] == ("没有这个标记", "")
