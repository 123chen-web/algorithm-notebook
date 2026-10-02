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


class GroupPointsAccumulator:
    """Score streamed activity once, with separate joining windows/day caps.

    A user has one timezone but can have a different joining instant in each
    group. Optional review_days retains full-history local dates for streaks.
    """

    def __init__(self, members_by_group, review_days=None):
        self.totals = {}
        self.timezones = {}
        self.memberships = defaultdict(list)
        self.days = defaultdict(lambda: [0, 0])
        self.review_days = review_days
        for group_id, members in members_by_group.items():
            membership = {row["id"]: row for row in members}
            self.totals[group_id] = dict.fromkeys(membership, 0)
            for row in membership.values():
                user_id = row["id"]
                self.timezones[user_id] = row["timezone"]
                self.memberships[user_id].append((group_id, _timestamp(row["joined_at"])))

    def _add(self, row, time_key, index, points, cap):
        user_id = row["user_id"]
        memberships = self.memberships.get(user_id)
        if not memberships:
            return
        occurred_at = _timestamp(row[time_key])
        day = today_in_timezone(self.timezones[user_id], occurred_at)
        if index == 0 and self.review_days is not None:
            self.review_days.setdefault(user_id, set()).add(day)
        for group_id, joined_at in memberships:
            if occurred_at < joined_at:
                continue
            bucket = self.days[(group_id, user_id, day)]
            previous = bucket[index]
            bucket[index] = min(cap, previous + points)
            self.totals[group_id][user_id] += bucket[index] - previous
            if index == 0 and previous == 0:
                self.totals[group_id][user_id] += CHECKIN_BONUS

    def add_review(self, row):
        self._add(row, "reviewed_at", 0, REVIEW_POINTS, REVIEW_DAILY_CAP)

    def add_problem(self, row):
        self._add(row, "created_at", 1, RECORD_POINTS, RECORD_DAILY_CAP)


def points_by_user(members, review_rows, problem_rows):
    """Return every current member's score, with inclusive joining boundaries.

    Members expose id/timezone/joined_at. Reviews expose user_id/reviewed_at;
    problems expose user_id/created_at. Both dictionaries and sqlite rows work.
    Rows from former members and activity before joining are ignored.
    """
    accumulator = GroupPointsAccumulator({None: members})
    for row in review_rows:
        accumulator.add_review(row)
    for row in problem_rows:
        accumulator.add_problem(row)
    return accumulator.totals[None]


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
