"""榜单测试共用的造数据工具：全部用代码直接写库，不依赖任何文件。"""
from datetime import datetime, timedelta, timezone

from db import connect

# “现在”固定为北京时间 2026-10-04 10:00；于是“昨日”是北京时间 2026-10-03，
# 对应 UTC 区间 [2026-10-02T16:00:00, 2026-10-03T16:00:00)。
NOW = datetime(2026, 10, 4, 2, 0, tzinfo=timezone.utc)
YESTERDAY_START = "2026-10-02T16:00:00+00:00"
YESTERDAY_END = "2026-10-03T16:00:00+00:00"
NOON_YESTERDAY = "2026-10-03T04:00:00+00:00"  # 北京时间昨天 12:00


def add_user(username, *, trial=False, banned=False, deleted=False, opt_out=False, tz="Asia/Shanghai"):
    with connect(write=True) as conn:
        return conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at, is_trial, "
            "is_banned, deleted_at, public_rank_opt_out) VALUES (?, 'unused', ?, "
            "'2026-01-01T00:00:00+00:00', ?, ?, ?, ?)",
            (username, tz, int(trial), int(banned),
             "2026-09-01T00:00:00+00:00" if deleted else None, int(opt_out)),
        ).lastrowid


def add_problem(user_id, thinking="", created_at="2026-01-02T00:00:00+00:00", title="我的标题"):
    with connect(write=True) as conn:
        return conn.execute(
            "INSERT INTO problems(user_id, title, language, code, thinking, created_at, zone) "
            "VALUES (?, ?, 'Python', '', ?, ?, '算法')",
            (user_id, title, thinking, created_at),
        ).lastrowid


def add_mistake(problem_id):
    with connect(write=True) as conn:
        return conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, '易错点', '2026-10-04')",
            (problem_id,),
        ).lastrowid


def add_review(mistake_id, reviewed_at, quality=4):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, ?, ?, '2099-01-01')",
            (mistake_id, quality, reviewed_at),
        )


def user_with_mistake(username, **flags):
    user_id = add_user(username, **flags)
    return user_id, add_mistake(add_problem(user_id))


def review_times(mistake_id, count, at=NOON_YESTERDAY):
    base = datetime.fromisoformat(at)
    for index in range(count):
        add_review(mistake_id, (base + timedelta(minutes=index)).isoformat(timespec="seconds"))


def user_with_reviews(username, count, **flags):
    """count 次复习，分散在 count 条不同的错题上（避免被“同一错题封顶”影响）。"""
    user_id = add_user(username, **flags)
    problem_id = add_problem(user_id)
    for index in range(count):
        add_review(add_mistake(problem_id), NOON_YESTERDAY)
    return user_id
