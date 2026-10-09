"""N2 笔记图片附件：迁移 71、上传（魔数识别/重编码/限额/孤儿清理）、
读取（响应头）、删除、越权、注销清理、导出清单。"""
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

import db
import main
from db import connect
from routers import note_files
from test_app import client, register


def image_bytes(fmt="PNG", size=(8, 8), color=(200, 30, 30), **kwargs):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def animated_gif_bytes():
    first = Image.new("RGB", (8, 8), (255, 0, 0))
    second = Image.new("RGB", (8, 8), (0, 0, 255))
    buf = io.BytesIO()
    first.save(buf, format="GIF", save_all=True, append_images=[second], loop=0, duration=100)
    return buf.getvalue()


def upload(client, data, name="x.png", mime="image/png"):
    return client.post(
        "/api/notes/attachments",
        files={"file": (name, data, mime)},
    )


# ── 迁移 71 ──

def test_migration_71_fresh_database(client):
    register(client)
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "note_attachments" in tables
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(note_attachments)")}
        assert cols == {"id", "user_id", "note_id", "sha256", "mime", "size_bytes", "created_at"}
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(note_attachments)")}
        assert {"idx_note_attachments_user", "idx_note_attachments_note"} <= indexes


def test_migration_upgrade_from_70_preserves_data(monkeypatch, tmp_path):
    # 先用只到 70 的迁移建旧库并造数据，再恢复完整迁移升级（照抄既有 18→ 升级用例套路）。
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "old.db"))
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 70])
        patch.setattr(db, "SCHEMA_VERSION", 70)
        db.init_db()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id,username,password_hash,timezone,created_at) "
            "VALUES (9,'up70','x','Asia/Shanghai','2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO notes(user_id,title,content,tags,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (9, "旧笔记", "N1 时代的正文", "", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
    db.init_db()
    db.init_db()  # 重复启动幂等
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
        assert conn.execute("SELECT title FROM notes").fetchone()[0] == "旧笔记"


# ── 上传：四种格式 + 魔数伪造 ──

@pytest.mark.parametrize("fmt,mime,name,expected", [
    ("PNG", "image/png", "a.png", "image/png"),
    ("JPEG", "image/jpeg", "a.jpg", "image/jpeg"),
    ("WEBP", "image/webp", "a.webp", "image/webp"),
    ("GIF", "image/gif", "a.gif", "image/gif"),
])
def test_upload_every_format_succeeds(client, fmt, mime, name, expected):
    register(client)
    response = upload(client, image_bytes(fmt=fmt), name=name, mime=mime)
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "url", "markdown"}
    assert body["url"] == f"/api/notes/attachments/{body['id']}"
    assert body["markdown"] == f"![](attachment:{body['id']})"


def test_forged_png_is_rejected_by_magic_bytes(client):
    register(client)
    # 扩展名/Content-Type 声称是 png，内容其实是文本——不信，按魔数识别。
    response = upload(client, b"this is not an image at all", name="evil.png", mime="image/png")
    assert response.status_code == 422
    assert "图片" in response.json()["detail"]


def test_empty_upload_is_rejected(client):
    register(client)
    response = upload(client, b"", name="empty.png")
    assert response.status_code in (422, 413)


# ── 重编码副作用：去 EXIF / GIF 取首帧 ──

def test_upload_strips_exif(client):
    register(client)
    buf = io.BytesIO()
    image = Image.new("RGB", (8, 8), (10, 200, 10))
    exif = Image.Exif()
    exif[271] = "Camera-X"  # Make
    image.save(buf, format="JPEG", exif=exif)
    response = upload(client, buf.getvalue(), name="with-exif.jpg")
    assert response.status_code == 201, response.text
    stored = Image.open(main.note_file_path(1, response.json()["id"]))
    assert stored.getexif().get(271) in (None, "")


def test_animated_gif_keeps_only_first_frame(client):
    register(client)
    response = upload(client, animated_gif_bytes(), name="ani.gif")
    assert response.status_code == 201, response.text
    stored = Image.open(main.note_file_path(1, response.json()["id"]))
    assert stored.n_frames == 1


# ── 限额 ──

def test_oversize_single_image_is_413(client):
    register(client)
    response = upload(client, b"x" * (3 * 1024 * 1024 + 10))
    assert response.status_code == 413
    assert "3MB" in response.json()["detail"]


