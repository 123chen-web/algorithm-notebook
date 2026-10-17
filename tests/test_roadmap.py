"""学习路线：套路进度、例题勾选、题目与套路的关联。

覆盖：登录与越权、pattern_id / example_key 白名单、状态三态与清空、限流与 CSRF、
题目的 pattern_ids（≤3、去重、编辑时"不传保持不变 / 传空清空"）、注销清理、导出、
迁移幂等。不调用 AI、不联网。
"""

import json

import pytest
from fastapi.testclient import TestClient

import ai
import db
import main
from db import connect
from test_app import client, new_problem, register
from core import config
from core import ratelimit
import pattern_match


ROADMAP = "/api/roadmap"
PROBLEMS = "/api/problems"
# 取自 static/data/patterns.json（服务端白名单），不是随便编的字符串。
PATTERN_A = "binary-search-basic"
PATTERN_B = "sliding-window"
PATTERN_C = "prefix-sum"
PATTERN_D = "union-find"


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("学习路线是纯读写自己的进度，不能调用 AI")

    for name in ("generate", "analyze_weaknesses", "recognize_photo"):
        monkeypatch.setattr(ai, name, unexpected_call)


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    ratelimit.reset_rate_limits()
    yield
    ratelimit.reset_rate_limits()


def example_key(pattern_id):
    keys = sorted(pattern_match.pattern_example_keys(pattern_id))
    assert keys, f"{pattern_id} 应该有例题"
    return keys[0]


def get_roadmap(client):
    response = client.get(ROADMAP)
    assert response.status_code == 200, response.text
    return response.json()


def put_status(client, pattern_id, status):
    return client.put(f"{ROADMAP}/{pattern_id}", json={"status": status})


def problem_body(**overrides):
    body = {
        "title": "二分查找",
        "zone": "算法",
        "language": "Python",
        "code": "def search(a, t):\n    return -1\n",
        "thinking": "结束条件没想清楚。",
        "mistakes": ["漏掉 left == right 的情况。"],
    }
    body.update(overrides)
    return body


def edit_body(**overrides):
    """编辑题目用的请求体：ProblemEdit 不收 mistakes。"""
    body = {key: value for key, value in problem_body().items() if key != "mistakes"}
    body.update(overrides)
    return body


def pattern_ids_of(problem_id):
    with connect() as conn:
        return [
            row["pattern_id"]
            for row in conn.execute(
                "SELECT pattern_id FROM problem_patterns WHERE problem_id = ? ORDER BY rowid",
                (problem_id,),
            )
        ]


# ---------- 登录与读取 ----------

def test_requires_login(client):
    assert client.get(ROADMAP).status_code == 401


def test_new_account_has_an_empty_roadmap(client):
    register(client)
    assert get_roadmap(client) == {"progress": {}, "examples_done": {}, "mistake_counts": {}, "notes": {}, "causes": {}}


def test_progress_examples_and_mistake_counts_round_trip(client):
    register(client)
    assert put_status(client, PATTERN_A, "learning").status_code == 200
    key = example_key(PATTERN_A)
    response = client.put(f"{ROADMAP}/{PATTERN_A}/examples/{key}", json={"done": True})
    assert response.status_code == 200, response.text
    new_problem(client)  # 不挂套路，不影响计数

    created = client.post(PROBLEMS, json=problem_body(pattern_ids=[PATTERN_A, PATTERN_B]))
    assert created.status_code == 201, created.text
    problem_id = created.json()["id"]

    data = get_roadmap(client)
    assert data["progress"] == {PATTERN_A: "learning"}
    assert data["examples_done"] == {PATTERN_A: [key]}
    assert data["mistake_counts"] == {PATTERN_A: 1, PATTERN_B: 1}


def test_clearing_a_status_removes_the_row(client):
    register(client)
    put_status(client, PATTERN_A, "mastered")
    assert get_roadmap(client)["progress"] == {PATTERN_A: "mastered"}
    assert put_status(client, PATTERN_A, "none").status_code == 200
    assert get_roadmap(client)["progress"] == {}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pattern_progress").fetchone()[0] == 0


def test_unchecking_an_example_removes_the_row(client):
    register(client)
    key = example_key(PATTERN_A)
    client.put(f"{ROADMAP}/{PATTERN_A}/examples/{key}", json={"done": True})
    assert get_roadmap(client)["examples_done"] == {PATTERN_A: [key]}
    assert client.put(f"{ROADMAP}/{PATTERN_A}/examples/{key}", json={"done": False}).status_code == 200
    assert get_roadmap(client)["examples_done"] == {}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pattern_example_done").fetchone()[0] == 0


# ---------- 白名单 ----------

def test_unknown_pattern_is_rejected(client):
    register(client)
    assert put_status(client, "not-a-real-pattern", "learning").status_code == 422
    assert client.put(
        f"{ROADMAP}/not-a-real-pattern/examples/luogu:P1", json={"done": True}
    ).status_code == 422


