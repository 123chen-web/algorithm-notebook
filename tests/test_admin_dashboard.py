"""全站管理看板的统计口径、UTC 边界和权限保护。"""

import pytest

import main
from db import connect
from test_app import client, register


NOW = "2026-09-28T00:30:00+00:00"
OLD = "2026-08-01T00:00:00+00:00"
SEVEN_DAYS_AGO = "2026-09-21T00:30:00+00:00"
BEFORE_SEVEN_DAYS = "2026-09-21T00:29:59+00:00"
FUTURE = "2026-09-28T00:30:01+00:00"


@pytest.fixture
def admin(client, monkeypatch):
    monkeypatch.setattr(main, "utc_now", lambda: NOW)
    monkeypatch.setenv("ADMIN_USERNAME", "alice")
    user = register(client)
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET created_at = ? WHERE id = ?", (OLD, user["id"])
        )
    return user["id"]


def insert_user(conn, username, created_at=OLD, is_trial=False):
    return conn.execute(
        "INSERT INTO users(username, password_hash, timezone, created_at, "
        "is_trial) VALUES (?, 'unused', 'Asia/Taipei', ?, ?)",
        (username, created_at, int(is_trial)),
    ).lastrowid


def insert_problem(conn, user_id, created_at=OLD, zone="算法"):
    return conn.execute(
        "INSERT INTO problems(user_id, title, language, code, thinking, "
        "created_at, zone) VALUES (?, '测试题目', 'Python', '', '', ?, ?)",
        (user_id, created_at, zone),
    ).lastrowid


def insert_mistake(conn, problem_id):
    return conn.execute(
        "INSERT INTO mistakes(problem_id, description, due_date) "
        "VALUES (?, '测试易错点', '2026-09-28')",
        (problem_id,),
    ).lastrowid


def insert_review(conn, mistake_id, reviewed_at):
    conn.execute(
        "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
        "VALUES (?, 4, ?, '2026-09-29')",
        (mistake_id, reviewed_at),
    )


def dashboard(client):
    response = client.get("/api/admin/dashboard")
    assert response.status_code == 200, response.text
    return response.json()


def test_non_admin_cannot_access_dashboard(client, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "some-other-admin")
    register(client)

    assert client.get("/api/admin/dashboard").status_code == 403


def test_dashboard_empty_statistics_are_zero(client, admin):
    result = dashboard(client)

    assert result["generated_at"] == NOW
    assert result["users"] == {
        "total": 1,
        "trial": 0,
        "registered": 1,
        "new_7_days": 0,
        "new_30_days": 0,
    }
    assert result["activity"] == {"active_users_7_days": 0}
    assert result["ai_usage"] == {"today": 0, "last_7_days": 0}
    assert result["content"] == {"problems": 0, "mistakes": 0, "posts": 0}
    assert result["pending_reports"] == 0
    assert result["subscriptions"] == {
        "active": 0,
        "paid_orders": 0,
        "paid_amount_cents": 0,
    }
    assert result["zones"] == [
        {"zone": zone, "mistake_count": 0}
        for zone in sorted(main.PROBLEM_ZONES)
    ]


def test_user_totals_and_registration_windows_use_utc(client, admin):
    with connect(write=True) as conn:
        insert_user(conn, "just-registered", NOW)
        insert_user(conn, "seven-day-boundary", SEVEN_DAYS_AGO, is_trial=True)
        insert_user(conn, "before-seven-days", BEFORE_SEVEN_DAYS)
        insert_user(conn, "thirty-day-boundary", "2026-08-29T00:30:00+00:00")
        insert_user(
            conn, "before-thirty-days", "2026-08-29T00:29:59+00:00", is_trial=True
        )
        insert_user(conn, "future-user", FUTURE)

    # 总人数包含旧账号与异常的未来时间记录；新增人数只包含窗口内记录。
    assert dashboard(client)["users"] == {
        "total": 7,
        "trial": 2,
        "registered": 5,
        "new_7_days": 2,
        "new_30_days": 4,
    }


def test_active_users_union_deduplicates_problems_and_reviews(client, admin):
    with connect(write=True) as conn:
        both = insert_user(conn, "both-actions")
        problem_only = insert_user(conn, "problem-only")
        review_only = insert_user(conn, "review-only")
        inactive = insert_user(conn, "inactive")
        future = insert_user(conn, "future-activity")

        both_problem = insert_problem(conn, both, NOW)
        insert_problem(conn, both, SEVEN_DAYS_AGO)
        both_mistake = insert_mistake(conn, both_problem)
        insert_review(conn, both_mistake, NOW)
        insert_review(conn, both_mistake, SEVEN_DAYS_AGO)

        insert_problem(conn, problem_only, SEVEN_DAYS_AGO)
        review_problem = insert_problem(conn, review_only, OLD)
        insert_review(conn, insert_mistake(conn, review_problem), SEVEN_DAYS_AGO)

        inactive_problem = insert_problem(conn, inactive, BEFORE_SEVEN_DAYS)
        inactive_mistake = insert_mistake(conn, inactive_problem)
        insert_review(conn, inactive_mistake, BEFORE_SEVEN_DAYS)
        # 以实际提交的评分为准，不把易错点上的状态时间当作评分记录。
        conn.execute(
            "UPDATE mistakes SET last_reviewed_at = ? WHERE id = ?",
            (NOW, inactive_mistake),
        )
        future_problem = insert_problem(conn, future, FUTURE)
        insert_review(conn, insert_mistake(conn, future_problem), FUTURE)

    # 管理员自己没有学习记录，三名其他用户贡献全站活跃人数。
    assert dashboard(client)["activity"] == {"active_users_7_days": 3}


