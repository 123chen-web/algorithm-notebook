"""小组域服务：成员、积分、周目标、今日动态。"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import secrets

import db
import images
import stats_summary
from core import config, time

from collections import defaultdict
from fastapi import HTTPException
from group_levels import GroupPointsAccumulator, level_summary
from scheduler import today_in_timezone
from learning_stats import current_streak
def member_group(conn, group_id, user_id):
    group = conn.execute(
        """
        SELECT g.* FROM study_groups g
        JOIN study_group_members m ON m.group_id = g.id
        WHERE g.id = ? AND m.user_id = ?
        """,
        (group_id, user_id),
    ).fetchone()
    if group is None:
        # 不区分不存在和未加入，避免枚举 ID 探测私人小组。
        raise HTTPException(404, "小组不存在")
    return group


def group_members_by_id(conn, group_ids):
    """Load all visible groups' members together, in preview order."""
    members = {group_id: [] for group_id in group_ids}
    if not members:
        return members
    placeholders = ",".join("?" for _ in group_ids)
    rows = conn.execute(
        f"""
        SELECT gm.group_id, u.id, u.username, u.timezone, u.avatar_version,
               gm.joined_at
        FROM study_group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id IN ({placeholders}) AND u.deleted_at IS NULL
        ORDER BY gm.joined_at ASC, u.id ASC
        """,
        tuple(group_ids),
    ).fetchall()
    for row in rows:
        members[row["group_id"]].append(row)
    return members


def group_points_by_id(conn, members_by_group, *, review_days=None):
    """Stream two activity queries over distinct members, regardless of groups.

    Parse each activity once, then apply each membership's inclusive joining
    instant and separate daily caps. SQL must not compare ISO strings: offsets
    and precision can differ for equal instants. Reviews of older problems count.
    """
    accumulator = GroupPointsAccumulator(members_by_group, review_days)
    user_ids = tuple(accumulator.memberships)
    if not user_ids:
        return accumulator.totals
    placeholders = ",".join("?" for _ in user_ids)
    review_rows = conn.execute(
        f"""
        SELECT p.user_id, r.reviewed_at
        FROM problems p
        JOIN mistakes m ON m.problem_id = p.id
        JOIN reviews r ON r.mistake_id = m.id
        WHERE p.user_id IN ({placeholders})
        """,
        user_ids,
    )
    for row in review_rows:
        accumulator.add_review(row)
    problem_rows = conn.execute(
        f"""
        SELECT p.user_id, p.created_at
        FROM problems p
        WHERE p.user_id IN ({placeholders})
        """,
        user_ids,
    )
    for row in problem_rows:
        accumulator.add_problem(row)
    return accumulator.totals


def group_member_avatar(member):
    return {
        "id": member["id"],
        "username": member["username"],
        "avatar_version": member["avatar_version"],
        "has_avatar": images.avatar_path(member["id"]).is_file(),
    }


