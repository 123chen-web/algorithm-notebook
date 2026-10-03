"""Bounded mistake-cluster references, snapshots and shared-quota generation."""
import json
import os
from contextlib import ExitStack
from datetime import datetime, timezone

from fastapi import HTTPException

import ai
from ai_limits import ai_slot, track_call
from db import connect


MIN_MISTAKES = 6
MAX_MISTAKES = 60


def cluster_reference(conn, user_id, total_mistakes):
    rows = conn.execute(
        """
        SELECT m.id AS mistake_id, p.created_at AS problem_created_at,
               substr(p.title, 1, 200) AS title, p.zone,
               substr(m.description, 1, 300) AS description,
               substr(p.thinking, 1, 300) AS thinking,
               COUNT(r.id) AS review_count,
               SUM(CASE WHEN r.quality < 3 THEN 1 ELSE 0 END) AS failed_review_count
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        LEFT JOIN reviews r ON r.mistake_id = m.id
        WHERE p.user_id = ?
        GROUP BY m.id
        ORDER BY p.created_at DESC, m.id DESC LIMIT ?
        """,
        (user_id, MAX_MISTAKES),
    ).fetchall()
    mistakes = []
    # 创建指纹留在服务端，发给模型的材料字段保持不变。
    fingerprints = {}
    for row in rows:
        item = dict(row)
        fingerprints[item["mistake_id"]] = item.pop("problem_created_at")
        thinking = item.pop("thinking")
        if not item["description"].strip():
            item["description"] = thinking
        mistakes.append(item)
    return {
        "total_mistakes": total_mistakes,
        "sample": {"mistake_count": len(mistakes)},
        "mistakes": mistakes,
    }, fingerprints


