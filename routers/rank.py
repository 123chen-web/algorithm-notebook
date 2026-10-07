"""rank routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from fastapi import Depends
from fastapi import Response
import hot_problems
import rank_board
import rank_cache
import rank_notice
from fastapi import APIRouter


router = APIRouter()


@router.get("/api/achievements")
def get_achievements(user=Depends(main.current_user)):
    with main.connect() as conn:
        # 多项统计共享只读快照，避免生成/删除记录时跨查询读到不同版本。
        conn.execute("BEGIN")
        metrics = main.learning_metrics(conn, user["id"], user["timezone"], main.today_for(user))
    return {"metrics": metrics, "achievements": main.evaluate_achievements(metrics)}


@router.get("/api/achievements/share-card")
def get_achievement_share_card(user=Depends(main.current_user)):
    today = main.today_for(user)
    with main.connect() as conn:
        # 与成就徽章一致，在同一只读快照内汇总所有指标。
        conn.execute("BEGIN")
        metrics = main.learning_metrics(conn, user["id"], user["timezone"], today)
    achievements = main.evaluate_achievements(metrics)
    png_bytes = main.render_achievement_card(user["username"], metrics, achievements, today)
    return Response(content=png_bytes, media_type="image/png")


@router.get("/api/leaderboard")
def leaderboard(user=Depends(main.current_user)):
    with main.connect() as conn:
        users = conn.execute(
            "SELECT id, username, timezone, is_trial, public_rank_opt_out FROM users "
            "WHERE deleted_at IS NULL AND is_banned = 0"
        ).fetchall()
        review_rows = conn.execute(
            """
            SELECT p.user_id AS user_id, r.reviewed_at
            FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            """
        ).fetchall()

    streaks = main.review_streaks_by_user(users, review_rows)

    # 体验账号不参与排行榜——跟体验账号能看 /api/plans 但不能真的下单是
    # 同一种"能看不能上榜"的模式；已排除的账号不占用前 LEADERBOARD_SIZE 名额。
    # 选择“不参与公开榜单”的用户同样不上榜，但仍能看到自己的连续天数。
    eligible = sorted(
        (
            row for row in users
            if not row["is_trial"] and not row["public_rank_opt_out"]
            and streaks[row["id"]] >= 1
        ),
        key=lambda row: (-streaks[row["id"]], row["id"]),
    )

    entries = [
        {
            "rank": index + 1,
            "display_name": row["username"],
            "streak_days": streaks[row["id"]],
        }
        for index, row in enumerate(eligible[:main.LEADERBOARD_SIZE])
    ]

    my_rank = None
    if not user["is_trial"] and not user["public_rank_opt_out"] and streaks[user["id"]] >= 1:
        for index, row in enumerate(eligible):
            if row["id"] == user["id"]:
                my_rank = index + 1
                break

    return {
        "entries": entries,
        "leaderboard_size": main.LEADERBOARD_SIZE,
        "me": {
            "streak_days": streaks[user["id"]],
            "rank": my_rank,
            "is_trial": user["is_trial"],
        },
    }


@router.put("/api/me/public-rank")
def update_public_rank(data: main.PublicRankSetting, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.recheck_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET public_rank_opt_out = ? WHERE id = ?",
            (0 if data.participate else 1, user["id"]),
        )
    rank_cache.invalidate()
    return {"participate": data.participate}


@router.get("/api/rank/yesterday")
def rank_yesterday(user=Depends(main.current_user)):
    return rank_board.yesterday_response(user)


@router.get("/api/rank/hot-problems")
def rank_hot_problems(user=Depends(main.current_user)):
    return hot_problems.hot_problems_response()


@router.get("/api/rank/notice")
def rank_notice_today(user=Depends(main.current_user)):
    with main.connect() as conn:
        return {"notice": rank_notice.current_notice(conn, rank_board.beijing_today())}
