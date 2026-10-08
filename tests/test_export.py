"""学习笔记本导出：完整保留自己的学习内容，不携带账号或交易数据。"""

from datetime import date, datetime, timedelta, timezone
from email.message import Message

import pytest

import ai
import main
from db import connect
from scheduler import today_in_timezone
from test_app import client, register


ENDPOINT = "/api/export"
TODAY = date(2026, 9, 19)
PROBLEM_KEYS = {
    "id", "title", "zone", "language", "code", "thinking", "created_at", "mistakes",
}
MISTAKE_KEYS = {
    "id", "description", "repetitions", "interval_days", "ease_factor",
    "due_date", "last_reviewed_at", "reviews", "variants", "tags",
    "pending_reason",
}
REVIEW_KEYS = {"quality", "reviewed_at", "next_due_date"}
VARIANT_KEYS = {
    "description", "model", "created_at", "result", "answer_code", "answer",
    "expected_answer", "notes", "result_updated_at",
}


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("导出只读取已有学习内容，不能调用 AI")

    monkeypatch.setattr(ai, "generate", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)
    monkeypatch.setattr(ai, "recognize_photo", unexpected_call)


def seed_notebook(user_id, marker):
    """显式写入不同分支、空分支和历史字段，返回预期的完整学习内容。"""
    problems = []
    with connect(write=True) as conn:
        for number, mistake_count in enumerate((2, 1, 0), start=1):
            problem = {
                "title": f"{marker}题目{number}",
                "zone": "算法" if number == 1 else "线性代数",
                "language": "Python",
                "code": f"# {marker}原始代码{number}\nprint('你好')\n",
                "thinking": f"{marker}解题思路{number}\n保留换行和中文。",
                "created_at": f"2026-08-{number:02d}T08:09:10+00:00",
            }
            problem["id"] = conn.execute(
                "INSERT INTO problems(user_id, title, zone, language, code, thinking, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, *problem.values()),
            ).lastrowid
            problem["mistakes"] = []
            for mistake_number in range(1, mistake_count + 1):
                mistake = {
                    "description": f"{marker}错因{number}-{mistake_number}",
                    "repetitions": mistake_number + 1,
                    "interval_days": mistake_number * 6,
                    "ease_factor": 1.7 + mistake_number / 10,
                    "due_date": f"2026-10-{mistake_number:02d}",
                    "last_reviewed_at": (
                        "2026-09-18T12:00:00+00:00" if number == 1 else None
                    ),
                }
                mistake["id"] = conn.execute(
                    "INSERT INTO mistakes(problem_id, description, repetitions, "
                    "interval_days, ease_factor, due_date, last_reviewed_at, version) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 7)",
                    (problem["id"], *mistake.values()),
                ).lastrowid
                mistake["pending_reason"] = False
                mistake["reviews"] = []
                mistake["variants"] = []
                mistake["tags"] = []
                if number == 1 and mistake_number == 1:
                    for tag in ("边界", "粗心"):
                        conn.execute(
                            "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) "
                            "VALUES (?, ?, ?, '2026-09-01T00:00:00+00:00')",
                            (mistake["id"], user_id, tag),
                        )
                        mistake["tags"].append(tag)
                # 第二道题没有复习/练习，第三道题连易错点也没有。
                if number == 1:
                    reviews = []
                    variants = []
                    # 先插入较晚记录；较早两条同一时刻，按插入 ID 稳定排序。
                    for index, day_offset in enumerate((2, 0, 0)):
                        day = TODAY - timedelta(days=mistake_number * 5 - day_offset)
                        timestamp = day.isoformat() + "T12:34:56+00:00"
                        review = {
                            "quality": mistake_number + index,
                            "reviewed_at": timestamp,
                            "next_due_date": (day + timedelta(days=index + 3)).isoformat(),
                        }
                        conn.execute(
                            "INSERT INTO reviews(mistake_id, quality, reviewed_at, "
                            "next_due_date) VALUES (?, ?, ?, ?)",
                            (mistake["id"], *review.values()),
                        )
                        reviews.append(review)
                        variant = {
                            "description": f"{marker}练习{mistake_number}-{index}",
                            "model": f"{marker}-model-{index}",
                            "created_at": timestamp,
                            "result": ("solved", "partial", "unattempted")[index],
                            "answer_code": f"# {marker}练习代码{index}\nreturn 42\n",
                            "answer": f"{marker}我的答案{index}",
                            "expected_answer": f"{marker}完整标准答案{index}",
                            "notes": f"{marker}历史笔记{index}",
                            "result_updated_at": timestamp if index != 2 else None,
                        }
                        conn.execute(
                            "INSERT INTO variants(mistake_id, description, model, "
                            "created_at, result, answer_code, answer, expected_answer, "
                            "notes, result_updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (mistake["id"], *variant.values()),
                        )
                        variants.append(variant)
                    mistake["reviews"] = [reviews[index] for index in (1, 2, 0)]
                    mistake["variants"] = [variants[index] for index in (1, 2, 0)]
                problem["mistakes"].append(mistake)
            problems.append(problem)
    return problems