def test_example_key_must_belong_to_the_pattern(client):
    register(client)
    # 是白名单里的套路，但这道例题不属于它。
    assert client.put(
        f"{ROADMAP}/{PATTERN_A}/examples/luogu:P9999", json={"done": True}
    ).status_code == 422
    # 换一个套路里真实存在的 key 也不行。
    other_key = example_key(PATTERN_B)
    assert client.put(
        f"{ROADMAP}/{PATTERN_A}/examples/{other_key}", json={"done": True}
    ).status_code == 422


@pytest.mark.parametrize("status", ["", "done", "master", "LEARNING", "learning "])
def test_status_must_be_one_of_the_three(client, status):
    register(client)
    assert put_status(client, PATTERN_A, status).status_code == 422


# ---------- 越权 ----------

def test_one_user_never_sees_another_users_progress(client):
    register(client, "alice")
    put_status(client, PATTERN_A, "mastered")
    client.post("/api/auth/logout")

    register(client, "bob")
    assert get_roadmap(client)["progress"] == {}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pattern_progress").fetchone()[0] == 1


def test_writes_without_the_csrf_header_are_rejected(client):
    register(client, "alice")
    put_status(client, PATTERN_A, "learning")
    with TestClient(main.app) as unsafe:
        assert unsafe.put(f"{ROADMAP}/{PATTERN_A}", json={"status": "mastered"}).status_code == 403
        assert unsafe.get(ROADMAP).status_code == 401


# ---------- 限流 ----------

def test_status_updates_are_rate_limited(client, monkeypatch):
    register(client)
    monkeypatch.setattr(config, "ROADMAP_LIMIT", 2)
    ratelimit.reset_rate_limits()
    assert put_status(client, PATTERN_A, "learning").status_code == 200
    assert put_status(client, PATTERN_B, "learning").status_code == 200
    assert put_status(client, PATTERN_C, "learning").status_code == 429


# ---------- 题目的 pattern_ids ----------

def test_create_problem_stores_up_to_three_unique_patterns(client):
    register(client)
    created = client.post(
        PROBLEMS,
        json=problem_body(pattern_ids=[PATTERN_A, PATTERN_B, PATTERN_A, PATTERN_C]),
    )
    assert created.status_code == 201, created.text
    assert created.json()["pattern_ids"] == [PATTERN_A, PATTERN_B, PATTERN_C]
    assert pattern_ids_of(created.json()["id"]) == [PATTERN_A, PATTERN_B, PATTERN_C]


def test_create_problem_without_pattern_ids_stores_none(client):
    register(client)
    created = client.post(PROBLEMS, json=problem_body())
    assert created.status_code == 201, created.text
    assert created.json()["pattern_ids"] == []


@pytest.mark.parametrize("pattern_ids", [
    ["not-a-real-pattern"],
    [PATTERN_A, PATTERN_B, PATTERN_C, PATTERN_D],
    ["", PATTERN_A],
])
def test_invalid_pattern_ids_are_rejected(client, pattern_ids):
    register(client)
    assert client.post(PROBLEMS, json=problem_body(pattern_ids=pattern_ids)).status_code == 422


def test_edit_keeps_patterns_when_the_field_is_omitted(client):
    register(client)
    created = client.post(PROBLEMS, json=problem_body(pattern_ids=[PATTERN_A]))
    problem_id = created.json()["id"]
    edited = client.put(f"{PROBLEMS}/{problem_id}", json=edit_body(title="改个名字"))
    assert edited.status_code == 200, edited.text
    assert edited.json()["pattern_ids"] == [PATTERN_A]
    assert pattern_ids_of(problem_id) == [PATTERN_A]


def test_edit_with_an_empty_list_clears_patterns(client):
    register(client)
    problem_id = client.post(PROBLEMS, json=problem_body(pattern_ids=[PATTERN_A, PATTERN_B])).json()["id"]
    edited = client.put(f"{PROBLEMS}/{problem_id}", json=edit_body(pattern_ids=[]))
    assert edited.status_code == 200, edited.text
    assert edited.json()["pattern_ids"] == []
    assert pattern_ids_of(problem_id) == []
    assert get_roadmap(client)["mistake_counts"] == {}


def test_edit_rejects_invalid_pattern_ids(client):
    register(client)
    problem_id = client.post(PROBLEMS, json=problem_body()).json()["id"]
    assert client.put(
        f"{PROBLEMS}/{problem_id}", json=edit_body(pattern_ids=["nope"])
    ).status_code == 422


def test_deleting_a_problem_drops_its_pattern_links(client):
    register(client)
    problem_id = client.post(PROBLEMS, json=problem_body(pattern_ids=[PATTERN_A])).json()["id"]
    assert client.delete(f"{PROBLEMS}/{problem_id}").status_code == 200
    assert pattern_ids_of(problem_id) == []
    assert get_roadmap(client)["mistake_counts"] == {}


# ---------- 注销与导出 ----------

