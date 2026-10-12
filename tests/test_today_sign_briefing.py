"""今日一签（宜忌）与今日简报：数据来自用户自己的记录 / 服务器每日缓存，不联网、不调用 AI。"""
import json
from datetime import date

import pytest

import digest
import main
import today_sign
from test_app import client, register


def body(**extra):
    data = {"title": "二分查找", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "我的思路", "mistakes": ["漏了边界"]}
    data.update(extra)
    return data


def test_sign_is_stable_per_user_and_day_and_falls_back_to_tips(client):
    register(client)
    first = client.get("/api/today-sign").json()
    again = client.get("/api/today-sign").json()
    assert first == again
    assert len(first["yi"]) == 2 and len(first["ji"]) == 2
    assert all(item["source"] == "tip" for item in first["yi"] + first["ji"])
    assert first["quote"] and first["note"]
    assert first["weekday"].startswith("周")


def test_sign_uses_the_users_own_data(client):
    register(client)
    pid = client.post("/api/problems", json=body(pattern_ids=["binary-search-basic"])).json()["id"]
    mid = client.get("/api/mistakes?due_only=false").json()["items"][0]["id"]
    client.put(f"/api/mistakes/{mid}/tags", json={"tags": ["boundary", "boundary"]})
    with main.connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = '2020-01-01' WHERE problem_id = ?", (pid,))
    sign = client.get("/api/today-sign").json()
    assert sign["yi"][0]["source"] == "data" and "二分查找" in sign["yi"][0]["text"]
    assert "1 条易错点今天到期" in sign["yi"][0]["detail"]
    assert sign["ji"][0]["source"] == "data" and "边界" in sign["ji"][0]["detail"]


def test_sign_requires_login(client):
    assert client.get("/api/today-sign").status_code == 401


def test_today_sign_is_deterministic_and_never_empty():
    day = date(2026, 10, 12)
    assert today_sign._seed(1, day) == today_sign._seed(1, day)
    assert today_sign._seed(1, day) != today_sign._seed(2, day)
    assert len(today_sign._pick(today_sign.GENERIC_YI, 5, 2)) == 2


def write_cache(tmp_path, monkeypatch, day):
    monkeypatch.setattr(digest, "_data_dir", lambda: tmp_path)
    papers = [{"title": f"Paper {i}", "summary": "abs", "url": f"https://arxiv.org/abs/{i}"} for i in range(3)]
    news = [{"title": f"News {i}", "summary": "", "url": f"https://example.com/{i}", "score": 100 - i} for i in range(4)]
    digest._number(papers, news)
    summaries = {str(i): f"一句话摘要{i}" for i in range(1, 8)}
    digest.save_cache(day, papers, news, summaries)


def test_briefing_reads_the_cache_and_never_goes_online(client, tmp_path, monkeypatch):
    register(client)
    assert client.get("/api/briefing/today").json() == {"date": None, "items": []}
    day = main.utc_now()[:10]
    write_cache(tmp_path, monkeypatch, day)
    monkeypatch.setattr(digest, "_http_get", lambda *a, **k: pytest.fail("请求时不能联网"))
    data = client.get("/api/briefing/today").json()
    assert data["date"] == day
    assert [item["source"] for item in data["items"]] == ["arXiv", "arXiv", "Hacker News", "Hacker News", "Hacker News"]
    assert data["items"][0]["summary"] == "一句话摘要1" and data["items"][2]["summary"] == "一句话摘要4"
    assert all(item["url"].startswith("https://") for item in data["items"])


def test_briefing_falls_back_to_a_recent_older_cache(client, tmp_path, monkeypatch):
    register(client)
    from datetime import timedelta
    older = (date.fromisoformat(main.utc_now()[:10]) - timedelta(days=1)).isoformat()
    write_cache(tmp_path, monkeypatch, older)
    assert client.get("/api/briefing/today").json()["date"] == older


def test_cache_only_flag_fetches_and_stores_without_sending(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(digest, "_data_dir", lambda: tmp_path)
    called = {}

    def fake_bundle(day, getter, sleep, force=False):
        called["day"] = day
        return {"papers": [1, 2], "news": [3], "summaries": {}, "items": []}

    monkeypatch.setattr(digest, "get_bundle", fake_bundle)
    assert digest.main(["--cache-only"]) == 0
    assert called["day"] == digest._cache_day()
    assert "论文 2 篇，新闻 1 条" in capsys.readouterr().out


def test_summary_survives_a_json_fence_and_reports_why_when_it_fails(monkeypatch, capsys):
    items = [{"id": 1, "title": "t", "summary": "s", "kind": "paper", "url": "https://a.com/1"}]

    class Msg:
        content = "```json\n{\"summaries\":[{\"id\":1,\"text\":\"一句话\"}]}\n```"

    class Choice:
        message = Msg()
        finish_reason = "stop"

    class Resp:
        choices = [Choice()]

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    assert kwargs["max_tokens"] >= 8000, "思考型模型需要足够的 token 余量"
                    return Resp()

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr(digest, "OpenAI", FakeClient)
    assert digest.summarize_items(items) == {1: "一句话"}

    Msg.content = ""
    Choice.finish_reason = "length"
    assert digest.summarize_items(items) == {}
    assert "finish_reason=length" in capsys.readouterr().out
