import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Request

import ai
import main
from db import connect, init_db
from test_app import client, register


ENDPOINT = "/api/insights/weakness-analysis"
DAY = "2026-09-19"


@pytest.fixture(autouse=True)
def block_real_analysis(monkeypatch):
    def unexpected_call(reference):
        pytest.fail("测试必须显式 mock 薄弱点分析，不能调用真实 AI")

    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)


def seed_mistakes(user_id, count=5):
    """每个易错点来自不同题目，保留可验证的时间顺序。"""
    ids = []
    with connect(write=True) as conn:
        for index in range(count):
            created_at = (
                datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=index)
            ).isoformat()
            problem_id = conn.execute(
                """
                INSERT INTO problems(
                    user_id, title, zone, language, code, thinking, created_at
                ) VALUES (?, ?, '算法', 'Python', ?, ?, ?)
                """,
                (
                    user_id, f"二分边界练习 {index + 1}",
                    "while left < right: pass", "没有明确区间的含义", created_at,
                ),
            ).lastrowid
            ids.append(conn.execute(
                """
                INSERT INTO mistakes(problem_id, description, due_date)
                VALUES (?, ?, ?)
                """,
                (problem_id, f"第 {index + 1} 次遗漏左右端点相等的情况", DAY),
            ).lastrowid)
    return ids


def analysis_result(reference, summary="闭区间与终止条件的对应关系尚未稳定掌握。"):
    return {
        "summary": summary,
        "patterns": [{
            "title": "区间不变量与循环边界脱节",
            "explanation": "不同题目反复遗漏单元素区间，可能没有先明确区间语义。",
            "action": "先写区间不变量，再手算空数组和单元素数组的退出过程。",
            "confidence": "较明确",
            "evidence": [
                {"mistake_id": item["mistake_id"], "observation": "再次遗漏端点相等的边界。"}
                for item in reference["mistakes"][:2]
            ],
        }],
    }


def attempts(user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, DAY),
        ).fetchone()
    return row["attempts"] if row else 0


@pytest.mark.parametrize("count", [0, 4])
def test_insufficient_history_never_checks_ai_or_spends_quota(client, monkeypatch, count):
    user_id = register(client)["id"]
    seed_mistakes(user_id, count)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 2)",
            (user_id, DAY),
        )

    def unexpected_quota(*args):
        pytest.fail("样本不足时应先返回友好提示")

    monkeypatch.setattr(main, "ai_quota", unexpected_quota)
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "insufficient_data"
    assert payload["minimum_mistakes"] == 5
    assert payload["mistake_count"] == count
    assert payload["message"]
    assert payload["insight"] is None
    assert attempts(user_id) == 2
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM weakness_insights").fetchone()[0] == 0


def test_analysis_at_threshold_uses_shared_quota_and_saves_grounded_result(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_ids = seed_mistakes(user_id)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 1)",
            (user_id, DAY),
        )
    calls = []

    def analyze(reference):
        calls.append(reference)
        return analysis_result(reference)

    monkeypatch.setattr(ai, "analyze_weaknesses", analyze)
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["mistake_count"] == 5
    assert len(calls) == 1
    assert calls[0]["total_mistakes"] == 5
    assert {item["mistake_id"] for item in calls[0]["mistakes"]} == set(mistake_ids)
    content = payload["insight"]["content"]
    assert content["summary"] == analysis_result(calls[0])["summary"]
    assert content["sample"]["mistake_count"] == 5
    assert content["sample"]["problem_count"] == 5
    assert content["sample"]["review_count"] == 0
    assert datetime.fromisoformat(payload["insight"]["created_at"]).tzinfo is not None
    with connect() as conn:
        stored = conn.execute(
            "SELECT * FROM weakness_insights WHERE user_id = ?", (user_id,),
        ).fetchone()
        assert json.loads(stored["content"]) == content
        for evidence in content["patterns"][0]["evidence"]:
            original = conn.execute(
                """SELECT p.id AS problem_id, p.title, p.zone
                   FROM mistakes m JOIN problems p ON p.id = m.problem_id
                   WHERE m.id = ?""",
                (evidence["mistake_id"],),
            ).fetchone()
            assert {key: evidence[key] for key in original.keys()} == dict(original)
    assert attempts(user_id) == 2
    assert client.post(f"/api/mistakes/{mistake_ids[0]}/variants").status_code == 429
    assert client.post(ENDPOINT).status_code == 429
    assert len(calls) == 1
    assert attempts(user_id) == 2


