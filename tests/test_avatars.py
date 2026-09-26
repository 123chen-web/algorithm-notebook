"""头像上传、展示和"先上线、事后举报"审核流程的测试。"""
import io

import pytest
from PIL import Image

import main
from db import connect
from test_app import client, register


@pytest.fixture(autouse=True)
def isolate_avatar_storage(tmp_path, monkeypatch):
    # 头像文件写在真实磁盘上，测试必须隔离到临时目录，
    # 不能碰项目里的 data/avatars。
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))


def make_image_bytes(fmt="JPEG", size=(400, 300), color=(200, 100, 50), mode="RGB"):
    image = Image.new(mode, size, color)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def upload_avatar(client, content, filename="avatar.jpg", content_type="image/jpeg"):
    return client.post(
        "/api/me/avatar",
        files={"file": (filename, content, content_type)},
    )


def test_upload_and_serve_round_trip(client):
    register(client)
    response = upload_avatar(client, make_image_bytes())
    assert response.status_code == 200
    assert response.json()["avatar_version"] == 1

    me = client.get("/api/me").json()
    assert me["avatar_version"] == 1

    fetched = client.get(f"/api/users/{me['id']}/avatar")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(fetched.content))
    image.load()
    assert image.format == "JPEG"
    assert image.size == (main.AVATAR_SIZE, main.AVATAR_SIZE)


def test_non_square_image_is_center_cropped_not_distorted(client):
    register(client)
    # 400x300：裁成 300x300 再缩放，不能被拉伸变形。
    upload_avatar(client, make_image_bytes(size=(400, 300)))
    me = client.get("/api/me").json()
    fetched = client.get(f"/api/users/{me['id']}/avatar")
    image = Image.open(io.BytesIO(fetched.content))
    assert image.size == (main.AVATAR_SIZE, main.AVATAR_SIZE)


