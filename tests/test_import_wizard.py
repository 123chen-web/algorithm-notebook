"""网页导入：持久预览、所有权、文件事务和重试。"""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import main
from db import connect
from test_app import client, register
from test_import_cuoti import cuoti


def question(title="二分边界"):
    return (f"## 题目 1：{title}\n\n**代码**\n\n```python\npass\n```\n\n"
            "**思路**\n\n维护区间。\n\n**易错点**\n\n- 空数组。\n")


def preview(client, content=None, filename="notes.md", zone="算法"):
    return client.post("/api/import/preview", data={"zone": zone},
                       files={"file": (filename, content or question(), "application/octet-stream")})


def confirm(client, token, indices=(0,)):
    return client.post("/api/import/confirm", json={"preview_id": token, "indices": list(indices)})


def counts():
    with connect() as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("problems", "mistakes"))


def test_preview_then_confirm_preserves_date_zone_and_no_notebook_write(client):
    register(client)
    response = preview(client, cuoti(), zone="后端")
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["title"] == "B3625 迷宫寻路"
    assert body["items"][0]["zone"] == "后端"
    assert counts() == (0, 0)
    response = confirm(client, body["preview_id"])
    assert response.status_code == 200
    assert response.json() == {"imported": 1, "duplicates": [], "failed": []}
    with connect() as conn:
        row = conn.execute("SELECT zone,created_at FROM problems").fetchone()
        assert dict(row) == {"zone": "后端", "created_at": "2026-09-23T12:00:00+00:00"}
        assert conn.execute("SELECT due_date FROM mistakes").fetchone()[0] == "2026-09-19"


def test_confirm_is_idempotent_and_stored_in_database(client):
    register(client)
    body = preview(client).json()
    first = confirm(client, body["preview_id"])
    assert first.status_code == 200
    assert confirm(client, body["preview_id"]).json() == first.json()
    with connect() as conn:
        result = conn.execute("SELECT result FROM import_previews WHERE token = ?", (body["preview_id"],)).fetchone()[0]
        assert json.loads(result) == first.json()
    assert counts() == (1, 1)
    assert confirm(client, body["preview_id"], (1,)).status_code == 409


def test_rechecks_duplicates_inside_confirm_and_selected_order(client):
    register(client)
    token = preview(client, question("A") + question("A") + question("B")).json()["preview_id"]
    assert confirm(client, token, (2, 1, 0, 2)).json() == {
        "imported": 2, "duplicates": [{"index": 1, "reason": "标题已存在，已跳过"}], "failed": []}
    assert counts() == (2, 2)


def test_two_previews_and_parallel_confirm_cannot_duplicate_titles(client):
    register(client)
    tokens = [preview(client).json()["preview_id"] for _ in range(2)]
    cookies = dict(client.cookies)
    def submit(token):
        with TestClient(main.app, cookies=cookies, headers={"X-CSRF-Protection": "1"}) as peer:
            return confirm(peer, token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, tokens))
    assert [r.status_code for r in responses] == [200, 200]
    assert sorted(r.json()["imported"] for r in responses) == [0, 1]
    assert counts() == (1, 1)


def test_confirm_rolls_back_whole_file_on_write_failure(client, monkeypatch):
    import import_notes
    register(client)
    token = preview(client, question("A") + question("B")).json()["preview_id"]
    original = import_notes.insert_record
    def fail_second(conn, record, *args):
        if record["title"] == "B":
            raise sqlite3.OperationalError("injected private detail")
        original(conn, record, *args)
    monkeypatch.setattr(import_notes, "insert_record", fail_second)
    response = confirm(client, token, (0, 1))
    assert response.status_code == 503
    assert "injected" not in response.text
    assert counts() == (0, 0)
    with connect() as conn:
        assert conn.execute("SELECT result FROM import_previews").fetchone()[0] is None


def test_expired_cross_account_and_banned_previews_are_rejected(client):
    first = register(client)
    token = preview(client).json()["preview_id"]
    register(client, "bob")
    assert confirm(client, token).status_code == 410
    with connect(write=True) as conn:
        conn.execute("UPDATE import_previews SET expires_at = 0 WHERE token = ?", (token,))
    client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"})
    assert confirm(client, token).status_code == 410
    fresh = preview(client).json()["preview_id"]
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (first["id"],))
    assert confirm(client, fresh).status_code == 401
    assert counts() == (0, 0)