def test_query_is_free_and_regeneration_replaces_the_single_saved_result(client, monkeypatch):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    empty = client.get(ENDPOINT)
    assert empty.status_code == 200
    assert empty.json()["status"] == "not_analyzed"
    assert empty.json()["insight"] is None
    assert attempts(user_id) == 0

    monkeypatch.setattr(ai, "analyze_weaknesses", analysis_result)
    first = client.post(ENDPOINT).json()["insight"]
    monkeypatch.setattr(
        ai, "analyze_weaknesses",
        lambda reference: analysis_result(reference, summary="最新分析：应先建立区间不变量。"),
    )
    updated = client.post(ENDPOINT)
    assert updated.status_code == 200
    latest = updated.json()["insight"]
    assert first["content"]["summary"] != latest["content"]["summary"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def unexpected_call(*args):
        pytest.fail("查询历史分析不应调用 AI 或配额计算")

    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)
    monkeypatch.setattr(main, "ai_quota", unexpected_call)
    fetched = client.get(ENDPOINT)
    assert fetched.status_code == 200
    assert fetched.json()["insight"] == latest
    assert attempts(user_id) == 2
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM weakness_insights WHERE user_id = ?", (user_id,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("has_previous_result", [False, True])
def test_ai_failure_refunds_quota_and_preserves_saved_result(
    client, monkeypatch, has_previous_result,
):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    previous = None
    if has_previous_result:
        monkeypatch.setattr(ai, "analyze_weaknesses", analysis_result)
        previous = client.post(ENDPOINT).json()["insight"]

    def failed_analysis(reference):
        raise HTTPException(502, "模拟 AI 服务失败")

    monkeypatch.setattr(ai, "analyze_weaknesses", failed_analysis)
    failed = client.post(ENDPOINT)
    assert failed.status_code == 502
    assert attempts(user_id) == int(has_previous_result)  # 失败这一次已退还
    assert client.get(ENDPOINT).json()["insight"] == previous


@pytest.mark.parametrize("blocked_by,expected_status", [("missing_key", 503), ("zero_quota", 429)])
def test_unavailable_ai_does_not_reserve_an_attempt(client, monkeypatch, blocked_by, expected_status):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    if blocked_by == "missing_key":
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
    else:
        monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "0")
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET is_trial = 1 WHERE id = ?", (user_id,))
    assert client.post(ENDPOINT).status_code == expected_status
    assert attempts(user_id) == 0


def test_analysis_history_counts_and_results_are_isolated_between_users(client, monkeypatch):
    alice = register(client)["id"]
    alice_ids = seed_mistakes(alice)
    captured = []

    def analyze(reference):
        captured.append(reference)
        return analysis_result(reference, summary=f"分析 {reference['mistakes'][0]['mistake_id']}")

    monkeypatch.setattr(ai, "analyze_weaknesses", analyze)
    alice_saved = client.post(ENDPOINT).json()["insight"]
    assert client.post("/api/auth/logout").status_code == 200
    bob = register(client, "bob")["id"]
    bob_ids = seed_mistakes(bob, 4)
    assert client.get(ENDPOINT).json()["insight"] is None
    insufficient = client.post(ENDPOINT).json()
    assert insufficient["status"] == "insufficient_data"
    assert insufficient["mistake_count"] == 4
    assert len(captured) == 1
    bob_ids += seed_mistakes(bob, 1)
    bob_saved = client.post(ENDPOINT).json()["insight"]
    assert {item["mistake_id"] for item in captured[0]["mistakes"]} == set(alice_ids)
    assert {item["mistake_id"] for item in captured[1]["mistakes"]} == set(bob_ids)
    assert bob_saved != alice_saved
    assert client.get(ENDPOINT).json()["insight"] == bob_saved
    assert attempts(alice) == attempts(bob) == 1
    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login", json={"username": "alice", "password": "a-test-password-123"},
    ).status_code == 200
    assert client.get(ENDPOINT).json()["insight"] == alice_saved


