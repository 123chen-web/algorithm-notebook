"""review routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from activity import day_counts
from ai_limits import refund_on_server_failure, duck_daily_limit
from contextlib import ExitStack
from datetime import date
from datetime import timedelta
from duck_prompt import check_turns
from fastapi import Depends
from fastapi import HTTPException
from pydantic import StringConstraints
from scheduler import preview_all
from scheduler import today_in_timezone
from tags import tags_for_mistakes
from typing import Annotated
import ai
import json
import os
from fastapi import APIRouter


router = APIRouter()

# 撤销窗口放宽到 24 小时；再长就更可能影响连续打卡/榜单。
UNDO_WINDOW_SECONDS = 24 * 3600


@router.get("/api/review/queue")
def rvb_get_queue(
    ignore_cap: bool = False, zone: str | None = None,
    tag: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None,
    user=Depends(main.current_user),
):
    if zone is not None and zone not in main.PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    with main.connect() as conn:
        conn.execute("BEGIN")
        fresh = main.rvb_account(conn, user["id"])
        today = main.today_for(fresh)
        sql = main.MISTAKE_SELECT + (
            " WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?"
        )
        params = [user["id"], today.isoformat()]
        if zone is not None:
            sql += " AND p.zone = ?"
            params.append(zone)
        if tag:
            sql += " AND EXISTS (SELECT 1 FROM mistake_tags t WHERE t.mistake_id = m.id AND t.tag = ?)"
            params.append(tag)
        rows = conn.execute(sql, params).fetchall()
        tags_by_id = tags_for_mistakes(conn, [row["id"] for row in rows])
        items = [
            main.mistake_public({**dict(row), "tags": tags_by_id[row["id"]]})
            for row in rows
        ]
        review_days, _, _ = day_counts(conn, user["id"], fresh["timezone"])
        done_today = review_days.get(today, 0)
    return main.rvb_review_queue(items, today, fresh["daily_review_cap"], done_today, ignore_cap)


@router.post("/api/mistakes/{mistake_id}/review")
def review_mistake(
    mistake_id: int,
    data: main.ReviewInput,
    user=Depends(main.current_user),
):
    with main.connect(write=True) as conn:
        fresh = main.rvb_account(conn, user["id"])
        now = main.utc_now()
        if data.client_op_id is not None:
            # 重复提交（含评分之后又被撤销的）一律返回第一次保存的响应，不重新评分。
            replay = main.pwa_review_op_replay(conn, user["id"], mistake_id, data.client_op_id, now)
            if replay is not None:
                return replay
        item = main.owned_mistake(conn, mistake_id, user["id"])

        if data.reviewed_at is None:
            reviewed_at = now
            day = main.today_for(fresh)
        else:
            # 补交：评分时间、调度用的“今天”都以客户端当时的时间为准，按用户本地时区换算。
            given = main.pwa_reviewed_at(data, item, now)
            reviewed_at = given.isoformat()
            day = today_in_timezone(fresh["timezone"], given)

        if data.version is not None and item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["suspended_at"] is not None:
            raise HTTPException(409, "这条易错点已暂停，请先恢复")
        if item["due_date"] > day.isoformat():
            raise HTTPException(409, "这条易错点尚未到期，今天不需要再次评分")

        overdue_days = max(0, (day - date.fromisoformat(item["due_date"])).days)
        state = main.schedule(
            repetitions=item["repetitions"],
            interval_days=item["interval_days"],
            ease_factor=item["ease_factor"],
            quality=data.quality,
            reviewed_on=day,
            overdue_days=overdue_days,
        )

        conn.execute(
            """
            UPDATE mistakes
            SET repetitions = ?, interval_days = ?, ease_factor = ?,
                due_date = ?, last_reviewed_at = ?, version = version + 1
            WHERE id = ?
            """,
            (
                state["repetitions"], state["interval_days"],
                state["ease_factor"], state["due_date"],
                reviewed_at, mistake_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO reviews(
                mistake_id, quality, reviewed_at, next_due_date,
                elapsed_days, scheduled_days, ease_before, repetitions_before,
                due_before, last_reviewed_before, version_after
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mistake_id, data.quality, reviewed_at, state["due_date"],
                item["interval_days"] + overdue_days, item["interval_days"],
                item["ease_factor"], item["repetitions"],
                item["due_date"], item["last_reviewed_at"], item["version"] + 1,
            ),
        )
        response = {**state, "version": item["version"] + 1}
        if data.client_op_id is not None:
            conn.execute(
                "INSERT INTO review_ops(user_id, client_op_id, mistake_id, response, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user["id"], data.client_op_id, mistake_id, json.dumps(response), now),
            )

    return response


@router.get("/api/mistakes/{mistake_id}/preview")
def rvb_preview(mistake_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        conn.execute("BEGIN")
        fresh = main.rvb_account(conn, user["id"])
        item = main.owned_mistake(conn, mistake_id, user["id"])
        day = main.today_for(fresh)
        previews = preview_all(
            repetitions=item["repetitions"], interval_days=item["interval_days"],
            ease_factor=item["ease_factor"], reviewed_on=day,
            overdue_days=max(0, (day - date.fromisoformat(item["due_date"])).days),
        )
    return {
        "version": item["version"],
        "previews": {
            quality: {"interval_days": state["interval_days"], "due_date": state["due_date"]}
            for quality, state in previews.items()
        },
    }


@router.post("/api/mistakes/{mistake_id}/review/undo")
def rvb_undo_review(mistake_id: int, data: main.RvbVersionInput, user=Depends(main.current_user)):
    # connect(write=True) 持有 BEGIN IMMEDIATE；重读、恢复、删除日志一起提交。
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        item = main.owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        review = conn.execute(
            "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id DESC LIMIT 1",
            (mistake_id,),
        ).fetchone()
        if review is None:
            raise HTTPException(409, "没有可以撤销的评分")
        if review["due_before"] is None:
            raise HTTPException(409, "这次评分太早，不能撤销")
        if main.datetime.fromisoformat(main.utc_now()) - main.datetime.fromisoformat(review["reviewed_at"]) > timedelta(seconds=UNDO_WINDOW_SECONDS):
            raise HTTPException(409, "超过 24 小时，不能撤销")
        if item["version"] != review["version_after"]:
            raise HTTPException(409, "这条记录之后又被修改过，不能撤销")
        restored = {
            "repetitions": review["repetitions_before"],
            "interval_days": review["scheduled_days"],
            "ease_factor": review["ease_before"],
            "due_date": review["due_before"],
            "last_reviewed_at": review["last_reviewed_before"],
            "version": item["version"] + 1,
        }
        conn.execute(
            """
            UPDATE mistakes SET repetitions = :repetitions, interval_days = :interval_days,
                ease_factor = :ease_factor, due_date = :due_date,
                last_reviewed_at = :last_reviewed_at, version = :version
            WHERE id = :id
            """,
            {**restored, "id": mistake_id},
        )
        conn.execute("DELETE FROM reviews WHERE id = ?", (review["id"],))
    return restored


@router.post("/api/mistakes/{mistake_id}/snooze")
def rvb_snooze(mistake_id: int, data: main.RvbSnoozeInput, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        fresh = main.rvb_account(conn, user["id"])
        item = main.owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["suspended_at"] is not None:
            raise HTTPException(409, "这条易错点已暂停，请先恢复")
        today = main.today_for(fresh)
        if item["due_date"] > today.isoformat():
            raise HTTPException(409, "这条还没到期")
        due = (today + timedelta(days=data.days)).isoformat()
        conn.execute(
            "UPDATE mistakes SET due_date = ?, version = version + 1 WHERE id = ?",
            (due, mistake_id),
        )
    return {"due_date": due, "version": item["version"] + 1}


@router.post("/api/mistakes/{mistake_id}/suspend")
def rvb_suspend(mistake_id: int, data: main.RvbVersionInput, user=Depends(main.current_user)):
    return main.rvb_set_suspension(mistake_id, data, user, True)


@router.post("/api/mistakes/{mistake_id}/unsuspend")
def rvb_unsuspend(mistake_id: int, data: main.RvbVersionInput, user=Depends(main.current_user)):
    return main.rvb_set_suspension(mistake_id, data, user, False)


@router.post("/api/mistakes/{mistake_id}/variants", status_code=201)
def create_variant(mistake_id: int, user=Depends(main.current_user)):
    # BEGIN IMMEDIATE 后重读套餐并扣额，与支付回调等写入串行执行。
    # 配额在短事务内原子扣除，网络请求期间不持有数据库写锁。
    with ExitStack() as stack:
        with main.connect(write=True) as conn:
            item = main.owned_mistake(conn, mistake_id, user["id"])
            # 完全没有复习记录时是 None，generate() 的提示词行为跟之前完全一样；
            # 有反复偏低/偏高的复习历史时才提示 AI 调整新题难度。
            item["mastery_signal"] = main.mastery_signal(conn, mistake_id)
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
                (
                    user["id"],
                    day,
                    limit,
                    limit,
                ),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")

        with refund_on_server_failure(user["id"], day, main.connect):
            with main.track_call(user["id"], "variant"):
                generated = ai.generate(item)
        is_code_zone = item["zone"] in main.CODE_ZONES
        rows = []
        for question in generated["questions"]:
            if is_code_zone:
                description = f"{question['question']}\n\n【样例】\n{question['answer']}"
                expected_answer = ""
            else:
                description = question["question"]
                expected_answer = question["answer"]
            rows.append((description, expected_answer))

        with main.connect(write=True) as conn:
            # 重新读取当前错因：生成期间用户可能已经自己编辑过，不能用生成前的
            # 旧快照来判断是否需要回填，否则可能覆盖掉用户刚写的内容。
            current = main.owned_mistake(conn, mistake_id, user["id"])
            variants = []
            created_at = main.utc_now()
            for description, expected_answer in rows:
                cursor = conn.execute(
                    """
                    INSERT INTO variants(
                        mistake_id, description, model, created_at, expected_answer
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        mistake_id, description, generated["model"],
                        created_at, expected_answer,
                    ),
                )
                created_row = dict(conn.execute(
                    main.VARIANT_SELECT + " WHERE v.id = ?", (cursor.lastrowid,),
                ).fetchone())
                variants.append(main.redact_pending_answer(created_row))
            if not current["description"].strip():
                conn.execute(
                    "UPDATE mistakes SET description = ? WHERE id = ?",
                    (generated["mistake_summary"], mistake_id),
                )
                current["description"] = generated["mistake_summary"]
        return {"variants": variants, "mistake_description": current["description"]}


