import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from core import config
from core import ratelimit
from models import ScratchPut


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    ratelimit.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def test_api_body_over_global_limit_gets_413(client):
    declared = str(config.API_MAX_BODY_BYTES + 1)
    response = client.post(
        "/api/auth/login", content=b"{}",
        headers={"Content-Length": declared, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_global_limit_leaves_room_for_photo_upload():
    assert config.API_MAX_BODY_BYTES > config.PHOTO_MAX_BYTES + 1024 * 1024


def test_body_under_limit_is_not_rejected_by_middleware(client):
    response = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert response.status_code != 413


def test_scratch_code_and_fixed_are_capped():
    # 模型层只挡极端大小（超过 100000 字符），40000 字符的精确上限由接口按合计返回 413。
    ScratchPut(version=0, code="x" * 100000, fixed="y" * 100000, table=None)
    for field in ("code", "fixed"):
        values = {"code": "", "fixed": "", field: "z" * 100001}
        with pytest.raises(ValidationError):
            ScratchPut(version=0, table=None, **values)


def _post_chunked(path, total_bytes):
    """直接在 ASGI 层发一个不带 Content-Length 的分块 multipart 上传。

    TestClient 会先把整个请求体读进内存再交给应用，看不出应用到底读了多少，
    所以这里手写 receive，记录应用实际拉走的字节数。
    """
    import asyncio

    head = (
        b'--X\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\n'
        b"Content-Type: image/png\r\n\r\n"
    )
    chunk = b"A" * (1 << 20)
    pieces = [head] + [chunk] * (total_bytes // len(chunk)) + [b"\r\n--X--\r\n"]
    pulled = {"bytes": 0}
    sent = []

    async def receive():
        if pieces:
            body = pieces.pop(0)
            pulled["bytes"] += len(body)
            return {"type": "http.request", "body": body, "more_body": bool(pieces)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "headers": [
            (b"host", b"test"),
            (b"x-csrf-protection", b"1"),
            (b"transfer-encoding", b"chunked"),
            (b"content-type", b"multipart/form-data; boundary=X"),
        ],
    }
    asyncio.run(main.app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, pulled["bytes"]


@pytest.mark.parametrize("path", ["/api/me/avatar", "/api/auth/login"])
def test_chunked_body_without_content_length_is_cut_off(client, path):
    # 回归：以前未登录的分块上传会被完整写进临时文件，然后才返回 401。
    status, pulled = _post_chunked(path, config.API_MAX_BODY_BYTES * 3)
    assert status == 413
    assert pulled <= config.API_MAX_BODY_BYTES + (2 << 20)


def test_chunked_body_under_limit_still_reaches_the_route(client):
    status, _ = _post_chunked("/api/me/avatar", 1 << 20)
    assert status == 401