def assert_export_keys(payload):
    assert set(payload) == {"exported_at", "username", "problems", "notes"}
    for note in payload["notes"]:
        assert set(note) == {"id", "title", "content", "tags", "problem_id", "problem_title", "pinned", "created_at", "updated_at", "deleted_at"}
    for problem in payload["problems"]:
        assert set(problem) == PROBLEM_KEYS
        for mistake in problem["mistakes"]:
            assert set(mistake) == MISTAKE_KEYS
            for review in mistake["reviews"]:
                assert set(review) == REVIEW_KEYS
            for variant in mistake["variants"]:
                assert set(variant) == VARIANT_KEYS


def export(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    payload = response.json()
    assert_export_keys(payload)
    timestamp = datetime.fromisoformat(payload["exported_at"])
    assert timestamp.utcoffset() == timedelta(0)
    return response


def all_keys(value):
    if isinstance(value, dict):
        return set(value).union(*(all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(all_keys(item) for item in value))
    return set()


def test_export_requires_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_export_preserves_all_fields_nesting_and_order_for_each_owner(client):
    expected = {}
    for username in ("alice", "bob"):
        user_id = register(client, username)["id"]
        expected[username] = seed_notebook(user_id, f"{username}独有")

    for username, other in (("alice", "bob"), ("bob", "alice")):
        logged_in = client.post(
            "/api/auth/login",
            json={"username": username, "password": "a-test-password-123"},
        )
        assert logged_in.status_code == 200
        response = export(client)
        payload = response.json()
        assert payload["username"] == username
        assert payload["problems"] == expected[username]
        assert f"{other}独有" not in response.text
        assert "历史笔记" in response.content.decode("utf-8")
        # 未作答练习的标准答案也完整保留，不能沿用展示接口的 *** 脱敏。
        assert "***" not in response.text


def test_export_excludes_account_billing_usage_and_forum_data(client):
    private_email = "private-export@example.com"
    user_id = register(client, email=private_email)["id"]
    expected = seed_notebook(user_id, "学习内容")
    private_values = [
        private_email, "Asia/Shanghai", client.cookies.get("session"),
        "private-plan-name", "private-order-id", "private-payment-reference",
        "private-forum-title", "private-forum-body", "private-forum-comment",
        "private-weakness-analysis", "2099-12-31T23:59:59+00:00",
    ]
    with connect(write=True) as conn:
        private_values.append(conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,),
        ).fetchone()[0])
        private_values.append(conn.execute(
            "SELECT token_hash FROM sessions WHERE user_id = ?", (user_id,),
        ).fetchone()[0])
        plan_id = conn.execute(
            "INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, created_at) "
            "VALUES ('private-plan-name', 30, 12, 990, '2026-01-01T00:00:00+00:00')",
        ).lastrowid
        conn.execute(
            "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
            (plan_id, "2099-12-31T23:59:59+00:00", user_id),
        )
        conn.execute(
            "INSERT INTO orders(id, user_id, plan_id, amount_cents, channel, status, "
            "provider_trade_no, created_at, paid_at) VALUES "
            "('private-order-id', ?, ?, 990, 'alipay', 'paid', "
            "'private-payment-reference', '2026-01-01T00:00:00+00:00', "
            "'2026-01-01T00:01:00+00:00')",
            (user_id, plan_id),
        )
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 73)",
            (user_id, TODAY.isoformat()),
        )
        post_id = conn.execute(
            "INSERT INTO posts(user_id, title, body, created_at) VALUES "
            "(?, 'private-forum-title', 'private-forum-body', '2026-01-01T00:00:00+00:00')",
            (user_id,),
        ).lastrowid
        conn.execute(
            "INSERT INTO post_comments(post_id, user_id, body, created_at) VALUES "
            "(?, ?, 'private-forum-comment', '2026-01-01T00:00:00+00:00')",
            (post_id, user_id),
        )
        conn.execute(
            "INSERT INTO weakness_insights(user_id, content, created_at) VALUES "
            "(?, 'private-weakness-analysis', '2026-01-01T00:00:00+00:00')",
            (user_id,),
        )

    account_response = client.get("/api/me")
    assert account_response.status_code == 200
    account = account_response.json()
    assert account["email"] == private_email
    assert account["plan_name"] == "private-plan-name"
    assert account["ai_daily_used"] == 73
    response = export(client)
    payload = response.json()
    assert payload["problems"] == expected
    excluded_keys = (set(account) - {"id", "username"}) | {
        "password_hash", "token_hash", "session", "sessions", "expires_at",
        "user_id", "problem_id", "mistake_id", "version", "last_reminder_sent",
        "orders", "plans", "payments", "amount_cents", "price_cents", "channel",
        "provider_trade_no", "paid_at", "closed_at", "refunded_at", "status",
        "ai_usage", "attempts", "posts", "post_comments", "weakness_insights",
    }
    assert all_keys(payload).isdisjoint(excluded_keys)
    for private_value in private_values:
        assert private_value not in response.text


