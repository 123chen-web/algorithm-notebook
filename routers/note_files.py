"""N2 笔记图片附件：上传（multipart）、读取、删除。

设计要点（与 main.py / routers/notes.py 配合）：
- 一律用 Pillow 按魔数识别真实图片，不信扩展名 / Content-Type；
  验证通过后统一重编码落盘、顺手剥离 EXIF；GIF 动图只保留首帧。
- 文件落盘 data/note_files/<user_id>/<id>.bin，不进 static/、不用用户原始文件名。
- 限额：单张 ≤3MB（413）；每用户附件总容量 ≤100MB（413）；
  每篇笔记正文引用附件 ≤20 张（在 routers/notes.py 保存 POST/PUT 时校验，422）。
- 每次上传顺带清理该用户：创建超过 24 小时、且不被任何笔记正文 attachment:ID
  引用的孤儿附件（删行 + 删文件）。
- 越权：访问 / 删除他人附件一律 404。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import hashlib
import io
import os
import re
import secrets
from datetime import datetime, timedelta

from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from PIL.Image import DecompressionBombError as ImageDecompressionBombError
from PIL import UnidentifiedImageError

import main
from fastapi import APIRouter


router = APIRouter()

NOTE_ATTACHMENT_MAX_BYTES = 3 * 1024 * 1024          # 单张 ≤ 3MB
NOTE_ATTACHMENT_TOTAL_BYTES = 100 * 1024 * 1024     # 每用户附件总容量 ≤ 100MB
NOTE_REF_PER_NOTE_MAX = 20                          # 每篇笔记正文引用附件数上限（保存时校验）
NOTE_ORPHAN_RETENTION = timedelta(hours=24)         # 超过 24h 且无引用才清理孤儿

# Pillow 识别出的真实格式 → 落盘 Content-Type。
_ALLOWED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}
# 导出清单里由 id + mime 派生的文件名后缀。
MIME_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

# 笔记正文里 ![](attachment:ID) 的引用形式；统计与孤儿清理共用同一规则。
ATTACHMENT_REF_RE = re.compile(r"attachment:(\d+)")


def attachment_export_name(attachment_id, mime):
    """导出时由 id + mime 派生的文件名：attachment-12.png（不含用户原始文件名）。"""
    ext = MIME_EXTENSIONS.get(mime, "bin")
    return f"attachment-{attachment_id}.{ext}"


def count_attachment_refs(content):
    """统计正文里 attachment:ID 引用个数（保存笔记时校验 ≤20）。"""
    return len(ATTACHMENT_REF_RE.findall(content or ""))


def _decode_image(content):
    # verify() 之后 Image 对象不能再用，必须从同一份字节重新 open 并 load()，
    # 与 main.decode_uploaded_image 同一套防截断套路；识别失败一律 422。
    try:
        Image.open(io.BytesIO(content)).verify()
        image = Image.open(io.BytesIO(content))
        image.load()
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        ImageDecompressionBombError,
    ):
        raise HTTPException(422, "文件不是有效的图片") from None
    if image.format not in _ALLOWED_FORMATS:
        raise HTTPException(422, "只支持 PNG、JPEG、WebP 或 GIF 格式的图片")
    return image


def _reencode(image):
    """统一重编码：剥离 EXIF；GIF 动图只保留首帧。返回 (mime, bytes)。"""
    fmt = image.format
    buffer = io.BytesIO()
    if fmt == "GIF":
        image.seek(0)
        image.save(buffer, format="GIF")
    elif fmt == "JPEG":
        rgb = image.convert("RGB") if image.mode not in ("RGB", "L") else image
        rgb.save(buffer, format="JPEG", quality=88)
    elif fmt == "WEBP":
        image.save(buffer, format="WEBP", quality=90)
    else:  # PNG
        image.save(buffer, format="PNG")
    return _ALLOWED_FORMATS[fmt], buffer.getvalue()


def _referenced_attachment_ids(conn, user_id):
    rows = conn.execute(
        "SELECT content FROM notes WHERE user_id = ?", (user_id,)
    ).fetchall()
    referenced = set()
    for row in rows:
        for ref in ATTACHMENT_REF_RE.findall(row["content"] or ""):
            referenced.add(int(ref))
    return referenced


def _cleanup_orphan_attachments(conn, user_id, now_iso):
    """删除该用户：创建超过 24 小时、且不被任何笔记正文引用的孤儿附件（行 + 文件）。"""
    threshold = (datetime.fromisoformat(now_iso) - NOTE_ORPHAN_RETENTION).isoformat()
    rows = conn.execute(
        "SELECT id FROM note_attachments WHERE user_id = ? AND created_at < ?",
        (user_id, threshold),
    ).fetchall()
    if not rows:
        return
    referenced = _referenced_attachment_ids(conn, user_id)
    for row in rows:
        if row["id"] in referenced:
            continue
        conn.execute(
            "DELETE FROM note_attachments WHERE id = ? AND user_id = ?",
            (row["id"], user_id),
        )
        try:
            main.note_file_path(user_id, row["id"]).unlink(missing_ok=True)
        except OSError:
            main.logger.warning(
                "清理孤儿附件文件失败 attachment_id=%s", row["id"], exc_info=True
            )


@router.post("/api/notes/attachments", status_code=201)
async def upload_attachment(
    user=Depends(main.current_user), file: UploadFile = File(...)
):
    content = await file.read(NOTE_ATTACHMENT_MAX_BYTES + 1)
    if len(content) > NOTE_ATTACHMENT_MAX_BYTES:
        raise HTTPException(413, "图片太大，单张最多 3MB")
    if not content:
        raise HTTPException(422, "文件是空的")

    image = await main.run_in_threadpool(_decode_image, content)
    mime, data = await main.run_in_threadpool(_reencode, image)
    sha256 = hashlib.sha256(data).hexdigest()

    with main.connect(write=True) as conn:
        # 解码期间账号可能已注销，写锁内复查后才落盘。
        main.recheck_account(conn, user["id"])
        total = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) AS used FROM note_attachments "
            "WHERE user_id = ?",
            (user["id"],),
        ).fetchone()["used"]
        if total + len(data) > NOTE_ATTACHMENT_TOTAL_BYTES:
            raise HTTPException(413, "图片附件总容量已达 100MB，请先删除不再使用的图片")
        now = main.utc_now()
        cursor = conn.execute(
            """
            INSERT INTO note_attachments(user_id, note_id, sha256, mime, size_bytes, created_at)
            VALUES (?, NULL, ?, ?, ?, ?)
            """,
            (user["id"], sha256, mime, len(data), now),
        )
        attachment_id = cursor.lastrowid
        directory = main.note_files_dir() / str(user["id"])
        directory.mkdir(parents=True, exist_ok=True)
        final_path = directory / f"{attachment_id}.bin"
        temp_path = directory / f".{attachment_id}-{secrets.token_hex(8)}.tmp"
        try:
            temp_path.write_bytes(data)
            os.replace(temp_path, final_path)
        finally:
            temp_path.unlink(missing_ok=True)
        _cleanup_orphan_attachments(conn, user["id"], now)
    return {
        "id": attachment_id,
        "url": f"/api/notes/attachments/{attachment_id}",
        "markdown": f"![](attachment:{attachment_id})",
    }


@router.get("/api/notes/attachments/{attachment_id}")
def get_attachment(attachment_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        row = conn.execute(
            "SELECT id, mime FROM note_attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "图片不存在")
    path = main.note_file_path(user["id"], attachment_id)
    if not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(
        path,
        media_type=row["mime"],
        headers={
            "Cache-Control": "private, max-age=86400",
            "Content-Disposition": "inline",
        },
    )


@router.delete("/api/notes/attachments/{attachment_id}")
def delete_attachment(attachment_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        row = conn.execute(
            "SELECT id FROM note_attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user["id"]),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "图片不存在")
        conn.execute(
            "DELETE FROM note_attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user["id"]),
        )
    try:
        main.note_file_path(user["id"], attachment_id).unlink(missing_ok=True)
    except OSError:
        main.logger.warning(
            "删除附件文件失败 attachment_id=%s", attachment_id, exc_info=True
        )
    return {"ok": True}
