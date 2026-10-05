"""POST /api/mistakes/{id}/duck：讲给小黄鸭听。

全部用假 AI 客户端（monkeypatch main.OpenAI），绝不真实调用。
覆盖：契约形状、turns 校验（422 不扣额度）、归属与登录、503 未配置、
429 额度、回复不合格带原因重试一次、仍不合格 502 退额度、AI 异常映射与退额度、
ai_calls 记账、提示词上下文。
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import ai
import main
from db import connect
from test_ai import FakeResponse, fake_openai_factory


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
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


def duck(client, mistake_id, turns, finish=False):
    return client.post(
        f"/api/mistakes/{mistake_id}/duck",
        json={"turns": turns, "finish": finish},
    )


def attempts(user_id=1):
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["attempts"] if row else 0


def ai_calls(user_id=1):
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT feature, ok, error FROM ai_calls WHERE user_id = ?", (user_id,)
        ).fetchall()]


GOOD = FakeResponse("哪里没想清楚？", "stop")
USER = {"role": "user", "text": "我当时的思路是维护一个闭区间。"}
DUCK = {"role": "duck", "text": "区间里剩一个元素时呢？"}


# ---------------- 成功路径与契约 ----------------

def test_chat_success_returns_reply_and_counts(client, monkeypatch):
    provider = fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER])

    assert response.status_code == 200
    body = response.json()
    assert body == {"reply": "哪里没想清楚？", "turns_used": 1, "ai_remaining": 1}
    messages = provider.requests[0]["messages"]
    assert messages[0]["role"] == "system" and "小黄鸭" in messages[0]["content"]
    assert [m["role"] for m in messages[1:]] == ["user", "user"], "资料块之后是用户发言"


def test_history_maps_duck_to_assistant_and_stays_alternating(client, monkeypatch):
    provider = fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER, DUCK, {"role": "user", "text": "剩一个时就退出了。"}])

    assert response.status_code == 200
    assert response.json()["turns_used"] == 2
    roles = [m["role"] for m in provider.requests[0]["messages"]]
    assert roles == ["system", "user", "user", "assistant", "user"]


def test_finish_appends_summary_instruction(client, monkeypatch):
    provider = fake_ai(monkeypatch, FakeResponse("你讲清了循环不变量，边界还模糊。", "stop"))
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER, DUCK], finish=True)

    assert response.status_code == 200
    assert response.json()["reply"] == "你讲清了循环不变量，边界还模糊。"
    assert "总结" in provider.requests[0]["messages"][-1]["content"]


def test_reply_is_cleaned_before_returning(client, monkeypatch):
    fake_ai(monkeypatch, FakeResponse("**对吗？**   再想想。", "stop"))
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER])

    assert response.status_code == 200
    assert response.json()["reply"] == "对吗？ 再想想。", "validate_reply 的清理结果才是返回文本"


def test_prompt_carries_the_mistake_context(client, monkeypatch):
    provider = fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)
    duck(client, mistake_id, [USER])

    data_block = provider.requests[0]["messages"][1]["content"]
    assert "<用户资料>" in data_block and "</用户资料>" in data_block
    assert "二分查找" in data_block and "算法" in data_block
    assert "维护闭区间" in data_block, "思路笔记进资料块"
    assert "循环结束条件漏掉 left == right" in data_block, "错因记录进资料块"


# ---------------- 422 校验（在扣额度之前） ----------------

@pytest.mark.parametrize("turns,finish", [
    ([], False),                                        # 空对话
    ([], True),
    ([DUCK], False),                                    # 不以 user 开头
    ([USER, DUCK], False),                              # 非总结时以 duck 结尾
    ([USER], True),                                     # 总结时以 user 结尾
    ([USER, USER], False),                              # 不交替
    ([{**USER, "text": "   "}], False),                 # 空白发言
    ([{**USER, "text": "字" * 2001}], False),           # 单条超长（pydantic）
    ([{"role": "system", "text": "x"}], False),         # 非法 role（pydantic）
])
def test_invalid_turns_are_422_and_never_touch_quota(client, monkeypatch, turns, finish):
    provider = fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, turns, finish=finish)

    assert response.status_code == 422
    assert provider.requests == [], "校验失败不能惊动 AI"
    assert attempts() == 0, "校验失败不扣额度"


def test_seven_user_turns_are_422(client, monkeypatch):
    fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)
    turns = []
    for index in range(7):
        turns += [{"role": "user", "text": f"第{index}句"}, {"role": "duck", "text": "嗯？"}]
    turns.pop()  # 以 user 结尾

    response = duck(client, mistake_id, turns)

    assert response.status_code == 422
    assert "6" in response.json()["detail"]


def test_total_chars_over_4000_are_422(client, monkeypatch):
    fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)
    long_turn = {"role": "user", "text": "字" * 1500}
    turns = [long_turn, DUCK, long_turn, DUCK, long_turn]

    assert duck(client, mistake_id, turns).status_code == 422


# ---------------- 归属与登录 ----------------

def test_unknown_mistake_is_404(client, monkeypatch):
    fake_ai(monkeypatch, GOOD)
    register(client)
    assert duck(client, 9999, [USER]).status_code == 404


def test_other_users_mistake_is_404(client, monkeypatch):
    fake_ai(monkeypatch, GOOD)
    register(client, "alice")
    mistake_id = new_mistake(client)
    register(client, "bob")  # 注册会换成 bob 的会话

    response = duck(client, mistake_id, [USER])

    assert response.status_code == 404, "记录必须属于当前用户"
    assert attempts() == 0


def test_unauthenticated_is_401(client, monkeypatch):
    fake_ai(monkeypatch, GOOD)
    register(client)
    mistake_id = new_mistake(client)
    client.cookies.clear()

    assert duck(client, mistake_id, [USER]).status_code == 401


# ---------------- 503 / 429 ----------------

def test_missing_api_key_is_503_without_touching_quota(client, monkeypatch):
    provider = fake_ai(monkeypatch, GOOD)
    monkeypatch.delenv("OPENAI_API_KEY")
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER])

    assert response.status_code == 503
    assert provider.requests == []
    assert attempts() == 0


def test_quota_exhaustion_is_429_and_stops_calling_ai(client, monkeypatch):
    provider = fake_ai(monkeypatch, GOOD, GOOD, GOOD)
    register(client)
    mistake_id = new_mistake(client)

    first = duck(client, mistake_id, [USER])
    second = duck(client, mistake_id, [USER, DUCK, USER])
    third = duck(client, mistake_id, [USER, DUCK, USER, DUCK, USER])

    assert first.status_code == 200 and first.json()["ai_remaining"] == 1
    assert second.status_code == 200 and second.json()["ai_remaining"] == 0
    assert third.status_code == 429
    assert "已用完" in third.json()["detail"]
    assert len(provider.requests) == 2, "额度用完后不再请求 AI"
    assert attempts() == 2


# ---------------- 回复不合格：带原因重试一次，仍不合格 502 退额度 ----------------

def test_invalid_reply_retries_once_with_the_reason_and_charges_once(client, monkeypatch):
    provider = fake_ai(monkeypatch, FakeResponse("答案是 left <= right。", "stop"), GOOD)
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER])

    assert response.status_code == 200
    assert response.json()["reply"] == "哪里没想清楚？"
    assert len(provider.requests) == 2, "不合格回复重试一次"
    retry_messages = provider.requests[1]["messages"]
    assert retry_messages[-2] == {"role": "assistant", "content": "答案是 left <= right。"}
    assert "gave_answer" in retry_messages[-1]["content"], "重试提示带上了不合格原因"
    assert attempts() == 1, "重试不重复扣额度"
    assert response.json()["ai_remaining"] == 1


def test_two_invalid_replies_are_502_and_refund_the_attempt(client, monkeypatch):
    provider = fake_ai(monkeypatch, FakeResponse("答案是 x。", "stop"), FakeResponse("```code```", "stop"), GOOD)
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER])

    assert response.status_code == 502
    assert "没答好" in response.json()["detail"]
    assert len(provider.requests) == 2
    assert attempts() == 0, "502 退回本次扣的额度"
    assert duck(client, mistake_id, [USER]).status_code == 200, "退回后还能正常用"


def test_truncated_reply_counts_as_a_failed_attempt(client, monkeypatch):
    provider = fake_ai(monkeypatch, FakeResponse("说到一半", "length"), GOOD)
    register(client)
    mistake_id = new_mistake(client)

    assert duck(client, mistake_id, [USER]).status_code == 200
    assert len(provider.requests) == 2, "finish_reason 不是 stop 也算一次不合格"


# ---------------- AI 异常映射与退额度 ----------------

def upstream_error(kind):
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    if kind == "timeout":
        return ai.APITimeoutError(request=request)
    if kind == "connection":
        return ai.APIConnectionError(request=request)
    return ai.RateLimitError("quota", response=httpx.Response(429, request=request), body=None)


@pytest.mark.parametrize("kind,status", [("timeout", 504), ("connection", 502), ("rate", 503)])
def test_upstream_errors_map_status_and_refund(client, monkeypatch, kind, status):
    fake_ai(monkeypatch, upstream_error(kind), GOOD)
    register(client)
    mistake_id = new_mistake(client)

    response = duck(client, mistake_id, [USER])

    assert response.status_code == status
    assert attempts() == 0, "上游失败退回额度"
    assert duck(client, mistake_id, [USER]).status_code == 200


# ---------------- 记账 ----------------

def test_ai_calls_are_recorded_for_success_and_failure(client, monkeypatch):
    fake_ai(monkeypatch, GOOD, FakeResponse("答案是 x。", "stop"), FakeResponse("", "stop"))
    register(client)
    mistake_id = new_mistake(client)

    assert duck(client, mistake_id, [USER]).status_code == 200
    assert duck(client, mistake_id, [USER]).status_code == 502

    calls = ai_calls()
    assert [call["feature"] for call in calls] == ["duck", "duck"]
    assert calls[0]["ok"] == 1 and calls[0]["error"] == ""
    assert calls[1]["ok"] == 0 and calls[1]["error"] == "http_502"
