"""草稿演算区后端接口 /api/mistakes/{id}/scratch 的测试。

覆盖：空记录默认值、保存与版本递增、409 版本冲突（含并发两个同版本 PUT）、
413 大小上限、2000 行上限、演算表结构校验与归一化、属主权限（他人 404）、
CSRF 与登录要求、删除错题 / 注销账号对草稿的清理。
"""
import re
import threading

import pytest
from fastapi.testclient import TestClient

import main
from db import connect
from test_app import client, new_problem, register

PASSWORD = "a-test-password-123"

EMPTY_SCRATCH = {"version": 0, "code": "", "fixed": "", "table": None,
                 "updated_at": None}


def scratch_url(mistake_id):
    return f"/api/mistakes/{mistake_id}/scratch"


def put_scratch(client, mistake_id, *, version=0, code="", fixed="", table=None):
    return client.put(
        scratch_url(mistake_id),
        json={"version": version, "code": code, "fixed": fixed, "table": table},
    )


def test_scratch_requires_login(client):
    # 未登录：GET / PUT 都 401。
    assert client.get(scratch_url(1)).status_code == 401
    assert put_scratch(client, 1).status_code == 401


def test_put_requires_csrf_header(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.put(
        scratch_url(mistake_id),
        json={"version": 0, "code": "a", "fixed": "", "table": None},
        headers={"X-CSRF-Protection": ""},
    )
    assert response.status_code == 403


def test_get_without_record_returns_empty_defaults(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.get(scratch_url(mistake_id))
    assert response.status_code == 200
    assert response.json() == EMPTY_SCRATCH


def test_get_unknown_mistake_is_404(client):
    register(client)
    assert client.get(scratch_url(99999)).status_code == 404
    assert put_scratch(client, 99999).status_code == 404


def test_first_save_starts_at_version_1_and_round_trips(client):
    register(client)
    mistake_id = new_problem(client)[0]
    table = {"cols": ["步骤", "变量"], "rows": [["1", "x"], ["2", "y"]]}
    response = put_scratch(client, mistake_id, code="print(1)", fixed="print(2)",
                           table=table)
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert isinstance(body["updated_at"], str) and body["updated_at"]
    assert set(body) == {"version", "updated_at"}

    saved = client.get(scratch_url(mistake_id)).json()
    assert saved["version"] == 1
    assert saved["code"] == "print(1)"
    assert saved["fixed"] == "print(2)"
    assert saved["table"] == table
    assert saved["updated_at"] == body["updated_at"]


def test_each_successful_put_increments_version_and_timestamp(client):
    register(client)
    mistake_id = new_problem(client)[0]
    first = put_scratch(client, mistake_id, code="a").json()
    second = put_scratch(client, mistake_id, version=1, code="b").json()
    assert first["version"] == 1
    assert second["version"] == 2
    current = client.get(scratch_url(mistake_id)).json()
    assert current["version"] == 2
    assert current["code"] == "b"
    assert current["updated_at"] == second["updated_at"]


def test_stale_version_gets_409_with_current_copy(client):
    register(client)
    mistake_id = new_problem(client)[0]
    put_scratch(client, mistake_id, code="第一版")
    put_scratch(client, mistake_id, version=1, code="第二版")

    # 拿着版本 0（或 1）的旧客户端提交：409，且 current 与 GET 同构。
    response = put_scratch(client, mistake_id, version=0, code="过期的提交")
    assert response.status_code == 409
    body = response.json()
    assert isinstance(body["detail"], str) and body["detail"]
    assert body["current"] == client.get(scratch_url(mistake_id)).json()
    assert body["current"]["version"] == 2
    assert body["current"]["code"] == "第二版"
    # 冲突的提交没有落库。
    assert client.get(scratch_url(mistake_id)).json()["code"] == "第二版"


def test_concurrent_puts_with_same_version_only_one_wins(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]

    # 第二个 TestClient 用同一账号登录，共享同一个临时数据库。
    other = TestClient(main.app, headers={"X-CSRF-Protection": "1"})
    login = other.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
    )
    assert login.status_code == 200

    barrier = threading.Barrier(2)
    results = []

    def save(active_client, code):
        barrier.wait()
        results.append(
            (code, put_scratch(active_client, mistake_id, code=code).status_code)
        )

    t1 = threading.Thread(target=save, args=(client, "窗口A"))
    t2 = threading.Thread(target=save, args=(other, "窗口B"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    statuses = sorted(status for _code, status in results)
    assert statuses == [200, 409]
    current = client.get(scratch_url(mistake_id)).json()
    assert current["version"] == 1
    assert current["code"] in {"窗口A", "窗口B"}
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mistake_scratch WHERE mistake_id = ?",
            (mistake_id,),
        ).fetchone()[0] == 1
    other.close()


def test_size_limit_boundary(client):
    register(client)
    mistake_id = new_problem(client)[0]
    # 三块序列化后合计恰好 40000：放行；40001：413。
    ok = put_scratch(client, mistake_id, code="a" * 40000)
    assert ok.status_code == 200
    too_big = put_scratch(client, mistake_id, version=1, code="a" * 40001)
    assert too_big.status_code == 413
    assert isinstance(too_big.json()["detail"], str)


def test_size_limit_counts_all_three_blocks(client):
    register(client)
    mistake_id = new_problem(client)[0]
    table = {"cols": ["c"], "rows": [["x"]]}
    # JSON.stringify({cols:["c"],rows:[["x"]]}) 长度为 29。
    serialized_table = '{"cols":["c"],"rows":[["x"]]}'
    assert len(serialized_table) == 29
    response = put_scratch(
        client, mistake_id,
        code="a" * 20000, fixed="b" * 19971, table=table,
    )
    assert response.status_code == 200
    response = put_scratch(
        client, mistake_id, version=1,
        code="a" * 20000, fixed="b" * 19972, table=table,
    )
    assert response.status_code == 413


@pytest.mark.parametrize("field", ["code", "fixed"], ids=["code", "fixed"])
def test_line_limit_is_2000(client, field):
    register(client)
    mistake_id = new_problem(client)[0]
    payload = {"version": 0, "code": "", "fixed": "", "table": None}
    payload[field] = "\n".join(["line"] * 2000)
    assert client.put(scratch_url(mistake_id), json=payload).status_code == 200
    payload["version"] = 1
    payload[field] = "\n".join(["line"] * 2001)
    response = client.put(scratch_url(mistake_id), json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("table", [
    None,
    {"cols": ["步骤", "变量"], "rows": [["1", "x"]]},
], ids=["null-table", "simple-table"])
def test_valid_tables_accepted(client, table):
    register(client)
    mistake_id = new_problem(client)[0]
    response = put_scratch(client, mistake_id, table=table)
    assert response.status_code == 200
    assert client.get(scratch_url(mistake_id)).json()["table"] == table


def test_table_control_characters_are_replaced_with_spaces(client):
    register(client)
    mistake_id = new_problem(client)[0]
    table = {"cols": ["步\x00骤"], "rows": [["a\x07b"]]}
    assert put_scratch(client, mistake_id, table=table).status_code == 200
    saved = client.get(scratch_url(mistake_id)).json()["table"]
    assert saved == {"cols": ["步 骤"], "rows": [["a b"]]}


def test_table_headers_and_cells_are_truncated_to_limits(client):
    register(client)
    mistake_id = new_problem(client)[0]
    table = {"cols": ["列" * 21], "rows": [["格" * 61]]}
    assert put_scratch(client, mistake_id, table=table).status_code == 200
    saved = client.get(scratch_url(mistake_id)).json()["table"]
    assert saved == {"cols": ["列" * 20], "rows": [["格" * 60]]}


def test_table_over_limit_rows_and_cols_are_dropped_and_short_rows_padded(client):
    register(client)
    mistake_id = new_problem(client)[0]
    table = {
        "cols": [f"c{i}" for i in range(21)],
        "rows": [["r"] for _ in range(61)],
    }
    assert put_scratch(client, mistake_id, table=table).status_code == 200
    saved = client.get(scratch_url(mistake_id)).json()["table"]
    assert len(saved["cols"]) == 20
    assert len(saved["rows"]) == 60
    # 不足宽度的行用空字符串补齐。
    assert saved["rows"][0] == ["r"] + [""] * 19


def test_table_empty_rows_padded_and_extra_cells_ignored(client):
    # 与 trace-table.js validate 一致：空行按宽度补空串；超出列数的单元格忽略。
    register(client)
    mistake_id = new_problem(client)[0]
    table = {"cols": ["a", "b"], "rows": [[], ["x", "y", "多余"]]}
    assert put_scratch(client, mistake_id, table=table).status_code == 200
    saved = client.get(scratch_url(mistake_id)).json()["table"]
    assert saved == {"cols": ["a", "b"], "rows": [["", ""], ["x", "y"]]}


@pytest.mark.parametrize("table", [
    {"cols": [], "rows": [["x"]]},
    {"cols": ["a"], "rows": []},
    {"cols": ["a"]},
    {"rows": [["x"]]},
    {"cols": ["a"], "rows": ["x"]},
    {"cols": [1], "rows": [["x"]]},
    {"cols": ["a"], "rows": [[1]]},
    [],
    "not-a-table",
    42,
], ids=[
    "empty-cols", "empty-rows", "missing-rows", "missing-cols",
    "row-not-array", "header-not-string",
    "cell-not-string",
    "table-is-list", "table-is-string", "table-is-number",
])
def test_invalid_table_structure_is_422(client, table):
    register(client)
    mistake_id = new_problem(client)[0]
    response = put_scratch(client, mistake_id, table=table)
    assert response.status_code == 422


def test_table_serialized_over_20000_chars_is_422(client):
    register(client)
    mistake_id = new_problem(client)[0]
    # 与 trace-table.js validate 的 MAX_SERIALIZED = 20000 对齐：
    # 20 列 × 60 行 × 每格 60 字符，序列化后远超 20000。
    table = {
        "cols": [f"h{i}" for i in range(20)],
        "rows": [["字" * 60 for _ in range(20)] for _ in range(60)],
    }
    response = put_scratch(client, mistake_id, table=table)
    assert response.status_code == 422


@pytest.mark.parametrize("body", [
    {"code": "", "fixed": "", "table": None},
    {"version": 0, "fixed": "", "table": None},
    {"version": 0, "code": "", "table": None},
    {"version": 0, "code": "", "fixed": ""},
    {"version": -1, "code": "", "fixed": "", "table": None},
    {"version": "0", "code": "", "fixed": "", "table": None},
    {"version": 0, "code": 1, "fixed": "", "table": None},
    {"version": 0, "code": "", "fixed": 2, "table": None},
    {"version": 0, "code": "", "fixed": "", "table": None, "extra": 1},
], ids=[
    "missing-version", "missing-code", "missing-fixed", "missing-table",
    "negative-version", "version-not-int", "code-not-string",
    "fixed-not-string", "extra-field",
])
def test_malformed_body_is_422(client, body):
    register(client)
    mistake_id = new_problem(client)[0]
    assert client.put(scratch_url(mistake_id), json=body).status_code == 422


def test_other_users_mistakes_return_404(client):
    register(client, "alice")
    mistake_id = new_problem(client)[0]
    put_scratch(client, mistake_id, code="alice 的草稿")
    client.post("/api/auth/logout")

    register(client, "bob")
    assert client.get(scratch_url(mistake_id)).status_code == 404
    response = put_scratch(client, mistake_id, code="bob 偷改")
    assert response.status_code == 404
    # 草稿没有被 bob 的写入影响。
    client.post("/api/auth/logout")
    client.post("/api/auth/login",
                json={"username": "alice", "password": PASSWORD})
    assert client.get(scratch_url(mistake_id)).json()["code"] == "alice 的草稿"


def test_deleting_mistake_removes_its_scratch(client):
    register(client)
    mistake_id = new_problem(client)[0]
    assert put_scratch(client, mistake_id, code="再见").status_code == 200
    assert client.delete(f"/api/mistakes/{mistake_id}").status_code == 200
    assert client.get(scratch_url(mistake_id)).status_code == 404
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mistake_scratch WHERE mistake_id = ?",
            (mistake_id,),
        ).fetchone()[0] == 0


def test_deleting_problem_cascades_to_scratch(client):
    register(client)
    mistake_id = new_problem(client)[0]
    put_scratch(client, mistake_id, code="随题删除")
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]
    assert client.delete(f"/api/problems/{problem_id}").status_code == 200
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM mistake_scratch WHERE mistake_id = ?",
            (mistake_id,),
        ).fetchone()[0] == 0


def test_deleting_account_removes_scratch_drafts(client):
    user = register(client)
    first, second = new_problem(client)
    put_scratch(client, first, code="草稿一")
    put_scratch(client, second, code="草稿二")
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mistake_scratch").fetchone()[0] == 0
        # 用户行本身按注销规则匿名保留。
        assert conn.execute(
            "SELECT deleted_at FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0] is not None


def test_updated_at_looks_like_utc_iso_timestamp(client):
    register(client)
    mistake_id = new_problem(client)[0]
    updated_at = put_scratch(client, mistake_id, code="x").json()["updated_at"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", updated_at)
