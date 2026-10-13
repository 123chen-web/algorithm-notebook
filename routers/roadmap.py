"""学习路线（把套路库搬进主站）：套路的学习状态、例题勾选，以及题目与套路的关联。

三张表（db 迁移 74）：

- pattern_progress：每个套路 0/1 条记录，status ∈ {learning, mastered}；没有记录 = 未学。
- pattern_example_done：勾过哪些经典例题（key = "<source>:<id>"）。
- problem_patterns：一道错题最多挂 3 个套路（上限由 pattern_match.normalize_pattern_ids 校验）。

pattern_id / example_key 必须在启动期读进来的 static/data/patterns.json 白名单里，
不在就 422——不把用户提交的字符串直接写进数据库。

main 命名空间下的名字一律用 main.<name> 在调用时取属性，方便测试 monkeypatch。
"""
from core import config, ratelimit, security, time
import ai
import ai_limits
import db
import os
from contextlib import ExitStack
from starlette import concurrency
import json
import pattern_match
import problem_progress
from models import InputModel
import pattern_match

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from typing import Literal


router = APIRouter()


class RoadmapStatus(InputModel):
    # none = 删掉记录回到"未学"。
    status: Literal["none", "learning", "mastered"]


class ExampleDone(InputModel):
    done: bool


def _known(pattern_id):
    if not pattern_match.pattern_is_known(pattern_id):
        raise HTTPException(422, "没有这个套路")
    return pattern_id


def _rate(user, what):
    if ratelimit.rate_limited(
        f"roadmap:{what}:{user['id']}", config.ROADMAP_LIMIT, config.ROADMAP_WINDOW_SECONDS
    ):
        raise HTTPException(429, "操作太频繁了，歇一下再试")


@router.get("/api/roadmap")
def get_roadmap(user=Depends(security.current_user)):
    """只返回本人数据：套路状态、勾过的例题、每个套路下记过几道错题。"""
    with db.connect() as conn:
        progress = {
            row["pattern_id"]: row["status"]
            for row in conn.execute(
                "SELECT pattern_id, status FROM pattern_progress WHERE user_id = ?",
                (user["id"],),
            )
        }
        examples_done = {}
        for row in conn.execute(
            "SELECT pattern_id, example_key FROM pattern_example_done "
            "WHERE user_id = ? ORDER BY rowid",
            (user["id"],),
        ):
            examples_done.setdefault(row["pattern_id"], []).append(row["example_key"])
        # 错题数：按题目计（一道题 = 一条记录），不是按易错点条目计。
        mistake_counts = {
            row["pattern_id"]: row["total"]
            for row in conn.execute(
                """
                SELECT pp.pattern_id AS pattern_id, COUNT(DISTINCT p.id) AS total
                FROM problem_patterns pp
                JOIN problems p ON p.id = pp.problem_id
                WHERE p.user_id = ?
                GROUP BY pp.pattern_id
                """,
                (user["id"],),
            )
        }
    # 套路被下架（清单里没有了）时，历史记录不再下发，前端也就不会渲染出空白卡片。
    progress = {key: value for key, value in progress.items() if pattern_match.pattern_is_known(key)}
    examples_done = {key: value for key, value in examples_done.items() if pattern_match.pattern_is_known(key)}
    mistake_counts = {key: value for key, value in mistake_counts.items() if pattern_match.pattern_is_known(key)}
    return {
        "progress": progress,
        "examples_done": examples_done,
        "mistake_counts": mistake_counts,
    }


