"""复习域服务：复习队列、离线操作回放、掌握信号。"""
import time
import uuid
from datetime import timedelta

import accounts
import db
import problem_progress
from core import config, time as core_time

import json
from datetime import date, datetime, timezone
from fastapi import HTTPException
def rvb_review_queue(items, today, cap, done_today, ignore_cap=False):
    """对已过滤的到期候选排序/截断；不修改输入或调度状态。"""
    ordered = sorted(items, key=lambda item: (
        -max(0, (today - date.fromisoformat(item["due_date"])).days)
        / max(item["interval_days"], 1),
        item["due_date"], item["id"],
    ))
    remaining = None if cap is None or ignore_cap else max(0, cap - done_today)
    selected = ordered if remaining is None else ordered[:remaining]
    return {
        "today": today.isoformat(), "cap": cap, "done_today": done_today,
        "remaining_today": remaining, "total_due": len(ordered),
        "capped": len(ordered) > len(selected), "items": selected,
    }


def redo_overconfident_hint(conn, mistake_id, since_iso):
    """防自评虚高：最近 14 天内“只回忆”打了高分（>=4）、紧接着下一次又评成
    低分（<=2），出现 >=2 次时返回 True，下次复习默认引导到重做。

    只读最近 14 天的评分，按时间顺序找 recall 高分→低分的相邻对。
    """
    rows = conn.execute(
        "SELECT mode, quality FROM reviews WHERE mistake_id = ? AND reviewed_at >= ?"
        " ORDER BY reviewed_at, id",
        (mistake_id, since_iso),
    ).fetchall()
    count = 0
    for prev, cur in zip(rows, rows[1:]):
        if prev["mode"] == "recall" and prev["quality"] >= 4 and cur["quality"] <= 2:
            count += 1
            if count >= 2:
                return True
    return False


def pwa_review_op_replay(conn, user_id, mistake_id, client_op_id, now):
    """离线补交的幂等入口：清理 30 天前的记录，再查是否已处理过这个 client_op_id。

    必须在写事务（BEGIN IMMEDIATE）里调用：查和后面的写同属一个事务，
    并发的重复提交会在写锁上排队，后到的一定能看到先到的记录。
    命中时返回第一次保存的响应（不再评分），未命中返回 None。
    """
    cutoff = (datetime.fromisoformat(now) - config.REVIEW_OP_RETENTION).isoformat(timespec="seconds")
    conn.execute("DELETE FROM review_ops WHERE created_at < ?", (cutoff,))
    saved = conn.execute(
        "SELECT mistake_id, response FROM review_ops WHERE user_id = ? AND client_op_id = ?",
        (user_id, client_op_id),
    ).fetchone()
    if saved is None:
        return None
    if saved["mistake_id"] != mistake_id:
        raise HTTPException(422, "这个 client_op_id 已用于另一道题")
    return json.loads(saved["response"])


def pwa_reviewed_at(data, item, now):
    """校验并规范化客户端给的评分时间；返回 UTC、秒精度的 datetime。"""
    server_now = datetime.fromisoformat(now)
    given = data.reviewed_at.astimezone(timezone.utc)
    if given > server_now + config.REVIEW_FUTURE_SLACK:
        raise HTTPException(422, "评分时间不能晚于当前时间")
    if given < server_now - config.REVIEW_BACKFILL_WINDOW:
        raise HTTPException(422, "评分时间不能早于 7 天前")
    given = given.replace(microsecond=0)
    last = item["last_reviewed_at"]
    if last is not None and given < datetime.fromisoformat(last):
        raise HTTPException(409, "这道题之后已经有更新的评分")
    return given


def rvb_set_suspension(mistake_id, data, user, suspended):
    with db.connect(write=True) as conn:
        accounts.rvb_account(conn, user["id"])
        item = problem_progress.owned_mistake(conn, mistake_id, user["id"])
        # 同一请求重试时携带的仍是旧版本；目标状态已达成就直接返回，不再写库。
        if (item["suspended_at"] is not None) == suspended:
            return {"suspended_at": item["suspended_at"], "version": item["version"]}
        if item["version"] != data.version:
            raise HTTPException(409, "这条记录已更新，请刷新后再操作")
        suspended_at = core_time.utc_now() if suspended else None
        conn.execute(
            "UPDATE mistakes SET suspended_at = ?, version = version + 1 WHERE id = ?",
            (suspended_at, mistake_id),
        )
    return {"suspended_at": suspended_at, "version": item["version"] + 1}


def mastery_signal(conn, mistake_id):
    # 只看最近几次复习：太久以前的表现代表性不够，用户可能早已改善或退步。
    # 只有 2 次以上评分一致偏低/偏高才给方向性提示；1 次或忽高忽低都太吵，
    # 保持不调整（None），这不是精确科学，宁可保守也不要被噪声带偏。
    rows = conn.execute(
        "SELECT quality FROM reviews WHERE mistake_id = ? "
        "ORDER BY reviewed_at DESC, id DESC LIMIT 3",
        (mistake_id,),
    ).fetchall()
    if len(rows) < 2:
        return None
    qualities = [row["quality"] for row in rows]
    if all(quality < 3 for quality in qualities):
        return "struggling"
    if all(quality >= 4 for quality in qualities):
        return "mastering"
    return None


def redact_pending_answer(variant):
    # 还没提交过练习结果的变体，标准答案只用来告诉前端"这道题能不能自动判对
    # 错"（真假即可），具体文字要等提交之后 save_variant_result 的响应才
    # 给出，否则打开网络面板就能在动手之前看到答案，等于白设计这个校验。
    if variant["expected_answer"] and not variant["result_updated_at"]:
        variant["expected_answer"] = "***"
    return variant

