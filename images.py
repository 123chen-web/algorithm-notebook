"""图片处理：头像/照片解码、缩放、路径。"""
import io
import os
from pathlib import Path

from PIL import Image

from core import config

from fastapi import HTTPException
from PIL import UnidentifiedImageError
from db import ROOT
import db
def avatar_dir():
    # 相对路径以项目目录为基准，跟 DATABASE_PATH 的解析方式一致；
    # 放在 data/ 下面，备份时复制整个 data 目录就会一起带上。
    path = Path(os.getenv("AVATAR_DIR", "data/avatars")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def avatar_path(user_id):
    return avatar_dir() / f"{user_id}.jpg"


def note_files_dir():
    # N2 笔记图片附件：相对路径同样以项目目录为基准，与 DATABASE_PATH 一致；
    # 不进 static/、不使用用户原始文件名，只按附件 id 落盘。
    path = Path(os.getenv("NOTE_FILES_DIR", "data/note_files")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def note_file_path(user_id, attachment_id):
    return note_files_dir() / str(user_id) / f"{attachment_id}.bin"


def sec_has_avatar(user_id):
    with db.connect() as conn:
        author = conn.execute(
            "SELECT 1 FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)
        ).fetchone()
    return author is not None and avatar_path(user_id).is_file()


def decode_uploaded_image(content: bytes) -> Image.Image:
    # verify() 只检查文件没有损坏，之后这个 Image 对象不能再用来处理，
    # 必须从同一份字节重新 open 一次；再调用 load() 强制完整解码，
    # 防止头部合法但数据被截断的文件绕过 verify()。
    try:
        Image.open(io.BytesIO(content)).verify()
        image = Image.open(io.BytesIO(content))
        image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
    ):
        # SyntaxError: Pillow reports broken PNG checksums with this exception.
        # DecompressionBombError：Pillow 按文件头声明的像素数提前拒绝，
        # 不会真的去解码一个几万乘几万像素的图片撑爆内存；这里只是把它
        # 也归为"文件不是有效的图片"，不让它变成一个裸的 500。
        raise HTTPException(400, "文件不是有效的图片") from None
    if image.format not in config.ALLOWED_IMAGE_FORMATS:
        raise HTTPException(400, "只支持 JPEG、PNG 或 WebP 格式的图片")
    return image


def resize_avatar_to_square_jpeg(image: Image.Image) -> bytes:
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    image = image.resize((config.AVATAR_SIZE, config.AVATAR_SIZE), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def resize_photo_for_recognition(image: Image.Image) -> bytes:
    # 保留原始长宽比（不像头像那样裁成正方形），只在图片过大时按最长边
    # 缩小；手写题目和代码要保持完整可读，裁剪会丢内容。
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    width, height = image.size
    longest = max(width, height)
    if longest > config.PHOTO_MAX_DIMENSION:
        scale = config.PHOTO_MAX_DIMENSION / longest
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()

