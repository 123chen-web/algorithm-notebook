"""review routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
from core import config, ratelimit, security, time
from models.ai import DuckInput
from models.review import ReviewInput
from models.review import RvbSnoozeInput
from models.review import SpreadUndoInput
from models.review import RvbVersionInput
from models.review import VariantResult
import accounts
import ai
import ai_limits
import db
import pattern_match
import problem_progress
import review_lib
import review_verify
import scheduler
from models import InputModel
from problem_progress import enrich_problems

from activity import day_counts
from ai_limits import refund_on_server_failure, duck_daily_limit
from contextlib import ExitStack
from datetime import timedelta
from duck_prompt import check_turns
from fastapi import Depends
from fastapi import HTTPException
from pydantic import StringConstraints
from scheduler import preview_all
from scheduler import today_in_timezone
from tags import tags_for_mistakes
from typing import Annotated, Literal
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
    user=Depends(security.current_user),
):
    if zone is not None and zone not in config.PROBLEM_ZONES:
        raise HTTPException(400, "分区不存在")
    with db.connect() as conn:
        conn.execute("BEGIN")
        fresh = accounts.rvb_account(conn, user["id"])
        today = time.today_for(fresh)
        sql = config.MISTAKE_SELECT + (
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
            problem_progress.mistake_public({**dict(row), "tags": tags_by_id[row["id"]]})
            for row in rows
        ]
        items = pattern_match.attach_pattern_ids(conn, enrich_problems(conn, user['id'], items, today.isoformat()))
        last_modes = review_verify.last_modes(conn, [item["id"] for item in items])
        for item in items:
            item["last_review_mode"] = last_modes.get(item["id"])
        review_days, _, _ = day_counts(conn, user["id"], fresh["timezone"])
        done_today = review_days.get(today, 0)
    return review_lib.rvb_review_queue(items, today, fresh["daily_review_cap"], done_today, ignore_cap)


@router.post("/api/review/spread")
def spread_backlog(user=Depends(security.current_user)):
    """补欠账：今天上限放不下的逾期记录，按“最该复习”的先后排进明天起的日子，每天不超过上限。

    和 Anki 的“推迟”一样只改到期日（记忆状态不动）；返回每条原来的到期日和新版本号，前端可一键撤销。
    所有分区一起摊，不看筛选。没设每日上限时没有“欠账”可言，返回 409。
    """
    with db.connect(write=True) as conn:
        fresh = accounts.rvb_account(conn, user["id"])
        cap = fresh["daily_review_cap"]
        if cap is None:
            raise HTTPException(409, "先设一个每日上限，才知道每天摊多少条")
        today = time.today_for(fresh)
        rows = conn.execute(
            "SELECT m.id, m.due_date, m.interval_days, m.version FROM mistakes m "
            "JOIN problems p ON p.id = m.problem_id "
            "WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?",
            (user["id"], today.isoformat()),
        ).fetchall()
        ordered = review_lib.queue_order([dict(row) for row in rows], today)
        review_days, _, _ = day_counts(conn, user["id"], fresh["timezone"])
        keep = max(0, cap - review_days.get(today, 0))
        load = {
            row[0]: row[1] for row in conn.execute(
                "SELECT m.due_date, COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id "
                "WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date > ? GROUP BY m.due_date",
                (user["id"], today.isoformat()),
            )
        }
        plan = review_lib.spread_plan(ordered[keep:], today, cap, load)
        moved = []
        for item, due in plan:
            conn.execute("UPDATE mistakes SET due_date = ?, version = version + 1 WHERE id = ?", (due, item["id"]))
            moved.append({"id": item["id"], "from": item["due_date"], "to": due, "version": item["version"] + 1})
    days = sorted({entry["to"] for entry in moved})
    return {"moved": moved, "days": len(days), "last": days[-1] if days else None, "cap": cap}


@router.post("/api/review/spread/undo")
def spread_backlog_undo(data: SpreadUndoInput, user=Depends(security.current_user)):
    """撤销补欠账：版本号对得上（摊开后没复习、没改过）的才改回原来的到期日。"""
    restored = 0
    with db.connect(write=True) as conn:
        for entry in data.items:
            cursor = conn.execute(
                "UPDATE mistakes SET due_date = ?, version = version + 1 "
                "WHERE id = ? AND version = ? AND problem_id IN (SELECT id FROM problems WHERE user_id = ?)",
                (entry.due_date, entry.id, entry.version, user["id"]),
            )
            restored += cursor.rowcount
    return {"restored": restored, "skipped": len(data.items) - restored}


@router.post("/api/mistakes/{mistake_id}/review")
def review_mistake(
    mistake_id: int,
    data: ReviewInput,
    user=Depends(security.current_user),
):
    with db.connect(write=True) as conn:
        fresh = accounts.rvb_account(conn, user["id"])
        now = time.utc_now()
        if data.client_op_id is not None:
            # 重复提交（含评分之后又被撤销的）一律返回第一次保存的响应，不重新评分。
            replay = review_lib.pwa_review_op_replay(conn, user["id"], mistake_id, data.client_op_id, now)
            if replay is not None:
                return replay
        item = problem_progress.owned_mistake(conn, mistake_id, user["id"])

        if data.reviewed_at is None:
            reviewed_at = now
            day = time.today_for(fresh)
        else:
            # 补交：评分时间、调度用的“今天”都以客户端当时的时间为准，按用户本地时区换算。
            given = review_lib.pwa_reviewed_at(data, item, now)
            reviewed_at = given.isoformat()
            day = today_in_timezone(fresh["timezone"], given)

        if data.version is not None and item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["suspended_at"] is not None:
            raise HTTPException(409, "这条易错点已暂停，请先恢复")
        if item["due_date"] > day.isoformat():
            raise HTTPException(409, "这条易错点尚未到期，今天不需要再次评分")

        # 成绩由做题结果算出（验证方式）或被自评上限压住（只回忆），再交给 FSRS 排期。
        quality, evidence = review_verify.settle(data.mode, data.quality, data.evidence, item["zone"])
        card, last_day = scheduler.card_state(item, fresh["timezone"])
        state = scheduler.schedule(card, last_day, item["repetitions"], quality, day)
        state, streak_capped = review_verify.limit_unverified_streak(
            state, day, data.mode, review_verify.previous_mode(conn, mistake_id), item["zone"],
        )

        conn.execute(
            """
            UPDATE mistakes
            SET repetitions = ?, interval_days = ?, ease_factor = ?,
                stability = ?, difficulty = ?,
                due_date = ?, last_reviewed_at = ?, version = version + 1
            WHERE id = ?
            """,
            (
                state["repetitions"], state["interval_days"],
                state["ease_factor"], state["stability"], state["difficulty"],
                state["due_date"], reviewed_at, mistake_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO reviews(
                mistake_id, quality, reviewed_at, next_due_date,
                elapsed_days, scheduled_days, ease_before, repetitions_before,
                due_before, last_reviewed_before, version_after, mode, evidence,
                stability_before, difficulty_before
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mistake_id, quality, reviewed_at, state["due_date"],
                (day - last_day).days if last_day else 0, item["interval_days"],
                item["ease_factor"], item["repetitions"],
                item["due_date"], item["last_reviewed_at"], item["version"] + 1,
                data.mode, evidence, item["stability"], item["difficulty"],
            ),
        )
        # leech 判定：连续失败 8 次（对标 Anki 默认）自动暂停。
        # 暂停是本次评分的一部分，共用评分日志的 version_after，才能撤销。
        # 评分前已拒绝暂停的记录，因此撤销时原暂停状态必然是 NULL。
        leech_suspended = False
        if quality < 3:
            recent = conn.execute(
                "SELECT quality FROM reviews WHERE mistake_id = ? ORDER BY id DESC LIMIT 8",
                (mistake_id,),
            ).fetchall()
            if len(recent) == 8 and all(row["quality"] < 3 for row in recent):
                conn.execute(
                    "UPDATE mistakes SET suspended_at = ? WHERE id = ?",
                    (now, mistake_id),
                )
                leech_suspended = True
        final_version = item["version"] + 1
        # 稳定度和难度只在服务端内部使用，不放进响应，接口形状保持不变。
        response = {
            **{key: state[key] for key in ("repetitions", "interval_days", "ease_factor", "due_date")},
            "version": final_version,
        }
        if leech_suspended:
            response["leech_suspended"] = True
        if streak_capped:
            response["unverified_cap"] = True
        # 复仇成功：本次评分满足条件且该条从未触发过时，写入标记并附在响应里，
        # 前端评分后立刻盖章庆祝。每条只触发一次。
        revenge = review_lib.check_revenge(
            conn, mistake_id, quality, data.mode, reviewed_at, evidence
        )
        if revenge is not None:
            conn.execute(
                "UPDATE mistakes SET revenge_at = ? WHERE id = ? AND revenge_at IS NULL",
                (reviewed_at, mistake_id),
            )
        response["revenge"] = revenge
        if data.client_op_id is not None:
            conn.execute(
                "INSERT INTO review_ops(user_id, client_op_id, mistake_id, response, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user["id"], data.client_op_id, mistake_id, json.dumps(response), now),
            )

    return response


@router.get("/api/mistakes/{mistake_id}/redo-hint")
def rvb_redo_hint(mistake_id: int, user=Depends(security.current_user)):
    """防自评虚高的轻提示：最近 14 天内“只回忆”打高分、紧接着又评低分出现
    >=2 次时 hint 为 True，前端下次复习默认引导到重做。"""
    with db.connect() as conn:
        problem_progress.owned_mistake(conn, mistake_id, user["id"])  # 不存在/非本人 → 404
        since = (time.datetime.now(time.timezone.utc) - timedelta(days=14)).isoformat()
        hint = review_lib.redo_overconfident_hint(conn, mistake_id, since)
    return {"hint": hint}


@router.get("/api/mistakes/{mistake_id}/preview")
def rvb_preview(mistake_id: int, mode: Literal["recall", "rewrite", "redo", "similar"] = "recall",
                user=Depends(security.current_user)):
    with db.connect() as conn:
        conn.execute("BEGIN")
        fresh = accounts.rvb_account(conn, user["id"])
        item = problem_progress.owned_mistake(conn, mistake_id, user["id"])
        day = time.today_for(fresh)
        card, last_day = scheduler.card_state(item, fresh["timezone"])
        previews = review_verify.limit_previews(
            preview_all(card, last_day, item["repetitions"], day),
            day, mode, review_verify.previous_mode(conn, mistake_id), item["zone"],
        )
    return {
        "version": item["version"],
        "previews": {
            quality: {"interval_days": state["interval_days"], "due_date": state["due_date"]}
            for quality, state in previews.items()
        },
    }


@router.post("/api/mistakes/{mistake_id}/review/undo")
def rvb_undo_review(mistake_id: int, data: RvbVersionInput, user=Depends(security.current_user)):
    # connect(write=True) 持有 BEGIN IMMEDIATE；重读、恢复、删除日志一起提交。
    with db.connect(write=True) as conn:
        accounts.rvb_account(conn, user["id"])
        item = problem_progress.owned_mistake(conn, mistake_id, user["id"])
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
        if time.datetime.fromisoformat(time.utc_now()) - time.datetime.fromisoformat(review["reviewed_at"]) > timedelta(seconds=UNDO_WINDOW_SECONDS):
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
                ease_factor = :ease_factor, stability = :stability, difficulty = :difficulty,
                due_date = :due_date, last_reviewed_at = :last_reviewed_at, version = :version, suspended_at = NULL
            WHERE id = :id
            """,
            {**restored, "stability": review["stability_before"],
             "difficulty": review["difficulty_before"], "id": mistake_id},
        )
        conn.execute("DELETE FROM reviews WHERE id = ?", (review["id"],))
    return restored


