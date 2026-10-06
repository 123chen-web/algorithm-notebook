"""AI 调用成本与名额回归；所有服务商调用均由假客户端替代。"""
import asyncio
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, UploadFile

import ai
import ai_limits
import main
from db import connect
from test_ai import FakeResponse, fake_openai_factory, sectioned
from test_app import client, mock_generated_practice, register
from test_clusters import cluster_result, seed_mistakes
from test_photo_recognition import FAKE_RECOGNITION, make_image_bytes, upload_photo
from test_weakness_insights import analysis_result


BUSY = "AI 现在比较忙，请稍后再试；这次没有消耗额度。"
FEATURES = ("variant", "weakness", "photo", "clusters")


@pytest.fixture(autouse=True)
def isolated_ai(monkeypatch):
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "6")
    monkeypatch.setattr(ai_limits, "_semaphore_state", None)

    def unexpected_request(**kwargs):
        pytest.fail("测试不能调用真实 OpenAI 服务")

    monkeypatch.setattr(ai, "OpenAI", unexpected_request)


def call_rows():
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM ai_calls ORDER BY id")]


def attempts(user_id):
    with connect() as conn:
        row = conn.execute("SELECT attempts FROM ai_usage WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else 0


def request_ai(client, feature, mistake_id):
    if feature == "photo":
        return upload_photo(client, make_image_bytes())
    endpoint = {
        "variant": f"/api/mistakes/{mistake_id}/variants",
        "weakness": "/api/insights/weakness-analysis",
        "clusters": "/api/insights/clusters",
    }[feature]
    return client.post(endpoint)


def mock_result(feature, reference):
    return {
        "variant": mock_generated_practice,
        "weakness": analysis_result,
        "photo": lambda jpeg: dict(FAKE_RECOGNITION),
        "clusters": cluster_result,
    }[feature](reference)


def mock_ai(monkeypatch, feature, replacement=None):
    name = {
        "variant": "generate", "weakness": "analyze_weaknesses",
        "photo": "recognize_photo", "clusters": "cluster_mistakes",
    }[feature]
    monkeypatch.setattr(ai, name, replacement or (lambda reference: mock_result(feature, reference)))


@pytest.mark.parametrize("feature", FEATURES)
def test_four_entries_record_one_success_without_usage(client, monkeypatch, feature):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    mock_ai(monkeypatch, feature)
    response = request_ai(client, feature, mistake_id)
    assert response.status_code == (201 if feature == "variant" else 200)
    rows = call_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["user_id"] == user_id and row["feature"] == feature
    assert row["ok"] == 1 and row["error"] == "" and row["duration_ms"] >= 0
    assert row["prompt_tokens"] is None and row["completion_tokens"] is None
    assert datetime.fromisoformat(row["created_at"]).utcoffset() == timezone.utc.utcoffset(None)
    assert attempts(user_id) == 1


@pytest.mark.parametrize("feature", FEATURES)
@pytest.mark.parametrize("usage", [None, SimpleNamespace(prompt_tokens=123, completion_tokens=45)])
def test_mock_provider_records_tokens_and_actual_request_model(client, monkeypatch, feature, usage):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    monkeypatch.setenv("OPENAI_MODEL", "configured-test-model")
    captured = []

    class Completions:
        def create(self, **kwargs):
            captured.append(kwargs)
            if feature == "variant":
                content = sectioned(summary="MODEL_RESPONSE_PRIVATE_TEXT")
            elif feature == "photo":
                content = json.dumps({
                    "valid": True, "zone": "算法", "title": "识别二分查找",
                    "language": "Python", "original_question": "查找目标值",
                    "original_work": "pass", "thinking": "没有明确边界", "description": "边界遗漏",
                }, ensure_ascii=False)
            else:
                raw = kwargs["messages"][1]["content"]
                reference = json.loads(raw.split("\n", 1)[1].rsplit("\n", 1)[0])
                content = json.dumps(mock_result(feature, reference), ensure_ascii=False)
            response = FakeResponse(content, model="provider-response-model")
            response.usage = usage
            return response

    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(Completions()))
    response = request_ai(client, feature, mistake_id)
    assert response.status_code == (201 if feature == "variant" else 200)
    row, = call_rows()
    assert row["model"] == captured[0]["model"] == "configured-test-model"
    assert row["prompt_tokens"] == (123 if usage else None)
    assert row["completion_tokens"] == (45 if usage else None)
    assert row["ok"] == 1
    assert "MODEL_RESPONSE_PRIVATE_TEXT" not in json.dumps(row)
    assert "SECRET_CODE_FULL_TEXT" not in json.dumps(row)


@pytest.mark.parametrize("feature", FEATURES)
def test_failed_ai_keeps_status_refunds_quota_and_releases_slot(client, monkeypatch, feature):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")

    def fail(reference):
        raise HTTPException(504, "PRIVATE_EXCEPTION_WITH_USER_CONTENT")

    mock_ai(monkeypatch, feature, fail)
    response = request_ai(client, feature, mistake_id)
    assert response.status_code == 504
    assert response.json()["detail"] == "PRIVATE_EXCEPTION_WITH_USER_CONTENT"
    row, = call_rows()
    assert row["ok"] == 0 and row["error"] == "http_504"
    assert "PRIVATE_EXCEPTION_WITH_USER_CONTENT" not in json.dumps(row)
    assert attempts(user_id) == 0  # AI 服务端失败（504）退还这次额度
    mock_ai(monkeypatch, feature)
    assert request_ai(client, feature, mistake_id).status_code == (201 if feature == "variant" else 200)
    assert len(call_rows()) == 2 and attempts(user_id) == 1


def test_accounting_columns_only_contain_cost_metadata(client):
    with connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_calls)")}
    assert columns == {
        "id", "user_id", "feature", "model", "prompt_tokens", "completion_tokens",
        "ok", "error", "duration_ms", "created_at",
    }


@pytest.mark.parametrize("feature", FEATURES)
def test_busy_request_does_not_spend_or_record_and_next_request_succeeds(client, monkeypatch, feature):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    entered, release = threading.Event(), threading.Event()

    def generate(reference):
        entered.set()
        assert release.wait(10), "未及时放行假 AI 调用"
        return mock_result(feature, reference)

    mock_ai(monkeypatch, feature, generate)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(request_ai, client, feature, mistake_id)
        try:
            assert entered.wait(5), "首个请求未进入假 AI"
            assert attempts(user_id) == 1 and call_rows() == []
            busy = request_ai(client, feature, mistake_id)
            assert busy.status_code == 429 and busy.json()["detail"] == BUSY
            assert attempts(user_id) == 1 and call_rows() == []
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == (201 if feature == "variant" else 200)
    mock_ai(monkeypatch, feature)
    assert request_ai(client, feature, mistake_id).status_code == (201 if feature == "variant" else 200)
    assert len(call_rows()) == 2 and attempts(user_id) == 2


def test_one_global_slot_is_shared_between_features(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    mock_ai(monkeypatch, "variant")
    mock_ai(monkeypatch, "photo")
    original = ai.generate

    def generate(reference):
        nested = request_ai(client, "photo", mistake_id)
        assert nested.status_code == 429 and nested.json()["detail"] == BUSY
        assert attempts(user_id) == 1 and call_rows() == []
        return original(reference)

    monkeypatch.setattr(ai, "generate", generate)
    assert request_ai(client, "variant", mistake_id).status_code == 201
    assert request_ai(client, "photo", mistake_id).status_code == 200
    assert [row["feature"] for row in call_rows()] == ["variant", "photo"]


@pytest.mark.parametrize("feature,branch", [
    (feature, branch) for feature in FEATURES for branch in ("key", "quota")
] + [("weakness", "data"), ("clusters", "data")])
def test_existing_early_branches_keep_priority_when_slot_is_busy(client, monkeypatch, feature, branch):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id, count=0 if branch == "data" else 6)
    if branch == "key":
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if branch == "quota":
        with connect(write=True) as conn:
            conn.execute("INSERT INTO ai_usage VALUES (?, '2026-09-19', 2)", (user_id,))
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    with ai_limits.ai_slot():
        response = request_ai(client, feature, mistake_id[0] if mistake_id else 0)
    if branch == "key":
        assert response.status_code == 503
        assert response.json()["detail"] == "服务端尚未配置 AI 服务密钥"
    elif branch == "quota":
        assert response.status_code == 429
        assert response.json()["detail"] == "今天的 AI 生成次数已用完"
    else:
        assert response.status_code == 200 and response.json()["status"] == "insufficient_data"
    assert attempts(user_id) == (2 if branch == "quota" else 0)
    assert call_rows() == []


def test_provider_failure_still_records_actual_model(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    monkeypatch.setenv("OPENAI_MODEL", "failed-request-model")

    class Completions:
        def create(self, **kwargs):
            raise HTTPException(504, "PRIVATE_PROVIDER_ERROR")

    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(Completions()))
    assert request_ai(client, "variant", mistake_id).status_code == 504
    row, = call_rows()
    assert row["model"] == "failed-request-model" and row["error"] == "http_504"
    assert row["prompt_tokens"] is None and row["completion_tokens"] is None


