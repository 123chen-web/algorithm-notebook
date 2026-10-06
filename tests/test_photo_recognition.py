"""拍照上传识别题目：图片校验复用头像那套安全标准，AI 调用走独立的
photo 配额扣减路径（跟生成练习题共用同一张 ai_usage 表和同一套原子扣减逻辑）。
"""
import io

import pytest
from PIL import Image

import ai
import main
from db import connect
from test_app import client, register


def make_image_bytes(fmt="JPEG", size=(400, 300), color=(200, 100, 50), mode="RGB"):
    image = Image.new(mode, size, color)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def upload_photo(client, content, filename="photo.jpg", content_type="image/jpeg"):
    return client.post(
        "/api/problems/photo",
        files={"file": (filename, content, content_type)},
    )


FAKE_RECOGNITION = {
    "zone": "算法",
    "title": "二分查找边界",
    "language": "Python",
    "code": "【题目原文】\n给定有序数组，找目标值。\n\n【原始代码 / 解题过程】\ndef search(a, t):\n    return -1\n",
    "thinking": "以为左闭右开区间，实际写成了左闭右闭。",
    "description": "循环结束条件写反了",
}


def test_recognize_photo_returns_fields_and_consumes_one_ai_use(client, monkeypatch):
    account = register(client)
    calls = []

    def fake_recognize(jpeg_bytes):
        calls.append(jpeg_bytes)
        return dict(FAKE_RECOGNITION)

    monkeypatch.setattr(ai, "recognize_photo", fake_recognize)

    response = upload_photo(client, make_image_bytes())
    assert response.status_code == 200
    assert response.json() == FAKE_RECOGNITION
    assert len(calls) == 1
    # 传给 AI 的必须是重新编码后的 JPEG 字节，不是原始上传内容本身。
    reencoded = Image.open(io.BytesIO(calls[0]))
    assert reencoded.format == "JPEG"

    me = client.get("/api/me").json()
    assert me["ai_daily_used"] == 1


def test_recognized_fields_are_accepted_by_create_problem_unchanged(client, monkeypatch):
    # 识别结果要能直接喂给已有的新增记录接口，不需要额外转换。
    register(client)
    monkeypatch.setattr(ai, "recognize_photo", lambda jpeg_bytes: dict(FAKE_RECOGNITION))
    fields = upload_photo(client, make_image_bytes()).json()
    description = fields.pop("description")

    # /api/problems 的 description 是通过 mistakes 列表传入的，不是顶层字段；
    # 前端预填表单时也必须这样映射，不能把识别结果原样透传。
    response = client.post(
        "/api/problems",
        json={**fields, "mistakes": [description] if description else []},
    )
    assert response.status_code == 201, response.json()


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
    response = upload_photo(client, content, filename, content_type)
    assert response.status_code in (400, 422)


def test_rejects_disallowed_but_valid_image_format(client):
    register(client)
    # BMP 是真实、能被 Pillow 正确解码的图片格式，但不在允许列表里。
    response = upload_photo(client, make_image_bytes(fmt="BMP"), "a.bmp", "image/bmp")
    assert response.status_code == 400


def test_rejects_oversized_upload(client, monkeypatch):
    monkeypatch.setattr(main, "PHOTO_MAX_BYTES", 100)
    register(client)
    response = upload_photo(client, make_image_bytes(size=(400, 400)))
    assert response.status_code == 413


def test_rejects_decompression_bomb_as_bad_image_not_500(client, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    register(client)
    response = upload_photo(client, make_image_bytes(size=(200, 200)))
    assert response.status_code == 400


def test_large_photo_is_downscaled_but_keeps_aspect_ratio(client, monkeypatch):
    register(client)
    captured = {}

    def fake_recognize(jpeg_bytes):
        captured["image"] = Image.open(io.BytesIO(jpeg_bytes))
        return dict(FAKE_RECOGNITION)

    monkeypatch.setattr(ai, "recognize_photo", fake_recognize)
    upload_photo(client, make_image_bytes(size=(4000, 2000)))

    width, height = captured["image"].size
    assert max(width, height) == main.PHOTO_MAX_DIMENSION
    assert abs(width / height - 2) < 0.01


def test_quota_exhausted_returns_429_and_never_calls_ai(client, monkeypatch):
    account = register(client)
    calls = []
    monkeypatch.setattr(ai, "recognize_photo", lambda jpeg_bytes: calls.append(1) or dict(FAKE_RECOGNITION))
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, ?)",
            (account["id"], "2026-09-19", 2),
        )

    response = upload_photo(client, make_image_bytes())
    assert response.status_code == 429
    assert calls == []


def test_quota_is_still_consumed_when_photo_has_no_recognizable_content(client, monkeypatch):
    # 识别不出有效内容属于用户内容问题（422），不退额度；只有 AI 服务端失败（502/503/504）才退还。
    account = register(client)

    def refuse(jpeg_bytes):
        from fastapi import HTTPException
        raise HTTPException(422, "未能从图片中识别出清晰、有效的学习内容")

    monkeypatch.setattr(ai, "recognize_photo", refuse)
    response = upload_photo(client, make_image_bytes())
    assert response.status_code == 422

    me = client.get("/api/me").json()
    assert me["ai_daily_used"] == 1


def test_missing_api_key_returns_503_without_consuming_quota(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    register(client)
    response = upload_photo(client, make_image_bytes())
    assert response.status_code == 503

    me = client.get("/api/me").json()
    assert me["ai_daily_used"] == 0


def test_trial_account_can_recognize_photo_within_its_own_smaller_quota(client, monkeypatch):
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "1")
    trial = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).json()
    monkeypatch.setattr(ai, "recognize_photo", lambda jpeg_bytes: dict(FAKE_RECOGNITION))

    first = upload_photo(client, make_image_bytes())
    assert first.status_code == 200
    second = upload_photo(client, make_image_bytes())
    assert second.status_code == 429
