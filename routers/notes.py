"""notes routes: 记笔记（Memos 式时间线，可关联题目）。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import StringConstraints
from tags import normalize_tag
from typing import Annotated
from fastapi import APIRouter


router = APIRouter()

# 笔记标签独立于易错点标签体系：只借用 normalize_tag 做空白折叠，
# 去重/数量上限在这里单独实现，互不干扰。
NOTE_TAG_MAX = 10
NOTE_CONTENT_MAX = 20000
NOTE_COUNT_MAX = 2000
NoteTitle = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
NoteContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_CONTENT_MAX)
]
NoteTag = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]


def normalize_note_tags(values):
    """规范化笔记标签：去逗号（逗号是存储分隔符）、去重（不分大小写）、最多 10 个。"""
    seen = set()
    result = []
    for value in values or []:
        tag = normalize_tag(value).replace(",", "").replace("，", "")
        tag = " ".join(tag.split())
        if not tag:
            continue
        if len(tag) > 20:
            raise HTTPException(422, "标签最多 20 个字")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    if len(result) > NOTE_TAG_MAX:
        raise HTTPException(422, f"每条笔记最多 {NOTE_TAG_MAX} 个标签")
    return result


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class NoteCreate(BaseModel):
    title: NoteTitle = ""
    content: NoteContent
    tags: list[NoteTag] = []
    problem_id: int | None = None
    pinned: bool = False


class NoteUpdate(BaseModel):
    title: NoteTitle | None = None
    content: NoteContent | None = None
    tags: list[NoteTag] | None = None
    problem_id: int | None = None
    pinned: bool | None = None


def owned_note(conn, note_id, user_id):
    row = conn.execute(
        """
        SELECT n.*, p.title AS problem_title
        FROM notes n LEFT JOIN problems p ON p.id = n.problem_id
        WHERE n.id = ? AND n.user_id = ? AND n.deleted_at IS NULL
        """,
        (note_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "笔记不存在")
    return note_public(dict(row))


def note_public(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "tags": [tag for tag in row["tags"].split(",") if tag],
        "problem_id": row["problem_id"],
        "problem_title": row["problem_title"],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def check_problem_ownership(conn, problem_id, user_id):
    """problem_id 只能关联自己的题目；他人/不存在的题目一律 422。"""
    if problem_id is None:
        return
    row = conn.execute(
        "SELECT id FROM problems WHERE id = ? AND user_id = ?",
        (problem_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(422, "关联的题目不存在或不属于你")


@router.get("/api/notes")
def list_notes(
    q: str | None = None,
    tag: str | None = None,
    problem_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
    user=Depends(main.current_user),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    sql = (
        "SELECT n.*, p.title AS problem_title FROM notes n "
        "LEFT JOIN problems p ON p.id = n.problem_id "
        "WHERE n.user_id = ? AND n.deleted_at IS NULL"
    )
    count_sql = "SELECT COUNT(*) FROM notes n WHERE n.user_id = ? AND n.deleted_at IS NULL"
    params: list = [user["id"]]
    if q:
        like = f"%{_escape_like(q)}%"
        sql += " AND (n.title LIKE ? ESCAPE '\\' OR n.content LIKE ? ESCAPE '\\')"
        count_sql += " AND (n.title LIKE ? ESCAPE '\\' OR n.content LIKE ? ESCAPE '\\')"
        params += [like, like]
    if tag:
        like = f"%,{_escape_like(tag)},%"
        sql += " AND (',' || n.tags || ',') LIKE ? ESCAPE '\\'"
        count_sql += " AND (',' || n.tags || ',') LIKE ? ESCAPE '\\'"
        params.append(like)
    if problem_id is not None:
        sql += " AND n.problem_id = ?"
        count_sql += " AND n.problem_id = ?"
        params.append(problem_id)
    sql += " ORDER BY n.pinned DESC, n.updated_at DESC LIMIT ? OFFSET ?"
    with main.connect() as conn:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
    return {"notes": [note_public(dict(row)) for row in rows], "total": total}


@router.post("/api/notes", status_code=201)
def create_note(data: NoteCreate, user=Depends(main.current_user)):
    tags = normalize_note_tags(data.tags)
    now = main.utc_now()
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        count = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id = ?", (user["id"],)).fetchone()[0]
        if count >= NOTE_COUNT_MAX:
            raise HTTPException(422, f"每个账号最多保存 {NOTE_COUNT_MAX} 条笔记（含已删除笔记）")
        check_problem_ownership(conn, data.problem_id, user["id"])
        cursor = conn.execute(
            """
            INSERT INTO notes(user_id, title, content, tags, problem_id, pinned,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], data.title, data.content, ",".join(tags),
                data.problem_id, 1 if data.pinned else 0, now, now,
            ),
        )
        note_id = cursor.lastrowid
        row = conn.execute(
            """
            SELECT n.*, p.title AS problem_title
            FROM notes n LEFT JOIN problems p ON p.id = n.problem_id
            WHERE n.id = ?
            """,
            (note_id,),
        ).fetchone()
    return note_public(dict(row))


@router.get("/api/notes/{note_id}")
def get_note(note_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        return owned_note(conn, note_id, user["id"])


@router.put("/api/notes/{note_id}")
def update_note(note_id: int, data: NoteUpdate, user=Depends(main.current_user)):
    tags = normalize_note_tags(data.tags) if data.tags is not None else None
    now = main.utc_now()
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        owned_note(conn, note_id, user["id"])
        if data.problem_id is not None:
            check_problem_ownership(conn, data.problem_id, user["id"])
        updates: list[str] = []
        params: list = []
        if data.title is not None:
            updates.append("title = ?")
            params.append(data.title)
        if data.content is not None:
            updates.append("content = ?")
            params.append(data.content)
        if tags is not None:
            updates.append("tags = ?")
            params.append(",".join(tags))
        if "problem_id" in data.model_fields_set:
            updates.append("problem_id = ?")
            params.append(data.problem_id)
        if data.pinned is not None:
            updates.append("pinned = ?")
            params.append(1 if data.pinned else 0)
        updates.append("updated_at = ?")
        params.append(now)
        params.append(note_id)
        conn.execute(
            f"UPDATE notes SET {', '.join(updates)} WHERE id = ?", params
        )
        return owned_note(conn, note_id, user["id"])


@router.delete("/api/notes/{note_id}")
def delete_note(note_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        owned_note(conn, note_id, user["id"])
        conn.execute(
            "UPDATE notes SET deleted_at = ? WHERE id = ?",
            (main.utc_now(), note_id),
        )
    return {"ok": True}
