"""mistakes routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from datetime import date
from fastapi import Depends
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import StringConstraints
from scheduler import today_in_timezone
from tags import SUGGESTED_TAGS
from tags import TAGS_PER_MISTAKE
from tags import TAG_MAX_LENGTH
from tags import TagError
from tags import normalize_tags
from tags import replace_tags
from tags import tags_for_mistakes
from tags import user_tag_counts
from typing import Annotated
import json
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/mistakes")
def list_mistakes(
    due_only: bool = True, zone: str | None = None, created_on: date | None = None,
    tag: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None,
    user=Depends(main.current_user),
):
    if zone is not None and zone not in main.PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    day = main.today_for(user).isoformat()
    sql = main.MISTAKE_SELECT + " WHERE p.user_id = ?"
    params = [user["id"]]
    if due_only:
        sql += " AND m.suspended_at IS NULL AND m.due_date <= ?"
        params.append(day)
    if zone is not None:
        sql += " AND p.zone = ?"
        params.append(zone)
    if tag:
        sql += " AND EXISTS (SELECT 1 FROM mistake_tags t WHERE t.mistake_id = m.id AND t.tag = ?)"
        params.append(tag)

    with main.connect() as conn:
        if created_on is not None:
            # 侧栏日历按用户本地日筛选"这一天新增的记录"：时区换算只能在 Python 里做。
            problem_ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id, created_at FROM problems WHERE user_id = ?", (user["id"],)
                )
                if today_in_timezone(user["timezone"], main.datetime.fromisoformat(row["created_at"]))
                == created_on
            ]
            sql += " AND p.id IN (SELECT value FROM json_each(?))"
            params.append(json.dumps(problem_ids))
        sql += " ORDER BY m.due_date ASC, m.id ASC"
        rows = conn.execute(sql, params).fetchall()
        tags_by_id = tags_for_mistakes(conn, [row["id"] for row in rows])
    return {
        "today": day,
        "items": [{**dict(row), "tags": tags_by_id[row["id"]]} for row in rows],
    }


@router.get("/api/tags")
def list_tags(user=Depends(main.current_user)):
    with main.connect() as conn:
        counts = user_tag_counts(conn, user["id"])
    return {
        "tags": counts,
        "suggestions": list(SUGGESTED_TAGS),
        "limits": {"per_mistake": TAGS_PER_MISTAKE, "length": TAG_MAX_LENGTH},
    }


@router.put("/api/mistakes/{mistake_id}/tags")
def set_mistake_tags(mistake_id: int, data: main.MistakeTags, user=Depends(main.current_user)):
    try:
        tags = normalize_tags(data.tags)
    except TagError as error:
        raise HTTPException(422, str(error)) from None
    with main.connect(write=True) as conn:
        main.owned_mistake(conn, mistake_id, user["id"])
        try:
            replace_tags(conn, user["id"], mistake_id, tags)
        except TagError as error:
            raise HTTPException(422, str(error)) from None
    return {"tags": tags}


@router.get("/api/mistakes/{mistake_id}")
def get_mistake(mistake_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        item = main.owned_mistake(conn, mistake_id, user["id"])
        item["reviews"] = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id DESC",
                (mistake_id,),
            )
        ]
        item["variants"] = [
            main.redact_pending_answer(dict(row))
            for row in conn.execute(
                main.VARIANT_SELECT + " WHERE v.mistake_id = ? ORDER BY v.id DESC",
                (mistake_id,),
            )
        ]
        item["tags"] = tags_for_mistakes(conn, [mistake_id])[mistake_id]
    item["today"] = main.today_for(user).isoformat()
    return item


@router.put("/api/mistakes/{mistake_id}")
def edit_mistake(mistake_id: int, data: main.MistakeEdit, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        item = main.owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")

        conn.execute(
            "UPDATE mistakes SET description = ?, version = version + 1 WHERE id = ?",
            (data.description, mistake_id),
        )
    return {**item, "description": data.description, "version": item["version"] + 1}


@router.delete("/api/mistakes/{mistake_id}")
def delete_mistake(mistake_id: int, user=Depends(main.current_user)):
    # 只删除这一条易错点；同一题下的其他易错点不受影响。
    with main.connect(write=True) as conn:
        main.owned_mistake(conn, mistake_id, user["id"])
        conn.execute("DELETE FROM mistakes WHERE id = ?", (mistake_id,))
    return {"ok": True}


@router.get("/api/mistakes/{mistake_id}/scratch")
def get_mistake_scratch(mistake_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        main.owned_mistake(conn, mistake_id, user["id"])
        return main.scratch_state(conn, mistake_id)


@router.put("/api/mistakes/{mistake_id}/scratch")
def put_mistake_scratch(mistake_id: int, data: main.ScratchPut,
                        user=Depends(main.current_user)):
    # 纯内容校验放在写事务之前：行数、演算表结构（422），合计大小（413）。
    if (
        main._scratch_line_count(data.code) > main.SCRATCH_MAX_LINES
        or main._scratch_line_count(data.fixed) > main.SCRATCH_MAX_LINES
    ):
        raise HTTPException(422, f"代码草稿和修正代码最多各 {main.SCRATCH_MAX_LINES} 行")
    table = None
    if data.table is not None:
        table = main.normalize_scratch_table(data.table)
        if table is None:
            raise HTTPException(422, "演算表结构不合法")
    if main.scratch_serialized_size(data.code, data.fixed, table) > main.SCRATCH_SIZE_LIMIT:
        raise HTTPException(413, f"草稿内容合计最多 {main.SCRATCH_SIZE_LIMIT} 字符")
    table_json = (
        json.dumps(table, ensure_ascii=False, separators=(",", ":"))
        if table is not None else None
    )

    # connect(write=True) 已取得 BEGIN IMMEDIATE 写锁；版本比较与写入在同一
    # 事务内，两个同版本并发 PUT 只有先拿到写锁的一个能成功。
    with main.connect(write=True) as conn:
        main.owned_mistake(conn, mistake_id, user["id"])
        row = conn.execute(
            "SELECT version FROM mistake_scratch WHERE mistake_id = ?",
            (mistake_id,),
        ).fetchone()
        current_version = row["version"] if row is not None else 0
        if data.version != current_version:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "草稿已在其他窗口被修改，请先选择处理方式",
                    "current": main.scratch_state(conn, mistake_id),
                },
            )
        updated_at = main.utc_now()
        next_version = current_version + 1
        if row is None:
            conn.execute(
                """
                INSERT INTO mistake_scratch(
                    mistake_id, user_id, version, code, fixed, table_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (mistake_id, user["id"], next_version, data.code, data.fixed,
                 table_json, updated_at),
            )
        else:
            conn.execute(
                """
                UPDATE mistake_scratch
                SET version = ?, code = ?, fixed = ?, table_json = ?, updated_at = ?
                WHERE mistake_id = ?
                """,
                (next_version, data.code, data.fixed, table_json, updated_at,
                 mistake_id),
            )
    return {"version": next_version, "updated_at": updated_at}
