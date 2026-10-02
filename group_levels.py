"""Study-group growth from current members' activity after joining.

Scores are derived on reads; no points or level state is persisted. Calendar
days use each member's timezone, as the review-streak calculation does.
"""

from collections import defaultdict
from datetime import datetime, timezone

from scheduler import today_in_timezone


REVIEW_POINTS = 1
REVIEW_DAILY_CAP = 10
CHECKIN_BONUS = 5
RECORD_POINTS = 3
RECORD_DAILY_CAP = 9

LEVELS = (
    {"number": 1, "name": "初稿", "min_points": 0},
    {"number": 2, "name": "批注", "min_points": 120},
    {"number": 3, "name": "圈点", "min_points": 360},
    {"number": 4, "name": "朱批", "min_points": 800},
    {"number": 5, "name": "精批", "min_points": 1500},
    {"number": 6, "name": "优等", "min_points": 2600},
    {"number": 7, "name": "金榜", "min_points": 4200},
    {"number": 8, "name": "满分", "min_points": 6500},
)

RULES = (
    {
        "key": "review", "label": "复习评分",
        "points": REVIEW_POINTS, "daily_cap": REVIEW_DAILY_CAP,
    },
    {
        "key": "checkin", "label": "每日打卡",
        "points": CHECKIN_BONUS, "daily_cap": CHECKIN_BONUS,
    },
    {
        "key": "record", "label": "新增题目记录",
        "points": RECORD_POINTS, "daily_cap": RECORD_DAILY_CAP,
    },
)


def _timestamp(value):
    timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    # Application timestamps are UTC ISO strings. Interpret a legacy naive
    # timestamp as UTC instead of inheriting the server's local timezone.
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def points_by_user(members, review_rows, problem_rows):
    """Return every current member's score, with inclusive joining boundaries.

    Members expose id/timezone/joined_at. Reviews expose user_id/reviewed_at;
    problems expose user_id/created_at. Both dictionaries and sqlite rows work.
    Rows from former members and activity before joining are ignored.
    """
    membership = {
        row["id"]: (row["timezone"], _timestamp(row["joined_at"]))
        for row in members
    }
    totals = dict.fromkeys(membership, 0)
    # Each bucket stores review points and record points, already capped.
    days = defaultdict(lambda: [0, 0])
    for rows, time_key, index, points, cap in (
        (review_rows, "reviewed_at", 0, REVIEW_POINTS, REVIEW_DAILY_CAP),
        (problem_rows, "created_at", 1, RECORD_POINTS, RECORD_DAILY_CAP),
    ):
        for row in rows:
            user_id = row["user_id"]
            member = membership.get(user_id)
            if member is None:
                continue
            member_timezone, joined_at = member
            occurred_at = _timestamp(row[time_key])
            if occurred_at < joined_at:
                continue
            day = today_in_timezone(member_timezone, occurred_at)
            bucket = days[(user_id, day)]
            bucket[index] = min(cap, bucket[index] + points)

    for (user_id, _), (review_points, record_points) in days.items():
        totals[user_id] += review_points + record_points
        if review_points:
            totals[user_id] += CHECKIN_BONUS
    return totals


def level_summary(points):
    """Summarize the attained level and progress within its current interval."""
    points = max(0, int(points))
    index = 0
    for candidate, level in enumerate(LEVELS):
        if points < level["min_points"]:
            break
        index = candidate
    level = LEVELS[index]
    following = LEVELS[index + 1] if index + 1 < len(LEVELS) else None
    return {
        "number": level["number"],
        "name": level["name"],
        "points": points,
        "floor": level["min_points"],
        "next_name": following["name"] if following else None,
        "next_points": following["min_points"] if following else None,
        "points_to_next": following["min_points"] - points if following else None,
        "progress": (
            (points - level["min_points"])
            / (following["min_points"] - level["min_points"])
            if following else 1.0
        ),
    }