@router.post("/api/mistakes/{mistake_id}/snooze")
def rvb_snooze(mistake_id: int, data: RvbSnoozeInput, user=Depends(security.current_user)):
    with db.connect(write=True) as conn:
        fresh = accounts.rvb_account(conn, user["id"])
        item = problem_progress.owned_mistake(conn, mistake_id, user["id"])
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        if item["suspended_at"] is not None:
            raise HTTPException(409, "这条易错点已暂停，请先恢复")
        today = time.today_for(fresh)
        if item["due_date"] > today.isoformat():
            raise HTTPException(409, "这条还没到期")
        due = (today + timedelta(days=data.days)).isoformat()
        conn.execute(
            "UPDATE mistakes SET due_date = ?, version = version + 1 WHERE id = ?",
            (due, mistake_id),
        )
    return {"due_date": due, "version": item["version"] + 1}


@router.post("/api/mistakes/{mistake_id}/suspend")
def rvb_suspend(mistake_id: int, data: RvbVersionInput, user=Depends(security.current_user)):
    return review_lib.rvb_set_suspension(mistake_id, data, user, True)


@router.post("/api/mistakes/{mistake_id}/unsuspend")
def rvb_unsuspend(mistake_id: int, data: RvbVersionInput, user=Depends(security.current_user)):
    return review_lib.rvb_set_suspension(mistake_id, data, user, False)


