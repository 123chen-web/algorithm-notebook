"""总览页的两张"开门卡片"：今日一签（今日宜忌）和今日简报。

- GET /api/today-sign：按用户和日期固定生成，来自用户自己的数据，不联网、不用 AI。
- GET /api/briefing/today：只读服务器每天凌晨抓好的简报缓存（digest.py --cache-only），
  请求时不联网、不调用 AI；没有缓存就返回空列表，前端把卡片藏起来。
"""
import main

import digest
import today_sign

from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()

BRIEFING_PAPERS = 2
BRIEFING_NEWS = 3
LOOKBACK_DAYS = 3  # 服务器时区和 UTC 日期可能错开，往前找最近 3 天里最新的一份


@router.get("/api/today-sign")
def get_today_sign(user=Depends(main.current_user)):
    if main.rate_limited(f"today-sign:{user['id']}", 60, 60):
        raise HTTPException(429, "请求太频繁，请稍后再试")
    day = main.today_for(user)
    with main.connect() as conn:
        return today_sign.build_sign(conn, user["id"], day)


def _latest_bundle():
    day = date.fromisoformat(main.utc_now()[:10])
    for back in range(LOOKBACK_DAYS):
        candidate = (day - timedelta(days=back)).isoformat()
        cached = digest.load_cache(candidate)
        if cached is not None:
            return candidate, cached
    return None, None


@router.get("/api/briefing/today")
def get_today_briefing(user=Depends(main.current_user)):
    if main.rate_limited(f"briefing:{user['id']}", 60, 60):
        raise HTTPException(429, "请求太频繁，请稍后再试")
    day, cached = _latest_bundle()
    if cached is None:
        return {"date": None, "items": []}
    papers, news = cached["papers"], cached["news"]
    digest._number(papers, news)  # 与缓存里摘要编号一致：论文在前、新闻在后
    summaries = cached.get("summaries", {})

    def card(item, source):
        return {
            "id": item["id"],
            "kind": item["kind"],
            "source": source,
            "title": item["title"],
            "summary": summaries.get(str(item["id"]), ""),
            "url": item["url"],
        }

    items = [card(item, "arXiv") for item in papers[:BRIEFING_PAPERS]]
    items += [card(item, "Hacker News") for item in news[:BRIEFING_NEWS]]
    return {"date": day, "items": items}