@router.put("/api/roadmap/{pattern_id}")
def set_pattern_status(pattern_id: str, data: RoadmapStatus, user=Depends(security.current_user)):
    _known(pattern_id)
    _rate(user, "status")
    with db.connect(write=True) as conn:
        if data.status == "none":
            conn.execute(
                "DELETE FROM pattern_progress WHERE user_id = ? AND pattern_id = ?",
                (user["id"], pattern_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO pattern_progress(user_id, pattern_id, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, pattern_id)
                DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
                """,
                (user["id"], pattern_id, data.status, time.utc_now()),
            )
    return {"ok": True, "pattern_id": pattern_id, "status": data.status}


@router.put("/api/roadmap/{pattern_id}/examples/{example_key}")
def set_example_done(
    pattern_id: str, example_key: str, data: ExampleDone, user=Depends(security.current_user)
):
    _known(pattern_id)
    # key 必须是这个套路 examples 里真实存在的那几道，避免把任意字符串写进去。
    if example_key not in pattern_match.pattern_example_keys(pattern_id):
        raise HTTPException(422, "这道例题不属于这个套路")
    _rate(user, "example")
    with db.connect(write=True) as conn:
        if data.done:
            conn.execute(
                """
                INSERT INTO pattern_example_done(user_id, pattern_id, example_key, done_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, pattern_id, example_key) DO NOTHING
                """,
                (user["id"], pattern_id, example_key, time.utc_now()),
            )
        else:
            conn.execute(
                "DELETE FROM pattern_example_done "
                "WHERE user_id = ? AND pattern_id = ? AND example_key = ?",
                (user["id"], pattern_id, example_key),
            )
    return {"ok": True, "pattern_id": pattern_id, "example_key": example_key, "done": data.done}


# ---------------------------------------------------------------------------
# 自动归类：根据题目链接、题名、标签、思路猜它属于哪些套路（本地规则，不花 AI 额度）。
# 结果只是建议，用户确认后才通过 PUT /api/problems/{id}/patterns 真正归类。
# ---------------------------------------------------------------------------

class SuggestInput(InputModel):
    title: str = Field(default="", max_length=200)
    thinking: str = Field(default="", max_length=8000)
    source_url: str = Field(default="", max_length=2000)
    zone: str = Field(default="算法", max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=20)


class PatternsInput(InputModel):
    pattern_ids: list[str] = Field(max_length=config.MAX_PATTERNS_PER_PROBLEM + 5)


@router.post("/api/roadmap/suggest")
def suggest_patterns(data: SuggestInput, user=Depends(security.current_user)):
    """新增记录页边写边猜：给最多 3 个"可能属于"的套路。"""
    _rate(user, "suggest")
    suggestions = pattern_match.suggest(
        title=data.title, thinking=data.thinking, source_url=data.source_url,
        zone=data.zone, tags=[str(tag)[:20] for tag in data.tags],
    )
    return {"suggestions": suggestions}


@router.get("/api/roadmap/inbox")
def classification_inbox(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user=Depends(security.current_user),
):
    """"待分类箱"：算法分区里还没归到任何套路的题（新的在前），每道带最多 3 条建议。"""
    _rate(user, "inbox")
    with db.connect() as conn:
        base = (
            "FROM problems p WHERE p.user_id = ? AND p.zone = '算法' "
            "AND NOT EXISTS (SELECT 1 FROM problem_patterns pp WHERE pp.problem_id = p.id)"
        )
        total = conn.execute(f"SELECT COUNT(*) {base}", (user["id"],)).fetchone()[0]
        rows = conn.execute(
            f"SELECT p.id, p.title, p.zone, p.thinking, p.code, p.source_url, p.created_at "
            f"{base} ORDER BY p.id DESC LIMIT ? OFFSET ?",
            (user["id"], limit, offset),
        ).fetchall()
        tag_rows = conn.execute(
            "SELECT m.problem_id, t.tag FROM mistake_tags t JOIN mistakes m ON m.id = t.mistake_id "
            "WHERE m.problem_id IN (SELECT value FROM json_each(?)) ORDER BY t.rowid",
            (json.dumps([row["id"] for row in rows]),),
        ).fetchall() if rows else []
    tags = {}
    for row in tag_rows:
        tags.setdefault(row["problem_id"], [])
        if row["tag"] not in tags[row["problem_id"]]:
            tags[row["problem_id"]].append(row["tag"])
    items = []
    for row in rows:
        thinking = row["thinking"] or ""
        placeholder = thinking.startswith("（速记")
        items.append({
            "problem_id": row["id"],
            "title": row["title"],
            "snippet": "" if placeholder else thinking.strip().replace("\n", " ")[:80],
            "source_url": row["source_url"] or "",
            "created_at": row["created_at"],
            "tags": tags.get(row["id"], []),
            "suggestions": pattern_match.suggest(
                title=row["title"], thinking=thinking, source_url=row["source_url"] or "",
                zone=row["zone"], tags=tags.get(row["id"], []), code=(row["code"] or "")[:3000],
            ),
        })
    return {"total": total, "items": items}


@router.get("/api/roadmap/patterns/{pattern_id}/problems")
def pattern_classified_problems(pattern_id: str, user=Depends(security.current_user)):
    """某个套路\"已归类好的题\"（学习路线卡片上的\"你记过 N 道错题\"徽标点进去的列表）。只读本人数据。"""
    checked = pattern_match.validated_pattern_ids([pattern_id])[0]
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.id, p.title FROM problems p "
            "JOIN problem_patterns pp ON pp.problem_id = p.id "
            "WHERE p.user_id = ? AND pp.pattern_id = ? ORDER BY p.id DESC LIMIT 100",
            (user["id"], checked),
        ).fetchall()
    return {"pattern_id": checked, "problems": [
        {"problem_id": row[0], "title": row[1]} for row in rows]}


@router.put("/api/problems/{problem_id}/patterns")
def set_problem_patterns_route(problem_id: int, data: PatternsInput, user=Depends(security.current_user)):
    """把一道题归到 0–3 个套路（全量替换；传 [] 就是移出分类）。别人的题一律 404。"""
    _rate(user, "classify")
    pattern_ids = pattern_match.validated_pattern_ids(data.pattern_ids)
    with db.connect(write=True) as conn:
        problem_progress.owned_problem(conn, problem_id, user["id"])
        pattern_match.set_problem_patterns(conn, problem_id, pattern_ids)
    return {"ok": True, "problem_id": problem_id, "pattern_ids": pattern_ids}


class AiSuggestInput(InputModel):
    problem_id: int


@router.post("/api/roadmap/ai-suggest")
async def ai_suggest_patterns(data: AiSuggestInput, user=Depends(security.current_user)):
    """待分类箱里“让 AI 猜一下”：只猜用户自己的算法题，最多 3 个套路，花 1 次 AI 额度。
    结果只是建议，真正归类仍由用户点选确认。"""
    _rate(user, "ai-suggest")
    with db.connect() as conn:
        problem = problem_progress.owned_problem(conn, data.problem_id, user["id"])
    if problem["zone"] not in pattern_match.SUGGEST_ZONES:
        raise HTTPException(422, "AI 归类只支持算法分区的题")
    material = {
        "title": str(problem["title"])[:200],
        "source_url": str(problem.get("source_url") or "")[:300],
        "thinking": str(problem.get("thinking") or "")[:600],
    }
    # 额度的扣减、退还和生成练习题、拍照识别共用同一套规则：写事务里原子扣减，
    # AI 调用放到事务外，服务端失败（502/503/504）退还。
    with ExitStack() as stack:
        with db.connect(write=True) as conn:
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
                (user["id"], day, limit, limit),
            )
            if cursor.rowcount != 1:
                raise HTTPException(429, "今天的 AI 生成次数已用完")
            remaining = max(0, limit - quota["ai_daily_used"] - 1)
        with ai_limits.refund_on_server_failure(user["id"], day, db.connect):
            with ai_limits.track_call(user["id"], "pattern_suggest"):
                picked = await concurrency.run_in_threadpool(
                    ai.suggest_patterns, material, pattern_match.ai_candidates_text(), pattern_match.pattern_is_known,
                )
    suggestions = []
    for item in picked:
        info = pattern_match.pattern_info(item["pattern_id"])
        suggestions.append({
            "pattern_id": item["pattern_id"], "title": info["title"], "category": info["category"],
            "reason": item["reason"], "source": "ai",
        })
    return {"suggestions": suggestions, "ai_daily_remaining": remaining}
