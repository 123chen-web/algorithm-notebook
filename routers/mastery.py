"""掌握度热力图 + 下周主攻（GROWTH F3）。

只读聚合，不写库、不调用 AI。公式固定（详见模块注释与 GROWTH_REPORT.md）：

- 对/错：reviews.quality >= 3 记为"对"(1)，< 3 记为"错"(0)，
  与 main.mastery_signal 的口径一致。
- 题分：取该题最近 ≤3 次复习结果（最近的排第一），按权重 [0.5, 0.3, 0.2]
  加权求和。不到 3 次时缺的项按 0 计（不做归一化——只答对一次不足以
  证明掌握，最多只能拿到 0.5）；一次复习都没有的题不参与均值。
- 标签分：该标签下所有"有题分"的题的题分的算术均值；无数据为 null。
- 热力图周列：按用户本地时区划分周（周一为一周的开始），格子只统计
  "发生在该周内的复习"（每周内每题取最近 ≤3 次）；该周该标签没有
  任何复习数据则为 null。
- 下周主攻：选"分数最低且错题数 ≥5"的标签；plan 取该标签下按
  due_date 最早的 7 道错题 id。

main 命名空间下的名字一律用 main.<name> 在调用时取属性，
沿用 routers/rank.py 的写法，方便测试 monkeypatch。
"""
import main

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query


router = APIRouter()


# 题分权重：第 1/2/3 近的一次复习的权重。
WEIGHTS = (0.5, 0.3, 0.2)
# quality >= CORRECT_QUALITY 记为答对；与 main.mastery_signal 一致。
CORRECT_QUALITY = 3
# 下周主攻：标签至少要有这么多道错题才参与评选。
FOCUS_MIN_MISTAKES = 5
# 下周主攻计划最多列出这么多道题。
FOCUS_PLAN_SIZE = 7


def is_correct(quality):
    """单次复习结果：quality >= 3 为对。"""
    return quality >= CORRECT_QUALITY


def mistake_score(results):
    """题分。

    results：最近 ≤3 次复习的对错（True/False），最近的排第一。
    返回 0~1 的加权分；results 为空（没有复习记录）时返回 None，
    表示该题不参与标签均值。
    """
    results = list(results)[:3]
    if not results:
        return None
    return round(sum(weight * (1 if ok else 0) for weight, ok in zip(WEIGHTS, results)), 3)


def tag_score(scores):
    """标签分：题分列表的算术均值；全空则为 None。"""
    valid = [score for score in scores if score is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 3)


def _local_day(tzname, stamp):
    """ reviewed_at 文本 → 用户本地日期；与 mastery._local_day 同口径。

    没有时区信息的旧数据按 UTC 理解，避免被服务器本地时区带偏。
    """
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(ZoneInfo(tzname)).date()


def _week_starts(today, weeks):
    """返回 weeks 个周一（date），从最旧到最新；最后一周是 today 所在周。"""
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(days=7 * index) for index in range(weeks - 1, -1, -1)]


def _load_mistakes(conn, user_id):
    """该用户未暂停的错题：{mistake_id: {"tags": [...], "due": "YYYY-MM-DD"}}。"""
    mistakes = {}
    rows = conn.execute(
        """
        SELECT m.id AS mistake_id, m.due_date, t.tag
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        LEFT JOIN mistake_tags t
               ON t.mistake_id = m.id AND t.user_id = p.user_id
        WHERE p.user_id = ? AND m.suspended_at IS NULL
        """,
        (user_id,),
    ).fetchall()
    for row in rows:
        item = mistakes.setdefault(
            row["mistake_id"], {"tags": [], "due": row["due_date"]}
        )
        if row["tag"] is not None and row["tag"] not in item["tags"]:
            item["tags"].append(row["tag"])
    return mistakes