@router.post("/api/mistakes/{mistake_id}/duck")
def duck_panel_chat(mistake_id: int, data: main.DuckInput, user=Depends(main.current_user)):
    """讲给小黄鸭听：独立每日额度，复用并发/记账与失败退还规则。

    校验失败（422）在扣额度之前；502/503/504 退回本次扣的额度，
    回复不合格重试一次仍失败也按 502 退回，不消耗用户的当日额度。
    """
    turns = [{"role": turn.role, "text": turn.text} for turn in data.turns]
    try:
        check_turns(turns)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not turns:
        raise HTTPException(422, "对话不能为空")
    if data.finish:
        if turns[-1]["role"] != "duck":
            raise HTTPException(422, "请先完成至少一轮对话，再结束并总结")
    elif turns[-1]["role"] != "user":
        raise HTTPException(422, "新的发言必须以用户发言结尾")

    with ExitStack() as stack:
        with main.connect(write=True) as conn:
            item = main.owned_mistake(conn, mistake_id, user["id"])
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")

            day = main.today_for(user).isoformat()
            main.rvb_account(conn, user['id'])
            limit = duck_daily_limit()
            usage = conn.execute('SELECT attempts FROM duck_usage WHERE user_id = ? AND day = ?', (user['id'], day)).fetchone()
            used = usage['attempts'] if usage else 0
            if used >= limit:
                raise HTTPException(429, "今天的小黄鸭次数已用完，明天再来吧。")
            stack.enter_context(main.ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO duck_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = duck_usage.attempts + 1
                WHERE duck_usage.attempts < ?
                """,
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的小黄鸭次数已用完，明天再来吧。")

        with refund_on_server_failure(user["id"], day, main.connect, duck=True):
            with main.track_call(user["id"], "duck"):
                reply = main.duck_ai_reply(item, turns, data.finish)

    return {
        "reply": reply,
        "turns_used": sum(1 for turn in turns if turn["role"] == "user"),
        "ai_remaining": max(0, limit - used - 1),
    }


@router.put("/api/variants/{variant_id}/result")
def save_variant_result(
    variant_id: int,
    data: main.VariantResult,
    user=Depends(main.current_user),
):
    with main.connect(write=True) as conn:
        owned = conn.execute(
            """
            SELECT v.id, v.expected_answer
            FROM variants v
            JOIN mistakes m ON m.id = v.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE v.id = ? AND p.user_id = ?
            """,
            (variant_id, user["id"]),
        ).fetchone()
        if owned is None:
            raise HTTPException(404, "变体题不存在")

        result = data.result
        if owned["expected_answer"] and data.answer.strip():
            result = (
                "solved"
                if main.normalize_math_answer(data.answer)
                == main.normalize_math_answer(owned["expected_answer"])
                else "failed"
            )

        conn.execute(
            """
            UPDATE variants
            SET result = ?, answer_code = ?, answer = ?, result_updated_at = ?
            WHERE id = ?
            """,
            (
                result, data.answer_code, data.answer,
                main.utc_now(), variant_id,
            ),
        )
        result = conn.execute(
            main.VARIANT_SELECT + " WHERE v.id = ?",
            (variant_id,),
        ).fetchone()

    # 保存练习结果不会隐式修改原易错点的复习计划。
    return dict(result)
