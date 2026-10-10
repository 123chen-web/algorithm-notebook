"""刷题伴侣插件后端接口：POST /api/users/api-token 与
POST /api/problems/import-from-extension。

覆盖：token 签发/轮换（明文只返回一次、库里只存哈希）、未登录签发 401、
导入成功路径（字段全部落库）、坏 token/缺 token 401、字段校验 422、
超大题面拒绝、CSRF 头要求、迁移 24 在新库上完整应用。
"""
import pytest
from fastapi.testclient import TestClient

import main
from db import SCHEMA_VERSION, connect, schema_version


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def register(client, username="alice"):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-test-password-123",
            "accept_terms": True,
            "invite_code": "test-invite",
            "email": f"{username}@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 201
    return response.json()


def issue_token(client):
    response = client.post("/api/users/api-token")
    assert response.status_code == 200
    return response.json()["token"]


def auth_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "X-CSRF-Protection": "1",
    }


def import_payload(**overrides):
    payload = {
        "source": "leetcode",
        "source_url": "https://leetcode.com/problems/two-sum/",
        "title": "Two Sum",
        "content": "Given an array of integers nums and an integer target...",
        "difficulty": "简单",
        "tags": ["数组", "哈希表"],
        "language": "Python",
    }
    payload.update(overrides)
    return payload


def import_problem(client, token, **overrides):
    return client.post(
        "/api/problems/import-from-extension",
        json=import_payload(**overrides),
        headers=auth_headers(token),
    )


def test_issue_token_returns_plaintext_once_and_stores_only_hash(client):
    register(client)
    token = issue_token(client)
    assert token.startswith("oyt_")
    assert len(token) > 40
    with connect() as conn:
        row = conn.execute(
            "SELECT api_token_hash FROM users WHERE username = 'alice'"
        ).fetchone()
    assert row["api_token_hash"] is not None
    assert row["api_token_hash"] != token
    assert row["api_token_hash"] == main.token_hash(token)
    # 明文只在签发时返回：再查一次用户信息接口也不应泄露。
    me = client.get("/api/me")
    assert token not in me.text


def test_issue_token_requires_login(client):
    response = client.post("/api/users/api-token")
    assert response.status_code == 401


def test_reissue_rotates_token_and_old_one_stops_working(client):
    register(client)
    old_token = issue_token(client)
    assert import_problem(client, old_token).status_code == 201
    new_token = issue_token(client)
    assert new_token != old_token
    assert import_problem(client, old_token).status_code == 401
    assert import_problem(client, new_token).status_code == 201


def test_import_creates_problem_with_all_fields(client):
    register(client)
    token = issue_token(client)
    response = import_problem(client, token)
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Two Sum"
    assert body["source"] == "leetcode"
    with connect() as conn:
        problem = conn.execute(
            "SELECT * FROM problems WHERE id = ?", (body["id"],)
        ).fetchone()
    assert problem["user_id"] == 1
    assert problem["title"] == "Two Sum"
    assert problem["source"] == "leetcode"
    assert problem["source_url"] == "https://leetcode.com/problems/two-sum/"
    assert problem["statement"].startswith("Given an array")
    assert problem["difficulty"] == "简单"
    assert problem["language"] == "Python"
    assert problem["zone"] == "算法"
    # 导入不等建错题：mistakes 表应为空。
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM mistakes WHERE problem_id = ?", (body["id"],)
        ).fetchone()["n"]
    assert count == 0


def test_import_rejects_missing_or_bad_token(client):
    register(client)
    token = issue_token(client)
    # 缺 Authorization 头。
    response = client.post(
        "/api/problems/import-from-extension",
        json=import_payload(),
        headers={"X-CSRF-Protection": "1"},
    )
    assert response.status_code == 401
    # 错误的 token。
    response = client.post(
        "/api/problems/import-from-extension",
        json=import_payload(),
        headers=auth_headers("oyt_" + "x" * 43),
    )
    assert response.status_code == 401
    # 好 token 仍可用。
    assert import_problem(client, token).status_code == 201