def test_transparent_png_is_composited_onto_white_not_left_black(client):
    register(client)
    transparent = Image.new("RGBA", (200, 200), (10, 20, 30, 0))
    buffer = io.BytesIO()
    transparent.save(buffer, format="PNG")
    upload_avatar(client, buffer.getvalue(), filename="a.png", content_type="image/png")

    me = client.get("/api/me").json()
    fetched = client.get(f"/api/users/{me['id']}/avatar")
    image = Image.open(io.BytesIO(fetched.content)).convert("RGB")
    # 全透明区域合成到白底后应该接近纯白，而不是黑色或者原始的 (10,20,30)。
    pixel = image.getpixel((main.AVATAR_SIZE // 2, main.AVATAR_SIZE // 2))
    assert all(channel > 240 for channel in pixel)


@pytest.mark.parametrize(
    "content,filename,content_type",
    [
        (b"not an image at all", "a.jpg", "image/jpeg"),
        (b"\x00\x01\x02\x03", "a.png", "image/png"),
        (b"", "a.jpg", "image/jpeg"),
    ],
)
def test_rejects_non_image_content_regardless_of_claimed_type(
    client, content, filename, content_type
):
    register(client)
    response = upload_avatar(client, content, filename, content_type)
    assert response.status_code in (400, 422)


def test_rejects_disallowed_but_valid_image_format(client):
    register(client)
    # BMP 是真实、能被 Pillow 正确解码的图片格式，但不在允许列表里；
    # 必须按 image.format 校验，不能只看请求头或文件名后缀。
    response = upload_avatar(
        client, make_image_bytes(fmt="BMP"), "a.bmp", "image/bmp"
    )
    assert response.status_code == 400


def test_rejects_oversized_upload(client, monkeypatch):
    monkeypatch.setattr(main, "AVATAR_MAX_BYTES", 100)
    register(client)
    response = upload_avatar(client, make_image_bytes(size=(400, 400)))
    assert response.status_code == 413


def test_rejects_decompression_bomb_as_bad_image_not_500(client, monkeypatch):
    # 不用真的构造一张几万乘几万像素的图片去撑内存：把 Pillow 的像素上限
    # 临时调低，一张普通图片就会命中同一个 DecompressionBombError 分支，
    # 用来确认这个分支被正确归类成 400，不会变成没处理过的裸 500。
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    register(client)
    response = upload_avatar(client, make_image_bytes(size=(200, 200)))
    assert response.status_code == 400


def test_trial_account_cannot_upload_avatar(client):
    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    response = upload_avatar(client, make_image_bytes())
    assert response.status_code == 403


def test_viewing_avatar_requires_login(client):
    register(client, "alice")
    upload_avatar(client, make_image_bytes())
    me = client.get("/api/me").json()
    client.post("/api/auth/logout")
    assert client.get(f"/api/users/{me['id']}/avatar").status_code == 401


def test_user_without_avatar_returns_404(client):
    register(client)
    me = client.get("/api/me").json()
    assert client.get(f"/api/users/{me['id']}/avatar").status_code == 404


def test_delete_own_avatar(client):
    register(client)
    upload_avatar(client, make_image_bytes())
    me = client.get("/api/me").json()

    deleted = client.delete("/api/me/avatar")
    assert deleted.status_code == 200
    assert deleted.json()["avatar_version"] == 2
    assert client.get(f"/api/users/{me['id']}/avatar").status_code == 404


def test_reupload_reuses_same_user_scoped_path_no_leftover_files(client, tmp_path):
    register(client)
    upload_avatar(client, make_image_bytes(color=(10, 10, 10)))
    upload_avatar(client, make_image_bytes(color=(200, 200, 200)))
    avatar_dir = main.avatar_dir()
    files = [p for p in avatar_dir.iterdir() if p.is_file()]
    assert len(files) == 1


# ---- 举报（先上线，事后举报） ----


def report_avatar(client, user_id, reason="头像不合适"):
    return client.post(
        f"/api/users/{user_id}/avatar/report", json={"reason": reason}
    )


def become_admin(monkeypatch, username):
    monkeypatch.setenv("ADMIN_USERNAME", username)


def test_report_avatar_creates_pending_report_visible_to_admin(client, monkeypatch):
    register(client, "alice")
    upload_avatar(client, make_image_bytes())
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_avatar(client, alice_id, "违规图片").status_code == 201
    client.post("/api/auth/logout")

    register(client, "carol")
    become_admin(monkeypatch, "carol")
    reports = client.get("/api/admin/reports").json()["reports"]
    assert len(reports) == 1
    assert reports[0]["type"] == "avatar"
    assert reports[0]["avatar_owner_id"] == alice_id
    assert reports[0]["avatar_owner_username"] == "alice"
    assert reports[0]["reporter_username"] == "bob"
    assert reports[0]["reason"] == "违规图片"


def test_reports_of_different_types_are_merged_and_sorted(client, monkeypatch):
    register(client, "alice")
    upload_avatar(client, make_image_bytes())
    alice_id = client.get("/api/me").json()["id"]
    post = client.post("/api/posts", json={"title": "t", "body": "b"}).json()
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_avatar(client, alice_id).status_code == 201
    assert client.post(f"/api/posts/{post['id']}/report", json={"reason": "x"}).status_code == 201
    become_admin(monkeypatch, "bob")

    reports = client.get("/api/admin/reports").json()["reports"]
    assert {report["type"] for report in reports} == {"avatar", "post"}
    assert [report["created_at"] for report in reports] == sorted(
        report["created_at"] for report in reports
    )


def test_cannot_report_own_avatar(client):
    register(client)
    me = client.get("/api/me").json()
    assert report_avatar(client, me["id"]).status_code == 400


def test_duplicate_pending_avatar_report_rejected(client):
    register(client, "alice")
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_avatar(client, alice_id).status_code == 201
    assert report_avatar(client, alice_id).status_code == 409


def test_avatar_report_rate_limited(client, monkeypatch):
    monkeypatch.setattr(main, "REPORT_LIMIT", 1)
    register(client, "alice")
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "bob")
    bob_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "carol")
    assert report_avatar(client, alice_id).status_code == 201
    assert report_avatar(client, bob_id).status_code == 429


def test_trial_account_cannot_report_avatar(client):
    register(client, "alice")
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert report_avatar(client, alice_id).status_code == 403


def test_admin_resolve_avatar_report(client, monkeypatch):
    register(client, "alice")
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_avatar(client, alice_id).status_code == 201
    become_admin(monkeypatch, "bob")
    report_id = client.get("/api/admin/reports").json()["reports"][0]["id"]

    resolved = client.post(f"/api/admin/avatar-reports/{report_id}/resolve")
    assert resolved.status_code == 200
    assert client.get("/api/admin/reports").json()["reports"] == []

    again = client.post(f"/api/admin/avatar-reports/{report_id}/resolve")
    assert again.status_code == 404


def test_admin_clear_avatar_removes_file_and_resolves_reports(client, monkeypatch):
    register(client, "alice")
    upload_avatar(client, make_image_bytes())
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "bob")
    report_avatar(client, alice_id)
    become_admin(monkeypatch, "bob")

    cleared = client.delete(f"/api/admin/users/{alice_id}/avatar")
    assert cleared.status_code == 200
    assert client.get(f"/api/users/{alice_id}/avatar").status_code == 404
    assert client.get("/api/admin/reports").json()["reports"] == []


def test_non_admin_cannot_resolve_or_clear_avatar(client):
    register(client, "alice")
    upload_avatar(client, make_image_bytes())
    alice_id = client.get("/api/me").json()["id"]
    client.post("/api/auth/logout")

    register(client, "bob")
    assert client.delete(f"/api/admin/users/{alice_id}/avatar").status_code == 403
    assert client.post("/api/admin/avatar-reports/1/resolve").status_code == 403