@router.post("/api/mistakes/{mistake_id}/variants", status_code=201)
def create_variant(mistake_id: int, user=Depends(security.current_user)):
    # BEGIN IMMEDIATE 后重读套餐并扣额，与支付回调等写入串行执行。
    # 配额在短事务内原子扣除，网络请求期间不持有数据库写锁。
    with ExitStack() as stack:
        with db.connect(write=True) as conn:
            item = problem_progress.owned_mistake(conn, mistake_id, user["id"])
            # 完全没有复习记录时是 None，generate() 的提示词行为跟之前完全一样；
            # 有反复偏低/偏高的复习历史时才提示 AI 调整新题难度。
            item["mastery_signal"] = review_lib.mastery_signal(conn, mistake_id)
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")

            day = time.today_for(user).isoformat()
            quota = ai_limits.ai_quota(conn, user["id"], day)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_used"] >= limit:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            stack.enter_context(ai_limits.ai_slot())
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

        with refund_on_server_failure(user["id"], day, db.connect):
            with ai_limits.track_call(user["id"], "variant"):
                generated = ai.generate(item)
        is_code_zone = item["zone"] in config.CODE_ZONES
        rows = []
        for question in generated["questions"]:
            if is_code_zone:
                description = f"{question['question']}\n\n【样例】\n{question['answer']}"
                expected_answer = ""
            else:
                description = question["question"]
                expected_answer = question["answer"]
            rows.append((description, expected_answer))

        with db.connect(write=True) as conn:
            # 重新读取当前错因：生成期间用户可能已经自己编辑过，不能用生成前的
            # 旧快照来判断是否需要回填，否则可能覆盖掉用户刚写的内容。
            current = problem_progress.owned_mistake(conn, mistake_id, user["id"])
            variants = []
            created_at = time.utc_now()
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
                    config.VARIANT_SELECT + " WHERE v.id = ?", (cursor.lastrowid,),
                ).fetchone())
                variants.append(review_lib.redact_pending_answer(created_row))
            if not current["description"].strip():
                conn.execute(
                    "UPDATE mistakes SET description = ? WHERE id = ?",
                    (generated["mistake_summary"], mistake_id),
                )
                current["description"] = generated["mistake_summary"]
        return {"variants": variants, "mistake_description": current["description"]}