def test_preview_limit_per_user_and_account_erasure(client):
    user = register(client)
    for _ in range(3):
        assert preview(client).status_code == 200
    assert preview(client).status_code == 429
    with connect(write=True) as conn:
        main.delete_account_data(conn, user["id"], main.utc_now())
        assert conn.execute("SELECT COUNT(*) FROM import_previews").fetchone()[0] == 0


def test_completed_imports_release_active_preview_slots_without_losing_receipts(client):
    register(client)
    first = None
    for index in range(3):
        token = preview(client, question(f"题{index}")).json()["preview_id"]
        assert confirm(client, token).json()["imported"] == 1
        first = first or token
    assert preview(client, question("第四题")).status_code == 200
    assert confirm(client, first).json()["imported"] == 1
    assert counts() == (3, 3)


@pytest.mark.parametrize("filename", ["notes.md", "notes.markdown", "NOTES.MD"])
def test_markdown_extensions_match_frontend(client, filename):
    register(client)
    assert preview(client, filename=filename).status_code == 200


@pytest.mark.parametrize("content,filename,status", [
    (b"x" * (2 * 1024 * 1024 + 1), "big.md", 413),
    (b"\xff", "bad.md", 422), (b"\0", "bad.md", 422),
    (question(), "notes.txt", 415), (question() * 201, "notes.md", 413),
    ("not a notebook", "notes.md", 422),
], ids=["oversized", "bad-utf8", "nul", "extension", "too-many-records", "no-records"])
def test_invalid_uploads_rejected_before_preview_storage(client, content, filename, status):
    register(client)
    assert preview(client, content, filename).status_code == status
    assert counts() == (0, 0)


@pytest.mark.parametrize("indices", [[True], [-1], [99], [], ["0"]])
def test_confirm_rejects_invalid_indices(client, indices):
    register(client)
    token = preview(client).json()["preview_id"]
    assert confirm(client, token, indices).status_code == 422
    assert counts() == (0, 0)


def exported_problem(**overrides):
    return {"title": "导出题", "language": "Python", "code": "pass\n", "thinking": "维护区间。",
            "created_at": "2026-09-01T12:00:00+00:00", "mistakes": [
                {"description": "（待补）", "pending_reason": True, "tags": [], "reviews": []}], **overrides}


def test_export_json_retains_pending_flag_and_resets_schedule(client):
    register(client)
    token = preview(client, json.dumps({"problems": [exported_problem()]}, ensure_ascii=False), "export.json").json()["preview_id"]
    assert confirm(client, token).json()["imported"] == 1
    with connect() as conn:
        row = dict(conn.execute("SELECT description,pending_reason,repetitions,interval_days,due_date FROM mistakes").fetchone())
        assert row == {"description": main.QUICK_MISTAKE_PLACEHOLDER, "pending_reason": 1,
                       "repetitions": 0, "interval_days": 0, "due_date": "2026-09-19"}


@pytest.mark.parametrize("date", ["2026-02-31T12:00:00+00:00", "https://example.com", 123])
def test_invalid_json_original_dates_rejected(client, date):
    register(client)
    content = json.dumps({"problems": [exported_problem(created_at=date)]}, ensure_ascii=False)
    assert preview(client, content, "export.json").status_code == 422


def test_csrf_and_login_required(client):
    assert preview(client).status_code == 401
    register(client)
    assert client.post("/api/import/preview", files={"file": ("n.md", question())},
                       headers={"X-CSRF-Protection": "0"}).status_code == 403


def test_chunked_oversized_body_rejected_before_multipart_spooling(client, monkeypatch):
    import starlette.formparsers
    register(client)
    def no_spooling(*args, **kwargs):
        pytest.fail("oversized request reached multipart temporary-file parsing")
    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", no_spooling)
    def chunks():
        yield b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="big.md"\r\n\r\n'
        for _ in range(34):
            yield b"x" * 65536
        yield b"\r\n--boundary--\r\n"
    response = client.post("/api/import/preview", content=chunks(),
                           headers={"Content-Type": "multipart/form-data; boundary=boundary"})
    assert response.status_code == 413
    assert counts() == (0, 0)
