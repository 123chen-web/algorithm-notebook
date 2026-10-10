"""画板 API（迁移 72）测试：CRUD、越权 404、乐观锁 409、场景校验、
缩略图校验/重编码、配额、软删除、账号注销清理、数据导出。"""
import io
import os

import pytest
from PIL import Image

from test_app import client, register  # noqa: F401

PASSWORD = "a-test-password-123"


@pytest.fixture(autouse=True)
def isolate_drawing_dir(tmp_path, monkeypatch):
    # 缩略图绝不允许写进仓库真实数据目录。
    monkeypatch.setenv("DRAWING_DIR", str(tmp_path / "drawings"))


def login(client, username):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200


def make_note(client, **overrides):
    payload = {"content": "一条笔记"}
    payload.update(overrides)
    response = client.post("/api/notes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_drawing(client, **overrides):
    payload = {"title": "我的画板"}
    payload.update(overrides)
    response = client.post("/api/drawings", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def scene(elements=None, **extra):
    data = {
        "type": "excalidraw",
        "version": 1,
        "source": "https://excalidraw.com",
        "elements": elements if elements is not None else [],
        "appState": {},
        "files": {},
    }
    data.update(extra)
    return data


def png_bytes(size=(900, 600), noise=False):
    buf = io.BytesIO()
    image = Image.new("RGB", size, (255, 255, 255))
    if noise:
        # 随机噪声让 PNG 难以压缩，稳定超过 300KB。
        import random
        pixels = [
            (random.randrange(256), random.randrange(256), random.randrange(256))
            for _ in range(size[0] * size[1])
        ]
        image.putdata(pixels)
    image.save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()


def save_scene(client, drawing_id, payload, version=None):
    body = {"scene": payload, "version": version if version is not None else 1}
    return client.put(f"/api/drawings/{drawing_id}", json=body)


# ---------------------------------------------------------------- 基础流程

def test_create_list_get_and_update(client):
    register(client)
    created = create_drawing(client, title="草图")
    assert created["id"] > 0
    assert created["version"] == 1

    listed = client.get("/api/drawings").json()
    assert listed["total"] == 1
    item = listed["items"][0]
    assert item["title"] == "草图"
    assert item["version"] == 1
    assert "scene" not in item and "scene_json" not in item
    assert item["has_thumb"] is False

    detail = client.get(f"/api/drawings/{created['id']}").json()
    assert detail["scene"] is None  # 空场景

    payload = scene([{"id": "e1", "type": "rectangle"}])
    saved = save_scene(client, created["id"], payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2

    detail = client.get(f"/api/drawings/{created['id']}").json()
    assert detail["scene"]["elements"][0]["type"] == "rectangle"
    assert detail["version"] == 2


def test_requires_login(client):
    assert client.get("/api/drawings").status_code == 401
    assert client.post("/api/drawings", json={"title": "x"}).status_code == 401


def test_other_users_drawings_are_404(client):
    register(client)
    drawing = create_drawing(client)
    did = drawing["id"]
    save_scene(client, did, scene([{"id": "a", "type": "ellipse"}]))

    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.get("/api/drawings").json()["items"] == []
    assert client.get(f"/api/drawings/{did}").status_code == 404
    assert save_scene(client, did, scene()).status_code == 404
    assert client.patch(f"/api/drawings/{did}", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/drawings/{did}").status_code == 404
    assert client.get(f"/api/drawings/{did}/thumb").status_code == 404
    thumb_put = client.put(
        f"/api/drawings/{did}/thumb", content=png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    assert thumb_put.status_code == 404


def test_note_id_must_belong_to_me(client):
    register(client)
    note = make_note(client)
    ok = create_drawing(client, note_id=note["id"])
    assert ok["id"] > 0

    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.post(
        "/api/drawings", json={"title": "x", "note_id": note["id"]}
    ).status_code == 422
    assert client.post(
        "/api/drawings", json={"title": "x", "note_id": 999999}
    ).status_code == 422
    for bad in ("1", [1], True, 0, -3):
        response = client.post(
            "/api/drawings", json={"title": "x", "note_id": bad}
        )
        assert response.status_code == 422, bad


# ---------------------------------------------------------------- 乐观锁

def test_version_conflict_returns_409_chinese(client):
    register(client)
    did = create_drawing(client)["id"]
    assert save_scene(client, did, scene([{"id": "a", "type": "rectangle"}]), version=1).status_code == 200
    # 另一个标签页还拿着 version=1 保存。
    conflict = save_scene(client, did, scene([{"id": "b", "type": "rectangle"}]), version=1)
    assert conflict.status_code == 409
    assert "在别处被修改" in conflict.json()["detail"]
    for bad_version in (0, -1, "1", 1.5, True, None):
        response = client.put(
            f"/api/drawings/{did}", json={"scene": scene(), "version": bad_version}
        )
        assert response.status_code == 422, bad_version


# ---------------------------------------------------------------- 场景校验

def test_scene_must_be_object_with_elements_array(client):
    register(client)
    did = create_drawing(client)["id"]
    for bad in ("字符串", 42, [], None, {"elements": "x"}, {"elements": {}}, {}):
        response = save_scene(client, did, bad)
        assert response.status_code == 422, repr(bad)


def test_scene_rejects_embeddable_elements(client):
    register(client)
    did = create_drawing(client)["id"]
    for kind in ("embeddable", "iframe"):
        response = save_scene(client, did, scene([{"id": "x", "type": kind}]))
        assert response.status_code == 422
        assert "embeddable" in response.json()["detail"] or "嵌入网页" in response.json()["detail"]


def test_scene_rejects_more_than_5000_elements(client):
    register(client)
    did = create_drawing(client)["id"]
    too_many = [{"id": f"e{i}", "type": "rectangle"} for i in range(5001)]
    assert save_scene(client, did, scene(too_many)).status_code == 422
    edge = [{"id": f"e{i}", "type": "rectangle"} for i in range(5000)]
    assert save_scene(client, did, scene(edge)).status_code == 200


def test_scene_size_limit_includes_files(client):
    register(client)
    did = create_drawing(client)["id"]
    # files 内嵌图片同样计入 2MB 上限。
    oversized = scene([], files={"f1": {"mimeType": "image/png", "dataURL": "A" * (2 * 1024 * 1024 + 100)}})
    response = save_scene(client, did, oversized)
    assert response.status_code == 422
    assert "2MB" in response.json()["detail"]
    # files 必须是对象。
    assert save_scene(client, did, scene([], files=[])).status_code == 422


# ---------------------------------------------------------------- 缩略图

def test_thumb_validation_reencode_and_headers(client):
    import routers.drawings as drawings_routes

    register(client)
    did = create_drawing(client)["id"]
    url = f"/api/drawings/{did}/thumb"

    assert client.get(url).status_code == 404
    # 非 PNG 内容一律拒绝。
    assert client.put(
        url, content=b"not an image", headers={"Content-Type": "image/png"}
    ).status_code == 422
    assert client.put(
        url, content=jpeg_bytes(), headers={"Content-Type": "image/jpeg"}
    ).status_code == 422

    ok = client.put(url, content=png_bytes(), headers={"Content-Type": "image/png"})
    assert ok.status_code == 200, ok.text

    # 文件落在数据目录 drawings/<user_id>/<id>.png，且是服务端重编码的 PNG。
    owner = 1
    target = drawings_routes.drawing_thumb_path(owner, did)
    assert target.is_file()
    with Image.open(target) as check:
        assert check.format == "PNG"

    fetched = client.get(url)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    cache_control = fetched.headers["cache-control"]
    assert "private" in cache_control
    assert fetched.headers["x-content-type-options"] == "nosniff"


def test_thumb_oversized_rejected(client):
    register(client)
    did = create_drawing(client)["id"]
    big = png_bytes(size=(1400, 1400), noise=True)
    assert len(big) > 300 * 1024
    response = client.put(
        f"/api/drawings/{did}/thumb",
        content=big,
        headers={"Content-Type": "image/png"},
    )
    assert response.status_code == 413


# ---------------------------------------------------------------- 配额与删除

def test_quota_and_soft_delete(client, monkeypatch):
    import routers.drawings as drawings_routes

    register(client)
    monkeypatch.setattr(drawings_routes, "DRAWING_QUOTA", 2)
    first = create_drawing(client)
    create_drawing(client)
    response = client.post("/api/drawings", json={"title": "第三张"})
    assert response.status_code == 422
    assert "上限" in response.json()["detail"]

    # 软删除后配额释放，且列表/详情都不可见。
    assert client.delete(f"/api/drawings/{first['id']}").status_code == 200
    assert client.get(f"/api/drawings/{first['id']}").status_code == 404
    listed = client.get("/api/drawings").json()
    assert listed["total"] == 1  # 两张里软删除一张
    create_drawing(client)
    assert client.get("/api/drawings").json()["total"] == 2  # 软删除释放配额


def test_rename_drawing(client):
    register(client)
    did = create_drawing(client, title="旧标题")["id"]
    ok = client.patch(f"/api/drawings/{did}", json={"title": "新标题"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["title"] == "新标题"
    assert client.patch(f"/api/drawings/{did}", json={"title": "   "}).status_code == 422
    assert client.patch(
        f"/api/drawings/{did}", json={"title": "字" * 101}
    ).status_code == 422


# ---------------------------------------------------------------- 注销与导出

def test_account_deletion_removes_rows_and_thumb_files(client):
    import main
    from db import connect
    import routers.drawings as drawings_routes

    register(client)
    did = create_drawing(client, title="待注销")["id"]
    save_scene(client, did, scene([{"id": "a", "type": "diamond"}]))
    client.put(
        f"/api/drawings/{did}/thumb",
        content=png_bytes(),
        headers={"Content-Type": "image/png"},
    )
    with connect() as conn:
        owner = conn.execute("SELECT id FROM users WHERE username='alice'").fetchone()[0]
    target = drawings_routes.drawing_thumb_path(owner, did)
    assert target.is_file()

    with connect(write=True) as conn:
        main.delete_account_data(conn, owner, main.utc_now())

    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM note_drawings WHERE user_id=?", (owner,)
        ).fetchone()[0] == 0
    assert not target.exists()
    assert not target.parent.exists()


# ---------------------------------------------------------------- 迁移 72

def test_fresh_database_is_version_72(tmp_path, monkeypatch):
    import db
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "fresh72.db"))
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 73
        assert db.SCHEMA_VERSION == 73
        assert db.MIGRATIONS[-1][0] == 73
        versions = [version for version, _name, _fn in db.MIGRATIONS]
        assert versions == sorted(versions)
        assert len(versions) == len(set(versions))
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(note_drawings)")}
        assert cols == {
            "id", "user_id", "note_id", "title", "scene_json", "thumb_path",
            "version", "created_at", "updated_at", "deleted_at",
        }
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(note_drawings)")}
        assert "idx_note_drawings_user" in indexes


def test_migration_72_upgrades_v71_and_is_idempotent(client):
    import db
    register(client)
    make_note(client)
    did = create_drawing(client)["id"]
    save_scene(client, did, scene([{"id": "a", "type": "rectangle"}]))

    # 模拟一台停在 71 版的旧库：删掉 72 号表、把版本号拨回 71。
    with db.connect(write=True) as conn:
        conn.execute("DROP TABLE note_drawings")
        conn.execute("PRAGMA user_version = 71")
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='note_drawings'"
        ).fetchone() is None

    db.init_db()
    db.init_db()  # 重复启动幂等
    with db.connect() as conn:
        assert db.schema_version(conn) == 73
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='note_drawings'"
        ).fetchone() is not None
        # 旧库里的用户与笔记数据保留。
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1

    # 升级完成后接口立即可用。
    new_drawing = create_drawing(client)
    assert new_drawing["version"] == 1


def test_export_contains_drawings(client):
    register(client)
    note = make_note(client)
    did = create_drawing(client, title="导出画板", note_id=note["id"])["id"]
    save_scene(client, did, scene([{"id": "a", "type": "rectangle"}]))

    exported = client.get("/api/export").json()
    assert "drawings" in exported
    row = next(item for item in exported["drawings"] if item["id"] == did)
    assert row["title"] == "导出画板"
    assert isinstance(row["scene"], dict)
    assert row["scene"]["elements"][0]["type"] == "rectangle"
