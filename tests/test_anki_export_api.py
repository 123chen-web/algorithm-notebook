"""GET /api/export/anki：把自己的未删除错题导出成 Anki 制表符文本。

覆盖：登录与账号状态、scope 过滤（all/zone/weak/mastered）、他人数据不可见、
体验/封禁/注销账号拒绝、5000 条上限（413）、响应头、每用户每小时 10 次限流、
表格注入字符、与 anki_export.build_anki_text 的输出逐字节一致。
"""

from datetime import datetime, timezone
from email.message import Message

import pytest

import main
from anki_export import build_anki_text
from db import connect
from test_app import client, register

ENDPOINT = "/api/export/anki"
ANKI_HEADER = "#separator:tab\n#html:true\n#guid column:1\n#tags column:4\n"


# ---------------- 造数助手 ----------------

def add_problem(
    conn, user_id, *, title="题目", zone="算法", language="Python",
    code="原始代码", thinking="当时的思路", created_at="2026-09-10T00:00:00+00:00",
):
    return conn.execute(
        "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, title, zone, language, code, thinking, created_at),
    ).lastrowid


def add_mistake(
    conn, problem_id, *, description="错因", due_date="2026-09-19",
    last_reviewed_at=None, repetitions=0, interval_days=0, ease_factor=2.5,
):
    return conn.execute(
        "INSERT INTO mistakes(problem_id, description, repetitions, interval_days, "
        "ease_factor, due_date, last_reviewed_at, version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (problem_id, description, repetitions, interval_days, ease_factor,
         due_date, last_reviewed_at),
    ).lastrowid


def add_tag(conn, mistake_id, user_id, tag):
    conn.execute(
        "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) "
        "VALUES (?, ?, ?, '2026-09-01T00:00:00+00:00')",
        (mistake_id, user_id, tag),
    )


def add_review(conn, mistake_id, *, quality, reviewed_at, next_due_date):
    conn.execute(
        "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
        "VALUES (?, ?, ?, ?)",
        (mistake_id, quality, reviewed_at, next_due_date),
    )


def add_variant(
    conn, mistake_id, *, result, answer_code="", created_at="2026-09-15T00:00:00+00:00",
    result_updated_at="2026-09-15T00:00:00+00:00",
):
    conn.execute(
        "INSERT INTO variants(mistake_id, description, model, created_at, result, "
        "answer_code, answer, expected_answer, notes, result_updated_at) "
        "VALUES (?, '练习', 'mock-model', ?, ?, ?, '', '', '', ?)",
        (mistake_id, created_at, result, answer_code, result_updated_at),
    )


def anki_get(client, scope="all", zone=None):
    url = ENDPOINT + "?scope=" + scope
    if zone is not None:
        url += "&zone=" + zone
    return client.get(url)


# ---------------- 鉴权与账号状态 ----------------

def test_requires_login(client):
    assert anki_get(client).status_code == 401


def test_trial_account_rejected(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    response = anki_get(client)
    assert response.status_code == 403
    assert "体验" in response.json()["detail"]


def test_banned_account_rejected(client):
    user = register(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user["id"],))
    assert anki_get(client).status_code == 401


def test_deleted_account_rejected(client):
    user = register(client)
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET deleted_at = '2026-09-19T00:00:00+00:00' WHERE id = ?",
            (user["id"],),
        )
    assert anki_get(client).status_code == 401


def test_get_works_without_csrf_header(client):
    # 与现有 /api/export 一致：普通 <a> 导航只带会话 cookie，不带自定义 CSRF 头。
    register(client)
    del client.headers["X-CSRF-Protection"]
    response = anki_get(client)
    assert response.status_code == 200


# ---------------- 参数校验 ----------------

def test_unknown_scope_rejected(client):
    register(client)
    response = client.get(ENDPOINT + "?scope=everything")
    assert response.status_code == 400


def test_zone_scope_requires_zone(client):
    register(client)
    response = client.get(ENDPOINT + "?scope=zone")
    assert response.status_code == 400


def test_zone_scope_rejects_unknown_zone(client):
    register(client)
    response = client.get(ENDPOINT + "?scope=zone&zone=不存在的分区")
    assert response.status_code == 400


# ---------------- 范围过滤与归属 ----------------

def test_scope_all_only_returns_own_undeleted_records(client):
    alice = register(client, "alice")
    with connect(write=True) as conn:
        p = add_problem(conn, alice["id"], title="alice的题", thinking="alice思路")
        add_mistake(conn, p, description="alice错因")
    # 第二位用户登录
    register(client, "bob")
    with connect(write=True) as conn:
        p = add_problem(conn, conn.execute(
            "SELECT id FROM users WHERE username = 'bob'").fetchone()[0],
            title="bob的题", thinking="bob思路")
        add_mistake(conn, p, description="bob错因")

    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    )
    assert response.status_code == 200
    response = anki_get(client)
    assert response.status_code == 200
    assert "alice" in response.text
    assert "bob" not in response.text


