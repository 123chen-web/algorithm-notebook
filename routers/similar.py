"""similar routes: 举一反三（复习后按错题标签补 3 道题）。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException

import recommend as recommend_logic


router = APIRouter()

# 每次返回 3 道；纯读聚合查询，不调用 AI、不占额度。
SIMILAR_COUNT = 3
SIMILAR_RATE_LIMIT = 30
SIMILAR_RATE_WINDOW = 60


def _owned_mistake(conn, mistake_id, user_id):
    # 错题归属校验：别人的错题和不存在的错题都 404，避免信息泄露。
    row = conn.execute(
        """
        SELECT m.id, m.problem_id, p.user_id, p.language
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE m.id = ?
        """,
        (mistake_id,),
    ).fetchone()
    if row is None or row["user_id"] != user_id:
        raise HTTPException(404, "记录不存在")
    return dict(row)


def _mistake_tags(conn, mistake_id, user_id):
    return [
        row["tag"]
        for row in conn.execute(
            "SELECT tag FROM mistake_tags WHERE mistake_id = ? AND user_id = ? ORDER BY tag",
            (mistake_id, user_id),
        )
    ]


def _cf_items_for_tags(conn, user, today, cn_tags):
    """复用 Task C 的推荐查询取 CF 题。

    recommend_for_today 同用户同一天同一份缓存结果稳定，且内部负责落库去重
    （同一道题只推荐一次）。调用方必须用写连接：它内部 INSERT problem_recommendations。
    错题的中文标签按 recommend.TAG_MAP 映射到 CF 标签后优先排前。
    """
    items, _hint = recommend_logic.recommend_for_today(conn, user, today)
    wanted = set()
    for tag in cn_tags:
        wanted.update(recommend_logic.TAG_MAP.get(tag) or ())
    if not wanted:
        return items
    matched = [item for item in items if set(item.get("tags") or ()) & wanted]
    matched_ids = {id(item) for item in matched}
    return matched + [item for item in items if id(item) not in matched_ids]


def _cf_similar_item(cf_item, language):
    title = cf_item.get("name") or f"{cf_item['contest_id']}{cf_item['idx']}"
    return {
        "title": title,
        "source": "codeforces",
        "difficulty": cf_item.get("rating"),
        "tags": list(cf_item.get("tags") or []),
        "url": cf_item.get("url"),
        "reason": cf_item.get("reason") or "",
        "add_payload": {
            # quick 模式：前端剥掉下面 NewProblem 没有的字段后，
            # 直接 POST /api/problems 建题；沿用当前题目语言，空时默认 Python。
            "title": title,
            "zone": "算法",
            "language": language or "Python",
            "quick": True,
            "source_tag": "举一反三",
            "source_url": cf_item.get("url"),
        },
    }


def _site_fillers(conn, user_id, exclude_problem_id, cn_tags, need):
    """站内补齐：本人同标签的其它题目（排除刚复习的错题所属题目），按 id 倒序取。

    add_payload 从站内题预填标题/分区/语言，一键加入即复制建题。
    """
    if need <= 0 or not cn_tags:
        return []
    placeholders = ",".join("?" for _ in cn_tags)
    rows = conn.execute(
        f"""
        SELECT p.id, p.title, p.zone, p.language,
               GROUP_CONCAT(DISTINCT t.tag) AS tags
        FROM problems p
        JOIN mistakes m ON m.problem_id = p.id
        JOIN mistake_tags t ON t.mistake_id = m.id AND t.user_id = p.user_id
        WHERE p.user_id = ? AND p.id != ? AND t.tag IN ({placeholders})
        GROUP BY p.id
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (user_id, exclude_problem_id, *cn_tags, need),
    ).fetchall()
    items = []
    for row in rows:
        tags = (row["tags"] or "").split(",") if row["tags"] else []
        items.append(
            {
                "title": row["title"],
                "source": "站内",
                "difficulty": None,
                "tags": [tag for tag in tags if tag],
                "url": None,
                "reason": "站内同标签题：" + "、".join(tags),
                "add_payload": {
                    "title": row["title"],
                    "zone": row["zone"],
                    "language": row["language"] or "",
                    "quick": True,
                    "source_tag": "举一反三",
                },
            }
        )
    return items


@router.get("/api/review/similar")
def get_review_similar(mistake_id: int, user=Depends(main.current_user)):
    # 先取 CF 推荐（走 recommend 的当天稳定缓存与落库去重），不足 3 道再用站内题补齐。
    if main.rate_limited(f"similar:{user['id']}", SIMILAR_RATE_LIMIT, SIMILAR_RATE_WINDOW):
        raise HTTPException(429, "操作过于频繁，请稍后再试")
    today = main.today_for(user)
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        mistake = _owned_mistake(conn, mistake_id, user["id"])
        cn_tags = _mistake_tags(conn, mistake_id, user["id"])
        cf_items = _cf_items_for_tags(conn, user, today, cn_tags)
        items = [_cf_similar_item(item, mistake["language"]) for item in cf_items[:SIMILAR_COUNT]]
        need = SIMILAR_COUNT - len(items)
        items.extend(_site_fillers(conn, user["id"], mistake["problem_id"], cn_tags, need))
    return {"mistake_id": mistake_id, "tags": cn_tags, "items": items}
