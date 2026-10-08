"""problems routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from ai_limits import refund_on_server_failure
from contextlib import ExitStack
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
import ai
import os
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/zones")
def list_zones():
    return {"zones": list(main.PROBLEM_ZONES), "code_zones": list(main.CODE_ZONES)}


@router.get("/api/problems")
def list_problems(limit: int = 30, user=Depends(main.current_user)):
    """最近的题目（id/title/zone），给记笔记 composer 的"关联题目"下拉用。"""
    limit = max(1, min(limit, 100))
    with main.connect() as conn:
        rows = conn.execute(
            "SELECT id, title, zone FROM problems WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (user["id"], limit),
        ).fetchall()
    return {"problems": [dict(row) for row in rows]}


@router.post("/api/problems", status_code=201)
def create_problem(data: main.NewProblem, user=Depends(main.current_user)):
    day = main.today_for(user).isoformat()
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        cursor = conn.execute(
            """
            INSERT INTO problems(
                user_id, title, zone, language, code, thinking, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], data.title, data.zone, data.language,
                data.code, data.thinking, main.utc_now(),
            ),
        )
        problem_id = cursor.lastrowid
        mistake_ids = []
        if data.quick and not data.mistakes:
            # 速记：只留证据，自动建 1 条"待补原因"的易错点。
            cursor = conn.execute(
                """
                INSERT INTO mistakes(problem_id, description, due_date, pending_reason)
                VALUES (?, ?, ?, 1)
                """,
                (problem_id, main.QUICK_MISTAKE_PLACEHOLDER, day),
            )
            mistake_ids.append(cursor.lastrowid)
        else:
            for description in data.mistakes:
                cursor = conn.execute(
                    """
                    INSERT INTO mistakes(problem_id, description, due_date)
                    VALUES (?, ?, ?)
                    """,
                    (problem_id, description, day),
                )
                mistake_ids.append(cursor.lastrowid)

    return {"id": problem_id, "mistake_ids": mistake_ids}


@router.post("/api/problems/photo")
async def recognize_problem_photo(user=Depends(main.current_user), file: UploadFile = File(...)):
    # 只识别、不落库：返回结构化字段供前端预填新增记录表单，用户确认后
    # 仍然走 create_problem 那条已有校验路径，这里不重复实现建档逻辑。
    content = await file.read(main.PHOTO_MAX_BYTES + 1)
    if len(content) > main.PHOTO_MAX_BYTES:
        raise HTTPException(413, f"图片太大，最多 {main.PHOTO_MAX_BYTES // (1024 * 1024)} MiB")
    if not content:
        raise HTTPException(400, "文件是空的")

    image = main.decode_uploaded_image(content)
    jpeg_bytes = await main.run_in_threadpool(main.resize_photo_for_recognition, image)

    # 配额检查和扣减跟生成练习题共用同一套逻辑：同一次 BEGIN IMMEDIATE 事务内
    # 原子扣减，AI 服务端失败（502/503/504）时退还；AI 调用本身放到事务外面执行，不在网络
    # 请求期间持有数据库写锁。
    with ExitStack() as stack:
        with main.connect(write=True) as conn:
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
            day = main.today_for(user).isoformat()
            quota = main.ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(main.ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO ai_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = ai_usage.attempts + 1
                WHERE ai_usage.attempts < ?
                """,
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")

        with refund_on_server_failure(user["id"], day, main.connect):
            with main.track_call(user["id"], "photo"):
                return await main.run_in_threadpool(ai.recognize_photo, jpeg_bytes)


@router.put("/api/problems/{problem_id}")
def edit_problem(problem_id: int, data: main.ProblemEdit, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.owned_problem(conn, problem_id, user["id"])
        conn.execute(
            """
            UPDATE problems
            SET title = ?, zone = ?, language = ?, code = ?, thinking = ?
            WHERE id = ?
            """,
            (
                data.title, data.zone, data.language,
                data.code, data.thinking, problem_id,
            ),
        )
        updated = conn.execute(
            "SELECT * FROM problems WHERE id = ?", (problem_id,)
        ).fetchone()
    return dict(updated)


@router.delete("/api/problems/{problem_id}")
def delete_problem(problem_id: int, user=Depends(main.current_user)):
    # 级联删除该题下的全部易错点、复习记录和变体题。
    with main.connect(write=True) as conn:
        main.owned_problem(conn, problem_id, user["id"])
        conn.execute("DELETE FROM problems WHERE id = ?", (problem_id,))
    return {"ok": True}
