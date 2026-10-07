"""Bounded notebook uploads with SQLite previews and atomic, idempotent confirmation.

Markdown parsing and record insertion reuse the CLI. Previews are shared across
workers; they contain private notes, expire after 30 minutes, and are erased on
account deletion. Names owned by main are always accessed as main.xxx.
"""
import json
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, StrictInt, ValidationError
from starlette.datastructures import UploadFile

import import_notes
import main

router = APIRouter()
IMPORT_MAX_BYTES = 2 * 1024 * 1024
IMPORT_MAX_RECORDS = 200
PREVIEW_TTL_SECONDS = 30 * 60
PREVIEWS_PER_USER = 3
PREVIEW_RECEIPTS_PER_USER = 20
PREVIEWS_TOTAL = 200
IMPORT_MAX_REQUEST_BYTES = IMPORT_MAX_BYTES + 64 * 1024


class ImportConfirm(main.InputModel):
    preview_id: str = Field(min_length=1, max_length=128)
    indices: list[StrictInt] = Field(min_length=1, max_length=IMPORT_MAX_RECORDS)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 字段重复")
        result[key] = value
    return result


def _original_date(record):
    value = record.get("created_at")
    if value is None:
        return
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("原记录日期无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("原记录日期无效") from exc
    if parsed.tzinfo is None:
        raise ValueError("原记录日期需要包含时区")


def _parse_export(text, zone):
    payload = json.loads(text, object_pairs_hook=_unique_object)
    if not isinstance(payload, dict) or not isinstance(payload.get("problems"), list):
        raise ValueError("JSON 需要是本站导出的 problems 列表")
    if len(payload["problems"]) > IMPORT_MAX_RECORDS:
        raise HTTPException(413, "一次最多导入 200 道题")
    records = []
    for item in payload["problems"]:
        if not isinstance(item, dict) or not isinstance(item.get("mistakes"), list):
            raise ValueError("题目或易错点结构无效")
        descriptions, pending = [], []
        for mistake in item["mistakes"]:
            if not isinstance(mistake, dict) or not isinstance(mistake.get("description"), str):
                raise ValueError("易错点结构无效")
            flag = mistake.get("pending_reason", False)
            if not isinstance(flag, bool):
                raise ValueError("待补标记需要是布尔值")
            descriptions.append(main.QUICK_MISTAKE_PLACEHOLDER if flag else mistake["description"])
            pending.append(flag)
        fields = {key: item.get(key) for key in ("title", "language", "code", "thinking")}
        # Ordinary validation protects all limits; no client-supplied scheduling is imported.
        valid = main.NewProblem(**fields, zone=zone, mistakes=descriptions, quick=False)
        record = {**valid.model_dump(exclude={"zone", "quick"}), "fallback": False,
                  "pending": pending}
        if "created_at" in item:
            record["created_at"] = item["created_at"]
        _original_date(record)
        records.append(record)
    return records, []


def _parse_markdown(text):
    # CLI accepts a path. Use only the default system temp directory; no fallback.
    fd, filename = tempfile.mkstemp(suffix=".md", prefix="oy-import-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        records, skipped = import_notes.parse_file(Path(filename))
        for record in records:
            _original_date(record)
        return records, skipped
    finally:
        os.unlink(filename)


async def _bounded_request(request):
    # Check actual bytes before multipart parsing can spool files into TMP.
    # This also bounds chunked uploads without a trustworthy Content-Length.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > IMPORT_MAX_REQUEST_BYTES:
            raise HTTPException(413, "上传请求太大，文件最多 2 MiB")
        body.extend(chunk)
    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}
    return Request(request.scope, receive)


async def _read_form(request):
    bounded = await _bounded_request(request)
    async with bounded.form(max_files=1, max_fields=1, max_part_size=64 * 1024) as form:
        if set(form) - {"file", "zone"} or len(form.getlist("file")) != 1 or len(form.getlist("zone")) > 1:
            raise HTTPException(422, "请选择一个文件和目标分区")
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise HTTPException(422, "请先选择文件")
        content = await file.read(IMPORT_MAX_BYTES + 1)
        return file.filename, form.get("zone", "算法"), content


@router.post("/api/import/preview")
async def preview_import(request: Request, user=Depends(main.current_user)):
    filename, zone, content = await _read_form(request)
    if zone not in main.PROBLEM_ZONES:
        raise HTTPException(422, "请选择一个有效的题目分区")
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".md", ".markdown", ".json"}:
        raise HTTPException(415, "请上传 .md、.markdown 或本站导出的 .json 文件")
    if len(content) > IMPORT_MAX_BYTES:
        raise HTTPException(413, "文件太大，最多 2 MiB")
    try:
        text = content.decode("utf-8-sig")
        if not text.strip() or "\0" in text:
            raise ValueError("文件是空的或包含无效字符")
        records, skipped = (_parse_export(text, zone) if suffix == ".json" else _parse_markdown(text))
        if not records:
            raise ValueError("文件没有可导入的题目")
        if len(records) > IMPORT_MAX_RECORDS:
            raise HTTPException(413, "一次最多导入 200 道题")
    except (ValueError, UnicodeDecodeError, ValidationError, RecursionError) as exc:
        message = "JSON 结构或内容无效" if isinstance(exc, (ValidationError, RecursionError)) else str(exc)
        raise HTTPException(422, f"解析失败：{message}") from exc

    token, now = secrets.token_urlsafe(32), int(time.time())
    with main.connect(write=True) as conn:
        main.sec_recheck_session(conn, user["id"], request)
        conn.execute("DELETE FROM import_previews WHERE expires_at <= ?", (now,))
        if conn.execute("SELECT COUNT(*) FROM import_previews WHERE user_id = ? AND result IS NULL", (user["id"],)).fetchone()[0] >= PREVIEWS_PER_USER:
            raise HTTPException(429, "已有 3 份未确认预览，请完成导入或稍后再试")
        if conn.execute("SELECT COUNT(*) FROM import_previews WHERE user_id = ?", (user["id"],)).fetchone()[0] >= PREVIEW_RECEIPTS_PER_USER:
            raise HTTPException(429, "短时间预览次数较多，最多保留 20 份预览或确认记录；请 30 分钟后再试")
        if conn.execute("SELECT COUNT(*) FROM import_previews").fetchone()[0] >= PREVIEWS_TOTAL:
            raise HTTPException(429, "当前导入预览较多，请稍后再试")
        existing = import_notes.existing_titles(conn, user["id"])
        conn.execute("INSERT INTO import_previews(token,user_id,zone,records,expires_at) VALUES (?,?,?,?,?)",
                     (token, user["id"], zone, json.dumps(records, ensure_ascii=False), now + PREVIEW_TTL_SECONDS))
    items, seen = [], set()
    for index, record in enumerate(records):
        warnings = []
        if record["title"] in existing or record["title"] in seen:
            warnings.append("标题重复，导入时会跳过")
        if record["fallback"]:
            warnings.append("原笔记未记录思路，已用题目描述代填")
        if suffix == ".json":
            warnings.append("重新安排复习；历史评分、变体和标签不恢复")
        seen.add(record["title"])
        items.append({"index": index, "title": record["title"], "zone": zone,
                      "mistakes_count": len(record["mistakes"]), "warnings": warnings})
    return {"preview_id": token, "items": items, "skipped": skipped,
            "expires_in": PREVIEW_TTL_SECONDS}


