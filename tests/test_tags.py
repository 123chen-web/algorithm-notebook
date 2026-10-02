"""错因标签：贴标签、按标签筛选、归属与级联、数量限制。纯本地逻辑，不调用 AI。"""

import pytest

import ai
from db import connect
from tags import SUGGESTED_TAGS, TAG_MAX_LENGTH, TAGS_PER_MISTAKE, TAGS_PER_USER, normalize_tag, normalize_tags
from test_app import client, new_problem, register


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("标签是纯本地逻辑，不能调用 AI")

    for name in ("generate", "analyze_weaknesses", "recognize_photo"):
        monkeypatch.setattr(ai, name, unexpected_call)


def put_tags(client, mistake_id, tags):
    return client.put(f"/api/mistakes/{mistake_id}/tags", json={"tags": tags})


def listed(client, **params):
    response = client.get("/api/mistakes", params={"due_only": "false", **params})
    assert response.status_code == 200
    return response.json()["items"]


def test_requires_login(client):
    assert client.get("/api/tags").status_code == 401
    assert client.put("/api/mistakes/1/tags", json={"tags": []}).status_code == 401


def test_new_account_has_no_tags_but_gets_suggestions_and_limits(client):
    register(client)
    data = client.get("/api/tags").json()
    assert data["tags"] == []
    assert data["suggestions"] == list(SUGGESTED_TAGS)
    assert data["limits"] == {"per_mistake": TAGS_PER_MISTAKE, "length": TAG_MAX_LENGTH}


def test_tags_are_normalized_deduplicated_and_returned_in_order(client):
    register(client)
    first, second = new_problem(client)
    response = put_tags(client, first, ["  边界 ", "概念  混淆", "Off-By-One", "off-by-one", "", "   ", "边界"])
    assert response.status_code == 200
    assert response.json() == {"tags": ["边界", "概念 混淆", "Off-By-One"]}
    detail = client.get(f"/api/mistakes/{first}").json()
    assert detail["tags"] == ["边界", "概念 混淆", "Off-By-One"]
    items = {item["id"]: item for item in listed(client)}
    assert items[first]["tags"] == ["边界", "概念 混淆", "Off-By-One"]
    assert items[second]["tags"] == []


def test_putting_a_new_set_replaces_the_old_one_and_keeps_the_order_sent(client):
    register(client)
    mistake = new_problem(client)[0]
    put_tags(client, mistake, ["a标签", "b标签", "c标签"])
    assert put_tags(client, mistake, ["c标签", "新标签", "a标签"]).json()["tags"] == ["c标签", "新标签", "a标签"]
    # 顺序就是用户排的顺序，读回来不变。
    assert client.get(f"/api/mistakes/{mistake}").json()["tags"] == ["c标签", "新标签", "a标签"]
    assert put_tags(client, mistake, []).json() == {"tags": []}
    assert client.get(f"/api/mistakes/{mistake}").json()["tags"] == []


def test_editing_tags_never_changes_the_review_version(client):
    register(client)
    mistake = new_problem(client)[0]
    before = client.get(f"/api/mistakes/{mistake}").json()["version"]
    put_tags(client, mistake, ["边界"])
    assert client.get(f"/api/mistakes/{mistake}").json()["version"] == before
    # 带着旧的 version 照样能评分：贴标签不会让评分变成"过期"。
    review = client.post(f"/api/mistakes/{mistake}/review", json={"quality": 4, "version": before})
    assert review.status_code == 200


@pytest.mark.parametrize("tags,fragment", [
    (["字" * (TAG_MAX_LENGTH + 1)], "标签最多"),
    ([f"标签{index}" for index in range(TAGS_PER_MISTAKE + 1)], "最多 8 个"),
])
def test_invalid_tags_are_rejected_with_a_chinese_message(client, tags, fragment):
    register(client)
    mistake = new_problem(client)[0]
    response = put_tags(client, mistake, tags)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


@pytest.mark.parametrize("body", [{"tags": "边界"}, {"tags": [1, 2]}, {}, {"tags": ["a"], "extra": 1}, {"tags": ["x"] * 51}])
def test_malformed_bodies_are_422(client, body):
    register(client)
    mistake = new_problem(client)[0]
    assert client.put(f"/api/mistakes/{mistake}/tags", json=body).status_code == 422


def test_control_characters_are_dropped_and_emoji_are_fine(client):
    register(client)
    mistake = new_problem(client)[0]
    assert put_tags(client, mistake, ["粗\x00心\x07", "🔥 高频"]).json()["tags"] == ["粗心", "🔥 高频"]
    assert normalize_tag("a　\tb\n c") == "a b c"
    assert normalize_tags(["A", "a", "B"]) == ["A", "B"]