def test_import_validates_inputs(client):
    register(client)
    token = issue_token(client)
    # 非法 source。
    assert import_problem(client, token, source="unknown").status_code == 422
    # 非 http(s) 链接。
    assert (
        import_problem(client, token, source_url="ftp://example.com/x").status_code
        == 422
    )
    # 空标题。
    assert import_problem(client, token, title="   ").status_code == 422
    # 空题面。
    assert import_problem(client, token, content="  ").status_code == 422
    # 超大题面（>100000 字）拒绝。
    assert import_problem(client, token, content="x" * 100_001).status_code == 422
    # 非法 zone。
    assert import_problem(client, token, zone="不存在的分区").status_code == 422
    # 标签超长（与错题标签同一套规范）。
    assert import_problem(client, token, tags=["x" * 21]).status_code == 422
    # 边界：正好 100000 字通过。
    assert import_problem(client, token, content="x" * 100_000).status_code == 201


def test_import_requires_csrf_header(client):
    register(client)
    token = issue_token(client)
    # 用空值覆盖掉 fixture 默认带的 X-CSRF-Protection 头，
    # 走中间件里 headers.get(...) != "1" 的同一分支。
    response = client.post(
        "/api/problems/import-from-extension",
        json=import_payload(),
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Protection": ""},
    )
    assert response.status_code == 403


def test_migration_28_applies_on_fresh_database(client):
    # client fixture 已在新库上跑完 init_db。新库版本等于当前最新迁移
    # （迁移 72 为笔记画板；迁移 28 的结构效果在下方列断言中仍然成立）。
    with connect() as conn:
        assert schema_version(conn) == SCHEMA_VERSION == 73
        user_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)")
        }
        problem_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(problems)")
        }
    assert "api_token_hash" in user_columns
    for column in ("source", "source_url", "statement", "difficulty"):
        assert column in problem_columns


def test_revoke_requires_session_and_immediately_invalidates_token(client):
    assert client.delete("/api/users/api-token").status_code == 401
    register(client)
    token = issue_token(client)
    assert import_problem(client, token).status_code == 201
    assert client.delete("/api/users/api-token").json() == {"ok": True}
    assert import_problem(client, token).status_code == 401
    assert client.delete("/api/users/api-token").status_code == 200
    assert import_problem(client, issue_token(client)).status_code == 201


def test_bearer_failures_have_identical_response_and_cookie_is_not_a_fallback(client):
    owner = register(client)["id"]
    token = issue_token(client)
    responses = []
    for header in ("", "Basic x", "Bearer ", "Bearer oyt_invalid"):
        responses.append(client.post("/api/problems/import-from-extension", json=import_payload(),
                                     headers={"Authorization": header}))
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned=1 WHERE id=?", (owner,))
    responses.append(import_problem(client, token))
    assert all(response.status_code == 401 for response in responses)
    assert all(response.json() == {"detail": "API token 无效"} for response in responses)


def test_limit_is_per_user_and_per_hour_even_across_token_rotation(client, monkeypatch):
    owner = register(client)["id"]
    token = issue_token(client)
    clock = [1000.0]
    monkeypatch.setattr(main.time, "time", lambda: clock[0])
    for _ in range(60):
        assert import_problem(client, token).status_code == 201
    assert import_problem(client, token).status_code == 429
    token = issue_token(client)
    assert import_problem(client, token).status_code == 429
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM problems WHERE user_id=?", (owner,)).fetchone()[0] == 60
        assert conn.execute("SELECT lifetime_problem_count FROM users WHERE id=?", (owner,)).fetchone()[0] == 60
    client.post("/api/auth/logout")
    register(client, username="bob")
    assert import_problem(client, issue_token(client)).status_code == 201
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"})
    clock[0] += 3601
    assert import_problem(client, token).status_code == 201


def test_account_deletion_clears_token_and_old_token_is_401(client):
    owner = register(client)["id"]
    token = issue_token(client)
    response = client.post("/api/me/delete-account", json={"password": "a-test-password-123"})
    assert response.status_code == 200
    assert import_problem(client, token).status_code == 401
    with connect() as conn:
        assert conn.execute("SELECT api_token_hash FROM users WHERE id=?", (owner,)).fetchone()[0] is None


def test_revoke_between_auth_read_and_insert_is_still_401(client):
    from routers.plugin_api import _bearer_user
    owner = register(client)["id"]
    token = issue_token(client)
    def revoked_after_read():
        identity = _bearer_user(f"Bearer {token}")
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET api_token_hash=NULL WHERE id=?", (owner,))
        return identity
    main.app.dependency_overrides[_bearer_user] = revoked_after_read
    try:
        response = import_problem(client, token)
    finally:
        main.app.dependency_overrides.pop(_bearer_user)
    assert response.status_code == 401
    assert response.json() == {"detail": "API token 无效"}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM problems WHERE user_id=?", (owner,)).fetchone()[0] == 0
