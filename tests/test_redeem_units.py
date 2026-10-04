"""兑换码规范化、图片重编码与共享套餐函数的纯内存单元检查，无数据库。"""
import io

import pytest
from fastapi import HTTPException
from PIL import Image, PngImagePlugin

import main
import payments


NOW = "2026-10-03T01:02:03+00:00"


class Subscription:
    def __init__(self, expiry):
        self.expiry = expiry
        self.plan = None

    def execute(self, sql, params):
        if sql.startswith("SELECT"):
            return self
        self.plan, self.expiry, user_id = params
        assert user_id == 23
        return self

    def fetchone(self):
        return {"plan_expires_at": self.expiry}


@pytest.mark.parametrize("expiry,expected", [
    (None, "2026-10-05T01:02:03+00:00"),
    ("2026-10-01T01:02:03+00:00", "2026-10-05T01:02:03+00:00"),
    (NOW, "2026-10-05T01:02:03+00:00"),
    ("2026-10-06T01:02:03+00:00", "2026-10-08T01:02:03+00:00"),
])
def test_shared_activation_preserves_payment_expiry_rule(expiry, expected):
    subscription = Subscription(expiry)
    assert payments.activate_plan(subscription, 23, 9, 2, NOW) == expected
    assert subscription.plan == 9
    assert subscription.expiry == expected


def test_code_normalization_and_hash():
    raw = "ABCD-EFGH-JKMN-PQRS"
    tolerant = " ａｂｃｄ－ｅｆｇｈ　ｊｋｍｎ\nｐｑｒｓ "
    assert main.normalize_redeem_code(tolerant) == "ABCDEFGHJKMNPQRS"
    assert main.redeem_code_hash(raw) == main.redeem_code_hash(tolerant)
    assert len(main.redeem_code_hash(raw)) == 64


@pytest.mark.parametrize("file_format", ["PNG", "JPEG"])
def test_qr_reencode_shrinks_and_removes_metadata(file_format):
    image = Image.new("RGB", (2400, 1200), "white")
    buffer = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "must disappear")
    image.save(buffer, format=file_format, pnginfo=metadata)
    png = main.encode_pay_qr(buffer.getvalue())
    with Image.open(io.BytesIO(png)) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == (2000, 1000)
        assert not decoded.info


def test_qr_rejects_decompression_bomb_warning(monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20)).save(buffer, format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 300)
    with pytest.raises(HTTPException) as error:
        main.encode_pay_qr(buffer.getvalue())
    assert error.value.status_code == 400
    assert error.value.detail == "文件不是有效的图片"
