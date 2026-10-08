"""F4 费曼模式（POST /api/mistakes/{id}/explain）的后端测试。

全部用假 AI 客户端（monkeypatch main.OpenAI），绝不真实调用。
覆盖：成功契约、他人错题 404、空讲解 422、每题每天限 3 次 429、
AI 额度 429、ai_calls 记账、explanations 落库、score>=80 视为讲清楚了、
AI 回复不合法 502 且退回额度。
"""
from datetime import date
import json

import pytest
from fastapi.testclient import TestClient

import main
from db import connect
from test_ai import FakeResponse, fake_openai_factory

# main.py 的 generated 挂载区不允许手改；测试里单独挂载本分支的新路由。
from routers.explain import router as explain_router

main.app.include_router(explain_router)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "10")
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 9, 19))
    # explanations.created_at 写真实 UTC；把“现在”定在假日期的白天，
    # 保证落库行落在被测的自然日区间内。
    monkeypatch.setattr(main, "utc_now", lambda: "2026-09-19T04:00:00+00:00")
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def register(client, username="alice"):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-test-password-123",
            "accept_terms": True,
            "invite_code": "test-invite",
            "email": f"{username}@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 201
    return response.json()


def new_mistake(client):
    response = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "zone": "算法",
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "维护闭区间，但对结束条件理解不清楚。",
            "mistakes": ["循环结束条件漏掉 left == right。"],
        },
    )
    assert response.status_code == 201
    return response.json()["mistake_ids"][0]


GOOD_SCORE = {
    "score": 85,
    "missing_points": ["漏掉了 left == right 时的收敛情况。"],
    "follow_up": "当区间里只剩一个元素时，循环会怎么走？",
}


class QueueCompletions:
    """按顺序抛出/返回预设结果，并记录每次请求的 messages。"""

    def __init__(self, *items):
        self.items = list(items)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fake_ai(monkeypatch, *items):
    provider = QueueCompletions(*items)
    monkeypatch.setattr(main, "OpenAI", fake_openai_factory(provider))
    return provider


def explain(client, mistake_id, explanation):
    return client.post(
        f"/api/mistakes/{mistake_id}/explain",
        json={"explanation": explanation},
    )


def score_json(result):
    return FakeResponse(json.dumps(result, ensure_ascii=False), "stop")


def attempts(user_id=1):
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["attempts"] if row else 0


def ai_calls(user_id=1):
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT feature, ok, error FROM ai_calls WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        ]


def explanations(user_id=1):
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT user_id, mistake_id, explanation, score, missing_points "
                "FROM explanations WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        ]


# ---------------- 成功路径与契约 ----------------

def test_explain_success_writes_record_and_tracks(client, monkeypatch):
    provider = fake_ai(monkeypatch, score_json(GOOD_SCORE))
    register(client)
    mistake_id = new_mistake(client)

    response = explain(client, mistake_id, "我认为关键是维护闭区间，收敛时处理边界。")

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 85
    assert body["missing_points"] == ["漏掉了 left == right 时的收敛情况。"]
    assert body["follow_up"] == "当区间里只剩一个元素时，循环会怎么走？"
    assert body["mastered"] is True
    assert body["explanation_id"] > 0
    assert body["ai_remaining"] == 9

    rows = explanations()
    assert len(rows) == 1
    assert rows[0]["mistake_id"] == mistake_id
    assert rows[0]["score"] == 85
    assert json.loads(rows[0]["missing_points"]) == ["漏掉了 left == right 时的收敛情况。"]
    assert "维护闭区间" in rows[0]["explanation"]

    assert attempts() == 1
    calls = ai_calls()
    assert len(calls) == 1
    assert calls[0]["feature"] == "explain"
    assert calls[0]["ok"] == 1
    assert calls[0]["error"] == ""

    # 发给 AI 的上下文包含题目资料与用户讲解。
    sent = provider.requests[0]["messages"]
    assert sent[0]["role"] == "system"
    user_text = " ".join(m["content"] for m in sent if m["role"] == "user")
    assert "二分查找" in user_text
    assert "维护闭区间" in user_text


def test_explain_score_below_80_not_mastered(client, monkeypatch):
    result = dict(GOOD_SCORE, score=60)
    fake_ai(monkeypatch, score_json(result))
    register(client)
    mistake_id = new_mistake(client)

    response = explain(client, mistake_id, "大概是二分查找写错了。")

    assert response.status_code == 200
    body = response.json()
    assert body["score"] == 60
    assert body["mastered"] is False
    assert explanations()[0]["score"] == 60


