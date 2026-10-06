"""补原因接口（POST /api/mistakes/{id}/reason）的测试。"""
import pytest

import main
from tags import REASON_SUGGEST_TAGS
from test_app import client, new_problem, register


def quick_mistake_id(client):
    response = client.post(
        "/api/problems",
        json={"title": "速记", "zone": "算法", "language": "Python", "quick": True},
    )
    assert response.status_code == 201
    return response.json()["mistake_ids"][0]


def reason_url(mistake_id):
    return f"/api/mistakes/{mistake_id}/reason"


def test_reason_success_updates_all_fields(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    before = client.get(f"/api/mistakes/{mistake_id}").json()
    assert before["pending_reason"] is True

    # 先打两个标签，确认补原因时是整体替换。
    client.put(
        f"/api/mistakes/{mistake_id}/tags",
        json={"tags": ["旧标签一", "旧标签二"]},
    )
    mid = client.get(f"/api/mistakes/{mistake_id}").json()

    response = client.post(
        reason_url(mistake_id),
        json={
            "description": "循环结束条件漏掉 left == right。",
            "tags": ["边界", "粗心"],
            "version": mid["version"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["description"] == "循环结束条件漏掉 left == right。"
    assert body["tags"] == ["边界", "粗心"]
    assert body["pending_reason"] is False
    assert body["version"] == mid["version"] + 1

    after = client.get(f"/api/mistakes/{mistake_id}").json()
    assert after["description"] == "循环结束条件漏掉 left == right。"
    assert after["tags"] == ["边界", "粗心"]
    assert after["pending_reason"] is False
    # 调度字段不动。
    assert after["due_date"] == mid["due_date"]
    assert after["repetitions"] == mid["repetitions"]
    assert after["interval_days"] == mid["interval_days"]
    assert after["ease_factor"] == mid["ease_factor"]


def test_reason_version_conflict_409(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    response = client.post(
        reason_url(mistake_id),
        json={"description": "旧版本提交。", "tags": [], "version": version + 99},
    )
    assert response.status_code == 409


def test_reason_other_users_record_404(client):
    register(client, username="alice")
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]

    # 换一个用户登录（同一个 TestClient 会带上旧 cookie：先登出再注册）。
    client.post("/api/auth/logout")
    register(client, username="bob", email="bob@example.com")
    response = client.post(
        reason_url(mistake_id),
        json={"description": "想改别人的。", "tags": [], "version": version},
    )
    assert response.status_code == 404


def test_reason_too_many_tags_rejected(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    response = client.post(
        reason_url(mistake_id),
        json={
            "description": "标签太多。",
            "tags": ["一", "二", "三", "四"],
            "version": version,
        },
    )
    assert response.status_code == 422
    # 记录没被改动。
    after = client.get(f"/api/mistakes/{mistake_id}").json()
    assert after["pending_reason"] is True
    assert after["version"] == version


def test_reason_tag_normalization_still_applies(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    # 超长标签仍被 normalize_tags 拒绝。
    response = client.post(
        reason_url(mistake_id),
        json={"description": "x", "tags": ["超长" * 20], "version": version},
    )
    assert response.status_code == 422


def test_reason_description_validation_same_as_mistake_text(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    # MistakeText 允许留空（错因描述可选），与创建时一致。
    response = client.post(
        reason_url(mistake_id),
        json={"description": "   ", "tags": [], "version": version},
    )
    assert response.status_code == 200
    # 超长描述：MistakeText 最大长度 2000。
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    response = client.post(
        reason_url(mistake_id),
        json={"description": "x" * 2001, "tags": [], "version": version},
    )
    assert response.status_code == 422


def test_reason_allowed_on_suspended_mistake(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    suspend = client.post(
        f"/api/mistakes/{mistake_id}/suspend", json={"version": version}
    )
    assert suspend.status_code == 200

    response = client.post(
        reason_url(mistake_id),
        json={"description": "暂停中也能补。", "tags": ["没思路"], "version": version + 1},
    )
    assert response.status_code == 200
    assert response.json()["pending_reason"] is False


def test_reason_repeated_submission_still_bumps_version(client):
    register(client)
    mistake_id = quick_mistake_id(client)
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    payload = {"description": "同样内容。", "tags": ["边界"], "version": version}
    first = client.post(reason_url(mistake_id), json=payload)
    assert first.status_code == 200
    assert first.json()["version"] == version + 1
    # 再次提交同样内容：与现有编辑一致，version 仍递增。
    second = client.post(
        reason_url(mistake_id),
        json={**payload, "version": version + 1},
    )
    assert second.status_code == 200
    assert second.json()["version"] == version + 2
    assert second.json()["description"] == "同样内容。"
    assert second.json()["tags"] == ["边界"]


def test_reason_nonexistent_mistake_404(client):
    register(client)
    response = client.post(
        reason_url(999999),
        json={"description": "x", "tags": [], "version": 0},
    )
    assert response.status_code == 404


def test_reason_trial_account_allowed(client):
    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    response = client.post(
        "/api/problems",
        json={"title": "体验速记", "zone": "算法", "language": "Python", "quick": True},
    )
    mistake_id = response.json()["mistake_ids"][0]
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    response = client.post(
        reason_url(mistake_id),
        json={"description": "体验账号补原因。", "tags": [], "version": version},
    )
    assert response.status_code == 200
    assert response.json()["pending_reason"] is False


def test_tags_suggest_order_and_dedup(client):
    register(client)
    new_problem(client)
    items = client.get("/api/mistakes", params={"due_only": False}).json()["items"]
    # 给标签制造不同的使用次数。
    client.put(
        f"/api/mistakes/{items[0]['id']}/tags",
        json={"tags": ["自定一", "自定二", "边界"]},
    )
    client.put(
        f"/api/mistakes/{items[1]['id']}/tags",
        json={"tags": ["自定一"]},
    )

    body = client.get("/api/tags/suggest").json()
    mine = body["mine"]
    assert len(mine) <= 12
    counts = [item["count"] for item in mine]
    assert counts == sorted(counts, reverse=True)
    assert mine[0] == {"tag": "自定一", "count": 2}
    mine_tags = {item["tag"] for item in mine}
    # mine 里已有的不在 builtin 重复。
    assert "边界" not in body["builtin"]
    assert "自定一" not in body["builtin"]
    assert "自定二" not in body["builtin"]
    # builtin 保持内置顺序。
    assert body["builtin"] == [
        tag for tag in REASON_SUGGEST_TAGS if tag not in mine_tags
    ]


def test_tags_suggest_builtin_when_no_tags(client):
    register(client)
    body = client.get("/api/tags/suggest").json()
    assert body["mine"] == []
    assert body["builtin"] == list(REASON_SUGGEST_TAGS)
