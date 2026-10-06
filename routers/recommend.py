"""recommend routes: 每日推荐题（Codeforces 官方题库缓存）。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel

import recommend as recommend_logic


router = APIRouter()


class RecommendState(BaseModel):
    state: str


@router.get("/api/recommend")
def get_recommend(user=Depends(main.current_user)):
    # 登录用户都能看（含体验账号）；纯统计 + 读缓存，不调用 AI、不占额度。
    if main.rate_limited(f"recommend:{user['id']}", 30, 60):
        raise HTTPException(429, "操作过于频繁，请稍后再试")
    today = main.today_for(user)
    with main.connect(write=True) as conn:
        items, hint = recommend_logic.recommend_for_today(conn, user, today)
    return {"items": items, "hint": hint}


@router.post("/api/recommend/{contest_id}/{idx}")
def set_recommend_state(contest_id: int, idx: str, data: RecommendState, user=Depends(main.current_user)):
    # 只能改自己的记录；rowcount 为 0 时 404，同时掩盖"记录不存在"和"别人的记录"。
    # X-CSRF-Protection 由全局中间件统一校验。
    if data.state not in ("done", "dismissed"):
        raise HTTPException(422, "state 只能是 done 或 dismissed")
    with main.connect(write=True) as conn:
        cursor = conn.execute(
            """
            UPDATE problem_recommendations SET state = ?
            WHERE user_id = ? AND contest_id = ? AND idx = ?
            """,
            (data.state, user["id"], contest_id, idx),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "记录不存在")
    return {"ok": True}
