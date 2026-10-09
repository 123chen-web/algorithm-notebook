"""扩展 v2 的后端配套接口：新增来源、导入带错因、按来源查重、追加错因、今日待复习条数。"""
import pytest

import main
from db import connect
from test_plugin_api import auth_headers, client, import_payload, import_problem, issue_token, register  # noqa: F401


def luogu_payload(**overrides):
    payload = {
        "source": "luogu",
        "source_url": "https://www.luogu.com.cn/problem/P1001",
        "title": "P1001 A+B Problem",
        "content": "输入两个整数，输出它们的和。",
        "zone": "算法",
    }
    payload.update(overrides)
    return payload


def post_import(client, token, payload):
    return client.post("/api/problems/import-from-extension", json=payload, headers=auth_headers(token))


def by_source(client, token, source, source_id):
    return client.get(
        "/api/problems/by-source", params={"source": source, "source_id": source_id}, headers=auth_headers(token)
    )


def test_luogu_and_atcoder_are_accepted_only_on_their_own_domains(client):
    register(client)
    token = issue_token(client)
    assert post_import(client, token, luogu_payload()).status_code == 201
    atcoder = luogu_payload(
        source="atcoder",
        source_url="https://atcoder.jp/contests/abc300/tasks/abc300_a",
        title="A - N-choice question",
    )
    assert post_import(client, token, atcoder).status_code == 201
    assert post_import(client, token, luogu_payload(source_url="https://evil.example.com/problem/P1001")).status_code == 422
    assert post_import(client, token, luogu_payload(source_url="https://luogu.com.cn.evil.example/problem/P1")).status_code == 422
    bad_atcoder = dict(atcoder, source_url="https://www.luogu.com.cn/problem/P1001")
    assert post_import(client, token, bad_atcoder).status_code == 422


def test_existing_sources_keep_their_original_lenient_rules(client):
    register(client)
    token = issue_token(client)
    response = import_problem(client, token, source="codeforces", source_url="https://example.com/p/1")
    assert response.status_code == 201


def test_import_with_pending_reason_creates_a_mistake_in_the_same_request(client):
    register(client)
    token = issue_token(client)
    body = post_import(client, token, luogu_payload(pending_reason="  我以为暴力能过，其实 n 到 2e5  ")).json()
    assert body["mistake_id"]
    with connect() as conn:
        mistake = conn.execute("SELECT * FROM mistakes WHERE id = ?", (body["mistake_id"],)).fetchone()
    assert mistake["problem_id"] == body["id"]
    assert mistake["description"] == "我以为暴力能过，其实 n 到 2e5"
    assert mistake["pending_reason"] == 0
    without = post_import(client, token, luogu_payload(source_url="https://www.luogu.com.cn/problem/P1002")).json()
    assert without["mistake_id"] is None


@pytest.mark.parametrize("reason", ["", "   ", "x" * 501])
def test_pending_reason_is_validated(client, reason):
    register(client)
    token = issue_token(client)
    assert post_import(client, token, luogu_payload(pending_reason=reason)).status_code == 422


def test_by_source_finds_existing_problems_by_normalised_identifier(client):
    register(client)
    token = issue_token(client)
    luogu = post_import(client, token, luogu_payload()).json()
    cf = import_problem(client, token, source="codeforces", source_url="https://codeforces.com/problemset/problem/1730/a").json()
    contest = import_problem(client, token, source="codeforces", source_url="https://codeforces.com/contest/1731/problem/B").json()
    lc = import_problem(client, token, source="leetcode-cn", source_url="https://leetcode.cn/problems/two-sum/description/").json()
    nc = import_problem(client, token, source="nowcoder", source_url="https://ac.nowcoder.com/acm/problem/200500").json()
    at = post_import(client, token, luogu_payload(
        source="atcoder", source_url="https://atcoder.jp/contests/abc300/tasks/abc300_a", title="A")).json()
    assert by_source(client, token, "luogu", "p1001").json() == {"exists": True, "problem_id": luogu["id"]}
    assert by_source(client, token, "codeforces", "1730a").json() == {"exists": True, "problem_id": cf["id"]}
    assert by_source(client, token, "codeforces", "1731B").json() == {"exists": True, "problem_id": contest["id"]}
    assert by_source(client, token, "leetcode-cn", "two-sum").json() == {"exists": True, "problem_id": lc["id"]}
    assert by_source(client, token, "nowcoder", "200500").json() == {"exists": True, "problem_id": nc["id"]}
    assert by_source(client, token, "atcoder", "abc300_a").json() == {"exists": True, "problem_id": at["id"]}
    assert by_source(client, token, "luogu", "P9999").json() == {"exists": False, "problem_id": None}
    # 同一标识但来源不同不会串。
    assert by_source(client, token, "leetcode", "two-sum").json() == {"exists": False, "problem_id": None}