def group_detail(conn, group_id, user_id):
    group = member_group(conn, group_id, user_id)
    members_by_group = group_members_by_id(conn, (group_id,))
    members = members_by_group[group_id]
    # Streaks retain their full-history meaning; growth counts only activity
    # since joining. Reuse the streamed reviews for both, including trial users.
    review_days = {}
    member_points = group_points_by_id(
        conn, members_by_group, review_days=review_days,
    )[group_id]
    streaks = {
        row["id"]: current_streak(review_days.get(row["id"], set()), time.today_for(row))
        for row in members
    }
    points = sum(member_points.values())
    ranked_members = sorted(
        members, key=lambda row: (-streaks[row["id"]], row["username"])
    )
    my_preference = conn.execute(
        "SELECT include_in_summary FROM study_group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    ).fetchone()
    return {
        "id": group["id"],
        "name": group["name"],
        "invite_code": group["invite_code"],
        "created_at": group["created_at"],
        "is_creator": group["created_by"] == user_id,
        "include_in_summary": bool(my_preference[0]) if my_preference else True,
        "member_limit": config.GROUP_MAX_MEMBERS,
        "level": level_summary(points),
        "points": points,
        "members": [
            {
                # 小组由熟人邀请，刻意展示真实用户名；全站排行榜仍匿名。
                **group_member_avatar(row),
                "current_streak_days": streaks[row["id"]],
                "points": member_points[row["id"]],
                "is_creator": group["created_by"] == row["id"],
            }
            for row in ranked_members
        ],
        "weakness_by_zone": stats_summary.weakness_by_zone(
            conn, {row["id"] for row in members}, min_cohort=config.GROUP_WEAKNESS_MIN_COHORT
        ),
        "weekly_goal": group_weekly_goal(conn, group_id),
    }


def group_week_start(now=None):
    """本周一（北京时间 Asia/Shanghai）的日期，ISO 字符串。"""
    shanghai = ZoneInfo(config.GROUP_WEEK_TIMEZONE)
    current = now if now is not None else datetime.now(timezone.utc)
    local = current.astimezone(shanghai)
    return (local.date() - timedelta(days=local.weekday())).isoformat()


def _as_utc(value):
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def group_weekly_goal_progress(conn, group_id, goal_type):
    """实时统计本周小组目标进度（不落库）。

    口径：
    - 统计窗口：北京时间本周一 00:00（含）到下周一 00:00（不含）。
    - 只统计成员加入该小组当刻及之后的行为（与小组等级积分口径一致）。
    - review：reviews 表的复习记录条数；record：problems 表的新增题目条数。
    - 每人每个本地日（按各自时区，与小组积分的日封顶口径一致）对合计的
      贡献封顶 GROUP_WEEKLY_GOAL_DAILY_CAP（20），防止刷数。
    - 不新增定时任务；目标可改，进度由数据实时得出、不可删除。
    """
    week_start_dt = _as_utc(group_week_start()).replace(
        tzinfo=ZoneInfo(config.GROUP_WEEK_TIMEZONE)
    )
    week_end_dt = week_start_dt + timedelta(days=7)
    members = conn.execute(
        """
        SELECT u.id, u.timezone, gm.joined_at
        FROM study_group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ? AND u.deleted_at IS NULL
        """,
        (group_id,),
    ).fetchall()
    if not members:
        return 0
    memberships = {
        row["id"]: (row["timezone"], _as_utc(row["joined_at"])) for row in members
    }
    user_ids = tuple(memberships)
    placeholders = ",".join("?" for _ in user_ids)
    if goal_type == "review":
        rows = conn.execute(
            f"""
            SELECT p.user_id, r.reviewed_at AS occurred_at
            FROM problems p
            JOIN mistakes m ON m.problem_id = p.id
            JOIN reviews r ON r.mistake_id = m.id
            WHERE p.user_id IN ({placeholders})
            """,
            user_ids,
        )
    else:
        rows = conn.execute(
            f"""
            SELECT user_id, created_at AS occurred_at FROM problems
            WHERE user_id IN ({placeholders})
            """,
            user_ids,
        )
    days = defaultdict(int)
    for row in rows:
        timezone_name, joined_at = memberships[row["user_id"]]
        occurred_at = _as_utc(row["occurred_at"])
        if occurred_at < joined_at:
            continue
        if not week_start_dt <= occurred_at < week_end_dt:
            continue
        days[(row["user_id"], today_in_timezone(timezone_name, occurred_at))] += 1
    return sum(
        min(config.GROUP_WEEKLY_GOAL_DAILY_CAP, count) for count in days.values()
    )


def group_weekly_goal(conn, group_id):
    """本周小组目标（含实时进度），未设定返回 None。"""
    week_start = group_week_start()
    goal = conn.execute(
        """
        SELECT goal_type, target, week_start FROM group_weekly_goals
        WHERE group_id = ? AND week_start = ?
        """,
        (group_id, week_start),
    ).fetchone()
    if goal is None:
        return None
    total = group_weekly_goal_progress(conn, group_id, goal["goal_type"])
    return {
        "goal_type": goal["goal_type"],
        "target": goal["target"],
        "week_start": goal["week_start"],
        "total": total,
        "progress": min(100, round(total / goal["target"] * 100)),
    }


def group_today_feed(conn, group_id):
    """组内今日动态：每位成员今天（按各自时区本地日）的复习道数与是否达标。

    只返回数量与是否达标，不含题目名、错题内容、笔记。关闭隐私开关
    （users.show_group_today = 0）的成员只显示"未公开"。显示名沿用
    group_member_avatar 的字段。
    """
    members = conn.execute(
        """
        SELECT u.id, u.username, u.timezone, u.avatar_version, u.show_group_today
        FROM study_group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ? AND u.deleted_at IS NULL
        ORDER BY gm.joined_at ASC, u.id ASC
        """,
        (group_id,),
    ).fetchall()
    feed = []
    for member in members:
        entry = {
            **group_member_avatar(member),
            "visible": bool(member["show_group_today"]),
        }
        if not entry["visible"]:
            feed.append(entry)
            continue
        today = today_in_timezone(member["timezone"])
        # 按成员时区逐条判断本地日：复习记录量小，直接在 Python 里过滤。
        reviewed_today = 0
        for row in conn.execute(
            """
            SELECT r.reviewed_at FROM problems p
            JOIN mistakes m ON m.problem_id = p.id
            JOIN reviews r ON r.mistake_id = m.id
            WHERE p.user_id = ?
            """,
            (member["id"],),
        ):
            if today_in_timezone(member["timezone"], _as_utc(row["reviewed_at"])) == today:
                reviewed_today += 1
        entry["reviews_today"] = reviewed_today
        entry["goal_met"] = reviewed_today >= config.GROUP_TODAY_GOAL_REVIEWS
        feed.append(entry)
    return feed


# ---- 薄弱点汇总（组长视角，纯聚合，不含任何个人明细） ----

# 参与汇总的最少合格成员数：低于此数不显示任何汇总，防止从汇总反推个人。
WEAKNESS_SUMMARY_MIN_MEMBERS = 3

_PATTERN_TITLES = None


def _pattern_titles():
    """套路 id → 中文标题（只读 id/title 两列，进程内缓存）。"""
    global _PATTERN_TITLES
    if _PATTERN_TITLES is None:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parent / "static" / "data" / "patterns.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _PATTERN_TITLES = {
                item["id"]: item.get("title") or item["id"] for item in data
            }
        except (OSError, ValueError, KeyError, TypeError):
            _PATTERN_TITLES = {}
    return _PATTERN_TITLES


