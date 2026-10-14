"""笔记历史版本、回收站、保存冲突（迁移 87，routers/note_history.py）。"""
from datetime import datetime, timedelta, timezone

import pytest

import db
from core import time
from test_app import client, register  # noqa: F401

PASSWORD = "a-test-password-123"


@pytest.fixture
def clock(monkeypatch):
    """可拨动的 UTC 时钟：版本合并按 10 分钟算，测试里手动往前拨。"""
    state = {"now": datetime(2026, 10, 14, 8, 0, tzinfo=timezone.utc)}

    def now():
        return state["now"].isoformat(timespec="seconds")

    monkeypatch.setattr(time, "utc_now", now)

    def advance(minutes):
        state["now"] += timedelta(minutes=minutes)

    return advance


def make_note(client, content="第一版正文", title="二分笔记"):
    response = client.post("/api/notes", json={"title": title, "content": content, "tags": ["边界"]})
    assert response.status_code == 201, response.text
    return response.json()


def put(client, note_id, **body):
    return client.put(f"/api/notes/{note_id}", json=body)


def revisions(client, note_id):
    response = client.get(f"/api/notes/{note_id}/revisions")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_edits_keep_the_previous_version_and_autosaves_within_ten_minutes_coalesce(client, clock):
    register(client)
    note = make_note(client)
    clock(1)
    assert put(client, note["id"], content="第二版").status_code == 200
    clock(1)
    assert put(client, note["id"], content="第三版").status_code == 200  # 10 分钟内：不再多存
    items = revisions(client, note["id"])
    assert [item["preview"] for item in items] == ["第一版正文"]
    clock(11)
    assert put(client, note["id"], content="第四版").status_code == 200
    assert [item["preview"] for item in revisions(client, note["id"])] == ["第三版", "第一版正文"]


def test_pin_or_unchanged_save_does_not_create_a_version(client, clock):
    register(client)
    note = make_note(client)
    clock(30)
    assert put(client, note["id"], pinned=True).status_code == 200
    assert put(client, note["id"], content="第一版正文", tags=["边界"]).status_code == 200
    assert revisions(client, note["id"]) == []


def test_restoring_a_version_is_itself_undoable(client, clock):
    register(client)
    note = make_note(client)
    clock(1)
    put(client, note["id"], content="改坏了的正文", title="新标题")
    (first,) = revisions(client, note["id"])
    detail = client.get(f"/api/notes/{note['id']}/revisions/{first['id']}").json()
    assert detail["content"] == "第一版正文" and detail["title"] == "二分笔记" and detail["tags"] == ["边界"]
    clock(1)  # 仍在 10 分钟内，但恢复一定先把当前内容存一份
    restored = client.post(f"/api/notes/{note['id']}/revisions/{first['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["content"] == "第一版正文" and restored.json()["title"] == "二分笔记"
    previews = [item["preview"] for item in revisions(client, note["id"])]
    assert previews[0] == "改坏了的正文"


def test_versions_are_capped_at_fifty_per_note(client, clock):
    register(client)
    note = make_note(client)
    for index in range(55):
        clock(11)
        assert put(client, note["id"], content=f"第 {index} 次").status_code == 200
    items = revisions(client, note["id"])
    assert len(items) == 50
    assert items[0]["preview"] == "第 53 次"


def test_stale_base_updated_at_is_rejected_instead_of_overwriting(client, clock):
    register(client)
    note = make_note(client)
    clock(1)
    fresh = put(client, note["id"], content="另一个窗口改的", base_updated_at=note["updated_at"])
    assert fresh.status_code == 200
    clock(1)
    stale = put(client, note["id"], content="旧窗口的内容", base_updated_at=note["updated_at"])
    assert stale.status_code == 409
    assert "别处" in stale.json()["detail"]
    assert client.get(f"/api/notes/{note['id']}").json()["content"] == "另一个窗口改的"
    ok = put(client, note["id"], content="看过最新后再改", base_updated_at=fresh.json()["updated_at"])
    assert ok.status_code == 200


def test_deleted_notes_go_to_trash_and_can_be_restored_with_links_reconnected(client):
    register(client)
    target = make_note(client, title="单调栈", content="单调栈的正文")
    source = make_note(client, title="索引", content="见 [[单调栈]]")
    assert client.delete(f"/api/notes/{target['id']}").status_code == 200
    trash = client.get("/api/notes/trash").json()["items"]
    assert [item["id"] for item in trash] == [target["id"]]
    assert trash[0]["title"] == "单调栈" and trash[0]["deleted_at"]
    links = client.get(f"/api/notes/{source['id']}/links").json()["outgoing"]
    assert links[0]["exists"] is False
    restored = client.post(f"/api/notes/{target['id']}/restore")
    assert restored.status_code == 200 and restored.json()["id"] == target["id"]
    assert client.get("/api/notes/trash").json()["items"] == []
    links = client.get(f"/api/notes/{source['id']}/links").json()["outgoing"]
    assert links[0]["exists"] is True and links[0]["target_note_id"] == target["id"]


def test_purge_only_works_from_the_trash_and_removes_versions(client, clock):
    register(client)
    note = make_note(client)
    clock(1)
    put(client, note["id"], content="第二版")
    assert client.delete(f"/api/notes/{note['id']}/purge").status_code == 404, "没进回收站不能彻底删除"
    client.delete(f"/api/notes/{note['id']}")
    assert client.delete(f"/api/notes/{note['id']}/purge").status_code == 200
    assert client.get("/api/notes/trash").json()["items"] == []
    assert client.post(f"/api/notes/{note['id']}/restore").status_code == 404
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_revisions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0


def test_history_and_trash_are_private(client, clock):
    register(client)
    note = make_note(client)
    clock(1)
    put(client, note["id"], content="第二版")
    (revision,) = revisions(client, note["id"])
    client.delete(f"/api/notes/{note['id']}")
    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.get("/api/notes/trash").json()["items"] == []
    for method, path in (
        ("get", f"/api/notes/{note['id']}/revisions"),
        ("get", f"/api/notes/{note['id']}/revisions/{revision['id']}"),
        ("post", f"/api/notes/{note['id']}/revisions/{revision['id']}/restore"),
        ("post", f"/api/notes/{note['id']}/restore"),
        ("delete", f"/api/notes/{note['id']}/purge"),
    ):
        assert getattr(client, method)(path).status_code == 404, path


def test_writes_require_csrf_header(client):
    register(client)
    note = make_note(client)
    client.delete(f"/api/notes/{note['id']}")
    client.headers.pop("X-CSRF-Protection")
    assert client.post(f"/api/notes/{note['id']}/restore").status_code == 403
    assert client.delete(f"/api/notes/{note['id']}/purge").status_code == 403


def test_export_includes_versions_and_account_deletion_removes_them(client, clock):
    register(client)
    note = make_note(client)
    clock(1)
    put(client, note["id"], content="第二版")
    exported = client.get("/api/export").json()
    (saved,) = exported["notes"]
    assert [item["content"] for item in saved["revisions"]] == ["第一版正文"]
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code in (200, 204), response.text
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_revisions").fetchone()[0] == 0