def test_new_account_exports_empty_notebook(client):
    register(client)
    payload = export(client).json()
    assert payload["username"] == "alice"
    assert payload["problems"] == []


@pytest.mark.parametrize("is_trial", [False, True], ids=["no-plan", "trial"])
def test_export_is_read_only_without_ai_quota_or_subscription(
    client, monkeypatch, is_trial,
):
    if is_trial:
        response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
        assert response.status_code == 201
        user = response.json()
    else:
        user = register(client)
    expected = seed_notebook(user["id"], "保留笔记")
    with connect(write=True) as conn:
        assert conn.execute(
            "SELECT plan_id FROM users WHERE id = ?", (user["id"],),
        ).fetchone()[0] is None
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 100)",
            (user["id"], TODAY.isoformat()),
        )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "0")

    def unexpected_quota(*args, **kwargs):
        pytest.fail("导出不检查 AI 配额或套餐")

    monkeypatch.setattr(main, "ai_quota", unexpected_quota)
    with connect() as conn:
        before = list(conn.iterdump())
    for _ in range(2):
        payload = export(client).json()
        assert payload["username"] == user["username"]
        assert payload["problems"] == expected
    with connect() as conn:
        assert list(conn.iterdump()) == before


@pytest.mark.parametrize(
    "timezone_name,local_day",
    [("Asia/Taipei", "2026-09-20"), ("America/Los_Angeles", "2026-09-19")],
)
def test_download_uses_cookie_utf8_filename_local_date_and_utc_export_time(
    client, monkeypatch, timezone_name, local_day,
):
    user_id = register(client)["id"]
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET username = ?, timezone = ? WHERE id = ?",
            ("小明", timezone_name, user_id),
        )
    now = datetime(2026, 9, 19, 16, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "utc_now", lambda: now.isoformat())
    date_calls = []

    def frozen_today(user):
        date_calls.append((user["id"], user["timezone"]))
        return today_in_timezone(user["timezone"], now)

    monkeypatch.setattr(main, "today_for", frozen_today)
    # 普通 <a> 导航只有会话 cookie，不会附加 JS 使用的 CSRF 请求头。
    del client.headers["X-CSRF-Protection"]
    response = export(client)
    assert date_calls == [(user_id, timezone_name)]
    assert response.json() == {
        "exported_at": now.isoformat(), "username": "小明", "problems": [], "notes": [],
    }
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=UTF-8''")
    assert disposition.isascii()
    assert "%" in disposition
    header = Message()
    header["Content-Disposition"] = disposition
    assert header.get_content_disposition() == "attachment"
    assert header.get_filename() == f"欧叶OY导出_小明_{local_day}.json"


def test_export_is_rate_limited_per_user(client, monkeypatch):
    monkeypatch.setattr(main, "EXPORT_LIMIT", 2)
    register(client)
    assert client.get(ENDPOINT).status_code == 200
    assert client.get(ENDPOINT).status_code == 200
    limited = client.get(ENDPOINT)
    assert limited.status_code == 429
    assert "导出过于频繁" in limited.json()["detail"]
    # 另一个用户有自己的额度。
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.get(ENDPOINT).status_code == 200


def test_export_over_record_cap_is_rejected_not_truncated(client, monkeypatch):
    from test_app import new_problem

    register(client)
    new_problem(client)
    new_problem(client)
    monkeypatch.setattr(main, "EXPORT_MAX_RECORDS", 1)
    response = client.get(ENDPOINT)
    assert response.status_code == 413
    assert "记录超过 1 条" in response.json()["detail"]
    monkeypatch.setattr(main, "EXPORT_MAX_RECORDS", 2)
    assert len(client.get(ENDPOINT).json()["problems"]) == 2