def test_distinct_tag_kinds_per_user_are_capped_but_reuse_is_free(client):
    register(client)
    mistakes = []
    for _ in range(8):
        mistakes.extend(new_problem(client))
    assert len(mistakes) >= 16
    kinds = [f"种类{index}" for index in range(TAGS_PER_USER)]
    for position in range(0, TAGS_PER_USER, TAGS_PER_MISTAKE):
        assert put_tags(client, mistakes[position // TAGS_PER_MISTAKE], kinds[position:position + TAGS_PER_MISTAKE]).status_code == 200
    # 已有的种类可以反复用在别的易错点上，不占新名额。
    assert put_tags(client, mistakes[-1], kinds[:TAGS_PER_MISTAKE]).status_code == 200
    too_many = put_tags(client, mistakes[-1], ["全新的一种"])
    assert too_many.status_code == 422
    assert "标签种类太多" in too_many.json()["detail"]
    # 给一条易错点换掉旧标签，腾出名额以后就能加新的。
    assert put_tags(client, mistakes[0], []).status_code == 200
    assert put_tags(client, mistakes[-1], ["全新的一种"]).status_code == 200


def test_counts_are_per_user_sorted_by_count_then_name(client):
    register(client, "alice")
    first, second = new_problem(client)
    third = new_problem(client)[0]
    put_tags(client, first, ["边界", "粗心"])
    put_tags(client, second, ["边界"])
    put_tags(client, third, ["边界", "粗心", "公式"])
    assert client.get("/api/tags").json()["tags"] == [
        {"tag": "边界", "count": 3}, {"tag": "粗心", "count": 2}, {"tag": "公式", "count": 1},
    ]
    client.post("/api/auth/logout")
    register(client, "bob")
    assert client.get("/api/tags").json()["tags"] == []


def test_other_users_mistakes_cannot_be_tagged_or_seen(client):
    register(client, "alice")
    mistake = new_problem(client)[0]
    put_tags(client, mistake, ["私有标签"])
    client.post("/api/auth/logout")
    register(client, "bob")
    assert put_tags(client, mistake, ["偷偷改"]).status_code == 404
    assert client.get(f"/api/mistakes/{mistake}").status_code == 404
    assert listed(client) == []
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"})
    assert client.get(f"/api/mistakes/{mistake}").json()["tags"] == ["私有标签"]


def test_filtering_by_tag_combines_with_zone_due_and_created_date(client):
    register(client)
    algo_a, algo_b = new_problem(client, "算法")
    math_a = new_problem(client, "高等数学")[0]
    put_tags(client, algo_a, ["边界", "粗心"])
    put_tags(client, math_a, ["边界"])
    with connect(write=True) as conn:
        # 钉死录入时间（上海 12:00），筛选才不依赖真实日期。
        conn.execute("UPDATE problems SET created_at = '2026-09-19T04:00:00+00:00' WHERE id = "
                     "(SELECT problem_id FROM mistakes WHERE id = ?)", (algo_a,))
        conn.execute("UPDATE problems SET created_at = '2026-09-10T04:00:00+00:00' WHERE id = "
                     "(SELECT problem_id FROM mistakes WHERE id = ?)", (math_a,))
    assert {item["id"] for item in listed(client, tag="边界")} == {algo_a, math_a}
    assert {item["id"] for item in listed(client, tag="粗心")} == {algo_a}
    assert {item["id"] for item in listed(client, tag="边界", zone="高等数学")} == {math_a}
    assert {item["id"] for item in listed(client, tag="边界", due_only="true")} == {algo_a, math_a}
    assert {item["id"] for item in listed(client, tag="边界", created_on="2026-09-19")} == {algo_a}
    assert {item["id"] for item in listed(client, tag="边界", created_on="2026-09-10")} == {math_a}
    assert {item["id"] for item in listed(client, tag="粗心", created_on="2026-09-10")} == set()
    assert listed(client, tag="不存在") == []
    assert {item["id"] for item in listed(client)} >= {algo_a, algo_b, math_a}
    assert client.get("/api/mistakes", params={"tag": "字" * 41}).status_code == 422


def test_ascii_tags_filter_case_insensitively(client):
    register(client)
    mistake = new_problem(client)[0]
    put_tags(client, mistake, ["Off-By-One"])
    assert [item["id"] for item in listed(client, tag="off-by-one")] == [mistake]
    assert [item["id"] for item in listed(client, tag="OFF-BY-ONE")] == [mistake]


def test_deleting_a_mistake_or_problem_removes_its_tags(client):
    register(client)
    first, second = new_problem(client)
    put_tags(client, first, ["边界"])
    put_tags(client, second, ["边界", "粗心"])
    assert client.delete(f"/api/mistakes/{first}").status_code == 200
    remaining = client.get("/api/tags").json()["tags"]
    assert sorted(item["tag"] for item in remaining) == ["粗心", "边界"]
    assert all(item["count"] == 1 for item in remaining)
    problem_id = client.get(f"/api/mistakes/{second}").json()["problem_id"]
    assert client.delete(f"/api/problems/{problem_id}").status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mistake_tags").fetchone()[0] == 0


def test_trial_accounts_can_tag_their_own_records(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    mistake = new_problem(client)[0]
    assert put_tags(client, mistake, ["边界"]).status_code == 200
    assert client.get("/api/tags").json()["tags"] == [{"tag": "边界", "count": 1}]