@router.post("/api/mistakes/{mistake_id}/duck")
def duck_panel_chat(mistake_id: int, data: DuckInput, user=Depends(security.current_user)):
    """讲给小黄鸭听：独立每日额度，复用并发/记账与失败退还规则。

    校验失败（422）在扣额度之前；502/503/504 退回本次扣的额度，
    回复不合格直接按 502 退回额度，不自动重试输出解析失败。
    """
    turns = [{"role": turn.role, "text": turn.text} for turn in data.turns]
    try:
        check_turns(turns)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if not turns:
        raise HTTPException(422, "对话不能为空")
    if data.finish and data.hint:
        raise HTTPException(422, "总结时不能同时要提示")
    if data.finish:
        if turns[-1]["role"] != "duck":
            raise HTTPException(422, "请先完成至少一轮对话，再结束并总结")
    elif turns[-1]["role"] != "user":
        raise HTTPException(422, "新的发言必须以用户发言结尾")

    with ExitStack() as stack:
        with db.connect(write=True) as conn:
            item = problem_progress.owned_mistake(conn, mistake_id, user["id"])
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")

            day = time.today_for(user).isoformat()
            accounts.rvb_account(conn, user['id'])
            limit = duck_daily_limit()
            usage = conn.execute('SELECT attempts FROM duck_usage WHERE user_id = ? AND day = ?', (user['id'], day)).fetchone()
            used = usage['attempts'] if usage else 0
            if used >= limit:
                raise HTTPException(429, "今天的小黄鸭次数已用完，明天再来吧。")
            stack.enter_context(ai_limits.ai_slot())
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

        with refund_on_server_failure(user["id"], day, db.connect, duck=True):
            with ai_limits.track_call(user["id"], "duck"):
                reply = ai.duck_ai_reply(item, turns, data.finish, data.hint)

    return {
        "reply": reply,
        "turns_used": sum(1 for turn in turns if turn["role"] == "user"),
        "ai_remaining": max(0, limit - used - 1),
    }


