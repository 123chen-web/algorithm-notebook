"""boss routes (F5 Boss 战：每周挑战失败 3+ 次的错题)。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main
import json

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field


router = APIRouter()

# 一场最多几道题。
BOSS_ROUND_LIMIT = 5
# 单题作答时限（秒）：超过则判负，即使答案正确。
BOSS_TIME_LIMIT_SECONDS = 600
# 失败次数口径：quality 0-2 为"回答错误"（见 scheduler.schedule 的文档）。
BOSS_FAIL_QUALITY_MAX = 2
# 入池门槛：累计失败达到这个次数的错题才有资格当 Boss。
BOSS_POOL_FAIL_THRESHOLD = 3


class RoundResult(BaseModel):
    mistake_id: int
    correct: bool
    seconds: int = Field(ge=0)


def _fail_counts_cte():
    # mistakes 表没有 fail_count 列（说明文档的说法与基座不符）：失败次数从
    # reviews 推导，quality < 3 为失败；撤销的复习会物理删除 reviews 行，
    # 所以直接计数即可。
    return """
        SELECT mistake_id,
               SUM(CASE WHEN quality <= ? THEN 1 ELSE 0 END) AS fails
        FROM reviews
        GROUP BY mistake_id
    """


def boss_pool(conn, user_id, limit=BOSS_ROUND_LIMIT, mistake_id=None):
    """Boss 池：本人 fail_count>=3 且未毕业且未暂停的错题，按 due 最早排序。"""
    rows = conn.execute(
        """
        SELECT m.id AS mistake_id, p.title AS title, m.due_date AS due_date
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        LEFT JOIN (""" + _fail_counts_cte() + """) f
            ON f.mistake_id = m.id
        WHERE p.user_id = ?
          AND m.suspended_at IS NULL
          AND (? IS NULL OR m.id = ?)
          AND COALESCE(f.fails, 0) >= ?
          AND NOT EXISTS (
              SELECT 1 FROM boss_graduations g
              WHERE g.user_id = ? AND g.mistake_id = m.id
          )
        ORDER BY m.due_date ASC, m.id ASC
        LIMIT ?
        """,
        (BOSS_FAIL_QUALITY_MAX, user_id, mistake_id, mistake_id, BOSS_POOL_FAIL_THRESHOLD, user_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _get_session(conn, session_id, user_id):
    row = conn.execute(
        "SELECT * FROM boss_sessions WHERE id = ? AND user_id = ?",
        (session_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "挑战场次不存在")
    return row


@router.post("/api/boss/session")
def boss_start_session(user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        pool = boss_pool(conn, user["id"])
        if not pool:
            return {"empty": True}
        now = main.utc_now()
        cursor = conn.execute(
            "INSERT INTO boss_sessions(user_id, started_at, selected_mistake_ids) VALUES (?, ?, ?)",
            (user["id"], now, json.dumps([row["mistake_id"] for row in pool])),
        )
        session_id = cursor.lastrowid
    return {
        "session_id": session_id,
        "rounds": [
            {"mistake_id": row["mistake_id"], "title": row["title"]}
            for row in pool
        ],
    }


@router.post("/api/boss/session/{session_id}/round")
def boss_submit_round(session_id: int, data: RoundResult, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        session = _get_session(conn, session_id, user["id"])
        if session["finished_at"] is not None:
            raise HTTPException(409, "这场挑战已经结算，不能再提交回合")

        # 回合合法性：必须是本人的错题。前端只会提交本场发出的题目，
        # 下面先查重复、再查池资格，防篡改。
        main.owned_mistake(conn, data.mistake_id, user["id"])
        duplicate = conn.execute(
            "SELECT id FROM boss_rounds WHERE session_id = ? AND mistake_id = ?",
            (session_id, data.mistake_id),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(409, "这道题在本场已经挑战过")

        # 提交时仍在池里（fail_count>=3、未暂停、未毕业）：本场开出后被暂停/
        # 毕业的题不允许再打。
        if data.mistake_id not in json.loads(session["selected_mistake_ids"]):
            raise HTTPException(422, "这道题不在本场挑战范围内")
        eligible = boss_pool(conn, user["id"], limit=1, mistake_id=data.mistake_id)
        if all(row["mistake_id"] != data.mistake_id for row in eligible):
            raise HTTPException(422, "这道题不在本场挑战范围内")

        submitted = conn.execute(
            "SELECT COUNT(*) FROM boss_rounds WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        if submitted >= BOSS_ROUND_LIMIT:
            raise HTTPException(409, "本场挑战回合数已满")

        won = bool(data.correct) and data.seconds <= BOSS_TIME_LIMIT_SECONDS
        result = "win" if won else "loss"
        graduated = False
        if won:
            # 毕业记录主键是 (user_id, mistake_id)，重复提交靠唯一约束兜底。
            conn.execute(
                "INSERT OR IGNORE INTO boss_graduations(user_id, mistake_id, graduated_at) "
                "VALUES (?, ?, ?)",
                (user["id"], data.mistake_id, main.utc_now()),
            )
            graduated = True
        conn.execute(
            "INSERT INTO boss_rounds(session_id, mistake_id, result, seconds) "
            "VALUES (?, ?, ?, ?)",
            (session_id, data.mistake_id, result, data.seconds),
        )
        conn.execute(
            "UPDATE boss_sessions SET wins = wins + ?, losses = losses + ? WHERE id = ?",
            (1 if won else 0, 0 if won else 1, session_id),
        )
        totals = conn.execute(
            "SELECT wins, losses FROM boss_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return {
        "session_id": session_id,
        "mistake_id": data.mistake_id,
        "result": result,
        "graduated": graduated,
        "wins": totals["wins"],
        "losses": totals["losses"],
    }


@router.post("/api/boss/session/{session_id}/finish")
def boss_finish_session(session_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        session = _get_session(conn, session_id, user["id"])
        if session["finished_at"] is None:
            conn.execute(
                "UPDATE boss_sessions SET finished_at = ? WHERE id = ?",
                (main.utc_now(), session_id),
            )
            session = _get_session(conn, session_id, user["id"])
    return {"wins": session["wins"], "losses": session["losses"]}