def test_deleted_mistakes_and_problems_are_excluded(client):
    user = register(client)
    ids = []
    with connect(write=True) as conn:
        problem = add_problem(conn, user["id"], title="保留的题", thinking="保留")
        ids.append(add_mistake(conn, problem, description="保留的错因"))
        gone_problem = add_problem(conn, user["id"], title="整题删除", thinking="删")
        ids.append(add_mistake(conn, gone_problem, description="随题删除"))
    client.delete(f"/api/problems/{gone_problem}")

    response = anki_get(client)
    assert response.status_code == 200
    assert "保留的题" in response.text
    assert "整题删除" not in response.text


def test_zone_scope_filters_by_zone(client):
    user = register(client)
    with connect(write=True) as conn:
        p1 = add_problem(conn, user["id"], title="算法题", zone="算法")
        add_mistake(conn, p1, description="算法错因")
        p2 = add_problem(conn, user["id"], title="高数题", zone="高等数学")
        add_mistake(conn, p2, description="高数错因")

    response = anki_get(client, scope="zone", zone="高等数学")
    assert response.status_code == 200
    assert "高数题" in response.text
    assert "算法题" not in response.text


def _seed_mastery_split(user_id):
    """造一条薄弱记录（09-01 录入后再没复习）和一条已掌握记录（今天录入）。"""
    with connect(write=True) as conn:
        weak_problem = add_problem(
            conn, user_id, title="薄弱题", zone="算法",
            created_at="2026-09-01T00:00:00+00:00",
        )
        add_mistake(conn, weak_problem, description="薄弱错因", due_date="2026-09-19")
        strong_problem = add_problem(
            conn, user_id, title="掌握题", zone="前端",
            created_at="2026-09-19T01:00:00+00:00",
        )
        add_mistake(conn, strong_problem, description="掌握错因", due_date="2026-10-19")


def test_weak_scope_returns_only_not_mastered(client):
    _seed_mastery_split(register(client)["id"])
    response = anki_get(client, scope="weak")
    assert response.status_code == 200
    assert "薄弱题" in response.text
    assert "掌握题" not in response.text


def test_mastered_scope_returns_only_mastered(client):
    _seed_mastery_split(register(client)["id"])
    response = anki_get(client, scope="mastered")
    assert response.status_code == 200
    assert "掌握题" in response.text
    assert "薄弱题" not in response.text


def test_reviewed_long_interval_record_counts_as_mastered(client):
    user = register(client)
    with connect(write=True) as conn:
        problem = add_problem(
            conn, user["id"], title="复习过的题", zone="算法",
            created_at="2026-09-01T00:00:00+00:00",
        )
        mistake_id = add_mistake(
            conn, problem, description="复习后稳固", due_date="2026-10-18",
            last_reviewed_at="2026-09-18T12:00:00+00:00", repetitions=1, interval_days=30,
        )
        add_review(
            conn, mistake_id, quality=5,
            reviewed_at="2026-09-18T12:00:00+00:00", next_due_date="2026-10-18",
        )
    response = anki_get(client, scope="mastered")
    assert "复习过的题" in response.text
    response = anki_get(client, scope="weak")
    assert "复习过的题" not in response.text


# ---------------- 内容与 build_anki_text 一致 ----------------

def test_output_matches_build_anki_text_byte_for_byte(client):
    user = register(client)
    with connect(write=True) as conn:
        problem = add_problem(
            conn, user["id"], title="两数之和", zone="算法",
            code="def two_sum():\n    pass\n",
            thinking="题目链接：https://leetcode.cn/problems/two-sum/\n先排序再双指针",
        )
        mistake_id = add_mistake(conn, problem, description="暴力超时")
        add_tag(conn, mistake_id, user["id"], "数组")
        add_tag(conn, mistake_id, user["id"], "hot 100")
        # 较早一次做对的代码；后面又有一次未完成的练习，不应覆盖它。
        add_variant(
            conn, mistake_id, result="solved", answer_code="def two_sum():\n    return 1\n",
            created_at="2026-09-12T00:00:00+00:00", result_updated_at="2026-09-12T00:00:00+00:00",
        )
        add_variant(
            conn, mistake_id, result="unattempted", answer_code="",
            created_at="2026-09-16T00:00:00+00:00", result_updated_at=None,
        )

    expected_record = {
        "id": mistake_id,
        "title": "两数之和",
        "zone": "算法",
        "url": "https://leetcode.cn/problems/two-sum/",
        "cause": "暴力超时",
        "notes": "题目链接：https://leetcode.cn/problems/two-sum/\n先排序再双指针",
        "code": "def two_sum():\n    pass\n",
        "fixed_code": "def two_sum():\n    return 1\n",
        "tags": ["数组", "hot 100"],
    }
    response = anki_get(client)
    assert response.text == build_anki_text([expected_record])