def _database_members(conn, user_id, ids):
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT m.id AS mistake_id, m.problem_id, p.created_at AS problem_created_at,
               substr(p.title, 1, 200) AS title, p.zone,
               substr(m.description, 1, 300) AS description,
               substr(p.thinking, 1, 300) AS thinking, m.due_date
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND m.id IN ({placeholders})
        """,
        (user_id, *ids),
    ).fetchall()
    result = {}
    for row in rows:
        member = dict(row)
        thinking = member.pop("thinking")
        if not member["description"].strip():
            member["description"] = thinking
        result[member["mistake_id"]] = member
    return result


def _refresh_content(conn, user_id, content):
    ids = {
        member["mistake_id"]
        for cluster in content["clusters"] for member in cluster["members"]
    }
    current = _database_members(conn, user_id, ids)
    refreshed = []
    for cluster in content["clusters"]:
        members = []
        for member in cluster["members"]:
            source = current.get(member["mistake_id"])
            if source is not None and (
                "problem_created_at" not in member
                or member["problem_created_at"] == source["problem_created_at"]
            ):
                members.append({
                    **member, "title": source["title"], "due_date": source["due_date"],
                })
        if len(members) >= 2:
            refreshed.append({**cluster, "members": members})
    return {**content, "clusters": refreshed}


def cluster_state(conn, user_id, today, generation_completed=False):
    count = conn.execute(
        "SELECT COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id "
        "WHERE p.user_id = ?", (user_id,),
    ).fetchone()[0]
    row = conn.execute(
        "SELECT content, created_at FROM mistake_clusters WHERE user_id = ?", (user_id,),
    ).fetchone()
    insight = None
    new_since = 0
    if row:
        insight = {
            "content": _refresh_content(conn, user_id, json.loads(row["content"])),
            "created_at": row["created_at"],
        }
        additions = conn.execute(
            """
            SELECT p.created_at, COUNT(*) AS mistake_count
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? GROUP BY p.id
            """, (user_id,),
        ).fetchall()
        # Normalize offsets and retain microseconds; SQLite julianday rounds to milliseconds.
        snapshot_time = datetime.fromisoformat(row["created_at"])
        if snapshot_time.tzinfo is None:
            snapshot_time = snapshot_time.replace(tzinfo=timezone.utc)
        for addition in additions:
            added_at = datetime.fromisoformat(addition["created_at"])
            if added_at.tzinfo is None:
                added_at = added_at.replace(tzinfo=timezone.utc)
            if added_at > snapshot_time:
                new_since += addition["mistake_count"]
    if count < MIN_MISTAKES:
        status = "insufficient_data"
        if generation_completed:
            message = (
                f"这次已经调用 AI 并消耗了 1 次额度；当前有 {count} 条易错点，"
                f"数量不足，下次需要攒够 {MIN_MISTAKES} 条才能再归并。"
            )
        else:
            message = (
                f"再积累几条错题就能看出真正的共性了。当前有 {count} 条易错点，"
                f"还差 {MIN_MISTAKES - count} 条；本次不会调用 AI，也不消耗额度。"
            )
    elif insight is None:
        status = "not_generated"
        message = "还没有归并过。让 AI 把根因相近的易错点合成专题，方便一起复习。"
    else:
        status, message = "ready", None
    return {
        "status": status, "message": message, "minimum_mistakes": MIN_MISTAKES,
        "mistake_count": count, "today": today, "insight": insight, "new_since": new_since,
    }


def create_clusters(user, today_reader, quota_reader):
    """Reserve the shared allowance atomically, then release the lock before AI."""
    user_id = user["id"]
    today = today_reader(user).isoformat()
    with ExitStack() as stack:
        with connect(write=True) as conn:
            state = cluster_state(conn, user_id, today)
            if state["mistake_count"] < MIN_MISTAKES:
                return state
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 OpenAI API Key")
            reference, fingerprints = cluster_reference(conn, user_id, state["mistake_count"])
            quota = quota_reader(conn, user_id, today)
            limit = quota["ai_daily_limit"]
            if quota["ai_daily_remaining"] <= 0:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            # 名额跨越扣额提交、AI 和保存；写锁中只做一次非阻塞获取。
            stack.enter_context(ai_slot())
            cursor = conn.execute(
                """
                INSERT INTO ai_usage(user_id, day, attempts)
                SELECT ?, ?, 1 WHERE ? > 0
                ON CONFLICT(user_id, day) DO UPDATE
                SET attempts = ai_usage.attempts + 1
                WHERE ai_usage.attempts < ?
                """, (user_id, today, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

        with track_call(user_id, "clusters"):
            result = ai.cluster_mistakes(reference)
            # Also validate at the persistence boundary, including injected/mock providers.
            try:
                raw_result = json.dumps(result, ensure_ascii=False)
            except (TypeError, ValueError, RecursionError):
                raise HTTPException(502, ai.CLUSTERS_BAD_RESPONSE) from None
            content = ai._parse_mistake_clusters(raw_result, reference)
        input_order = {
            item["mistake_id"]: index for index, item in enumerate(reference["mistakes"])
        }
        ids = [mistake_id for cluster in content["clusters"] for mistake_id in cluster["mistake_ids"]]
        with connect(write=True) as conn:
            sources = _database_members(conn, user_id, ids)
            snapshots = []
            for cluster in content["clusters"]:
                members = [
                    sources[mistake_id]
                    for mistake_id in sorted(cluster["mistake_ids"], key=input_order.__getitem__)
                    if mistake_id in sources
                    and sources[mistake_id]["problem_created_at"] == fingerprints[mistake_id]
                ]
                if len(members) >= 2:
                    snapshots.append({
                        "title": cluster["title"], "explanation": cluster["explanation"],
                        "tip": cluster["tip"], "members": members,
                    })
            content["clusters"] = snapshots
            content["sample"] = reference["sample"]
            conn.execute(
                """
                INSERT INTO mistake_clusters(user_id, content, created_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE
                SET content = excluded.content, created_at = excluded.created_at
                WHERE excluded.created_at >= mistake_clusters.created_at
                """, (user_id, json.dumps(content, ensure_ascii=False), created_at),
            )
            return cluster_state(
                conn, user_id, today_reader(user).isoformat(), generation_completed=True,
            )