def test_sample_is_bounded_and_retains_repeat_failure_history(client, monkeypatch):
    user_id = register(client)["id"]
    ids = seed_mistakes(user_id, 45)
    newest = ids[-1]
    qualities = [0, 5, 2, 4, 1, 5, 0, 5, 2, 4, 1, 5]
    reviews = [
        {"quality": quality, "reviewed_at": f"2026-08-{index + 1:02d}T00:00:00+00:00"}
        for index, quality in enumerate(qualities)
    ]
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET description = ?", ("边界" * 1000,))
        conn.execute("UPDATE mistakes SET description = '' WHERE id = ?", (newest,))
        conn.execute(
            "UPDATE problems SET title = ?, thinking = ?, code = ? WHERE user_id = ?",
            ("题" * 300, "思" * 2000, "码" * 10000, user_id),
        )
        for review in reversed(reviews):
            conn.execute(
                """INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date)
                   VALUES (?, ?, ?, ?)""",
                (newest, review["quality"], review["reviewed_at"], DAY),
            )
    captured = []

    def analyze(reference):
        captured.append(reference)
        return analysis_result(reference)

    monkeypatch.setattr(ai, "analyze_weaknesses", analyze)
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    reference = captured[0]
    assert reference["total_mistakes"] == 45
    assert [item["mistake_id"] for item in reference["mistakes"]] == list(reversed(ids[-40:]))
    item = reference["mistakes"][0]
    assert item["review_count"] == 12
    assert item["failed_review_count"] == 6
    assert item["recent_reviews"] == reviews[-8:]
    assert len(item["thinking"]) <= 600
    assert len(item["work_excerpt"]) <= 800
    assert item["thinking"] and item["work_excerpt"]
    for item in reference["mistakes"]:
        assert len(item["title"]) <= 200
        assert len(item["description"]) <= 1000
        assert "code" not in item
    sample = response.json()["insight"]["content"]["sample"]
    assert sample["mistake_count"] == sample["problem_count"] == 40
    assert sample["period_start"] == reference["mistakes"][-1]["created_at"]
    assert sample["period_end"] == reference["mistakes"][0]["created_at"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_insights_require_authentication(client, method):
    assert getattr(client, method)(ENDPOINT).status_code == 401


def test_quota_is_reserved_under_write_lock_but_ai_runs_after_commit(client, monkeypatch):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    quota_read = main.ai_quota
    checks = []

    def read_while_locked(conn, queried_user_id, day):
        with connect() as competitor:
            competitor.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competitor.execute("BEGIN IMMEDIATE")
            quota = quota_read(conn, queried_user_id, day)
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                competitor.execute("BEGIN IMMEDIATE")
        checks.append("locked")
        return quota

    def analyze_after_commit(reference):
        with connect() as competitor:
            competitor.execute("PRAGMA busy_timeout = 0")
            competitor.execute("BEGIN IMMEDIATE")
            assert competitor.execute(
                "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
                (user_id, DAY),
            ).fetchone()["attempts"] == 1
        checks.append("committed")
        return analysis_result(reference)

    monkeypatch.setattr(main, "ai_quota", read_while_locked)
    monkeypatch.setattr(ai, "analyze_weaknesses", analyze_after_commit)
    assert client.post(ENDPOINT).status_code == 200
    assert checks == ["locked", "committed"]


def test_analysis_reloads_subscription_after_authentication(client, monkeypatch):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    with connect(write=True) as conn:
        plan_id = conn.execute(
            """INSERT INTO plans(name, period_days, ai_daily_limit, price_cents, created_at)
               VALUES ('新套餐', 30, 3, 990, ?)""", (main.utc_now(),),
        ).lastrowid
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 2)", (user_id, DAY),
        )

    def authenticate_then_upgrade(request: Request):
        user = main.current_user(request)
        with connect(write=True) as conn:
            conn.execute(
                "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
                (plan_id, "2099-01-01T00:00:00+00:00", user_id),
            )
        return user

    monkeypatch.setattr(ai, "analyze_weaknesses", analysis_result)
    main.app.dependency_overrides[main.current_user] = authenticate_then_upgrade
    try:
        assert client.post(ENDPOINT).status_code == 200
    finally:
        main.app.dependency_overrides.pop(main.current_user)
    assert attempts(user_id) == 3


def test_insight_table_migrates_idempotently_and_cascades_on_user_delete(client):
    user_id = register(client)["id"]
    # 模拟升级前已有用户、尚未创建分析表的数据库。
    with connect(write=True) as conn:
        conn.execute("DROP TABLE weakness_insights")
    init_db()
    init_db()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO weakness_insights(user_id, content, created_at) VALUES (?, ?, ?)",
            (user_id, '{"summary":"保留已有结果"}', main.utc_now()),
        )
    init_db()
    with connect(write=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM weakness_insights").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO weakness_insights(user_id, content, created_at) VALUES (?, '{}', ?)",
                (user_id, main.utc_now()),
            )
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        assert conn.execute("SELECT COUNT(*) FROM weakness_insights").fetchone()[0] == 0
