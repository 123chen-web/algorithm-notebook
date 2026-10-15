"""笔记“串联”接口：提到的笔记、被谁提到、相关套路、相关错题、同标签笔记（routers/note_connections.py）。"""
from test_app import client, new_problem, register  # noqa: F401


def make_note(client, content, title="", tags=()):
    response = client.post("/api/notes", json={"title": title, "content": content, "tags": list(tags)})
    assert response.status_code == 201, response.text
    return response.json()


def connections(client, note_id):
    response = client.get(f"/api/notes/{note_id}/connections")
    assert response.status_code == 200, response.text
    return response.json()


def test_mentions_both_ways_ignore_code_and_self(client):
    register(client)
    stack = make_note(client, "栈里存下标", title="单调栈", tags=["栈"])
    window = make_note(client, "和单调栈很像，但用队列。\n```\n# 并查集 写在代码里不算\n```", title="单调队列", tags=["栈", "队列"])
    make_note(client, "路径压缩", title="并查集")
    data = connections(client, window["id"])
    assert [item["title"] for item in data["note_mentions"]] == ["单调栈"]
    assert data["same_tag"][0]["id"] == stack["id"] and data["same_tag"][0]["shared"] == ["栈"]
    back = connections(client, stack["id"])
    assert [item["title"] for item in back["mentioned_by"]] == ["单调队列"]
    assert "单调栈" in back["mentioned_by"][0]["snippet"]


def test_patterns_and_related_mistakes(client):
    register(client)
    mistake_id = new_problem(client)[0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]
    title = "二分查找"
    note = make_note(client, "二分查找要先想清楚区间是左闭右闭还是左闭右开。")
    data = connections(client, note["id"])
    assert any("二分" in item["title"] for item in data["patterns"])
    assert all(item["term"] for item in data["patterns"])
    assert data["mistakes"] and data["mistakes"][0]["problem_id"] == problem_id, "正文提到题名就能串到这道错题"
    linked = client.post("/api/notes", json={"content": "复盘", "problem_id": problem_id}).json()
    related = connections(client, linked["id"])["mistakes"]
    assert related and related[0]["problem_id"] == problem_id
    assert {"id", "title", "due_date", "tags", "zone"} <= set(related[0])
    assert title is None or related[0]["title"] == title


def test_connections_are_private(client):
    register(client)
    note = make_note(client, "我的笔记")
    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.get(f"/api/notes/{note['id']}/connections").status_code == 404


def test_problem_related_notes_lists_linked_mentioned_and_same_pattern_notes(client):
    register(client)
    mistake_id = new_problem(client)[0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]
    linked = client.post("/api/notes", json={"content": "复盘这道题", "problem_id": problem_id}).json()
    wiki = make_note(client, "见 [[题:二分查找]]", title="索引")
    mention = make_note(client, "今天又在二分查找上栽了", title="周记")
    make_note(client, "和这道题无关", title="并查集")
    data = client.get(f"/api/problems/{problem_id}/related-notes").json()["items"]
    reasons = {item["id"]: item["reason"] for item in data}
    assert reasons[linked["id"]] == "关联了这道题"
    assert reasons[wiki["id"]] == "链接到了这道题"
    assert reasons[mention["id"]] == "提到了这道题"
    assert len(data) == 3, "无关的笔记不出现"
    assert all(item["preview"] is not None for item in data)


def test_problem_related_notes_are_private(client):
    register(client)
    mistake_id = new_problem(client)[0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]
    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.get(f"/api/problems/{problem_id}/related-notes").status_code == 404


def test_problem_related_notes_include_notes_about_the_same_pattern(client):
    register(client)
    response = client.post("/api/problems", json={
        "title": "搜索插入位置", "zone": "算法", "language": "Python", "code": "pass",
        "thinking": "边界没想清", "mistakes": ["右边界写错"], "pattern_ids": ["binary-search-basic"],
    })
    assert response.status_code == 201, response.text
    mistake_id = response.json()["mistake_ids"][0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]
    pattern_note = make_note(client, "二分查找：先想区间开闭", title="二分套路")
    items = client.get(f"/api/problems/{problem_id}/related-notes").json()["items"]
    assert [(item["id"], item["reason"].startswith("同一个套路")) for item in items] == [(pattern_note["id"], True)]


def test_roadmap_lists_my_notes_under_each_pattern(client):
    register(client)
    note = make_note(client, "二分查找：区间开闭先想清楚", title="二分套路")
    make_note(client, "代码里提到 ```二分查找``` 不算", title="无关")
    data = client.get("/api/roadmap").json()
    hits = [pattern_id for pattern_id, items in data["notes"].items() if any(item["id"] == note["id"] for item in items)]
    assert any(pattern_id.startswith("binary-search") for pattern_id in hits)
    assert all(len(items) <= 5 for items in data["notes"].values())
