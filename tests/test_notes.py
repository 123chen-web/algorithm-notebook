"""记笔记 API 测试：CRUD、归属隔离、校验、筛选排序、软删除。"""
import pytest

from test_app import client, new_problem, register  # noqa: F401

PASSWORD = "a-test-password-123"


def login(client, username):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200


def make_note(client, **overrides):
    payload = {"title": "二分笔记", "content": "注意边界条件", "tags": ["边界"]}
    payload.update(overrides)
    response = client.post("/api/notes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_and_get_note(client):
    register(client)
    note = make_note(client, title="我的笔记", content="hello **world**")
    assert note["title"] == "我的笔记"
    assert note["content"] == "hello **world**"
    assert note["tags"] == ["边界"]
    assert note["pinned"] is False
    assert note["problem_id"] is None

    fetched = client.get(f"/api/notes/{note['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == note["id"]


def test_create_note_empty_content_422(client):
    register(client)
    for bad in ("", "   ", "\n\t "):
        response = client.post("/api/notes", json={"content": bad})
        assert response.status_code == 422, repr(bad)


def test_create_note_title_optional(client):
    register(client)
    note = make_note(client, title="")
    assert note["title"] == ""


def test_note_tags_normalized_and_limited(client):
    register(client)
    note = make_note(client, tags=["边界", "边界", "  粗心  ", "BOUNDARY", "boundary"])
    assert note["tags"] == ["边界", "粗心", "BOUNDARY"]

    response = client.post(
        "/api/notes", json={"content": "x", "tags": [f"标签{i}" for i in range(11)]}
    )
    assert response.status_code == 422

    response = client.post("/api/notes", json={"content": "x", "tags": ["一" * 21]})
    assert response.status_code == 422


def test_note_tags_strip_commas(client):
    register(client)
    note = make_note(client, tags=["a,b", "c"])
    assert note["tags"] == ["ab", "c"]


def test_other_user_notes_are_404(client):
    register(client)
    note = make_note(client)
    note_id = note["id"]

    client.post("/api/auth/logout")
    register(client, username="bob")

    assert client.get(f"/api/notes/{note_id}").status_code == 404
    assert client.put(f"/api/notes/{note_id}", json={"title": "hack"}).status_code == 404
    assert client.delete(f"/api/notes/{note_id}").status_code == 404
    assert client.get("/api/notes").json() == {"notes": [], "total": 0}


def test_problem_id_must_belong_to_me(client):
    register(client)
    problem_id = client.post(
        "/api/problems",
        json={
            "title": "我的题", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["m"],
        },
    ).json()["id"]

    note = make_note(client, problem_id=problem_id)
    assert note["problem_id"] == problem_id
    assert note["problem_title"] == "我的题"

    client.post("/api/auth/logout")
    register(client, username="bob")
    response = client.post("/api/notes", json={"content": "x", "problem_id": problem_id})
    assert response.status_code == 422

    response = client.post("/api/notes", json={"content": "x", "problem_id": 999999})
    assert response.status_code == 422


def test_problem_deleted_sets_note_problem_null(client):
    register(client)
    problem_id = client.post(
        "/api/problems",
        json={
            "title": "待删题", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["m"],
        },
    ).json()["id"]
    note = make_note(client, problem_id=problem_id)
    assert client.delete(f"/api/problems/{problem_id}").status_code == 200
    fetched = client.get(f"/api/notes/{note['id']}").json()
    assert fetched["problem_id"] is None
    assert fetched["problem_title"] is None


def test_update_note(client):
    register(client)
    note = make_note(client)
    updated = client.put(
        f"/api/notes/{note['id']}",
        json={"title": "新标题", "content": "新内容", "tags": ["新标签"], "pinned": True},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "新标题"
    assert body["content"] == "新内容"
    assert body["tags"] == ["新标签"]
    assert body["pinned"] is True
    assert body["updated_at"] >= note["updated_at"]


def test_delete_note_is_soft(client):
    register(client)
    note = make_note(client)
    assert client.delete(f"/api/notes/{note['id']}").status_code == 200
    assert client.get(f"/api/notes/{note['id']}").status_code == 404
    assert client.get("/api/notes").json() == {"notes": [], "total": 0}


def test_list_pinned_first_and_search_and_tag_filter(client):
    register(client)
    first = make_note(client, title="普通笔记", content="关于动态规划", tags=["dp"])
    second = make_note(client, title="置顶笔记", content="二分查找要点", tags=["二分"], pinned=True)
    make_note(client, title="另一条", content="毫不相关的内容", tags=["dp"])

    listed = client.get("/api/notes").json()
    assert listed["total"] == 3
    # 置顶在上，其余按 updated_at 倒序
    assert [n["id"] for n in listed["notes"]][0] == second["id"]

    searched = client.get("/api/notes", params={"q": "动态规划"}).json()
    assert [n["id"] for n in searched["notes"]] == [first["id"]]

    searched = client.get("/api/notes", params={"q": "二分"}).json()
    assert [n["id"] for n in searched["notes"]] == [second["id"]]

    tagged = client.get("/api/notes", params={"tag": "dp"}).json()
    assert tagged["total"] == 2

    page = client.get("/api/notes", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3
    assert len(page["notes"]) == 2
    page2 = client.get("/api/notes", params={"limit": 2, "offset": 2}).json()
    assert len(page2["notes"]) == 1

    by_problem = client.get("/api/notes", params={"problem_id": 123456}).json()
    assert by_problem == {"notes": [], "total": 0}


def test_list_problems_recent(client):
    register(client)
    assert client.get("/api/problems").json() == {"problems": []}
    first_id = client.post(
        "/api/problems",
        json={
            "title": "第一题", "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["m"],
        },
    ).json()["id"]
    second_id = client.post(
        "/api/problems",
        json={
            "title": "第二题", "zone": "高等数学", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["m"],
        },
    ).json()["id"]
    problems = client.get("/api/problems").json()["problems"]
    assert [p["id"] for p in problems] == [second_id, first_id]
    assert problems[0]["title"] == "第二题"

    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.get("/api/problems").json() == {"problems": []}


def test_explicit_null_unlinks_but_omitted_problem_keeps_link(client):
    register(client)
    from db import connect
    new_problem(client)
    with connect() as conn:
        problem_id = conn.execute("SELECT id FROM problems").fetchone()[0]
    note = make_note(client, problem_id=problem_id)
    url = f"/api/notes/{note['id']}"
    assert client.put(url, json={"title": "???"}).json()["problem_id"] == problem_id
    assert client.put(url, json={"problem_id": None}).json()["problem_id"] is None


def test_notes_count_cap_includes_soft_deleted_records(client, monkeypatch):
    import routers.notes
    register(client)
    monkeypatch.setattr(routers.notes, "NOTE_COUNT_MAX", 2)
    one = make_note(client)
    make_note(client)
    client.delete(f"/api/notes/{one['id']}")
    response = client.post("/api/notes", json={"content": "超限"})
    assert response.status_code == 422
    assert "笔记" in response.json()["detail"]


def test_export_notes_including_deleted_and_account_deletion_clears_them(client):
    from db import connect
    register(client)
    new_problem(client)
    with connect() as conn:
        problem_id = conn.execute("SELECT id FROM problems").fetchone()[0]
        owner = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    live = make_note(client, problem_id=problem_id)
    deleted = make_note(client, title="????")
    client.delete(f"/api/notes/{deleted['id']}")
    exported = client.get("/api/export").json()["notes"]
    assert {n["id"] for n in exported} == {live["id"], deleted["id"]}
    row = next(n for n in exported if n["id"] == live["id"])
    assert row["title"] == live["title"] and row["content"] == live["content"]
    assert row["tags"] == ["边界"] and row["problem_title"] == "二分查找"
    assert row["created_at"] and row["updated_at"]
    assert next(n for n in exported if n["id"] == deleted["id"])["deleted_at"]
    with connect(write=True) as conn:
        import main
        main.delete_account_data(conn, owner, main.utc_now())
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM notes WHERE user_id=?", (owner,)).fetchone()[0] == 0
