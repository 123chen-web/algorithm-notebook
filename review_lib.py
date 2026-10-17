"""复习域服务：复习队列、离线操作回放、掌握信号。"""

import accounts
import db
import problem_progress
from core import config, time as core_time

import json
from datetime import date, datetime, timedelta, timezone
from fastapi import HTTPException
def queue_order(items, today):
    """“最该复习”的先后：逾期天数 / 间隔 越大越靠前，再按到期日、id。"""
    return sorted(items, key=lambda item: (
        -max(0, (today - date.fromisoformat(item["due_date"])).days)
        / max(item["interval_days"], 1),
        item["due_date"], item["id"],
    ))


SPREAD_MAX_DAYS = 3650  # 防御：理论上每天至少放得下 1 条，不会走到这么远


def spread_plan(excess, today, cap, load_by_day):
    """补欠账：把今天放不下的逾期记录（excess，已按最该复习排好）依次排进明天起的日子，
    每天连同原本就排在那天的（load_by_day：{日期字符串: 条数}）不超过 cap 条。
    返回 [(item, 新的到期日字符串)]，越该复习的排得越早。纯函数，不改输入。"""
    if not excess or not cap or cap < 1:
        return []
    plan = []
    pending = list(excess)
    offset = 1
    while pending and offset <= SPREAD_MAX_DAYS:
        day = (today + timedelta(days=offset)).isoformat()
        free = max(0, cap - int(load_by_day.get(day, 0)))
        for _ in range(free):
            if not pending:
                break
            plan.append((pending.pop(0), day))
        offset += 1
    return plan


def rvb_review_queue(items, today, cap, done_today, ignore_cap=False):
    """对已过滤的到期候选排序/截断；不修改输入或调度状态。"""
    ordered = queue_order(items, today)
    remaining = None if cap is None or ignore_cap else max(0, cap - done_today)
    selected = ordered if remaining is None else ordered[:remaining]
    # 今天做哪些仍按“最该复习”挑；挑出来之后穿插着排，同一个套路 / 同一道题尽量不挨着
    # （交错练习：先认出该用哪个套路，比连做同类题记得牢）。只改顺序，不加不减。
    selected = interleave(selected)
    return {
        "today": today.isoformat(), "cap": cap, "done_today": done_today,
        "remaining_today": remaining, "total_due": len(ordered),
        "capped": len(ordered) > len(selected), "items": selected,
    }


def _interleave_key(item):
    patterns = item.get("pattern_ids") or []
    if patterns:
        return f"pattern:{patterns[0]}"
    if item.get("problem_id") is not None:
        return f"problem:{item['problem_id']}"
    return f"item:{item.get('id')}"


def interleave(items):
    """按原顺序贪心地取：每次取第一条和上一条“不同类”的；实在只剩同类才挨着放。稳定、不丢不重。"""
    pending = list(items)
    result = []
    last = None
    while pending:
        index = next((i for i, item in enumerate(pending) if _interleave_key(item) != last), 0)
        chosen = pending.pop(index)
        result.append(chosen)
        last = _interleave_key(chosen)
    return result


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



# 复仇成功判定常量（阈值见 docs/features/revenge.md，请站长确认）。
REVENGE_MIN_GAP_DAYS = 7
REVENGE_SUCCESS_QUALITY = 4
REVENGE_FAIL_QUALITY = 2
REVENGE_MODES = ("rewrite", "redo")
# 失败类判题：建题时记录的 verdict 属于这些即视为“曾经失败过”。
REVENGE_FAIL_VERDICTS = ("WA", "TLE", "MLE", "RE", "CE", "NOANS")


def check_revenge(conn, mistake_id, quality, mode, reviewed_at, evidence=""):
    """判定本次评分是否构成“复仇成功”，返回 revenge dict 或 None。

    条件（全部满足才触发）：
    1. 本次评分 quality >= 4，且 mode 为 rewrite/redo（实测成绩，不是自评）；
    2. 该条此前至少有一次评分 <= 2，或建题时判题类型为失败类；
    3. 首次失败（低分记录的最早时间，或失败判题的建题时间）距今 >= 7 天；
    4. 该条从未触发过（mistakes.revenge_at IS NULL）。

    reviewed_at 为 ISO 时间字符串。本函数只做判定不写库；
    调用方在触发时把 mistakes.revenge_at 置为本次评分时间。
    返回的 dict 可直接序列化进评分响应和 /api/stats/revenge。
    """
    if quality < REVENGE_SUCCESS_QUALITY or mode not in REVENGE_MODES:
        return None
    row = conn.execute(
        "SELECT m.revenge_at, p.title, p.verdict, p.created_at"
        " FROM mistakes m JOIN problems p ON p.id = m.problem_id"
        " WHERE m.id = ?",
        (mistake_id,),
    ).fetchone()
    if row is None or row["revenge_at"] is not None:
        return None
    fail_row = conn.execute(
        "SELECT MIN(reviewed_at) FROM reviews"
        " WHERE mistake_id = ? AND quality <= ?",
        (mistake_id, REVENGE_FAIL_QUALITY),
    ).fetchone()
    first_fail_at = fail_row[0] if fail_row else None
    verdict_failed = (row["verdict"] or "") in REVENGE_FAIL_VERDICTS
    if verdict_failed and row["created_at"]:
        if first_fail_at is None or row["created_at"] < first_fail_at:
            first_fail_at = row["created_at"]
    if first_fail_at is None:
        return None
    try:
        gap_days = (
            datetime.fromisoformat(reviewed_at) - datetime.fromisoformat(first_fail_at)
        ).days
    except ValueError:
        return None
    if gap_days < REVENGE_MIN_GAP_DAYS:
        return None
    seconds = None
    if evidence:
        try:
            seconds = json.loads(evidence).get("seconds")
        except (ValueError, AttributeError):
            seconds = None
    return {
        "mistake_id": mistake_id,
        "problem_title": row["title"],
        "first_fail_date": first_fail_at[:10],
        "verdict": row["verdict"] or "",
        "mode": mode,
        "seconds": seconds if isinstance(seconds, int) else None,
        "days_gap": gap_days,
    }
