"""画板接口（自托管 Excalidraw）。

- 场景 JSON 存 SQLite（note_drawings，迁移 72），缩略图 PNG 存数据目录
  data/drawings/<user_id>/<drawing_id>.png（不放进 static/，不接受用户文件名）。
- 所有接口需要登录并沿用全局 X-CSRF-Protection 要求；非本人资源一律 404，
  不泄露画板是否存在。
- 乐观锁：PUT 场景必须带 version，与服务端不一致返回 409。
"""
import io
import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image, UnidentifiedImageError

import main
from db import ROOT


router = APIRouter()


DRAWING_TITLE_MAX = 100
DRAWING_SCENE_MAX_BYTES = 2 * 1024 * 1024
DRAWING_ELEMENTS_MAX = 5000
DRAWING_THUMB_MAX_BYTES = 300 * 1024
DRAWING_QUOTA = 50
# 缩略图最长边逐步压缩的下限，仍超限就拒绝。
DRAWING_THUMB_MIN_SIDE = 320
DRAWING_THUMB_START_SIDE = 1600
FORBIDDEN_ELEMENT_TYPES = {"embeddable", "iframe"}


# ---------------------------------------------------------------- 存储路径

def drawing_dir() -> Path:
    return Path(os.getenv("DRAWING_DIR", str(ROOT / "data" / "drawings"))).expanduser()


def drawing_thumb_path(user_id: int, drawing_id: int) -> Path:
    """缩略图固定在数据目录 drawings/<user_id>/<id>.png，路径不含任何用户输入。"""
    return drawing_dir() / str(user_id) / f"{int(drawing_id)}.png"


def purge_user_thumbs(user_id: int) -> None:
    """账号注销时删除该用户全部缩略图文件。"""
    shutil.rmtree(drawing_dir() / str(user_id), ignore_errors=True)


# ---------------------------------------------------------------- 校验

def validate_scene(scene: Any) -> str:
    """校验 Excalidraw 场景，返回紧凑序列化后的 scene_json 字符串。

    必须是 JSON 对象且含 elements 数组；元素最多 5000 个；拒绝
    embeddable/iframe 网页嵌入元素；序列化后（含 files 内嵌图片）≤ 2MB。
    """
    if not isinstance(scene, dict):
        raise HTTPException(422, "画板内容必须是 JSON 对象")
    elements = scene.get("elements")
    if not isinstance(elements, list):
        raise HTTPException(422, "画板内容缺少 elements 数组")
    if len(elements) > DRAWING_ELEMENTS_MAX:
        raise HTTPException(422, f"画板元素最多 {DRAWING_ELEMENTS_MAX} 个，请拆分后再保存")
    for element in elements:
        if isinstance(element, dict) and element.get("type") in FORBIDDEN_ELEMENT_TYPES:
            raise HTTPException(422, "画板不允许嵌入网页元素（embeddable/iframe）")
    files = scene.get("files")
    if files is not None and not isinstance(files, dict):
        raise HTTPException(422, "画板 files 字段必须是对象")
    # files（内嵌图片的 dataURL）在场景对象内，序列化大小自然计入 2MB 上限。
    raw = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > DRAWING_SCENE_MAX_BYTES:
        raise HTTPException(422, "画板内容超过 2MB 上限，请减少元素或内嵌图片")
    return raw.decode("utf-8")