def test_empty_notebook_returns_header_only(client):
    register(client)
    response = anki_get(client)
    assert response.status_code == 200
    assert response.text == ANKI_HEADER


def test_spreadsheet_injection_titles_are_escaped_like_build_anki_text(client):
    user = register(client)
    records = []
    with connect(write=True) as conn:
        for index, title in enumerate(("=1+1", "+1+1", "-1+1", "@SUM(A1)", "\tTab题")):
            problem = add_problem(conn, user["id"], title=title, zone="算法")
            mistake_id = add_mistake(conn, problem, description="注入")
            records.append({
                "id": mistake_id, "title": title, "zone": "算法", "url": "",
                "cause": "注入", "notes": "当时的思路", "code": "原始代码",
                "fixed_code": "", "tags": [],
            })
    response = anki_get(client)
    assert response.text == build_anki_text(records)
    # 以 = + - @ 开头的标题原样落在 HTML 字段里（制表符已被替换成空格），
    # 不会被当成表格公式行——每行仍以 oy-<id> GUID 开头。
    for line in response.text.splitlines()[4:]:
        assert line.split("\t")[0].startswith("oy-")


def test_non_http_link_line_is_not_exported_as_url(client):
    user = register(client)
    with connect(write=True) as conn:
        problem = add_problem(
            conn, user["id"], title="无题源题",
            thinking="题目链接：javascript:alert(1)\n别的思路",
        )
        add_mistake(conn, problem)
    response = anki_get(client)
    # 正面（第 2 列）不能出现链接行；思路原文仍保留在背面（第 3 列）。
    front = response.text.splitlines()[4].split("\t")[1]
    back = response.text.splitlines()[4].split("\t")[2]
    assert "链接" not in front
    assert "javascript:alert(1)" not in front
    assert "javascript:alert(1)" in back


# ---------------- 条数上限 ----------------

def _bulk(user_id, conn, count, zone="算法"):
    problem = add_problem(conn, user_id, title=f"{zone}批量题", zone=zone)
    for _ in range(count):
        add_mistake(conn, problem, description="批量错因")


def test_export_at_exactly_5000_records_succeeds(client):
    user = register(client)
    with connect(write=True) as conn:
        _bulk(user["id"], conn, 5000)
    response = anki_get(client)
    assert response.status_code == 200
    assert len(response.text.splitlines()) == 4 + 5000


def test_export_over_5000_returns_413_and_zone_export_escapes(client):
    user = register(client)
    with connect(write=True) as conn:
        _bulk(user["id"], conn, 5001, zone="算法")
        _bulk(user["id"], conn, 1, zone="前端")
    response = anki_get(client)
    assert response.status_code == 413
    assert "分区" in response.json()["detail"]
    # 提示按分区导出，且分区导出确实能拿到结果。
    response = anki_get(client, scope="zone", zone="前端")
    assert response.status_code == 200
    assert len(response.text.splitlines()) == 4 + 1


# ---------------- 响应头 ----------------

def test_response_headers_use_text_plain_and_utc_filename(client, monkeypatch):
    register(client)
    frozen = datetime(2026, 9, 19, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "utc_now", lambda: frozen.isoformat(timespec="seconds"))
    response = anki_get(client)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    disposition = response.headers["content-disposition"]
    header = Message()
    header["Content-Disposition"] = disposition
    assert header.get_content_disposition() == "attachment"
    assert header.get_filename() == "oy-anki-20260919.txt"


# ---------------- 限流 ----------------

def test_rate_limit_allows_10_per_hour_then_429_per_user(client):
    register(client)
    for _ in range(10):
        assert anki_get(client).status_code == 200
    response = anki_get(client)
    assert response.status_code == 429

    # 限流按用户计数：换一个账号不受影响。
    register(client, "bob")
    response = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "a-test-password-123"},
    )
    assert response.status_code == 200
    assert anki_get(client).status_code == 200


# ---------------- 只读 ----------------

def test_export_is_read_only(client):
    user = register(client)
    with connect(write=True) as conn:
        problem = add_problem(conn, user["id"], title="只读题")
        add_mistake(conn, problem)
    with connect() as conn:
        before = list(conn.iterdump())
    assert anki_get(client).status_code == 200
    with connect() as conn:
        assert list(conn.iterdump()) == before