def _load_reviews(conn, mistake_ids):
    """{mistake_id: [(reviewed_at 原文, quality), ...]}，按时间从新到旧排好。"""
    reviews = {mid: [] for mid in mistake_ids}
    if not mistake_ids:
        return reviews
    placeholders = ",".join("?" for _ in mistake_ids)
    for row in conn.execute(
        f"SELECT mistake_id, quality, reviewed_at FROM reviews "
        f"WHERE mistake_id IN ({placeholders}) ORDER BY id DESC",
        list(mistake_ids),
    ).fetchall():
        reviews[row["mistake_id"]].append((row["reviewed_at"], row["quality"]))
    for items in reviews.values():
        # reviewed_at 是 ISO 文本；解析成带时区时间再排，避免文本格式
        # 混杂（有的带时区、有的不带）时字典序失真。
        def sort_key(item):
            stamp, _quality = item
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

        items.sort(key=sort_key, reverse=True)
    return reviews


def _results(reviews):
    """复习记录 → 最近 ≤3 次的对错（最近的排第一）。"""
    return [is_correct(quality) for _stamp, quality in reviews[:3]]


@router.get("/api/mastery/heatmap")
def mastery_heatmap(
    weeks: int = Query(default=8, ge=1, le=26),
    user=Depends(main.current_user),
):
    today = main.today_for(user)
    starts = _week_starts(today, weeks)
    zone = user["timezone"]
    with main.connect() as conn:
        mistakes = _load_mistakes(conn, user["id"])
        reviews = _load_reviews(conn, list(mistakes))

    # 每题：全部复习的最近 ≤3 次（算标签总分用）+ 每周内的最近 ≤3 次。
    per_mistake_week = {}
    per_mistake_all = {}
    for mid in mistakes:
        items = reviews[mid]
        per_mistake_all[mid] = mistake_score(_results(items))
        by_week = []
        for start in starts:
            end = start + timedelta(days=7)
            week_items = [
                item
                for item in items
                if start <= _local_day(zone, item[0]) < end
            ]
            by_week.append(mistake_score(_results(week_items)))
        per_mistake_week[mid] = by_week

    # 标签集合：该用户未暂停错题上的全部标签。
    tags = sorted({tag for item in mistakes.values() for tag in item["tags"]})
    overall = {
        tag: tag_score(
            per_mistake_all[mid]
            for mid, item in mistakes.items()
            if tag in item["tags"]
        )
        for tag in tags
    }
    # 弱的排前面：分数从低到高；没有复习数据的标签沉底；同分按标签名。
    tags.sort(key=lambda tag: (overall[tag] is None, overall[tag] if overall[tag] is not None else 0, tag))

    cells = {}
    for tag in tags:
        column = []
        for week_index in range(weeks):
            scores = [
                per_mistake_week[mid][week_index]
                for mid, item in mistakes.items()
                if tag in item["tags"]
            ]
            column.append(tag_score(scores))
        cells[tag] = column

    return {
        "tags": tags,
        "weeks": [start.isoformat() for start in starts],
        "cells": cells,
    }


@router.get("/api/mastery/focus")
def mastery_focus(user=Depends(main.current_user)):
    with main.connect() as conn:
        mistakes = _load_mistakes(conn, user["id"])
        reviews = _load_reviews(conn, list(mistakes))

    scores = {mid: mistake_score(_results(reviews[mid])) for mid in mistakes}
    by_tag = {}
    for mid, item in mistakes.items():
        for tag in item["tags"]:
            entry = by_tag.setdefault(tag, {"count": 0, "scores": [], "ids": []})
            entry["count"] += 1
            entry["scores"].append(scores[mid])
            entry["ids"].append(mid)

    candidates = []
    for tag, entry in by_tag.items():
        score = tag_score(entry["scores"])
        if entry["count"] >= FOCUS_MIN_MISTAKES and score is not None:
            candidates.append((tag, score, entry))
    if not candidates:
        return {
            "tag": None,
            "score": None,
            "reason": "还没有错题数 ≥5 且有复习数据的标签，继续积累复习记录吧",
            "plan": [],
        }

    # 分数最低的胜出；同分按标签名，保证稳定。
    tag, score, entry = sorted(candidates, key=lambda item: (item[1], item[0]))[0]
    plan_ids = sorted(
        entry["ids"],
        key=lambda mid: (mistakes[mid]["due"] or "", mid),
    )[:FOCUS_PLAN_SIZE]
    return {
        "tag": tag,
        "score": score,
        "reason": f"该标签 {entry['count']} 道错题近三轮答对率 {round(score * 100)}%",
        "plan": plan_ids,
    }
