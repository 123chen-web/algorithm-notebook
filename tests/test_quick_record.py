"""速记模式（POST /api/problems quick=true）的后端测试。"""
from datetime import date

import main
from test_app import client, new_problem, register


def test_quick_create_minimal_fills_placeholders(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={"title": "速记一题", "zone": "算法", "language": "Python", "quick": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["mistake_ids"]) == 1
    mistake_id = body["mistake_ids"][0]

    response = client.get(f"/api/mistakes/{mistake_id}")
    assert response.status_code == 200
    item = response.json()
    assert item["description"] == main.QUICK_MISTAKE_PLACEHOLDER
    assert item["pending_reason"] is True
    assert item["code"] == main.QUICK_CODE_PLACEHOLDER
    assert item["thinking"] == main.QUICK_THINKING_PLACEHOLDER
    assert item["due_date"] == "2026-09-19"


def test_quick_placeholders_are_constants(client):
    assert main.QUICK_CODE_PLACEHOLDER == "（速记：代码待补）"
    assert main.QUICK_THINKING_PLACEHOLDER == "（速记：思路待补）"
    assert main.QUICK_MISTAKE_PLACEHOLDER == "（待补：为什么错）"


def test_quick_with_user_mistakes_not_pending(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "速记但给了原因",
            "zone": "算法",
            "language": "Python",
            "quick": True,
            "mistakes": ["这条是我自己写的。"],
        },
    )
    assert response.status_code == 201
    mistake_id = response.json()["mistake_ids"][0]
    item = client.get(f"/api/mistakes/{mistake_id}").json()
    assert item["pending_reason"] is False
    assert item["description"] == "这条是我自己写的。"


def test_quick_false_behavior_unchanged(client):
    register(client)
    # 不给 mistakes：仍 422。
    response = client.post(
        "/api/problems",
        json={"title": "普通", "zone": "算法", "language": "Python", "quick": False},
    )
    assert response.status_code == 422
    # 省略 quick：同样 422。
    response = client.post(
        "/api/problems",
        json={"title": "普通", "zone": "算法", "language": "Python"},
    )
    assert response.status_code == 422
    # 正常创建：pending_reason 为 False。
    new_problem(client)
    items = client.get("/api/mistakes", params={"due_only": False}).json()["items"]
    assert len(items) == 2
    assert all(item["pending_reason"] is False for item in items)


def test_quick_missing_title_or_zone_still_422(client):
    register(client)
    assert client.post("/api/problems", json={"zone": "算法", "language": "Python", "quick": True}).status_code == 422
    assert client.post("/api/problems", json={"title": "无分区", "quick": True}).status_code == 422
    assert client.post("/api/problems", json={"title": "错分区", "zone": "不存在", "quick": True}).status_code == 422


def test_quick_code_zone_language_rule_unchanged(client):
    register(client)
    # 算法分区仍要求 language。
    response = client.post(
        "/api/problems", json={"title": "缺语言", "zone": "算法", "quick": True}
    )
    assert response.status_code == 422
    # 数学分区可留空。
    response = client.post(
        "/api/problems", json={"title": "数学速记", "zone": "高等数学", "quick": True}
    )
    assert response.status_code == 201


def test_quick_empty_string_code_thinking_get_placeholders(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "空白占位",
            "zone": "算法",
            "language": "Python",
            "code": "   ",
            "thinking": "",
            "quick": True,
        },
    )
    assert response.status_code == 201
    item = client.get(f"/api/mistakes/{response.json()['mistake_ids'][0]}").json()
    assert item["code"] == main.QUICK_CODE_PLACEHOLDER
    assert item["thinking"] == main.QUICK_THINKING_PLACEHOLDER


def test_quick_trial_account_allowed(client):
    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    response = client.post(
        "/api/problems",
        json={"title": "体验速记", "zone": "算法", "language": "Python", "quick": True},
    )
    assert response.status_code == 201
    item = client.get(f"/api/mistakes/{response.json()['mistake_ids'][0]}").json()
    assert item["pending_reason"] is True


def test_quick_due_date_and_overview_count(client):
    register(client)
    today = date(2026, 9, 19).isoformat()
    client.post(
        "/api/problems",
        json={"title": "速记A", "zone": "算法", "language": "Python", "quick": True},
    )
    client.post(
        "/api/problems",
        json={"title": "速记B", "zone": "算法", "language": "Python", "quick": True},
    )
    items = client.get("/api/mistakes", params={"due_only": False}).json()["items"]
    assert {item["due_date"] for item in items} == {today}
    overview = client.get("/api/overview").json()
    assert overview["pending_reason_count"] == 2


def test_pending_reason_filter(client):
    register(client)
    new_problem(client)
    client.post(
        "/api/problems",
        json={"title": "速记", "zone": "算法", "language": "Python", "quick": True},
    )
    pending = client.get(
        "/api/mistakes", params={"due_only": False, "pending_reason": 1}
    ).json()["items"]
    done = client.get(
        "/api/mistakes", params={"due_only": False, "pending_reason": 0}
    ).json()["items"]
    assert len(pending) == 1
    assert len(done) == 2
    assert all(item["pending_reason"] is True for item in pending)
    assert all(item["pending_reason"] is False for item in done)
    # 非法值 400。
    assert client.get("/api/mistakes", params={"pending_reason": 2}).status_code == 400
    assert client.get("/api/mistakes", params={"pending_reason": -1}).status_code == 400


def test_pending_reason_in_review_queue(client):
    register(client)
    client.post(
        "/api/problems",
        json={"title": "速记", "zone": "算法", "language": "Python", "quick": True},
    )
    queue = client.get("/api/review/queue").json()
    assert len(queue["items"]) == 1
    assert queue["items"][0]["pending_reason"] is True


def test_export_json_includes_pending_reason(client):
    register(client)
    new_problem(client)
    client.post(
        "/api/problems",
        json={"title": "速记", "zone": "算法", "language": "Python", "quick": True},
    )
    problems = client.get("/api/export").json()["problems"]
    assert len(problems) == 2
    by_title = {p["title"]: p for p in problems}
    assert by_title["二分查找"]["mistakes"][0]["pending_reason"] is False
    assert by_title["速记"]["mistakes"][0]["pending_reason"] is True


def test_anki_export_masks_pending_reason(client):
    register(client)
    client.post(
        "/api/problems",
        json={"title": "速记", "zone": "算法", "language": "Python", "quick": True},
    )
    text = client.get("/api/export/anki").text
    assert "（待补）" in text
    assert main.QUICK_MISTAKE_PLACEHOLDER not in text