def test_total_user_quota_is_enforced(client, monkeypatch):
    register(client)
    monkeypatch.setattr(note_files, "NOTE_ATTACHMENT_TOTAL_BYTES", 120)
    first = upload(client, image_bytes())
    assert first.status_code == 201
    second = upload(client, image_bytes())
    assert second.status_code == 413
    assert "100MB" in second.json()["detail"]


def test_per_note_attachment_ref_limit_is_20(client):
    register(client)
    refs = " ".join(f"![](attachment:{i})" for i in range(21))
    response = client.post("/api/notes", json={"content": refs})
    assert response.status_code == 422
    assert "20" in response.json()["detail"]
    # 20 张放行
    refs20 = " ".join(f"![](attachment:{i})" for i in range(20))
    ok = client.post("/api/notes", json={"content": refs20})
    assert ok.status_code == 201
    # 代码块里的引用不算数
    code_refs = "```\n" + "\n".join(f"![](attachment:{i})" for i in range(30)) + "\n```"
    ok2 = client.post("/api/notes", json={"content": code_refs})
    assert ok2.status_code == 201


# ── GET / DELETE / 越权 ──

def test_get_attachment_headers_and_body(client):
    register(client)
    body = upload(client, image_bytes()).json()
    response = client.get(body["url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.headers["content-disposition"] == "inline"
    assert Image.open(io.BytesIO(response.content)).format == "PNG"


def test_delete_attachment(client):
    register(client)
    body = upload(client, image_bytes()).json()
    path = main.note_file_path(1, body["id"])
    assert path.is_file()
    deleted = client.delete(body["url"])
    assert deleted.status_code == 200
    assert not path.is_file()
    assert client.get(body["url"]).status_code == 404


def test_other_user_cannot_access_or_delete(client):
    register(client, "alice")
    body = upload(client, image_bytes()).json()
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.get(body["url"]).status_code == 404
    assert client.delete(body["url"]).status_code == 404


# ── 孤儿清理 ──

def test_orphan_older_than_24h_without_reference_is_cleaned(client):
    register(client)
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    keep = upload(client, image_bytes(color=(1, 1, 1))).json()
    orphan = upload(client, image_bytes(color=(2, 2, 2))).json()
    # keep 被笔记正文引用；orphan 无引用。
    client.post("/api/notes", json={"content": f"![](attachment:{keep['id']})"})
    with connect(write=True) as conn:
        conn.execute("UPDATE note_attachments SET created_at=? WHERE id=?", (old, keep["id"]))
        conn.execute("UPDATE note_attachments SET created_at=? WHERE id=?", (old, orphan["id"]))
    # 新一次上传触发清理：orphan 应被删行删文件，keep 因被引用保留。
    trigger = upload(client, image_bytes(color=(3, 3, 3)))
    assert trigger.status_code == 201
    assert not main.note_file_path(1, orphan["id"]).exists()
    assert main.note_file_path(1, keep["id"]).exists()
    with connect() as conn:
        rows = {r["id"] for r in conn.execute("SELECT id FROM note_attachments")}
    assert orphan["id"] not in rows
    assert keep["id"] in rows


def test_fresh_attachment_is_never_cleaned(client):
    register(client)
    fresh = upload(client, image_bytes()).json()
    trigger = upload(client, image_bytes())
    assert trigger.status_code == 201
    assert main.note_file_path(1, fresh["id"]).exists()


# ── 注销账号清理 ──

def test_account_deletion_removes_rows_and_files(client):
    register(client)
    body = upload(client, image_bytes()).json()
    path = main.note_file_path(1, body["id"])
    assert path.is_file()
    response = client.post("/api/me/delete-account", json={"password": "a-test-password-123"})
    assert response.status_code == 200
    assert not path.exists()
    with connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM note_attachments").fetchone()[0]
    assert rows == 0


# ── 导出清单 ──

def test_export_lists_attachments_without_binary(client):
    register(client)
    image = upload(client, image_bytes(fmt="JPEG"), name="x.jpg").json()
    note = client.post("/api/notes", json={
        "content": f"正文\n![](attachment:{image['id']})",
    }).json()
    payload = client.get("/api/export").json()
    exported = next(n for n in payload["notes"] if n["id"] == note["id"])
    assert exported["attachments"] == [{
        "id": image["id"],
        "filename": f"attachment-{image['id']}.jpg",
        "mime": "image/jpeg",
        "size_bytes": len(main.note_file_path(1, image["id"]).read_bytes()),
    }]
