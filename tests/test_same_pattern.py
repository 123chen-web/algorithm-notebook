"""同套路：GET /api/mistakes/{id}/same-pattern 列出这道题标过的套路下，你还错过的别的题。"""
from test_app import client, register  # noqa: F401


def record(client, title, patterns=(), mistakes=("边界没想清",)):
    response = client.post("/api/problems", json={
        "title": title, "zone": "算法", "language": "Python", "code": "pass",
        "thinking": "想当然", "mistakes": list(mistakes), "pattern_ids": list(patterns),
    })
    assert response.status_code == 201, response.text
    return response.json()["mistake_ids"]


def same_pattern(client, mistake_id):
    response = client.get(f"/api/mistakes/{mistake_id}/same-pattern")
    assert response.status_code == 200, response.text
    return response.json()["groups"]


def test_lists_other_problems_in_the_same_pattern_once_each_newest_first(client):
    register(client)
    old_first, old_second = record(client, "搜索插入位置", ["binary-search-basic"], ("左边界", "右边界"))
    newer = record(client, "二分查找", ["binary-search-basic"])[0]
    record(client, "两数之和", ["two-pointers-opposite"])
    now = record(client, "x 的平方根", ["binary-search-basic"])[0]

    groups = same_pattern(client, now)
    assert len(groups) == 1
    group = groups[0]
    assert group["pattern_id"] == "binary-search-basic" and "二分" in group["title"]
    assert group["total"] == 2
    assert [(item["id"], item["title"]) for item in group["items"]] == [(newer, "二分查找"), (old_second, "搜索插入位置")]
    assert group["items"][1]["description"] == "右边界", "同一道旧题取最近那条易错点"
    assert old_first not in [item["id"] for item in group["items"]]


def test_no_patterns_or_no_peers_gives_nothing(client):
    register(client)
    lone = record(client, "没标套路")[0]
    assert same_pattern(client, lone) == []
    only = record(client, "独苗", ["union-find"])[0]
    assert same_pattern(client, only) == []


def test_at_most_four_listed_with_the_total(client):
    register(client)
    for index in range(6):
        record(client, f"二分 {index}", ["binary-search-basic"])
    now = record(client, "当前", ["binary-search-basic"])[0]
    group = same_pattern(client, now)[0]
    assert group["total"] == 6 and len(group["items"]) == 4


def test_other_users_problems_never_show_and_their_mistake_is_404(client):
    register(client)
    mine = record(client, "我的二分", ["binary-search-basic"])[0]
    client.post("/api/auth/logout")
    register(client, username="bob")
    theirs = record(client, "鲍勃的二分", ["binary-search-basic"])[0]
    assert same_pattern(client, theirs) == []
    assert client.get(f"/api/mistakes/{mine}/same-pattern").status_code == 404
    client.post("/api/auth/logout")
    assert client.get(f"/api/mistakes/{theirs}/same-pattern").status_code == 401
