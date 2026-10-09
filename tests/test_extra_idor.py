"""跨用户资源访问隔离：A 用户的错题 / 题目 / 小组对 B 用户一律不可见。"""
import pytest

import main
from db import connect
from test_app import client, new_problem, register


@pytest.fixture
def shared_resources(client):
    alice = register(client, "alice")
    problem_id = client.post(
        "/api/problems",
        json={
            "title": "爱丽丝的题", "zone": "算法", "language": "Python",
            "code": "pass\n", "thinking": "思考。", "mistakes": ["爱丽丝的易错点"],
        },
    ).json()["id"]
    mistake_id = new_problem(client)[0]
    # 直接插一条变体，省掉 AI mock。
    with connect(write=True) as conn:
        variant_id = conn.execute(
            "INSERT INTO variants(mistake_id, description, model, created_at, expected_answer) "
            "VALUES (?, '变体描述', 'mock', ?, '1')",
            (mistake_id, main.utc_now()),
        ).lastrowid
    group = client.post("/api/groups", json={"name": "爱丽丝的小组"}).json()
    # 切到 bob：同一 client 再注册一次，会话变成 bob。
    bob = register(client, "bob")
    return {
        "alice_id": alice["id"],
        "bob_id": bob["id"],
        "problem_id": problem_id,
        "mistake_id": mistake_id,
        "variant_id": variant_id,
        "group_id": group["id"],
    }


def test_cannot_read_other_users_mistake(client, shared_resources):
    response = client.get(f"/api/mistakes/{shared_resources['mistake_id']}")
    assert response.status_code == 404


def test_cannot_edit_other_users_mistake(client, shared_resources):
    response = client.put(
        f"/api/mistakes/{shared_resources['mistake_id']}",
        json={"description": "被篡改", "version": 0},
    )
    assert response.status_code == 404


def test_cannot_delete_other_users_mistake(client, shared_resources):
    response = client.delete(f"/api/mistakes/{shared_resources['mistake_id']}")
    assert response.status_code == 404


def test_cannot_review_other_users_mistake(client, shared_resources):
    response = client.post(
        f"/api/mistakes/{shared_resources['mistake_id']}/review",
        json={"quality": 0, "version": 0},
    )
    assert response.status_code == 404


def test_cannot_preview_other_users_mistake(client, shared_resources):
    response = client.get(f"/api/mistakes/{shared_resources['mistake_id']}/preview")
    assert response.status_code == 404


def test_cannot_undo_other_users_mistake(client, shared_resources):
    response = client.post(
        f"/api/mistakes/{shared_resources['mistake_id']}/review/undo",
        json={"version": 0},
    )
    assert response.status_code == 404


def test_cannot_snooze_other_users_mistake(client, shared_resources):
    response = client.post(
        f"/api/mistakes/{shared_resources['mistake_id']}/snooze",
        json={"version": 0, "days": 1},
    )
    assert response.status_code == 404


def test_cannot_suspend_other_users_mistake(client, shared_resources):
    response = client.post(
        f"/api/mistakes/{shared_resources['mistake_id']}/suspend",
        json={"version": 0},
    )
    assert response.status_code == 404


def test_cannot_read_other_users_scratch(client, shared_resources):
    response = client.get(f"/api/mistakes/{shared_resources['mistake_id']}/scratch")
    assert response.status_code == 404


def test_cannot_write_other_users_scratch(client, shared_resources):
    response = client.put(
        f"/api/mistakes/{shared_resources['mistake_id']}/scratch",
        json={"code": "x", "fixed": "", "table": None, "version": 0},
    )
    assert response.status_code == 404


def test_cannot_set_other_users_tags(client, shared_resources):
    response = client.put(
        f"/api/mistakes/{shared_resources['mistake_id']}/tags",
        json={"tags": ["入侵"]},
    )
    assert response.status_code == 404


def test_cannot_set_other_users_reason(client, shared_resources):
    response = client.post(
        f"/api/mistakes/{shared_resources['mistake_id']}/reason",
        json={"description": "被改", "tags": [], "version": 0},
    )
    assert response.status_code == 404


def test_cannot_generate_variants_on_other_users_mistake(client, shared_resources):
    response = client.post(f"/api/mistakes/{shared_resources['mistake_id']}/variants")
    assert response.status_code == 404


def test_cannot_duck_chat_on_other_users_mistake(client, shared_resources):
    response = client.post(
        f"/api/mistakes/{shared_resources['mistake_id']}/duck",
        json={"turns": [{"role": "user", "text": "你好"}], "finish": False},
    )
    assert response.status_code == 404


def test_cannot_answer_other_users_variant(client, shared_resources):
    response = client.put(
        f"/api/variants/{shared_resources['variant_id']}/result",
        json={"result": "solved", "answer_code": "", "answer": "1"},
    )
    assert response.status_code == 404


def test_cannot_edit_other_users_problem(client, shared_resources):
    response = client.put(
        f"/api/problems/{shared_resources['problem_id']}",
        json={
            "title": "被改", "zone": "算法", "language": "Python",
            "code": "pass\n", "thinking": "被改",
        },
    )
    assert response.status_code == 404


def test_cannot_delete_other_users_problem(client, shared_resources):
    response = client.delete(f"/api/problems/{shared_resources['problem_id']}")
    assert response.status_code == 404


def test_cannot_view_other_users_group(client, shared_resources):
    response = client.get(f"/api/groups/{shared_resources['group_id']}")
    assert response.status_code == 404


def test_cannot_delete_other_users_group(client, shared_resources):
    response = client.delete(f"/api/groups/{shared_resources['group_id']}")
    assert response.status_code == 404


def test_cannot_leave_other_users_group(client, shared_resources):
    response = client.post(f"/api/groups/{shared_resources['group_id']}/leave")
    assert response.status_code == 404


def test_cannot_remove_member_from_other_users_group(client, shared_resources):
    response = client.delete(
        f"/api/groups/{shared_resources['group_id']}/members/{shared_resources['alice_id']}"
    )
    assert response.status_code == 404


def test_alice_data_intact_after_bob_attempts(client, shared_resources):
    # bob 的所有越权尝试都不应改动数据。
    with connect() as conn:
        mistake = conn.execute(
            "SELECT description, version FROM mistakes WHERE id = ?",
            (shared_resources["mistake_id"],),
        ).fetchone()
        assert mistake["description"] == "循环结束条件漏掉 left == right。"
        assert mistake["version"] == 0