def normalize_title(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HTTPException(422, "画板标题必须是字符串")
    title = value.strip()
    if len(title) > DRAWING_TITLE_MAX:
        raise HTTPException(422, f"画板标题最多 {DRAWING_TITLE_MAX} 字")
    return title


def get_owned_drawing(conn, drawing_id: int, user_id: int):
    """取本人未删除的画板；任何不匹配都返回 404（含已删除、他人画板）。"""
    row = conn.execute(
        "SELECT * FROM note_drawings WHERE id = ? AND user_id = ?",
        (drawing_id, user_id),
    ).fetchone()
    if row is None or row["deleted_at"]:
        raise HTTPException(404, "画板不存在")
    return row


def verify_owned_note(conn, note_id: Optional[int], user_id: int) -> None:
    if note_id is None:
        return
    if not isinstance(note_id, int) or isinstance(note_id, bool) or note_id <= 0:
        raise HTTPException(422, "关联笔记编号不正确")
    row = conn.execute(
        "SELECT id FROM notes WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
        (note_id, user_id),
    ).fetchone()
    if row is None:
        # 他人笔记同样报“不存在”，不泄露其他用户的笔记。
        raise HTTPException(422, "关联笔记不存在或不属于当前账号")


def parse_scene(row) -> Optional[dict]:
    if not row["scene_json"]:
        return None
    try:
        scene = json.loads(row["scene_json"])
    except (ValueError, TypeError):
        return None
    return scene if isinstance(scene, dict) else None


def drawing_payload(row, *, with_scene: bool) -> dict:
    payload = {
        "id": row["id"],
        "note_id": row["note_id"],
        "title": row["title"],
        "has_thumb": bool(row["thumb_path"]),
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if with_scene:
        payload["scene"] = parse_scene(row)
    return payload


# ---------------------------------------------------------------- 请求模型

class DrawingCreate(BaseModel):
    title: Any = None
    note_id: Any = None


class DrawingUpdate(BaseModel):
    scene: Any
    version: Any


class DrawingRename(BaseModel):
    title: Any = None


# ---------------------------------------------------------------- 接口

@router.post("/api/drawings", status_code=201)
def create_drawing(body: DrawingCreate, user=Depends(main.current_user)):
    title = normalize_title(body.title)
    note_id = body.note_id
    if note_id is not None and (not isinstance(note_id, int) or isinstance(note_id, bool)):
        raise HTTPException(422, "关联笔记编号不正确")
    now = main.utc_now()
    with main.connect(write=True) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM note_drawings WHERE user_id = ? AND deleted_at IS NULL",
            (user["id"],),
        ).fetchone()[0]
        if count >= DRAWING_QUOTA:
            raise HTTPException(422, f"画板数量已达上限（{DRAWING_QUOTA} 张），请先删除后再新建")
        verify_owned_note(conn, note_id, user["id"])
        cursor = conn.execute(
            """
            INSERT INTO note_drawings
                (user_id, note_id, title, scene_json, thumb_path, version,
                 created_at, updated_at, deleted_at)
            VALUES (?, ?, ?, '', NULL, 1, ?, ?, NULL)
            """,
            (user["id"], note_id, title, now, now),
        )
        drawing_id = cursor.lastrowid
        row = conn.execute(
            "SELECT * FROM note_drawings WHERE id = ?", (drawing_id,)
        ).fetchone()
    return {"id": row["id"], "version": row["version"]}


@router.get("/api/drawings")
def list_drawings(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(main.current_user),
):
    with main.connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM note_drawings WHERE user_id = ? AND deleted_at IS NULL",
            (user["id"],),
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM note_drawings
            WHERE user_id = ? AND deleted_at IS NULL
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (user["id"], limit, offset),
        ).fetchall()
    return {
        "items": [drawing_payload(row, with_scene=False) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/drawings/{drawing_id}")
def get_drawing(drawing_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        row = get_owned_drawing(conn, drawing_id, user["id"])
    return drawing_payload(row, with_scene=True)


@router.patch("/api/drawings/{drawing_id}")
def rename_drawing(drawing_id: int, body: DrawingRename, user=Depends(main.current_user)):
    title = normalize_title(body.title)
    if not title:
        raise HTTPException(422, "画板标题不能为空")
    now = main.utc_now()
    with main.connect(write=True) as conn:
        get_owned_drawing(conn, drawing_id, user["id"])
        conn.execute(
            "UPDATE note_drawings SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, now, drawing_id, user["id"]),
        )
        row = conn.execute(
            "SELECT * FROM note_drawings WHERE id = ?", (drawing_id,)
        ).fetchone()
    return drawing_payload(row, with_scene=False)


@router.put("/api/drawings/{drawing_id}")
def update_drawing(drawing_id: int, body: DrawingUpdate, user=Depends(main.current_user)):
    version = body.version
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise HTTPException(422, "版本号必须是不小于 1 的整数")
    scene_json = validate_scene(body.scene)
    now = main.utc_now()
    with main.connect(write=True) as conn:
        row = get_owned_drawing(conn, drawing_id, user["id"])
        if row["version"] != version:
            raise HTTPException(409, "这张画板已在别处被修改，请刷新后再保存")
        conn.execute(
            """
            UPDATE note_drawings
               SET scene_json = ?, version = version + 1, updated_at = ?
             WHERE id = ? AND user_id = ?
            """,
            (scene_json, now, drawing_id, user["id"]),
        )
        new_version = version + 1
    return {"id": drawing_id, "version": new_version}


@router.put("/api/drawings/{drawing_id}/thumb")
async def upload_thumb(request: Request, drawing_id: int, user=Depends(main.current_user)):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > DRAWING_THUMB_MAX_BYTES:
        raise HTTPException(413, "缩略图太大，最多 300KB")
    content = await request.body()
    if not content:
        raise HTTPException(400, "缩略图内容为空")
    if len(content) > DRAWING_THUMB_MAX_BYTES:
        raise HTTPException(413, "缩略图太大，最多 300KB")
    # 只认真实解码结果：必须是 PNG，随后服务端重新编码，不存用户原始字节。
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(422, "缩略图必须是有效的 PNG 图片")
    if image.format != "PNG":
        raise HTTPException(422, "缩略图必须是 PNG 格式")

    encoded = reencode_thumb(image)
    if encoded is None:
        raise HTTPException(422, "缩略图重新编码后仍超过 300KB，请减少画面内容")

    with main.connect(write=True) as conn:
        get_owned_drawing(conn, drawing_id, user["id"])
        target = drawing_thumb_path(user["id"], drawing_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{drawing_id}-{os.getpid()}.tmp")
        try:
            temporary.write_bytes(encoded)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        # thumb_path 相对 DRAWING_DIR 保存，便于迁移数据目录；不暴露给前端。
        rel = f"{user['id']}/{drawing_id}.png"
        conn.execute(
            "UPDATE note_drawings SET thumb_path = ? WHERE id = ? AND user_id = ?",
            (rel, drawing_id, user["id"]),
        )
    return {"ok": True}


def reencode_thumb(image: Image.Image) -> Optional[bytes]:
    """重新编码为 PNG，必要时逐步缩小最长边直到 ≤300KB。"""
    rgba = image.convert("RGBA") if image.mode not in ("RGB", "RGBA", "L", "P") else image
    side = DRAWING_THUMB_START_SIDE
    while True:
        frame = rgba
        if max(frame.size) > side:
            frame = frame.copy()
            frame.thumbnail((side, side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        frame.save(buf, format="PNG", optimize=True)
        encoded = buf.getvalue()
        if len(encoded) <= DRAWING_THUMB_MAX_BYTES:
            return encoded
        if side <= DRAWING_THUMB_MIN_SIDE:
            return None
        side = max(DRAWING_THUMB_MIN_SIDE, int(side * 0.8))


@router.get("/api/drawings/{drawing_id}/thumb")
def get_thumb(drawing_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        row = get_owned_drawing(conn, drawing_id, user["id"])
        rel = row["thumb_path"]
    if not rel:
        raise HTTPException(404, "缩略图不存在")
    path = drawing_dir() / rel
    if not path.is_file():
        raise HTTPException(404, "缩略图不存在")
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            # 缩略图属于私人内容：禁止共享缓存；nosniff 由全局中间件统一附加。
            "Cache-Control": "private, max-age=300",
        },
    )


@router.delete("/api/drawings/{drawing_id}")
def delete_drawing(drawing_id: int, user=Depends(main.current_user)):
    now = main.utc_now()
    with main.connect(write=True) as conn:
        get_owned_drawing(conn, drawing_id, user["id"])
        conn.execute(
            "UPDATE note_drawings SET deleted_at = ? WHERE id = ? AND user_id = ?",
            (now, drawing_id, user["id"]),
        )
    return {"ok": True}