@router.put("/api/variants/{variant_id}/result")
def save_variant_result(
    variant_id: int,
    data: VariantResult,
    user=Depends(security.current_user),
):
    with db.connect(write=True) as conn:
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
                if problem_progress.normalize_math_answer(data.answer)
                == problem_progress.normalize_math_answer(owned["expected_answer"])
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
                time.utc_now(), variant_id,
            ),
        )
        result = conn.execute(
            config.VARIANT_SELECT + " WHERE v.id = ?",
            (variant_id,),
        ).fetchone()

    # 保存练习结果不会隐式修改原易错点的复习计划。
    return dict(result)


AI_FEEDBACK_KINDS = ("variant", "duck", "weakness")
AI_FEEDBACK_LIMIT = 120
AI_FEEDBACK_WINDOW_SECONDS = 3600


class AiFeedbackInput(InputModel):
    kind: str
    ref_id: int | None = None
    useful: bool


@router.post("/api/ai-feedback")
def submit_ai_feedback(data: AiFeedbackInput, user=Depends(security.current_user)):
    """AI 回答有用/没用反馈：只收布尔值，不收文字。

    再次点击同一 (kind, ref_id) 就是改票。variant 的 ref_id 必须是当前用户
    自己的变体题 id；duck/weakness 的 ref_id 必须为 null。
    """
    if data.kind not in AI_FEEDBACK_KINDS:
        raise HTTPException(422, "不支持的反馈类型")
    if ratelimit.rate_limited(
        f"ai_feedback:{user['id']}", AI_FEEDBACK_LIMIT, AI_FEEDBACK_WINDOW_SECONDS
    ):
        raise HTTPException(429, "反馈太频繁，请稍后再试")
    if data.kind == "variant":
        if data.ref_id is None:
            raise HTTPException(422, "变体题反馈需要 ref_id")
    elif data.ref_id is not None:
        raise HTTPException(422, "该类型反馈不需要 ref_id")

    useful = 1 if data.useful else 0
    with db.connect(write=True) as conn:
        if data.kind == "variant":
            owned = conn.execute(
                """
                SELECT v.id FROM variants v
                JOIN mistakes m ON m.id = v.mistake_id
                JOIN problems p ON p.id = m.problem_id
                WHERE v.id = ? AND p.user_id = ?
                """,
                (data.ref_id, user["id"]),
            ).fetchone()
            if owned is None:
                raise HTTPException(404, "变体题不存在")
        # 变体题按 (用户, 变体题) 改票；小黄鸭/薄弱点分析没有可对应的具体对象，
        # 每次点击都记一条新反馈（界面在同一次回答后只给点一次），这样统计的是
        # 每次回答的好坏，不是"每人只留最后一票"。
        existing = None
        if data.kind == "variant":
            existing = conn.execute(
                "SELECT id FROM ai_feedback WHERE user_id = ? AND kind = ? AND ref_id = ?",
                (user["id"], data.kind, data.ref_id),
            ).fetchone()
        if existing:
            conn.execute(
                "UPDATE ai_feedback SET useful = ?, created_at = datetime('now') "
                "WHERE id = ?",
                (useful, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO ai_feedback(user_id, kind, ref_id, useful) "
                "VALUES (?, ?, ?, ?)",
                (user["id"], data.kind, data.ref_id, useful),
            )
    return {"ok": True}
