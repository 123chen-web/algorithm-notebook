"""套路自动归类：本地规则建议 + 接口（建议、待分类箱、归类）。不调用 AI、不联网。"""
import pytest

import main
import pattern_match
from test_app import client, register

ROADMAP = "/api/roadmap"


def ids(suggestions):
    return [item["pattern_id"] for item in suggestions]


def test_a_known_example_link_scores_highest():
    result = pattern_match.suggest(title="某道题", source_url="https://www.luogu.com.cn/problem/P1002")
    assert result and result[0]["score"] == 100
    assert "dp-grid" in ids(result) or "dp-counting" in ids(result)
    assert "经典例题" in result[0]["reason"]


def test_title_keywords_and_aliases_find_the_pattern():
    assert "binary-search-basic" in ids(pattern_match.suggest(title="二分查找的边界"))
    assert any(i.startswith("bfs-") for i in ids(pattern_match.suggest(title="迷宫 BFS 最短路")))
    assert "sliding-window" in ids(pattern_match.suggest(title="最长子串", thinking="用滑动窗口"))


def test_user_tags_count_and_everything_is_capped_at_three():
    result = pattern_match.suggest(title="某题", tags=["背包"])
    assert result and all(item["pattern_id"].startswith("knapsack") for item in result[:1])
    assert len(pattern_match.suggest(title="二分 双指针 前缀和 BFS DFS DP")) <= 3


def test_only_algorithm_problems_get_suggestions_and_nothing_matches_nonsense():
    assert pattern_match.suggest(title="二分查找", zone="高等数学") == []
    assert pattern_match.suggest(title="今天天气不错") == []


def test_already_chosen_patterns_can_be_excluded():
    first = pattern_match.suggest(title="二分查找")[0]["pattern_id"]
    assert first not in ids(pattern_match.suggest(title="二分查找", exclude=[first]))


def body(**extra):
    data = {"title": "迷宫 BFS", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "我的思路", "mistakes": ["漏了边界"]}
    data.update(extra)
    return data


def test_suggest_endpoint(client):
    register(client)
    response = client.post(f"{ROADMAP}/suggest", json={"title": "二分查找", "tags": ["边界"]})
    assert response.status_code == 200
    assert "binary-search-basic" in ids(response.json()["suggestions"])
    assert client.post(f"{ROADMAP}/suggest", json={"title": "x", "unknown": 1}).status_code == 422


def test_inbox_lists_only_unclassified_algorithm_problems_with_suggestions(client):
    register(client)
    a = client.post("/api/problems", json=body(title="二分查找的边界")).json()["id"]
    b = client.post("/api/problems", json=body(title="已分类", pattern_ids=["binary-search-basic"])).json()["id"]
    client.post("/api/problems", json=body(title="线代题", zone="线性代数", language=""))
    inbox = client.get(f"{ROADMAP}/inbox").json()
    assert inbox["total"] == 1
    item = inbox["items"][0]
    assert item["problem_id"] == a and b not in [i["problem_id"] for i in inbox["items"]]
    assert item["snippet"] == "我的思路"
    assert "binary-search-basic" in ids(item["suggestions"])


def test_classify_endpoint_validates_and_checks_ownership(client):
    register(client, "alice")
    pid = client.post("/api/problems", json=body()).json()["id"]
    ok = client.put(f"/api/problems/{pid}/patterns", json={"pattern_ids": ["bfs-grid", "bfs-grid", "dijkstra"]})
    assert ok.status_code == 200 and ok.json()["pattern_ids"] == ["bfs-grid", "dijkstra"]
    assert client.get(f"{ROADMAP}/inbox").json()["total"] == 0
    assert client.put(f"/api/problems/{pid}/patterns", json={"pattern_ids": ["nope"]}).status_code == 422
    assert client.put(f"/api/problems/{pid}/patterns", json={"pattern_ids": []}).json()["pattern_ids"] == []
    assert client.get(f"{ROADMAP}/inbox").json()["total"] == 1
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.put(f"/api/problems/{pid}/patterns", json={"pattern_ids": ["bfs-grid"]}).status_code == 404