def test_explain_rejects_other_users_mistake(client, monkeypatch):
    fake_ai(monkeypatch, score_json(GOOD_SCORE))
    register(client, "alice")
    mistake_id = new_mistake(client)
    register(client, "bob")

    response = explain(client, mistake_id, "讲解。")

    assert response.status_code == 404
    assert attempts(user_id=2) == 0
    assert explanations(user_id=2) == []


def test_explain_empty_text_is_422_and_costs_nothing(client, monkeypatch):
    provider = fake_ai(monkeypatch, score_json(GOOD_SCORE))
    register(client)
    mistake_id = new_mistake(client)

    assert explain(client, mistake_id, "   ").status_code == 422
    assert explain(client, mistake_id, "").status_code == 422

    assert provider.requests == []
    assert attempts() == 0
    assert explanations() == []


# ---------------- 限流与额度 ----------------

def test_explain_daily_per_mistake_limit_429(client, monkeypatch):
    provider = fake_ai(monkeypatch, *[score_json(GOOD_SCORE) for _ in range(3)])
    register(client)
    mistake_id = new_mistake(client)

    for _ in range(3):
        assert explain(client, mistake_id, "讲解。").status_code == 200
    response = explain(client, mistake_id, "第四次讲解。")

    assert response.status_code == 429
    # 第 4 次没走到 AI，不额外扣额度。
    assert attempts() == 3
    assert len(provider.requests) == 3
    assert len(explanations()) == 3


def test_explain_daily_limit_is_per_mistake(client, monkeypatch):
    fake_ai(monkeypatch, *[score_json(GOOD_SCORE) for _ in range(6)])
    register(client)
    first = new_mistake(client)
    second = new_mistake(client)

    for _ in range(3):
        assert explain(client, first, "讲解。").status_code == 200
    assert explain(client, second, "换一题。").status_code == 200


def test_explain_ai_quota_exhausted_429(client, monkeypatch):
    # 日额度 AI_DAILY_LIMIT=10（或体验账号的更低额度）：跨多题打满后不再请求 AI。
    # 每题每天限 3 次评分，所以用不同错题轮番请求。
    provider = fake_ai(monkeypatch, *[score_json(GOOD_SCORE) for _ in range(12)])
    register(client)
    mistake_ids = [new_mistake(client) for _ in range(4)]

    exhausted = False
    calls = 0
    for mistake_id in mistake_ids:
        for _ in range(3):
            response = explain(client, mistake_id, f"讲解 {calls}。")
            calls += 1
            if response.status_code == 429:
                exhausted = True
                break
            assert response.status_code == 200
        if exhausted:
            break
    assert exhausted, "AI 日额度打满后应返回 429"
    # 429 那次没有走到 AI，也不额外扣额度。
    used = attempts()
    assert used >= 1
    assert len(provider.requests) == used
    assert len(explanations()) == used


def test_explain_503_without_api_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    register(client)
    mistake_id = new_mistake(client)

    response = explain(client, mistake_id, "讲解。")

    assert response.status_code == 503


# ---------------- AI 失败退还 ----------------

def test_explain_invalid_ai_reply_502_and_refunds(client, monkeypatch):
    provider = fake_ai(
        monkeypatch,
        FakeResponse("不是 JSON", "stop"),
        FakeResponse("{仍然不是 JSON", "stop"),
    )
    register(client)
    mistake_id = new_mistake(client)

    response = explain(client, mistake_id, "讲解。")

    assert response.status_code == 502
    # 重试了一次：共 2 次 AI 请求。
    assert len(provider.requests) == 2
    # 退回本次扣的额度，不写讲解记录。
    assert attempts() == 0
    assert explanations() == []
    calls = ai_calls()
    assert len(calls) == 1
    assert calls[0]["feature"] == "explain"
    assert calls[0]["ok"] == 0


def test_explain_ai_timeout_504_and_refunds(client, monkeypatch):
    provider = fake_ai(monkeypatch, main.APITimeoutError("slow"))
    register(client)
    mistake_id = new_mistake(client)

    response = explain(client, mistake_id, "讲解。")

    assert response.status_code == 504
    assert len(provider.requests) == 1
    assert attempts() == 0
    assert explanations() == []
