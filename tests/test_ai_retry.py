"""AI 传输层重试一次 + 服务端失败退还额度；全部用假客户端，不联网、不真睡。"""
import json

import httpx
import openai
import pytest

import ai
from test_ai import FakeResponse
from test_app import client, register
from test_clusters import seed_mistakes
from test_weakness_insights import ENDPOINT, analysis_result, attempts

REQUEST = httpx.Request("POST", "https://provider.invalid/v1/chat/completions")


def status_error(cls, code):
    return cls("provider said no", response=httpx.Response(code, request=REQUEST), body=None)


class ScriptedCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(ai, "RETRY_DELAY_SECONDS", 0)

    def install(script):
        completions = ScriptedCompletions(script)

        class FakeClient:
            def __init__(self, **kwargs):
                self.chat = type("Chat", (), {"completions": completions})()

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        monkeypatch.setattr(ai, "OpenAI", FakeClient)
        return completions

    return install


def good_response(user_id):
    ids = [{"mistake_id": i} for i in range(1, 3)]
    return FakeResponse(json.dumps(analysis_result({"mistakes": ids}), ensure_ascii=False))


def test_retry_once_after_timeout_then_success_charges_one_attempt(client, provider):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    completions = provider([openai.APITimeoutError(request=REQUEST), good_response(user_id)])
    response = client.post(ENDPOINT)
    assert response.status_code == 200
    assert completions.calls == 2
    assert attempts(user_id) == 1


def test_two_timeouts_return_gateway_error_and_refund(client, provider):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    completions = provider([openai.APITimeoutError(request=REQUEST)] * 2)
    assert client.post(ENDPOINT).status_code == 504
    assert completions.calls == 2
    assert attempts(user_id) == 0


def test_auth_failure_is_not_retried_and_is_refunded(client, provider):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    completions = provider([status_error(openai.AuthenticationError, 401)])
    assert client.post(ENDPOINT).status_code == 502
    assert completions.calls == 1
    assert attempts(user_id) == 0


def test_malformed_model_output_is_not_retried_and_is_refunded(client, provider):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    completions = provider([FakeResponse("这不是 JSON")])
    assert client.post(ENDPOINT).status_code == 502
    assert completions.calls == 1
    assert attempts(user_id) == 0


def test_user_content_refusal_422_is_not_refunded(client, provider):
    user_id = register(client)["id"]
    seed_mistakes(user_id)
    completions = provider([FakeResponse(ai.REFUSAL_MARKER)])
    assert client.post(ENDPOINT).status_code == 422
    assert completions.calls == 1
    assert attempts(user_id) == 1


@pytest.mark.parametrize("error", [
    openai.APITimeoutError(request=REQUEST),
    openai.APIConnectionError(request=REQUEST),
    status_error(openai.InternalServerError, 500),
    status_error(openai.APIStatusError, 502),
    status_error(openai.APIStatusError, 503),
], ids=["timeout", "connection", "provider-500", "provider-502", "provider-503"])
def test_call_with_retry_retries_transient_errors_once_after_two_seconds(error):
    sleeps, calls = [], []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise error
        return "ok"

    assert ai.call_with_retry(flaky, sleep=sleeps.append) == "ok"
    assert len(calls) == 2 and sleeps == [2.0]


def test_call_with_retry_gives_up_after_the_second_transient_failure():
    sleeps, calls = [], []

    def always_down():
        calls.append(1)
        raise openai.APITimeoutError(request=REQUEST)

    with pytest.raises(openai.APITimeoutError):
        ai.call_with_retry(always_down, sleep=sleeps.append)
    assert len(calls) == 2 and sleeps == [2.0]


@pytest.mark.parametrize("error", [
    status_error(openai.AuthenticationError, 401),
    status_error(openai.BadRequestError, 400),
    status_error(openai.RateLimitError, 429),
    ValueError("解析失败"),
], ids=["auth", "bad-request", "quota", "parse"])
def test_call_with_retry_never_retries_other_errors(error):
    sleeps, calls = [], []

    def fail():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        ai.call_with_retry(fail, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []
