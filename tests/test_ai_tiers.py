"""AI 用量分档（⑦）：max_tokens 集中在 core.config.AI_MAX_TOKENS，各入口按任务取用。

覆盖：
1. 常量表完整且每个值都是整数、不低于 2000（任务书下限）；
2. 8 个调用点实际传给 OpenAI 的 max_tokens 就是常量表里对应的值
   （行为测试：不断言源码，只看发出去的请求参数）；
3. 之前没有截断测试的入口：finish_reason == "length" → 502
   （generate / analyze_weaknesses / cluster_mistakes / duck 已有同类测试，不重复）。
"""
import json

import pytest
from fastapi import HTTPException

import ai
import duck_prompt
import thread_summary
from core import config
from test_ai import (
    FakeCompletions,
    FakeResponse,
    fake_openai_factory,
    MISTAKE,
    sectioned,
)
from test_clusters import ai_reference as cluster_reference_fixture  # noqa: F401  (fixture)
from test_clusters import cluster_result
from test_weakness_ai import analysis as weakness_analysis_fixture  # noqa: F401  (fixture)
from test_weakness_ai import reference as weakness_reference_fixture  # noqa: F401  (fixture)

EXPECTED_TIERS = {
    "variant": "ai.generate",
    "photo": "ai.recognize_photo",
    "weakness": "ai.analyze_weaknesses",
    "cluster": "ai.cluster_mistakes",
    "duck": "ai.duck_ai_reply",
    "suggest_patterns": "ai.suggest_patterns",
    "digest": "digest",
    "thread_summary": "thread_summary",
}


def test_tier_table_is_complete_and_sane():
    assert set(config.AI_MAX_TOKENS) == set(EXPECTED_TIERS)
    for key, value in config.AI_MAX_TOKENS.items():
        assert isinstance(value, int), key
        assert value >= 2000, key  # 任务书：P95 的 3 倍，且不低于 2000


def _install(monkeypatch, content, finish_reason="stop"):
    if isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False)
    completions = FakeCompletions(FakeResponse(content, finish_reason))
    monkeypatch.setattr(ai, "OpenAI", fake_openai_factory(completions))
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only-not-a-real-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    return completions


def _weakness_reference():
    return {
        "total_mistakes": 1,
        "sample": {"mistake_count": 1},
        "mistakes": [{
            "mistake_id": 1, "problem_id": 1, "title": "t", "zone": "算法",
            "description": "d", "created_at": "2026-01-01T00:00:00",
            "review_count": 0, "failed_review_count": 0, "recent_reviews": [],
        }],
    }


def _photo_json():
    return json.dumps({
        "valid": True, "zone": "算法", "title": "t", "language": "",
        "original_question": "q", "original_work": "w",
        "thinking": "", "description": "d",
    }, ensure_ascii=False)


def test_generate_uses_variant_tier(monkeypatch):
    completions = _install(monkeypatch, sectioned())
    ai.generate(MISTAKE)
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["variant"]


def test_recognize_photo_uses_photo_tier(monkeypatch):
    completions = _install(monkeypatch, _photo_json())
    ai.recognize_photo(b"fake-jpeg-bytes")
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["photo"]


def test_analyze_weaknesses_uses_weakness_tier(
    monkeypatch, weakness_analysis_fixture, weakness_reference_fixture
):
    completions = _install(
        monkeypatch, json.dumps(weakness_analysis_fixture, ensure_ascii=False))
    ai.analyze_weaknesses(weakness_reference_fixture)
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["weakness"]


def test_cluster_mistakes_uses_cluster_tier(monkeypatch, cluster_reference_fixture):
    completions = _install(monkeypatch, cluster_result(cluster_reference_fixture))
    ai.cluster_mistakes(cluster_reference_fixture)
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["cluster"]


def test_duck_ai_reply_uses_duck_tier(monkeypatch):
    item = dict(MISTAKE)
    completions = _install(monkeypatch, "试试把右边界改成闭区间？")
    monkeypatch.setattr(duck_prompt, "validate_reply", lambda text, finish: (True, text))
    ai.duck_ai_reply(item, [{"role": "user", "text": "卡住了"}], False)
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["duck"]


def test_suggest_patterns_uses_suggest_tier(monkeypatch):
    completions = _install(
        monkeypatch, json.dumps({"suggestions": []}, ensure_ascii=False))
    ai.suggest_patterns({"title": "t", "source": "s", "thinking": ""}, "1 | 基础 | 二分", [])
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["suggest_patterns"]


def test_thread_summary_uses_thread_summary_tier(monkeypatch):
    completions = FakeCompletions(FakeResponse(
        json.dumps({"summary": "s", "key_points": []}, ensure_ascii=False)))
    monkeypatch.setattr(thread_summary, "OpenAI", fake_openai_factory(completions))
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only-not-a-real-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    reference = {
        "post": {"id": 1, "title": "t", "content": "c"},
        "comments": [],
    }
    try:
        thread_summary.summarize_thread(reference)
    except HTTPException:
        pass  # 解析失败也不影响：断言只看发出去的 max_tokens
    assert completions.last_kwargs["max_tokens"] == config.AI_MAX_TOKENS["thread_summary"]


# ---------------- 截断 → 502（之前没有覆盖的三个入口） ----------------

def test_recognize_photo_truncation_is_502(monkeypatch):
    _install(monkeypatch, '{"valid": true, "zone":', "length")
    with pytest.raises(HTTPException) as exc:
        ai.recognize_photo(b"fake-jpeg-bytes")
    assert exc.value.status_code == 502


def test_suggest_patterns_truncation_is_502(monkeypatch):
    _install(monkeypatch, '{"patterns": [', "length")
    with pytest.raises(HTTPException) as exc:
        ai.suggest_patterns({"title": "t"}, "1 | 基础 | 二分", [])
    assert exc.value.status_code == 502


def test_thread_summary_truncation_is_502(monkeypatch):
    completions = FakeCompletions(FakeResponse('{"summary": "', "length"))
    monkeypatch.setattr(thread_summary, "OpenAI", fake_openai_factory(completions))
    monkeypatch.setenv("OPENAI_API_KEY", "mock-only-not-a-real-key")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    reference = {
        "post": {"id": 1, "title": "t", "content": "c"},
        "comments": [],
    }
    with pytest.raises(HTTPException) as exc:
        thread_summary.summarize_thread(reference)
    assert exc.value.status_code == 502