@router.post("/api/import/confirm")
def confirm_import(data: ImportConfirm, request: Request, user=Depends(main.current_user)):
    indices = sorted(set(data.indices))
    try:
        with main.connect(write=True) as conn:
            main.sec_recheck_session(conn, user["id"], request)
            entry = conn.execute("SELECT * FROM import_previews WHERE token = ? AND user_id = ? AND expires_at > ?",
                                 (data.preview_id, user["id"], int(time.time()))).fetchone()
            if entry is None:
                raise HTTPException(410, "预览已过期或不存在，请重新上传文件")
            if entry["result"] is not None:
                if json.loads(entry["selected_indices"]) != indices:
                    raise HTTPException(409, "这份预览已确认；更改选择请重新上传文件")
                return json.loads(entry["result"])
            records = json.loads(entry["records"])
            if any(index < 0 or index >= len(records) for index in indices):
                raise HTTPException(422, "条目序号越界")
            existing = import_notes.existing_titles(conn, user["id"])
            result = {"imported": 0, "duplicates": [], "failed": []}
            now, day = main.utc_now(), main.today_for(user).isoformat()
            for index in indices:
                record = records[index]
                if record["title"] in existing:
                    result["duplicates"].append({"index": index, "reason": "标题已存在，已跳过"})
                    continue
                import_notes.insert_record(conn, record, user["id"], day, now)
                problem_id = conn.execute("SELECT id FROM problems WHERE user_id = ? AND title = ? ORDER BY id DESC LIMIT 1",
                                          (user["id"], record["title"])).fetchone()[0]
                conn.execute("UPDATE problems SET zone = ? WHERE id = ? AND user_id = ?", (entry["zone"], problem_id, user["id"]))
                if any(record.get("pending", [])):
                    ids = [row[0] for row in conn.execute("SELECT id FROM mistakes WHERE problem_id = ? ORDER BY id", (problem_id,))]
                    conn.executemany("UPDATE mistakes SET pending_reason = ? WHERE id = ?",
                                     [(int(flag), mistake_id) for flag, mistake_id in zip(record["pending"], ids)])
                existing.add(record["title"])
                result["imported"] += 1
            conn.execute("UPDATE import_previews SET selected_indices = ?, result = ?, records = '[]' WHERE token = ? AND user_id = ?",
                         (json.dumps(indices), json.dumps(result, ensure_ascii=False), data.preview_id, user["id"]))
            return result
    except sqlite3.Error as exc:
        raise HTTPException(503, "导入暂时失败，此文件未写入；请重试") from exc