def test_deleting_the_account_clears_roadmap_data(client):
    register(client, "alice")
    put_status(client, PATTERN_A, "learning")
    client.put(f"{ROADMAP}/{PATTERN_A}/examples/{example_key(PATTERN_A)}", json={"done": True})
    client.post(PROBLEMS, json=problem_body(pattern_ids=[PATTERN_A, PATTERN_B]))

    response = client.post(
        "/api/me/delete-account", json={"password": "a-test-password-123"}
    )
    assert response.status_code == 200, response.text
    with connect() as conn:
        for table in ("pattern_progress", "pattern_example_done", "problem_patterns"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table


def test_export_carries_the_roadmap_and_per_problem_patterns(client):
    register(client)
    put_status(client, PATTERN_A, "mastered")
    key = example_key(PATTERN_A)
    client.put(f"{ROADMAP}/{PATTERN_A}/examples/{key}", json={"done": True})
    client.post(PROBLEMS, json=problem_body(pattern_ids=[PATTERN_A]))

    response = client.get("/api/export")
    assert response.status_code == 200, response.text
    payload = json.loads(response.content.decode("utf-8"))
    roadmap = payload["roadmap"]
    assert roadmap["progress"] == [
        {"pattern_id": PATTERN_A, "status": "mastered", "updated_at": roadmap["progress"][0]["updated_at"]}
    ]
    assert roadmap["examples_done"] == [
        {"pattern_id": PATTERN_A, "examples": [{"example_key": key, "done_at": roadmap["examples_done"][0]["examples"][0]["done_at"]}]}
    ]
    assert payload["problems"][0]["pattern_ids"] == [PATTERN_A]
    assert all("password" not in json.dumps(payload) for _ in [0])


# ---------- 迁移 ----------

def test_migration_is_idempotent(client):
    register(client)
    put_status(client, PATTERN_A, "learning")
    # 再跑一遍建表语句：不能报错，也不能把已有数据弄丢。
    with connect(write=True) as conn:
        db._apply_roadmap(conn)
        db._apply_roadmap(conn)
    assert get_roadmap(client)["progress"] == {PATTERN_A: "learning"}
    with connect() as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
    assert {"pattern_progress", "pattern_example_done", "problem_patterns"} <= tables


def test_catalog_is_read_from_the_static_data_file(monkeypatch):
    catalog = pattern_match.load_pattern_catalog()
    assert PATTERN_A in catalog and PATTERN_B in catalog
    assert pattern_match.pattern_is_known(PATTERN_A)
    assert not pattern_match.pattern_is_known("nope")
    assert not pattern_match.pattern_is_known(None)
    assert pattern_match.pattern_example_keys(PATTERN_A)
    assert pattern_match.pattern_example_keys("nope") == frozenset()
    # 文件缺失时白名单为空，而不是抛异常打断启动。
    monkeypatch.setattr(config, "PATTERN_DATA_PATH", db.ROOT / "no" / "such" / "file.json")
    assert pattern_match.load_pattern_catalog(db.ROOT / "no" / "such" / "file.json") == {}


def test_normalize_pattern_ids_dedupes_and_caps(monkeypatch):
    assert pattern_match.normalize_pattern_ids(None) is None
    assert pattern_match.normalize_pattern_ids([]) == []
    assert pattern_match.normalize_pattern_ids([PATTERN_A, PATTERN_A, PATTERN_B]) == [PATTERN_A, PATTERN_B]
    with pytest.raises(ValueError):
        pattern_match.normalize_pattern_ids([PATTERN_A, PATTERN_B, PATTERN_C, PATTERN_D])
    with pytest.raises(ValueError):
        pattern_match.normalize_pattern_ids(["nope"])
    with pytest.raises(ValueError):
        pattern_match.normalize_pattern_ids("nope")


def test_roadmap_lists_my_most_common_causes_per_pattern(client):
    register(client)

    def record(title, tags, mistakes=1):
        response = client.post("/api/problems", json={
            "title": title, "zone": "算法", "language": "Python", "code": "pass", "thinking": "想当然",
            "mistakes": ["没想清"] * mistakes, "pattern_ids": ["binary-search-basic"],
        })
        assert response.status_code == 201, response.text
        for mistake_id, tag in zip(response.json()["mistake_ids"], tags):
            assert client.put(f"/api/mistakes/{mistake_id}/tags", json={"tags": [tag]}).status_code == 200

    record("一", ["boundary", "boundary"], mistakes=2)  # 同一道题两条同错因：按题算 1 次
    record("二", ["boundary"])
    record("三", ["misread"])
    record("四", ["other"])
    record("五", ["careless"])
    record("六", ["concept"])
    causes = client.get("/api/roadmap").json()["causes"]["binary-search-basic"]
    assert causes[0] == {"tag": "boundary", "count": 2}
    assert len(causes) == 3, "每个套路最多 3 个"
    assert "other" not in [entry["tag"] for entry in causes], "“其他”太笼统不算"
