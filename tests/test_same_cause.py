"""老毛病：GET /api/mistakes/{id}/same-cause 列出同一个错因下、别的题里记过的易错点。"""
from test_app import client, register  # noqa: F401


def record(client, title, mistakes=("循环条件写错。",)):
    response = client.post("/api/problems", json={
        "title": title, "zone": "算法", "language": "Python", "code": "pass",
        "thinking": "想当然了", "mistakes": list(mistakes),
    })
    assert response.status_code == 201, response.text
    return response.json()["mistake_ids"]


def tag(client, mistake_id, *tags):
    response = client.put(f"/api/mistakes/{mistake_id}/tags", json={"tags": list(tags)})
    assert response.status_code == 200, response.text


def same_cause(client, mistake_id):
    response = client.get(f"/api/mistakes/{mistake_id}/same-cause")
    assert response.status_code == 200, response.text
    return response.json()["groups"]


def test_lists_other_problems_with_the_same_cause_newest_first(client):
    register(client)
    old = record(client, "二分查找")[0]
    newer = record(client, "搜索插入位置")[0]
    unrelated = record(client, "两数之和")[0]
    now_first, now_second = record(client, "x 的平方根", ("边界没想清。", "除法写成整除。"))
    tag(client, old, "boundary")
    tag(client, newer, "boundary", "impl")
    tag(client, unrelated, "misread")
    tag(client, now_second, "boundary")  # 同一道题里的另一条不算“老毛病”
    tag(client, now_first, "boundary", "misread", "other")

    groups = same_cause(client, now_first)
    by_tag = {group["tag"]: group for group in groups}
    assert set(by_tag) == {"boundary", "misread"}, "“其他”太笼统不算；没有同伴的错因不出现"
    assert [item["title"] for item in by_tag["boundary"]["items"]] == ["搜索插入位置", "二分查找"]
    assert by_tag["boundary"]["total"] == 2
    assert by_tag["misread"]["items"][0]["id"] == unrelated
    assert by_tag["boundary"]["items"][0]["description"] == "循环条件写错。"


def test_at_most_three_per_cause_with_the_total(client):
    register(client)
    ids = [record(client, f"题 {index}")[0] for index in range(5)]
    for mistake_id in ids:
        tag(client, mistake_id, "careless")
    groups = same_cause(client, ids[0])
    assert len(groups[0]["items"]) == 3
    assert groups[0]["total"] == 4


def test_no_tags_or_no_peers_gives_empty_groups(client):
    register(client)
    lone = record(client, "孤零零")[0]
    assert same_cause(client, lone) == []
    tag(client, lone, "concept")
    assert same_cause(client, lone) == []


def test_other_users_data_never_shows_and_their_mistake_is_404(client):
    register(client)
    mine = record(client, "我的题")[0]
    tag(client, mine, "boundary")
    client.post("/api/auth/logout")
    register(client, username="bob")
    theirs = record(client, "鲍勃的题")[0]
    tag(client, theirs, "boundary")
    assert same_cause(client, theirs) == []
    assert client.get(f"/api/mistakes/{mine}/same-cause").status_code == 404
    client.post("/api/auth/logout")
    assert client.get(f"/api/mistakes/{theirs}/same-cause").status_code == 401


def test_an_old_problem_with_two_matching_mistakes_is_listed_once(client):
    register(client)
    first, second = record(client, "旧题", ("左边界错。", "右边界错。"))
    tag(client, first, "boundary")
    tag(client, second, "boundary")
    now = record(client, "新题")[0]
    tag(client, now, "boundary")
    group = same_cause(client, now)[0]
    assert group["total"] == 1, "按题算，不按易错点条数算"
    assert [(item["id"], item["description"]) for item in group["items"]] == [(second, "右边界错。")]


def test_limit_lets_the_note_draft_take_up_to_twenty(client):
    register(client)
    ids = [record(client, f"题 {index}")[0] for index in range(6)]
    for mistake_id in ids:
        tag(client, mistake_id, "careless")
    response = client.get(f"/api/mistakes/{ids[0]}/same-cause?limit=20")
    group = response.json()["groups"][0]
    assert len(group["items"]) == 5 and group["total"] == 5
    assert len(client.get(f"/api/mistakes/{ids[0]}/same-cause?limit=999").json()["groups"][0]["items"]) == 5
    assert len(client.get(f"/api/mistakes/{ids[0]}/same-cause?limit=0").json()["groups"][0]["items"]) == 1, "小于 1 按 1"
