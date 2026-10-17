"""“我的对策”：从“我的老毛病：错因”笔记读出对策（countermeasures.py + 两个只读接口）。"""
import countermeasures
from test_app import client, register  # noqa: F401

NOTE = "\n".join([
    "# 我的老毛病：边界/特例",
    "",
    "「边界/特例」这类错，我在 3 道题里都犯过：",
    "",
    "- [[题:二分查找]]：漏了 left == right",
    "",
    "## 我的对策",
    "",
    "- 先写清区间是 \\[lo, hi\\] 还是 \\[lo, hi)",
    "- 代入 n=0、n=1 走一遍",
    "- 下次看到……，先……",
    "",
    "## 别的小节",
    "- 这一行不是对策",
])


def make_note(client, content):
    response = client.post("/api/notes", json={"title": "", "content": content, "tags": []})
    assert response.status_code == 201, response.text
    return response.json()


def record(client, title, tag):
    response = client.post("/api/problems", json={
        "title": title, "zone": "算法", "language": "Python", "code": "pass",
        "thinking": "想当然", "mistakes": ["边界没想清"],
    })
    assert response.status_code == 201, response.text
    mistake_id = response.json()["mistake_ids"][0]
    assert client.put(f"/api/mistakes/{mistake_id}/tags", json={"tags": [tag]}).status_code == 200
    return mistake_id


def test_parse_reads_only_the_countermeasure_section_unescapes_and_skips_the_placeholder():
    label, lines = countermeasures.parse(NOTE)
    assert label == "边界/特例"
    assert lines == ["先写清区间是 [lo, hi] 还是 [lo, hi)", "代入 n=0、n=1 走一遍"]


def test_parse_ignores_other_notes_and_untouched_templates():
    assert countermeasures.parse("# 单调栈\n\n## 我的对策\n- 存下标") is None, "不是老毛病笔记"
    untouched = "# 我的老毛病：粗心/笔误\n\n## 我的对策\n\n- 下次看到……，先……\n"
    assert countermeasures.parse(untouched) is None, "还没写对策"
    escaped = "\\# 我的老毛病：粗心/笔误\n\n\\## 我的对策\n\n\\- 交之前读一遍题\n"
    assert countermeasures.parse(escaped) == ("粗心/笔误", ["交之前读一遍题"])


def test_mistake_countermeasures_follow_its_cause_tags(client):
    register(client)
    note = make_note(client, NOTE)
    make_note(client, "# 我的老毛病：审题/漏条件\n\n## 我的对策\n- 圈出每个数据范围\n")
    boundary = record(client, "二分查找", "boundary")
    careless = record(client, "两数之和", "careless")

    items = client.get(f"/api/mistakes/{boundary}/countermeasures").json()["items"]
    assert [(item["note_id"], item["label"], item["tag"]) for item in items] == [(note["id"], "边界/特例", "boundary")]
    assert items[0]["lines"][0] == "先写清区间是 [lo, hi] 还是 [lo, hi)"
    assert client.get(f"/api/mistakes/{careless}/countermeasures").json()["items"] == []

    everything = client.get("/api/countermeasures").json()["items"]
    assert {item["label"] for item in everything} == {"边界/特例", "审题/漏条件"}


def test_deleted_notes_and_other_users_never_show(client):
    register(client)
    note = make_note(client, NOTE)
    mistake = record(client, "二分查找", "boundary")
    client.post("/api/auth/logout")
    register(client, username="bob")
    assert client.get("/api/countermeasures").json()["items"] == []
    assert client.get(f"/api/mistakes/{mistake}/countermeasures").status_code == 404
    client.post("/api/auth/logout")
    assert client.get("/api/countermeasures").status_code == 401
    assert note["id"]


def test_deleted_note_is_not_used(client):
    register(client)
    note = make_note(client, NOTE)
    assert client.delete(f"/api/notes/{note['id']}").status_code in (200, 204)
    assert client.get("/api/countermeasures").json()["items"] == []