def test_unexpected_exception_is_classified_and_releases_slot(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")

    def fail(reference):
        raise ValueError("PRIVATE_USER_INPUT")

    mock_ai(monkeypatch, "variant", fail)
    with pytest.raises(ValueError, match="PRIVATE_USER_INPUT"):
        request_ai(client, "variant", mistake_id)
    row, = call_rows()
    assert row["error"] == "ValueError" and row["ok"] == 0
    assert "PRIVATE_USER_INPUT" not in json.dumps(row)
    mock_ai(monkeypatch, "variant")
    assert request_ai(client, "variant", mistake_id).status_code == 201


def test_cancelled_async_photo_request_releases_slot(client, monkeypatch):
    user = register(client)
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    mock_ai(monkeypatch, "photo")
    original_threadpool = main.run_in_threadpool

    async def exercise():
        entered = asyncio.Event()

        async def wait_in_worker(func, *args):
            if func is ai.recognize_photo:
                entered.set()
                await asyncio.Event().wait()
            return func(*args)

        monkeypatch.setattr(main, "run_in_threadpool", wait_in_worker)
        upload = UploadFile(file=io.BytesIO(make_image_bytes()), filename="photo.jpg")
        pending = asyncio.create_task(main.recognize_problem_photo(user=user, file=upload))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        finally:
            if not pending.done():
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
            await upload.close()
        with ai_limits.ai_slot():
            pass

    asyncio.run(exercise())
    monkeypatch.setattr(main, "run_in_threadpool", original_threadpool)
    assert attempts(user["id"]) == 1
    assert upload_photo(client, make_image_bytes()).status_code == 200
    assert call_rows()[-1]["ok"] == 1


def test_delete_account_retains_cost_and_clears_user_reference(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = seed_mistakes(user_id)[0]
    mock_ai(monkeypatch, "variant")
    assert request_ai(client, "variant", mistake_id).status_code == 201
    with connect(write=True) as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    row, = call_rows()
    assert row["user_id"] is None and row["ok"] == 1


@pytest.mark.parametrize("value,expected", [
    (None, 6), ("", 6), ("bad", 6), ("0", 6), ("-1", 6), ("1.5", 6), ("1", 1), (" 3 ", 3),
])
def test_max_concurrency_parses_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AI_MAX_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("AI_MAX_CONCURRENCY", value)
    assert ai_limits.max_concurrency() == expected


def test_slot_rebuilds_after_environment_change_and_releases_original_semaphore(monkeypatch):
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    with ai_limits.ai_slot():
        with pytest.raises(HTTPException, match=BUSY):
            with ai_limits.ai_slot():
                pass
        monkeypatch.setenv("AI_MAX_CONCURRENCY", "2")
        with ai_limits.ai_slot(), ai_limits.ai_slot():
            with pytest.raises(HTTPException):
                with ai_limits.ai_slot():
                    pass
    monkeypatch.setenv("AI_MAX_CONCURRENCY", "1")
    with ai_limits.ai_slot():
        pass


@pytest.mark.parametrize("response,prompt,completion", [
    (object(), None, None),
    (SimpleNamespace(usage=None), None, None),
    (SimpleNamespace(usage=SimpleNamespace(prompt_tokens=7)), 7, None),
    (SimpleNamespace(usage=SimpleNamespace(prompt_tokens="7", completion_tokens=1.5)), None, None),
    (SimpleNamespace(usage=SimpleNamespace(prompt_tokens=True, completion_tokens=-1)), None, None),
    (Mock(), None, None),
])
def test_note_usage_tolerates_missing_or_invalid_usage(monkeypatch, response, prompt, completion):
    state = {}
    token = ai_limits._call_usage.set(state)
    try:
        ai_limits.note_usage("mock-model", response)
    finally:
        ai_limits._call_usage.reset(token)
    assert state == {"model": "mock-model", "prompt_tokens": prompt, "completion_tokens": completion}


@pytest.mark.parametrize("failure", [False, True])
def test_accounting_failure_never_changes_request_result(monkeypatch, caplog, failure):
    @contextmanager
    def broken_database(write=False):
        raise RuntimeError("PRIVATE_DATABASE_ERROR")
        yield

    monkeypatch.setattr(ai_limits, "connect", broken_database)
    if failure:
        with pytest.raises(HTTPException) as caught:
            with ai_limits.track_call(1, "variant"):
                raise HTTPException(504, "PRIVATE_AI_ERROR")
        assert caught.value.status_code == 504 and caught.value.detail == "PRIVATE_AI_ERROR"
    else:
        with ai_limits.track_call(1, "variant"):
            ai_limits.note_usage("model", object())
    assert "RuntimeError" in caplog.text
    assert "PRIVATE_DATABASE_ERROR" not in caplog.text and "PRIVATE_AI_ERROR" not in caplog.text
    assert ai_limits._call_usage.get() is None