def test_by_source_only_sees_the_callers_own_problems(client):
    register(client, "alice")
    alice_token = issue_token(client)
    post_import(client, alice_token, luogu_payload())
    client.post("/api/auth/logout")
    register(client, "bob")
    bob_token = issue_token(client)
    assert by_source(client, bob_token, "luogu", "P1001").json() == {"exists": False, "problem_id": None}


def test_by_source_validates_source_and_identifier(client):
    register(client)
    token = issue_token(client)
    assert by_source(client, token, "unknown-site", "x").status_code == 422
    assert by_source(client, token, "luogu", "   ").status_code == 422
    assert by_source(client, token, "luogu", "P" * 201).status_code == 422


def test_append_reason_adds_a_mistake_without_touching_existing_ones(client):
    register(client)
    token = issue_token(client)
    problem = post_import(client, token, luogu_payload(pending_reason="第一条错因")).json()
    with connect() as conn:
        before = dict(conn.execute("SELECT * FROM mistakes WHERE id = ?", (problem["mistake_id"],)).fetchone())
    response = client.post(
        f"/api/problems/{problem['id']}/mistakes", json={"reason": " 第二条错因 "}, headers=auth_headers(token)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["problem_id"] == problem["id"] and body["status"] == "recorded" and body["id"] != problem["mistake_id"]
    with connect() as conn:
        after = dict(conn.execute("SELECT * FROM mistakes WHERE id = ?", (problem["mistake_id"],)).fetchone())
        added = conn.execute("SELECT * FROM mistakes WHERE id = ?", (body["id"],)).fetchone()
    assert after == before
    assert added["description"] == "第二条错因" and added["problem_id"] == problem["id"]


def test_append_reason_rejects_other_users_problem_and_bad_input(client):
    register(client, "alice")
    alice_token = issue_token(client)
    problem = post_import(client, alice_token, luogu_payload()).json()
    client.post("/api/auth/logout")
    register(client, "bob")
    bob_token = issue_token(client)
    url = f"/api/problems/{problem['id']}/mistakes"
    assert client.post(url, json={"reason": "x"}, headers=auth_headers(bob_token)).status_code == 404
    assert client.post("/api/problems/9999/mistakes", json={"reason": "x"}, headers=auth_headers(bob_token)).status_code == 404
    for body in ({"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}, {}):
        assert client.post(url, json=body, headers=auth_headers(alice_token)).status_code == 422


def test_due_count_counts_due_unsuspended_mistakes_only(client):
    register(client)
    token = issue_token(client)
    empty = client.get("/api/review/due-count", headers=auth_headers(token))
    assert empty.status_code == 200 and empty.json() == {"due_count": 0}
    problem = post_import(client, token, luogu_payload(pending_reason="一")).json()
    client.post(f"/api/problems/{problem['id']}/mistakes", json={"reason": "二"}, headers=auth_headers(token))
    client.post(f"/api/problems/{problem['id']}/mistakes", json={"reason": "三"}, headers=auth_headers(token))
    assert client.get("/api/review/due-count", headers=auth_headers(token)).json() == {"due_count": 3}
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET suspended_at = ? WHERE id = ?", (main.utc_now(), problem["mistake_id"]))
        conn.execute("UPDATE mistakes SET due_date = '2999-01-01' WHERE description = '二'")
    assert client.get("/api/review/due-count", headers=auth_headers(token)).json() == {"due_count": 1}


@pytest.mark.parametrize("method,url,body", [
    ("get", "/api/problems/by-source?source=luogu&source_id=P1", None),
    ("post", "/api/problems/1/mistakes", {"reason": "x"}),
    ("get", "/api/review/due-count", None),
])
def test_new_endpoints_reject_missing_wrong_and_revoked_tokens(client, method, url, body):
    register(client)
    token = issue_token(client)
    call = getattr(client, method)
    kwargs = {"json": body} if body is not None else {}
    assert call(url, **kwargs).status_code == 401
    assert call(url, headers=auth_headers("oyt_wrong"), **kwargs).status_code == 401
    assert client.delete("/api/users/api-token").status_code == 200
    assert call(url, headers=auth_headers(token), **kwargs).status_code == 401


def test_new_endpoints_are_rate_limited(client, monkeypatch):
    register(client)
    token = issue_token(client)
    monkeypatch.setattr("routers.plugin_api.READ_LIMIT_PER_HOUR", 2)
    monkeypatch.setattr("routers.plugin_api.APPEND_LIMIT_PER_HOUR", 2)
    main.reset_rate_limits()
    problem = post_import(client, token, luogu_payload()).json()
    assert [client.get("/api/review/due-count", headers=auth_headers(token)).status_code for _ in range(3)] == [200, 200, 429]
    url = f"/api/problems/{problem['id']}/mistakes"
    assert [client.post(url, json={"reason": "x"}, headers=auth_headers(token)).status_code for _ in range(3)] == [201, 201, 429]
