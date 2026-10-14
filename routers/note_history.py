"""笔记的历史版本与“最近删除”。

- 历史版本：每次改正文/标题/标签前，把改之前的样子存进 note_revisions。自动保存会很频繁，
  所以同一条笔记 10 分钟内只留最早那份（像 Google Docs / Notion 的版本记录那样按时间段合并），
  每条最多留 50 份，最老的先删。恢复某个版本前，会先把当前内容强制存一份，恢复也能反悔。
- 最近删除：删除笔记仍是软删除（deleted_at），这里列出来、可以恢复，也可以彻底删除。
  彻底删除会连同它的历史版本一起删掉（外键级联）。
- 所有接口只处理本人的数据，别人的笔记/版本一律 404。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

import accounts
import db
from core import security
from routers import notes

router = APIRouter()

REVISION_COALESCE = timedelta(minutes=10)
REVISION_KEEP = 50
REVISION_PREVIEW_CHARS = 80
TRASH_LIST_MAX = 200


def snapshot_before_update(conn, user_id, before, *, title, content, tags, now, force=False):
    """before 是 notes.owned_note 的结果；title/content/tags 是这次要写入的新值（None=不改）。

    只有真的改了正文/标题/标签才存；force=True 时跳过 10 分钟合并。必须在写事务里调用。
    """
    old_tags = ",".join(before["tags"])
    changed = (
        (title is not None and title != (before["title"] or ""))
        or (content is not None and content != (before["content"] or ""))
        or (tags is not None and ",".join(tags) != old_tags)
    )
    if not changed:
        return
    if not force:
        last = conn.execute(
            "SELECT created_at FROM note_revisions WHERE note_id = ? ORDER BY id DESC LIMIT 1",
            (before["id"],),
        ).fetchone()
        if last is not None:
            age = datetime.fromisoformat(now) - datetime.fromisoformat(last["created_at"])
            if age < REVISION_COALESCE:
                return
    conn.execute(
        """
        INSERT INTO note_revisions(note_id, user_id, title, content, tags, saved_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (before["id"], user_id, before["title"] or "", before["content"] or "", old_tags,
         before["updated_at"], now),
    )
    conn.execute(
        """
        DELETE FROM note_revisions WHERE note_id = ? AND id NOT IN (
            SELECT id FROM note_revisions WHERE note_id = ? ORDER BY id DESC LIMIT ?
        )
        """,
        (before["id"], before["id"], REVISION_KEEP),
    )


def _preview(content):
    text = " ".join((content or "").split())
    return text[:REVISION_PREVIEW_CHARS] + ("…" if len(text) > REVISION_PREVIEW_CHARS else "")


def _owned_revision(conn, note_id, revision_id, user_id):
    row = conn.execute(
        "SELECT * FROM note_revisions WHERE id = ? AND note_id = ? AND user_id = ?",
        (revision_id, note_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "这个版本不存在")
    return row


@router.get("/api/notes/trash")
def list_trash(user=Depends(security.current_user)):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, content, tags, deleted_at, updated_at FROM notes "
            "WHERE user_id = ? AND deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC LIMIT ?",
            (user["id"], TRASH_LIST_MAX),
        ).fetchall()
    return {"items": [
        {
            "id": row["id"],
            "title": notes._display_title(row["title"], row["content"]),
            "preview": _preview(row["content"]),
            "tags": [tag for tag in row["tags"].split(",") if tag],
            "deleted_at": row["deleted_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]}


def _deleted_note(conn, note_id, user_id):
    row = conn.execute(
        "SELECT id, title, content FROM notes WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
        (note_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "回收站里没有这条笔记")
    return row


@router.post("/api/notes/{note_id}/restore")
def restore_note(note_id: int, user=Depends(security.current_user)):
    with db.connect(write=True) as conn:
        accounts.rvb_account(conn, user["id"])
        row = _deleted_note(conn, note_id, user["id"])
        conn.execute("UPDATE notes SET deleted_at = NULL WHERE id = ?", (note_id,))
        warnings = notes._reparse_links(conn, note_id, user["id"], row["content"])
        # 删除时别人指向它的链接被置成悬空；恢复后按“显示标题”重新接上（只接仍然悬空的）。
        title = notes._display_title(row["title"], row["content"])
        if title:
            conn.execute(
                "UPDATE note_links SET target_note_id = ? WHERE user_id = ? AND link_kind = 'note' "
                "AND target_note_id IS NULL AND target_text = ? AND source_note_id != ?",
                (note_id, user["id"], title, note_id),
            )
        fresh = notes.owned_note(conn, note_id, user["id"])
    fresh["warnings"] = warnings
    return fresh


@router.delete("/api/notes/{note_id}/purge")
def purge_note(note_id: int, user=Depends(security.current_user)):
    """彻底删除回收站里的一条笔记（只能删已在回收站里的），历史版本跟着删。"""
    with db.connect(write=True) as conn:
        accounts.rvb_account(conn, user["id"])
        _deleted_note(conn, note_id, user["id"])
        conn.execute("DELETE FROM note_revisions WHERE note_id = ? AND user_id = ?", (note_id, user["id"]))
        conn.execute("DELETE FROM note_links WHERE source_note_id = ?", (note_id,))
        conn.execute(
            "UPDATE note_links SET target_note_id = NULL WHERE target_note_id = ?", (note_id,)
        )
        conn.execute("UPDATE note_drawings SET note_id = NULL WHERE note_id = ? AND user_id = ?", (note_id, user["id"]))
        conn.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user["id"]))
    return {"ok": True}


@router.get("/api/notes/{note_id}/revisions")
def list_revisions(note_id: int, user=Depends(security.current_user)):
    with db.connect() as conn:
        notes.owned_note(conn, note_id, user["id"])
        rows = conn.execute(
            "SELECT id, title, content, saved_at, created_at FROM note_revisions "
            "WHERE note_id = ? AND user_id = ? ORDER BY id DESC",
            (note_id, user["id"]),
        ).fetchall()
    return {"items": [
        {
            "id": row["id"],
            "title": notes._display_title(row["title"], row["content"]),
            "preview": _preview(row["content"]),
            "length": len(row["content"] or ""),
            "saved_at": row["saved_at"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]}


@router.get("/api/notes/{note_id}/revisions/{revision_id}")
def get_revision(note_id: int, revision_id: int, user=Depends(security.current_user)):
    with db.connect() as conn:
        notes.owned_note(conn, note_id, user["id"])
        row = _owned_revision(conn, note_id, revision_id, user["id"])
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "tags": [tag for tag in row["tags"].split(",") if tag],
        "saved_at": row["saved_at"],
        "created_at": row["created_at"],
    }


@router.post("/api/notes/{note_id}/revisions/{revision_id}/restore")
def restore_revision(note_id: int, revision_id: int, user=Depends(security.current_user)):
    with db.connect(write=True) as conn:
        accounts.rvb_account(conn, user["id"])
        notes.owned_note(conn, note_id, user["id"])
        row = _owned_revision(conn, note_id, revision_id, user["id"])
        data = notes.NoteUpdate(
            title=row["title"], content=row["content"],
            tags=[tag for tag in row["tags"].split(",") if tag],
        )
        return notes.apply_note_update(conn, user["id"], note_id, data, force_revision=True)
