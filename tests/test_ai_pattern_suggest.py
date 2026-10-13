"""待分类箱里的“让 AI 猜一下”：POST /api/roadmap/ai-suggest。

AI 调用一律用桩，测试里绝不联网、不计费。覆盖：额度扣减与退还、只猜自己的算法题、
AI 输出的校验（只留清单里真实存在的套路、去重、最多 3 个）、没有密钥时不扣额度。
"""
import json

import pytest
from fastapi import HTTPException

import ai
import pattern_match
from test_app import client, register


def make_problem(client, zone="算法", title="两数之和变种", **extra):
    body = {"title": title, "zone": zone, "quick": True, **({"language": "Python"} if zone == "算法" else {}), **extra}
    response = client.post("/api/problems", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def suggest(client, problem_id):
    return client.post("/api/roadmap/ai-suggest", json={"problem_id": problem_id})


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")


def test_requires_login(client):
    assert suggest(client, 1).status_code == 401


def test_returns_validated_suggestions_and_uses_one_ai_call(client, monkeypatch):
    register(client)
    pid = make_problem(client, thinking="用一个窗口扫过去")
    seen = {}

    def fake(material, candidates, known):
        seen.update(material=material, candidates=candidates)
        return [{"pattern_id": "binary-search-basic", "reason": "有序数组找边界"}]

    monkeypatch.setattr(ai, "suggest_patterns", fake)
    response = suggest(client, pid)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["suggestions"] == [{
        "pattern_id": "binary-search-basic", "title": "二分查找（左闭右闭）", "category": "二分",
        "reason": "有序数组找边界", "source": "ai",
    }]
    assert data["ai_daily_remaining"] >= 0
    # 只把题名、来源、思路发给 AI；候选清单是“id | 大类 | 名称”。
    assert set(seen["material"]) == {"title", "source_url", "thinking"}
    assert seen["material"]["title"] == "两数之和变种"
    assert "binary-search-basic | 二分 | " in seen["candidates"]
    assert client.get("/api/me").json()["ai_daily_used"] == 1


def test_other_users_problem_is_404_and_costs_nothing(client, monkeypatch):
    register(client, "alice")
    pid = make_problem(client)
    client.post("/api/auth/logout")
    register(client, "bob")
    monkeypatch.setattr(ai, "suggest_patterns", lambda *a: pytest.fail("must not call AI"))
    assert suggest(client, pid).status_code == 404
    assert client.get("/api/me").json()["ai_daily_used"] == 0


def test_non_algorithm_zone_is_rejected_before_spending_quota(client, monkeypatch):
    register(client)
    pid = make_problem(client, zone="高等数学", title="极限题")
    monkeypatch.setattr(ai, "suggest_patterns", lambda *a: pytest.fail("must not call AI"))
    response = suggest(client, pid)
    assert response.status_code == 422
    assert client.get("/api/me").json()["ai_daily_used"] == 0


def test_server_failure_refunds_the_quota(client, monkeypatch):
    register(client)
    pid = make_problem(client)

    def boom(*args):
        raise HTTPException(502, "AI 请求失败")

    monkeypatch.setattr(ai, "suggest_patterns", boom)
    assert suggest(client, pid).status_code == 502
    assert client.get("/api/me").json()["ai_daily_used"] == 0


def test_missing_key_returns_503_without_spending(client, monkeypatch):
    register(client)
    pid = make_problem(client)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert suggest(client, pid).status_code == 503
    assert client.get("/api/me").json()["ai_daily_used"] == 0


def test_daily_quota_exhausted_returns_429(client, monkeypatch):
    monkeypatch.setenv("AI_DAILY_LIMIT", "1")
    register(client)
    pid = make_problem(client)
    monkeypatch.setattr(ai, "suggest_patterns", lambda *a: [])
    assert suggest(client, pid).status_code == 200
    assert suggest(client, pid).status_code == 429


# ---- 解析器：AI 的话不能直接信 ----

KNOWN = pattern_match.pattern_is_known


def parse(payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return ai._parse_pattern_suggestions(text, KNOWN)


def test_parser_keeps_only_known_ids_dedupes_and_caps_at_three():
    items = [
        {"pattern_id": "no-such-pattern", "reason": "编的"},
        {"pattern_id": "binary-search-basic", "reason": "a"},
        {"pattern_id": "binary-search-basic", "reason": "重复"},
        {"pattern_id": "two-pointers", "reason": "b" * 200},
        {"pattern_id": 5, "reason": "类型不对"},
        "垃圾",
    ]
    known_ids = [i for i in ("sliding-window", "prefix-sum", "union-find") if KNOWN(i)]
    items += [{"pattern_id": i, "reason": "x"} for i in known_ids]
    result = parse({"suggestions": items})
    ids = [item["pattern_id"] for item in result]
    assert "no-such-pattern" not in ids and ids.count("binary-search-basic") == 1
    assert len(result) <= 3
    assert all(len(item["reason"]) <= 60 for item in result)


def test_parser_accepts_json_fences_and_empty_list():
    assert parse("```json\n" + json.dumps({"suggestions": []}) + "\n```") == []


@pytest.mark.parametrize("bad", ["不是 JSON", '["列表"]', '{"suggestions": "x"}', "{}"])
def test_parser_rejects_malformed_output(bad):
    with pytest.raises(HTTPException) as info:
        parse(bad)
    assert info.value.status_code == 502
