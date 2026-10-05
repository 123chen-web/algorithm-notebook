import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def test_api_body_over_global_limit_gets_413(client):
    declared = str(main.API_MAX_BODY_BYTES + 1)
    response = client.post(
        "/api/auth/login", content=b"{}",
        headers={"Content-Length": declared, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_global_limit_leaves_room_for_photo_upload():
    assert main.API_MAX_BODY_BYTES > main.PHOTO_MAX_BYTES + 1024 * 1024


def test_body_under_limit_is_not_rejected_by_middleware(client):
    response = client.post("/api/auth/login", json={"username": "x", "password": "y"})
    assert response.status_code != 413


def test_scratch_code_and_fixed_are_capped():
    # 模型层只挡极端大小（超过 100000 字符），40000 字符的精确上限由接口按合计返回 413。
    main.ScratchPut(version=0, code="x" * 100000, fixed="y" * 100000, table=None)
    for field in ("code", "fixed"):
        values = {"code": "", "fixed": "", field: "z" * 100001}
        with pytest.raises(ValidationError):
            main.ScratchPut(version=0, table=None, **values)