def eligible_summary_member_ids(conn, group_id):
    """参与汇总的合格成员 id：未注销 + 未退出汇总。"""
    rows = conn.execute(
        """
        SELECT u.id FROM study_group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = ? AND u.deleted_at IS NULL
          AND gm.include_in_summary = 1
        """,
        (group_id,),
    ).fetchall()
    return [row[0] for row in rows]


def group_weakness_summary(conn, group_id):
    """组长视角的成员薄弱点汇总。

    口径（同步写进 docs/features/groups.md）：
    - 只统计合格成员：未注销且未退出汇总（include_in_summary=1）。
    - 少于 3 个合格成员时不返回任何汇总（has_summary=False），只给原因文案。
    - 错因分布：合格成员 mistake_tags 按 14 个固定 code 聚合 → 4 大类。
    - 判题类型分布：合格成员 problems.verdict（非空）。
    - 套路：合格成员易错点最多的 5 个 pattern_id（经 problem_patterns 关联
      mistakes 计数）+ 全组学习路线进度（pattern_progress 的 learning/mastered）。
    - 本周活跃人数：本周一 00:00（北京）以来有 reviews 记录的合格成员数。
    - 复习完成率：本周到期的错题中，本周已复习过的去重错题占比。

    返回里只有聚合数字，没有用户名、题名、错题内容、笔记。
    """
    import error_taxonomy

    member_ids = eligible_summary_member_ids(conn, group_id)
    eligible = len(member_ids)
    if eligible < WEAKNESS_SUMMARY_MIN_MEMBERS:
        return {
            "group_id": group_id,
            "eligible_members": eligible,
            "has_summary": False,
            "reason": (
                f"参与汇总的成员只有 {eligible} 人，不足 "
                f"{WEAKNESS_SUMMARY_MIN_MEMBERS} 人。为保护个人隐私，"
                "人数不足时不显示任何汇总统计。"
            ),
        }
    placeholders = ",".join("?" for _ in member_ids)

    # ① 错因分布：4 大类 + 14 错因。
    cause_rows = conn.execute(
        f"""
        SELECT t.tag, COUNT(DISTINCT m.id) AS count
        FROM mistake_tags t
        JOIN mistakes m ON m.id = t.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE t.user_id IN ({placeholders}) AND p.user_id = t.user_id
        GROUP BY t.tag
        """,
        tuple(member_ids),
    ).fetchall()
    group_counts = {group: 0 for group, _ in error_taxonomy.CAUSE_GROUPS}
    causes = []
    total_causes = 0
    for row in cause_rows:
        code, count = row[0], row[1]
        total_causes += count
        try:
            group = error_taxonomy.cause_group(code)
            label = error_taxonomy.cause_label(code)
        except KeyError:
            continue
        group_counts[group] += count
        causes.append({
            "code": code, "label": label, "group": group, "count": count,
        })
    causes.sort(key=lambda item: (-item["count"], item["code"]))
    cause_groups = [
        {
            "code": group,
            "label": error_taxonomy.group_label(group),
            "count": group_counts[group],
            "percent": round(group_counts[group] / total_causes * 100) if total_causes else 0,
        }
        for group, _ in error_taxonomy.CAUSE_GROUPS
    ]

    # ② 判题类型分布。
    verdict_rows = conn.execute(
        f"""
        SELECT verdict, COUNT(*) AS count FROM problems
        WHERE user_id IN ({placeholders}) AND verdict != ''
        GROUP BY verdict ORDER BY count DESC
        """,
        tuple(member_ids),
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
            "code": code, "label": label, "count": count,
            "percent": round(count / verdict_total * 100) if verdict_total else 0,
        })

    # ③ 套路：错得最多的 5 个 + 学习路线进度。
    pattern_rows = conn.execute(
        f"""
        SELECT pp.pattern_id, COUNT(DISTINCT m.id) AS count
        FROM problem_patterns pp
        JOIN problems p ON p.id = pp.problem_id
        JOIN mistakes m ON m.problem_id = p.id
        WHERE p.user_id IN ({placeholders})
        GROUP BY pp.pattern_id
        ORDER BY count DESC, pp.pattern_id ASC
        LIMIT 5
        """,
        tuple(member_ids),
    ).fetchall()
    titles = _pattern_titles()
    top_patterns = [
        {
            "pattern_id": row[0],
            "title": titles.get(row[0], row[0]),
            "mistake_count": row[1],
        }
        for row in pattern_rows
    ]
    roadmap_rows = conn.execute(
        f"""
        SELECT status, COUNT(*) FROM pattern_progress
        WHERE user_id IN ({placeholders})
        GROUP BY status
        """,
        tuple(member_ids),
    ).fetchall()
    roadmap = {"learning": 0, "mastered": 0}
    for row in roadmap_rows:
        if row[0] in roadmap:
            roadmap[row[0]] = row[1]

    # ④ 本周活跃人数、复习完成率（周一起点用北京时间周一）。
    week_start = group_week_start()
    week_start_dt = _as_utc(week_start).replace(
        tzinfo=ZoneInfo(config.GROUP_WEEK_TIMEZONE)
    )
    week_end_dt = week_start_dt + timedelta(days=7)
    week_end_date = week_end_dt.date().isoformat()
    active_rows = conn.execute(
        f"""
        SELECT COUNT(DISTINCT p.user_id)
        FROM problems p
        JOIN mistakes m ON m.problem_id = p.id
        JOIN reviews r ON r.mistake_id = m.id
        WHERE p.user_id IN ({placeholders}) AND r.reviewed_at >= ?
        """,
        tuple(member_ids) + (week_start,),
    ).fetchone()
    due_rows = conn.execute(
        f"""
        SELECT COUNT(DISTINCT m.id)
        FROM problems p
        JOIN mistakes m ON m.problem_id = p.id
        WHERE p.user_id IN ({placeholders}) AND m.due_date <= ?
        """,
        tuple(member_ids) + (week_end_date,),
    ).fetchone()
    reviewed_rows = conn.execute(
        f"""
        SELECT COUNT(DISTINCT m.id)
        FROM problems p
        JOIN mistakes m ON m.problem_id = p.id
        JOIN reviews r ON r.mistake_id = m.id
        WHERE p.user_id IN ({placeholders})
          AND m.due_date <= ?
          AND r.reviewed_at >= ?
        """,
        tuple(member_ids) + (week_end_date, week_start),
    ).fetchone()
    due_count = due_rows[0]
    reviewed_count = reviewed_rows[0]

    return {
        "group_id": group_id,
        "eligible_members": eligible,
        "has_summary": True,
        "cause_groups": cause_groups,
        "causes": causes,
        "verdicts": verdicts,
        "top_patterns": top_patterns,
        "roadmap": roadmap,
        "active_this_week": active_rows[0],
        "review_completion": {
            "due": due_count,
            "reviewed": reviewed_count,
            "percent": round(reviewed_count / due_count * 100) if due_count else 0,
        },
    }


def generate_group_invite_code():
    return "".join(
        secrets.choice(config.GROUP_INVITE_CODE_ALPHABET)
        for _ in range(config.GROUP_INVITE_CODE_LENGTH)
    )


def require_group_capacity_for_user(conn, user_id):
    count = conn.execute(
        "SELECT COUNT(*) FROM study_group_members WHERE user_id = ?", (user_id,)
    ).fetchone()[0]
    if count >= config.GROUP_MAX_PER_USER:
        raise HTTPException(403, f"每人最多同时加入 {config.GROUP_MAX_PER_USER} 个小组")

