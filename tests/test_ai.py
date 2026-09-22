"""针对 ai.py 内部请求/解析逻辑的测试。

main.py 的测试整体替换 ai.generate，不会走到这里覆盖的代码；这里单独
用一个假的 OpenAI 客户端验证请求参数、以及各种 finish_reason 的处理，
因为这部分逻辑没有真实 API Key 时完全测不到，容易在改动时悄悄写错。
"""
import pytest
from fastapi import HTTPException

import ai

MISTAKE = {
    "title": "二分查找",
    "language": "Python",
    "code": "pass",
    "thinking": "先随便写写",
    "description": "右边界更新漏掉了等号",
}


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content, finish_reason):
        self.message = FakeMessage(content)
        self.finish_reason = finish_reason


class FakeResponse:
    def __init__(self, content, finish_reason="stop", model="gpt-4.1-mini"):
        self.choices = [FakeChoice(content, finish_reason)]
        self.model = model


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self.response


class FakeClient:
    def __init__(self, completions, **init_kwargs):
        self.chat = type("Chat", (), {"completions": completions})()
        self.init_kwargs = init_kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def fake_openai_factory(completions, captured_init=None):
    def factory(**kwargs):
        if captured_init is not None:
            captured_init.update(kwargs)
        return FakeClient(completions, **kwargs)

    return factory


def test_generate_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        ai.generate(MISTAKE)
    assert exc.value.status_code == 503


def test_generate_uses_chat_completions_and_returns_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    completions = FakeCompletions(FakeResponse("题目正文", "stop", "gpt-4.1-mini"))
    captured_init = {}
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions, captured_init))

    result = ai.generate(MISTAKE)

    assert result == {"description": "题目正文", "model": "gpt-4.1-mini"}
    # 走的是 messages 形式的 Chat Completions 接口，不是 Responses 接口的
    # instructions/input，这样才对第三方 OpenAI 兼容服务商也有效。
    assert "messages" in completions.last_kwargs
    assert completions.last_kwargs["messages"][0]["role"] == "system"
    # 没配置 OPENAI_BASE_URL 时，走真正 OpenAI 的默认地址（不传 base_url）。
    assert captured_init["base_url"] is None


def test_generate_passes_base_url_for_alternate_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-chat")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    completions = FakeCompletions(FakeResponse("题目正文", "stop", "deepseek-chat"))
    captured_init = {}
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions, captured_init))

    result = ai.generate(MISTAKE)

    assert result["model"] == "deepseek-chat"
    assert captured_init["base_url"] == "https://api.deepseek.com"
    assert completions.last_kwargs["model"] == "deepseek-chat"


@pytest.mark.parametrize("finish_reason", ["length", "content_filter"])
def test_generate_rejects_non_stop_finish_reasons(monkeypatch, finish_reason):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    completions = FakeCompletions(FakeResponse("没写完/被拒绝", finish_reason))
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions))

    with pytest.raises(HTTPException) as exc:
        ai.generate(MISTAKE)
    assert exc.value.status_code == 502


def test_generate_rejects_empty_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    completions = FakeCompletions(FakeResponse("   ", "stop"))
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions))

    with pytest.raises(HTTPException) as exc:
        ai.generate(MISTAKE)
    assert exc.value.status_code == 502


def test_generate_rejects_overlong_text(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    completions = FakeCompletions(FakeResponse("x" * 16001, "stop"))
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions))

    with pytest.raises(HTTPException) as exc:
        ai.generate(MISTAKE)
    assert exc.value.status_code == 502


def test_generate_stops_when_model_flags_content_as_off_topic(monkeypatch):
    # mistake 里的字段全部来自用户自己保存的数据，模型判定它们跟算法题
    # 无关（比如被当成越权指令、无关问答）时按约定只输出这一行标记。
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    completions = FakeCompletions(FakeResponse(ai.REFUSAL_MARKER, "stop"))
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions))

    with pytest.raises(HTTPException) as exc:
        ai.generate(MISTAKE)
    assert exc.value.status_code == 422

    # 标记本身长得像合法输出（较短、纯文本），确认它不会被超长/空文本
    # 那两条检查提前拦下，而是真的走到了专门的判断分支。
    assert len(ai.REFUSAL_MARKER) < 16000
    assert ai.REFUSAL_MARKER.strip() == ai.REFUSAL_MARKER
