"""stats routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
from core import config, security, time
from models.forum import PostSearchQuery
from models.groups import GoalSetting
from routers import groups
import accounts
import ai_limits
import db
import stats_summary
import weekly_recap
from problem_progress import enrich_problems, enrich_insight

from activity import activity_summary
from activity import day_counts
from ai_limits import refund_on_server_failure
from contextlib import ExitStack
from datetime import timedelta
from datetime import timezone
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from learning_stats import current_streak
from mastery import mastery_report
from search import search_all
import stats_summary
from stats_summary import ALLOWED_DAYS as ALLOWED_SUMMARY_DAYS
from typical import typical_report
from typing import Annotated
import ai
import clusters
import goal
import json
import os
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/insights/weakness-analysis")
def get_weakness_analysis(user=Depends(security.current_user)):
    with db.connect() as conn:
        return enrich_insight(conn, user['id'], stats_summary.weakness_analysis_state(conn, user['id']),
                              time.today_for(user).isoformat(), 'patterns')


@router.post("/api/insights/weakness-analysis")
def create_weakness_analysis(user=Depends(security.current_user)):
    with ExitStack() as stack:
        with db.connect(write=True) as conn:
            accounts.recheck_account(conn, user["id"])
            state = stats_summary.weakness_analysis_state(conn, user["id"])
            # 先判断数量，哪怕额度已用完或未配置 Key，也只返回积累材料的提示。
            if state["mistake_count"] < config.WEAKNESS_MIN_MISTAKES:
                return state
            if not os.getenv("OPENAI_API_KEY", "").strip():
                raise HTTPException(503, "服务端尚未配置 AI 服务密钥")
            reference = stats_summary.weakness_analysis_reference(conn, user["id"], state["mistake_count"])
            # 与生成练习题、拍照识别完全相同的套餐读取及原子扣额 SQL。
            # AI 服务端失败时退还；在发起外部请求前提交，网络调用不持有写锁。
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
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            # 记录材料快照时间，微秒精度区分同秒请求，旧请求晚完成也不覆盖新快照。
            created_at = time.datetime.now(timezone.utc).isoformat(timespec="microseconds")

        with refund_on_server_failure(user["id"], day, db.connect):
            with ai_limits.track_call(user["id"], "weakness"):
                content = ai.analyze_weaknesses(reference)
        content["sample"] = reference["sample"]
        by_id = {item["mistake_id"]: item for item in reference["mistakes"]}
        for pattern in content["patterns"]:
            for evidence in pattern["evidence"]:
                source = by_id[evidence["mistake_id"]]
                # 标题和分区由可信的来源映射补齐，不让模型虚构题目归属。
                evidence.update({key: source[key] for key in ("problem_id", "title", "zone")})

        with db.connect(write=True) as conn:
            accounts.recheck_account(conn, user["id"])
            conn.execute(
                """
                INSERT INTO weakness_insights(user_id, content, created_at) VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE
                SET content = excluded.content, created_at = excluded.created_at
                WHERE excluded.created_at >= weakness_insights.created_at
                """,
                (user["id"], json.dumps(content, ensure_ascii=False), created_at),
            )
            return enrich_insight(conn, user['id'], stats_summary.weakness_analysis_state(conn, user['id']),
                                  time.today_for(user).isoformat(), 'patterns')


@router.get("/api/insights/clusters")
def get_mistake_clusters(user=Depends(security.current_user)):
    with db.connect() as conn:
        return enrich_insight(conn, user['id'], clusters.cluster_state(conn, user['id'], time.today_for(user).isoformat()),
                              time.today_for(user).isoformat(), 'clusters')


@router.post("/api/insights/clusters")
def create_mistake_clusters(user=Depends(security.current_user)):
    state = clusters.create_clusters(user, time.today_for, ai_limits.ai_quota)
    with db.connect() as conn:
        return enrich_insight(conn, user['id'], state, time.today_for(user).isoformat(), 'clusters')


@router.get("/api/insights/growth")
def get_growth_insights(user=Depends(security.current_user)):
    # 纯统计，不调用 AI、不涉及配额——跟"薄弱点分析"是两个独立入口。
    with db.connect() as conn:
        zones = stats_summary.growth_by_zone(conn, user["id"], time.today_for(user))
        community = stats_summary.community_weakness_by_zone(conn)
    for zone in zones:
        stats = community.get(zone["zone"], {"sample_size": 0, "struggling_ratio": None})
        zone["community_sample_size"] = stats["sample_size"]
        zone["community_struggling_ratio"] = stats["struggling_ratio"]
    return {"zones": zones}


@router.get("/api/insights/weekly-recap")
def get_weekly_recap(user=Depends(security.current_user)):
    # 纯统计，不调用 AI、不涉及配额。
    with db.connect() as conn:
        # 多项统计共享只读快照，避免生成/删除记录时跨查询读到不同版本。
        conn.execute("BEGIN")
        recap = weekly_recap.weekly_recap(conn, user["id"], user["timezone"], time.today_for(user))
    return recap


@router.get("/api/stats/activity")
def get_activity_stats(
    weeks: Annotated[int, Query(ge=1, le=52)] = 26, user=Depends(security.current_user)
):
    # 热力图用：按用户本地日汇总复习/新增记录；只返回有活动的日子，前端补零。
    # 纯统计，不调用 AI、不涉及配额。
    with db.connect() as conn:
        conn.execute("BEGIN")
        reviews, records, _ = day_counts(conn, user["id"], user["timezone"])
    return activity_summary(reviews, records, time.today_for(user), weeks)


@router.get("/api/stats/summary")
def get_stats_summary(days: Annotated[int, Query()] = 30, user=Depends(security.current_user)):
    # 总览「趋势」区：待复习 / 连续打卡 / 复习次数 / 真实保持率 + 未来 14 天预测。
    # 全部按用户本地日，SQL 分组，纯统计，不调用 AI、不占额度。
    if days not in ALLOWED_SUMMARY_DAYS:
        raise HTTPException(422, "days 只能是 7、30 或 90")
    with db.connect() as conn:
        conn.execute("BEGIN")
        return stats_summary.summary(conn, user["id"], user["timezone"], time.today_for(user), days)


@router.get("/api/goal")
def get_goal(user=Depends(security.current_user)):
    # 总览「目标」卡：倒计时 + 今天建议复习多少条。纯统计，不调用 AI、不占额度。
    with db.connect() as conn:
        conn.execute("BEGIN")
        return {"goal": goal.read(conn, user["id"], user["timezone"], time.today_for(user))}


@router.put("/api/goal")
def put_goal(data: GoalSetting, user=Depends(security.current_user)):
    # 设置或修改目标：名称 ≤ 30 字，日期是用户本地时区未来 1–365 天内（goal.save 校验）。
    with db.connect(write=True) as conn:
        shaped = goal.save(
            conn, user["id"], user["timezone"], data.name, data.goal_date,
            time.utc_now(), time.today_for(user),
        )
    return {"goal": shaped}


@router.delete("/api/goal")
def delete_goal(user=Depends(security.current_user)):
    # 结束目标：只写 ended_at，行保留作历史；注销账号时才随用户整行删除。
    with db.connect(write=True) as conn:
        goal.end(conn, user["id"], time.utc_now())
    return {"goal": None}


@router.get("/api/stats/mastery")
def get_mastery(
    weeks: Annotated[int, Query(ge=4, le=26)] = 12, user=Depends(security.current_user)
):
    # 掌握度趋势：按分区估算保持率的变化曲线；纯统计，不调用 AI、不占额度。
    with db.connect() as conn:
        conn.execute("BEGIN")
        return mastery_report(conn, user["id"], user["timezone"], time.today_for(user), weeks)


@router.get("/api/stats/typical")
def get_typical_mistakes(user=Depends(security.current_user)):
    # 错因专题页"我的三大典型失误"：按错因标签统计仍未掌握的高频失误，模板生成提醒与清单。
    # 纯统计，不调用 AI、不占额度。
    with db.connect() as conn:
        conn.execute("BEGIN")
        return typical_report(conn, user["id"], user["timezone"], time.today_for(user))


@router.get("/api/stats/error-profile")
def get_error_profile(user=Depends(security.current_user)):
    """错因画像：大类分布、判题类型分布、最常见错因 Top 3。纯统计，不调用 AI。

    标签存的是词表 code，这里按 code 聚合，前端通过 /api/error-taxonomy
    做 code→中文名 映射（返回里也直接带上中文名，方便直接渲染）。
    """
    import error_taxonomy

    with db.connect() as conn:
        conn.execute("BEGIN")
        user_id = user["id"]
        # 大类分布：按错因 code → 大类 聚合。
        cause_rows = conn.execute(
            """
            SELECT t.tag, COUNT(DISTINCT m.id) AS count
            FROM mistake_tags t
            JOIN mistakes m ON m.id = t.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE t.user_id = ? AND p.user_id = ?
            GROUP BY t.tag
            """,
            (user_id, user_id),
        ).fetchall()
        group_counts = {group: 0 for group, _ in error_taxonomy.CAUSE_GROUPS}
        top_causes = []
        total = 0
        for row in cause_rows:
            code, count = row[0], row[1]
            total += count
            try:
                group = error_taxonomy.cause_group(code)
                label = error_taxonomy.cause_label(code)
            except KeyError:
                group, label = "environment", code
            group_counts[group] = group_counts.get(group, 0) + count
            top_causes.append({"code": code, "label": label, "count": count})
        top_causes.sort(key=lambda item: (-item["count"], item["code"]))
        groups = [
            {
                "code": group,
                "label": error_taxonomy.group_label(group),
                "count": group_counts.get(group, 0),
                "percent": round(group_counts.get(group, 0) / total * 100) if total else 0,
            }
            for group, _ in error_taxonomy.CAUSE_GROUPS
        ]
        # 判题类型分布：只统计填了 verdict 的题目。
        verdict_rows = conn.execute(
            """
            SELECT verdict, COUNT(*) AS count FROM problems
            WHERE user_id = ? AND verdict != ''
            GROUP BY verdict ORDER BY count DESC
            """,
            (user_id,),
        ).fetchall()
        verdict_total = sum(row[1] for row in verdict_rows)
        verdicts = []
        for row in verdict_rows:
            code, count = row[0], row[1]
            try:
                label = error_taxonomy.verdict_label(code)
            except KeyError:
                continue
            verdicts.append({
                "code": code,
                "label": label,
                "count": count,
                "percent": round(count / verdict_total * 100) if verdict_total else 0,
            })
        return {
            "groups": groups,
            "verdicts": verdicts,
            "top_causes": top_causes[:3],
            "total_tagged": total,
        }


@router.get("/api/overview")
def get_overview(user=Depends(security.current_user)):
    # 侧栏角标和"总览"页一次取齐，避免首页并发十几个请求。纯统计，不调用 AI。
    today = time.today_for(user)
    day = today.isoformat()
    with db.connect() as conn:
        conn.execute("BEGIN")
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(m.suspended_at IS NULL AND m.due_date <= :day), 0) AS due,
                   COALESCE(SUM(m.suspended_at IS NULL AND m.due_date < :day), 0) AS overdue
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = :user_id
            """,
            {"day": day, "user_id": user["id"]},
        ).fetchone()
        zone_rows = conn.execute(
            """
            SELECT p.zone, COUNT(*) AS total,
                   COALESCE(SUM(m.suspended_at IS NULL AND m.due_date <= ?), 0) AS due
            FROM mistakes m JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ?
            GROUP BY p.zone ORDER BY total DESC, p.zone
            """,
            (day, user["id"]),
        ).fetchall()
        preview_rows = conn.execute(
            config.MISTAKE_SELECT
            + " WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?"
            " AND m.id = (SELECT mm.id FROM mistakes mm WHERE mm.problem_id = p.id"
            " AND mm.suspended_at IS NULL AND mm.due_date <= ?"
            " ORDER BY mm.due_date, mm.id LIMIT 1)"
            " ORDER BY m.due_date ASC, m.id ASC LIMIT 5",
            (user["id"], day, day),
        ).fetchall()
        preview_rows = enrich_problems(conn, user['id'], [dict(row) for row in preview_rows], day)
        review_days, _, mistake_days = day_counts(conn, user["id"], user["timezone"])
        weakness = stats_summary.weakness_analysis_state(conn, user["id"])
        pending_reason_count = conn.execute(
            "SELECT COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id "
            "WHERE p.user_id = ? AND m.pending_reason = 1",
            (user["id"],),
        ).fetchone()[0]
        hot = conn.execute(
            """
            SELECT p.id, p.title, p.created_at, u.username,
                   (SELECT COUNT(*) FROM post_comments c
                    WHERE c.post_id = p.id AND c.deleted_at IS NULL) AS comment_count
            FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.deleted_at IS NULL AND p.created_at >= ?
            ORDER BY comment_count DESC, p.created_at DESC, p.id DESC LIMIT 1
            """,
            ((time.datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds"),),
        ).fetchone()
        # 复习=重做：本周（今天起往前 7 天）的重做统计，有重做数据才返回。
        week_start = (today - timedelta(days=6)).isoformat()
        redo_rows = conn.execute(
            """
            SELECT r.mode, r.quality FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            WHERE p.user_id = ? AND date(r.reviewed_at) >= ?
            """,
            (user["id"], week_start),
        ).fetchall()
    group_list = groups.list_groups(user)["groups"]
    group_list.sort(key=lambda group: (-group["level"]["points"], group["id"]))
    top_pattern = None
    if weakness["status"] == "ready" and weakness["insight"]["content"].get("patterns"):
        pattern = weakness["insight"]["content"]["patterns"][0]
        top_pattern = {"title": pattern["title"], "confidence": pattern["confidence"]}
    redo_reviews = [row for row in redo_rows if row["mode"] in ("rewrite", "redo")]
    recall_reviews = [row for row in redo_rows if row["mode"] == "recall"]
    redo_week = None
    if redo_reviews:
        passed = sum(1 for row in redo_reviews if row["quality"] >= 3)
        redo_avg = sum(row["quality"] for row in redo_reviews) / len(redo_reviews)
        recall_avg = (
            sum(row["quality"] for row in recall_reviews) / len(recall_reviews)
            if recall_reviews else None
        )
        redo_week = {
            "redo_count": len(redo_reviews),
            "pass_rate": round(passed / len(redo_reviews), 2),
            "redo_avg": round(redo_avg, 2),
            "recall_avg": round(recall_avg, 2) if recall_avg is not None else None,
            "recall_minus_redo": round(recall_avg - redo_avg, 2) if recall_avg is not None else None,
        }
    return {
        "today": day,
        "due_count": counts["due"],
        "overdue_count": counts["overdue"],
        "total_mistakes": counts["total"],
        "pending_reason_count": pending_reason_count,
        "streak_days": current_streak(set(review_days), today),
        "last7": [(today - timedelta(days=6 - offset)) in review_days for offset in range(7)],
        "zones": [dict(row) for row in zone_rows],
        # 与"本周战报"同一口径：含今天的最近 7 天 vs 紧邻的前 7 天；新增的是易错点条数。
        "week": {
            "reviews": stats_summary.window_total(review_days, today - timedelta(days=6), today),
            "prev_reviews": stats_summary.window_total(review_days, today - timedelta(days=13), today - timedelta(days=7)),
            "records": stats_summary.window_total(mistake_days, today - timedelta(days=6), today),
        },
        "due_preview": [
            {
                "id": row["id"],
                "problem_id": row["problem_id"],
                "title": row["title"],
                "zone": row["zone"],
                "description": row["description"],
                "due_date": row["due_date"],
                "overdue_days": max(0, (today - time.datetime.fromisoformat(row["due_date"]).date()).days),
                "repetitions": row["repetitions"],
                "pending_reason": bool(row["pending_reason"]),
                **{key: row[key] for key in ('progress', 'problem_tags', 'problem_due_count', 'problem_mistakes')},
            }
            for row in preview_rows
        ],
        "group_count": len(group_list),
        "groups_preview": [
            {
                "id": group["id"],
                "name": group["name"],
                "member_count": group["member_count"],
                "member_limit": group["member_limit"],
                "level": group["level"],
            }
            for group in group_list[:3]
        ],
        "weakness": {
            "status": weakness["status"],
            "mistake_count": weakness["mistake_count"],
            "minimum_mistakes": weakness["minimum_mistakes"],
            "top": top_pattern,
        },
        "hot_post": (
            {
                "id": hot["id"],
                "title": hot["title"],
                "comment_count": hot["comment_count"],
                "username": hot["username"],
                "created_at": hot["created_at"],
            }
            if hot is not None and hot["comment_count"] > 0 else None
        ),
        "redo_week": redo_week,
    }


@router.get("/api/search")
def global_search(
    q: PostSearchQuery = "", limit: Annotated[int, Query(ge=1, le=20)] = 8,
    user=Depends(security.current_user),
):
    # Ctrl K 命令面板用：我自己的题目/错因 + 讨论区帖子；纯 LIKE 查询，不调用 AI、不占额度。
    with db.connect() as conn:
        conn.execute("BEGIN")
        return search_all(conn, user["id"], q, limit)