def test_ai_usage_sums_attempts_for_utc_calendar_days(client, admin):
    with connect(write=True) as conn:
        other = insert_user(conn, "ai-user")
        conn.executemany(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, ?)",
            [
                (admin, "2026-09-28", 2),
                (other, "2026-09-28", 3),
                (other, "2026-09-27", 7),
                (other, "2026-09-22", 11),
                (other, "2026-09-21", 99),
                (other, "2026-09-29", 88),
            ],
        )

    # test_app.client 的 today_for 固定在 9 月 19 日，这里仍采用 UTC 的 28 日。
    assert dashboard(client)["ai_usage"] == {"today": 5, "last_7_days": 23}


def test_content_zone_ranking_and_pending_reports_are_sitewide(client, admin):
    with connect(write=True) as conn:
        other = insert_user(conn, "content-author")
        insert_problem(conn, admin)  # 没有易错点的题目仍计入题目总数。
        for zone, count in [("算法", 1), ("前端", 3), ("历史分区", 2), ("后端", 1)]:
            problem_id = insert_problem(conn, other, zone=zone)
            for _ in range(count):
                insert_mistake(conn, problem_id)

        post_ids = []
        for deleted_at in (None, None, NOW):
            post_ids.append(conn.execute(
                "INSERT INTO posts(user_id, title, body, created_at, deleted_at) "
                "VALUES (?, '帖子', '正文', ?, ?)",
                (other, OLD, deleted_at),
            ).lastrowid)
        comment_id = conn.execute(
            "INSERT INTO post_comments(post_id, user_id, body, created_at) "
            "VALUES (?, ?, '评论', ?)",
            (post_ids[0], other, OLD),
        ).lastrowid
        conn.executemany(
            "INSERT INTO reports(reporter_user_id, post_id, created_at, "
            "resolved_at) VALUES (?, ?, ?, ?)",
            [
                (admin, post_ids[0], OLD, None),
                (admin, post_ids[2], OLD, None),
                (admin, post_ids[1], OLD, NOW),
            ],
        )
        conn.execute(
            "INSERT INTO reports(reporter_user_id, comment_id, created_at) "
            "VALUES (?, ?, ?)",
            (admin, comment_id, OLD),
        )
        conn.executemany(
            "INSERT INTO avatar_reports(reporter_user_id, avatar_owner_id, "
            "created_at, resolved_at) VALUES (?, ?, ?, ?)",
            [
                (admin, other, OLD, None),
                (other, admin, OLD, None),
                (admin, other, OLD, NOW),
            ],
        )

    result = dashboard(client)
    assert result["content"] == {"problems": 5, "mistakes": 7, "posts": 2}
    assert result["pending_reports"] == 5
    assert result["zones"][:4] == [
        {"zone": "前端", "mistake_count": 3},
        {"zone": "历史分区", "mistake_count": 2},
        {"zone": "后端", "mistake_count": 1},
        {"zone": "算法", "mistake_count": 1},
    ]
    assert result["zones"][4:] == [
        {"zone": zone, "mistake_count": 0}
        for zone in sorted(set(main.PROBLEM_ZONES) - {"前端", "后端", "算法"})
    ]


def test_subscriptions_require_future_expiry_and_orders_require_paid_status(
    client, admin
):
    with connect(write=True) as conn:
        plan_id = conn.execute(
            "INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, "
            "created_at) VALUES ('月度套餐', 30, 10, 990, ?)",
            (OLD,),
        ).lastrowid
        subscribers = []
        for username, expiry, has_plan in [
            ("active", FUTURE, True),
            ("also-active", "2026-10-28T00:30:00+00:00", True),
            ("expires-now", NOW, True),
            ("expired", "2026-09-28T00:29:59+00:00", True),
            ("missing-expiry", None, True),
            ("missing-plan", FUTURE, False),
        ]:
            user_id = insert_user(conn, username)
            subscribers.append(user_id)
            conn.execute(
                "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
                (plan_id if has_plan else None, expiry, user_id),
            )
        for index, (status, amount) in enumerate([
            ("paid", 990),
            ("paid", 2500),
            ("pending", 5000),
            ("failed", 6000),
            ("closed", 7000),
            ("refunded", 8000),
        ]):
            conn.execute(
                "INSERT INTO orders(id, user_id, plan_id, amount_cents, channel, "
                "status, created_at, paid_at) VALUES (?, ?, ?, ?, 'alipay', ?, ?, ?)",
                (
                    f"order-{index}",
                    subscribers[index],
                    plan_id,
                    amount,
                    status,
                    OLD,
                    NOW if status in ("paid", "refunded") else None,
                ),
            )

    result = dashboard(client)
    assert result["subscriptions"] == {
        "active": 2,
        "paid_orders": 2,
        "paid_amount_cents": 3490,
    }
    # 正式账号指非体验账号，不是付费订阅人数。
    assert result["users"]["registered"] == 7
