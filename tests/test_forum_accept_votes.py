"""论坛采纳与有用的权限、删除隐私及批量读取回归。"""

import json
import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import main
from db import connect
from test_app import register
from test_forum import PASSWORD, create_comment, create_post
from test_forum_threads import client


NOW = "2026-10-03T08:00:00+00:00"


def switch_actor(client, forum, actor):
    client.cookies.clear()
    if actor is not None:
        client.cookies.set("session", forum["actors"][actor]["session"])


@pytest.fixture
def forum(client, monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    actors = {}
    for actor in ("owner", "author", "other"):
        user = register(client, f"forum_{actor}")
        actors[actor] = {**user, "session": client.cookies.get("session")}
    data = {"actors": actors}
    switch_actor(client, data, "owner")
    data["post"] = create_post(client, "二分区间边界", "如何写稳定的二分查找？")
    data["own"] = create_comment(client, data["post"]["id"], "楼主补充问题")
    switch_actor(client, data, "author")
    data["first"] = create_comment(client, data["post"]["id"], "先明确区间不变量")
    switch_actor(client, data, "other")
    data["second"] = create_comment(client, data["post"]["id"], "补充空数组和重复值测试")
    switch_actor(client, data, "owner")
    return data


def accepted_url(forum):
    return f"/api/posts/{forum['post']['id']}/accepted"


def helpful_url(comment):
    return f"/api/comments/{comment['id']}/helpful"


def accept_first(client, forum):
    switch_actor(client, forum, "owner")
    response = client.put(accepted_url(forum), json={"comment_id": forum["first"]["id"]})
    assert response.status_code == 200, response.text
    assert response.json() == {"accepted_comment_id": forum["first"]["id"]}


def seed_summary(forum):
    content = {
        "tldr": "样本结论：先明确区间不变量。",
        "points": [{"text": "检查边界", "floors": [forum["first"]["floor"]]}],
        "open_questions": [],
    }
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO post_summaries(post_id, signature, content, comment_count, created_at) "
            "VALUES (?, 'privacy-regression', ?, 3, ?)",
            (forum["post"]["id"], json.dumps(content, ensure_ascii=False), NOW),
        )


def assert_cleared(forum):
    with connect() as conn:
        assert conn.execute(
            "SELECT accepted_comment_id FROM posts WHERE id = ?", (forum["post"]["id"],)
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT 1 FROM post_summaries WHERE post_id = ?", (forum["post"]["id"],)
        ).fetchone() is None


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("actor,status,detail", [
    ("owner", 200, None),
    ("author", 404, "帖子不存在"),
    ("other", 404, "帖子不存在"),
    ("trial", 403, "体验账号不支持采纳"),
    (None, 401, "请先登录"),
])
def test_accept_permission_matrix(client, forum, method, actor, status, detail):
    if actor == "trial":
        client.cookies.clear()
        response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
        assert response.status_code == 201
    else:
        switch_actor(client, forum, actor)
    kwargs = {"json": {"comment_id": forum["first"]["id"]}} if method == "PUT" else {}
    response = client.request(method, accepted_url(forum), **kwargs)
    assert response.status_code == status, response.text
    if detail:
        assert response.json() == {"detail": detail}
    else:
        expected = forum["first"]["id"] if method == "PUT" else None
        assert response.json() == {"accepted_comment_id": expected}


def test_accept_switch_idempotence_cancel_and_read_contract(client, forum):
    post_id = forum["post"]["id"]
    initial = client.get(f"/api/posts/{post_id}").json()
    assert initial["accepted_comment_id"] is None
    listed = client.get("/api/posts").json()["posts"]
    assert next(post for post in listed if post["id"] == post_id)["solved"] is False
    for comment in (forum["first"], forum["first"], forum["second"], forum["second"]):
        response = client.put(accepted_url(forum), json={"comment_id": comment["id"]})
        assert response.status_code == 200
        assert response.json() == {"accepted_comment_id": comment["id"]}
        assert client.get(f"/api/posts/{post_id}").json()["accepted_comment_id"] == comment["id"]
        listed = client.get("/api/posts").json()["posts"]
        assert next(post for post in listed if post["id"] == post_id)["solved"] is True
    for _ in range(2):
        response = client.delete(accepted_url(forum))
        assert response.status_code == 200
        assert response.json() == {"accepted_comment_id": None}
    assert client.get(f"/api/posts/{post_id}").json()["accepted_comment_id"] is None
    assert client.get("/api/posts").json()["posts"][0]["solved"] is False


def test_owner_cannot_accept_own_comment(client, forum):
    response = client.put(accepted_url(forum), json={"comment_id": forum["own"]["id"]})
    assert response.status_code == 400
    assert response.json() == {"detail": "不能采纳自己的评论"}
    with connect() as conn:
        assert conn.execute("SELECT accepted_comment_id FROM posts").fetchone()[0] is None


@pytest.mark.parametrize("payload", [
    {}, {"comment_id": None}, {"comment_id": True}, {"comment_id": False},
    {"comment_id": "1"}, {"comment_id": 1.0}, {"comment_id": [1]},
    {"comment_id": 1, "extra": "invalid"},
])
def test_accept_invalid_body_returns_chinese_detail(client, forum, payload):
    response = client.put(accepted_url(forum), json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": "请求参数不正确，请检查后重试"}


@pytest.mark.parametrize("target_kind", [
    "other_post", "deleted", "missing", "max_sqlite_id", "overflow", "huge",
])
def test_accept_requires_visible_comment_in_same_post(client, forum, target_kind):
    accept_first(client, forum)
    if target_kind == "other_post":
        switch_actor(client, forum, "author")
        target_id = create_comment(client, create_post(client)["id"])["id"]
    elif target_kind == "deleted":
        switch_actor(client, forum, "other")
        target_id = forum["second"]["id"]
        assert client.delete(f"/api/comments/{target_id}").status_code == 200
    else:
        target_id = {
            "missing": 999999, "max_sqlite_id": 2**63 - 1,
            "overflow": 2**63, "huge": 10**100,
        }[target_kind]
    switch_actor(client, forum, "owner")
    response = client.put(accepted_url(forum), json={"comment_id": target_id})
    assert response.status_code == 400
    assert response.json() == {"detail": "这条评论不存在或已删除"}
    assert client.get(f"/api/posts/{forum['post']['id']}").json()["accepted_comment_id"] == forum["first"]["id"]


@pytest.mark.parametrize("deleted_by", ["author", "admin"])
def test_comment_delete_clears_acceptance_and_summary(client, forum, deleted_by):
    accept_first(client, forum)
    seed_summary(forum)
    if deleted_by == "admin":
        switch_actor(client, forum, "other")
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (forum["actors"]["other"]["id"],))
        url = f"/api/admin/comments/{forum['first']['id']}"
    else:
        switch_actor(client, forum, "author")
        url = f"/api/comments/{forum['first']['id']}"
    assert client.delete(url).status_code == 200
    assert_cleared(forum)
    detail = client.get(f"/api/posts/{forum['post']['id']}").json()
    assert detail["accepted_comment_id"] is None
    assert forum["first"]["id"] not in {comment["id"] for comment in detail["comments"]}
    assert client.get("/api/posts").json()["posts"][0]["solved"] is False


@pytest.mark.parametrize("deleted_by", ["author", "admin", "account"])
def test_removing_another_comment_keeps_answer_and_clears_summary(client, forum, deleted_by):
    accept_first(client, forum)
    seed_summary(forum)
    switch_actor(client, forum, "other")
    if deleted_by == "admin":
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (forum["actors"]["other"]["id"],))
        response = client.delete(f"/api/admin/comments/{forum['second']['id']}")
    elif deleted_by == "account":
        response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    else:
        response = client.delete(f"/api/comments/{forum['second']['id']}")
    assert response.status_code == 200, response.text
    with connect() as conn:
        assert conn.execute(
            "SELECT accepted_comment_id FROM posts WHERE id = ?", (forum["post"]["id"],)
        ).fetchone()[0] == forum["first"]["id"]
        assert conn.execute(
            "SELECT 1 FROM post_summaries WHERE post_id = ?", (forum["post"]["id"],)
        ).fetchone() is None
    switch_actor(client, forum, "owner")
    assert client.get(f"/api/posts/{forum['post']['id']}").json()["accepted_comment_id"] == forum["first"]["id"]


@pytest.mark.parametrize("deleted_by", ["owner", "admin"])
def test_post_delete_clears_acceptance_and_summary(client, forum, deleted_by):
    accept_first(client, forum)
    seed_summary(forum)
    if deleted_by == "admin":
        switch_actor(client, forum, "other")
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (forum["actors"]["other"]["id"],))
        url = f"/api/admin/posts/{forum['post']['id']}"
    else:
        url = f"/api/posts/{forum['post']['id']}"
    assert client.delete(url).status_code == 200
    assert_cleared(forum)
    response = client.get(f"/api/posts/{forum['post']['id']}")
    assert response.status_code == 404
    assert response.json() == {"detail": "帖子不存在"}
    assert client.get("/api/posts").json()["posts"] == []


@pytest.mark.parametrize("deleted_actor", ["author", "owner"])
def test_account_anonymization_clears_acceptance_and_summary(client, forum, deleted_actor):
    accept_first(client, forum)
    seed_summary(forum)
    switch_actor(client, forum, deleted_actor)
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    assert_cleared(forum)
    switch_actor(client, forum, "other")
    assert client.get(f"/api/posts/{forum['post']['id']}").json()["accepted_comment_id"] is None
    assert client.get("/api/posts").json()["posts"][0]["solved"] is False


@pytest.mark.parametrize("invalid_target", ["deleted", "physical_delete", "other_post"])
def test_reads_defend_against_stale_or_cross_post_acceptance(client, forum, invalid_target):
    with connect(write=True) as conn:
        if invalid_target == "deleted":
            target_id = forum["first"]["id"]
            conn.execute("UPDATE post_comments SET deleted_at = ? WHERE id = ?", (NOW, target_id))
        elif invalid_target == "physical_delete":
            target_id = forum["first"]["id"]
            conn.execute("DELETE FROM post_comments WHERE id = ?", (target_id,))
        else:
            post_id = conn.execute(
                "INSERT INTO posts(user_id, title, body, created_at) VALUES (?, '另一个帖子', '正文', ?)",
                (forum["actors"]["author"]["id"], NOW),
            ).lastrowid
            target_id = conn.execute(
                "INSERT INTO post_comments(post_id, user_id, body, created_at) VALUES (?, ?, '异帖评论', ?)",
                (post_id, forum["actors"]["author"]["id"], NOW),
            ).lastrowid
        conn.execute("UPDATE posts SET accepted_comment_id = ? WHERE id = ?", (target_id, forum["post"]["id"]))
    assert client.get(f"/api/posts/{forum['post']['id']}").json()["accepted_comment_id"] is None
    listing = client.get("/api/posts").json()["posts"]
    assert next(post for post in listing if post["id"] == forum["post"]["id"])["solved"] is False


def test_helpful_is_idempotent_and_counts_are_specific_to_viewer(client, forum):
    url = helpful_url(forum["first"])
    for _ in range(2):
        response = client.put(url)
        assert response.status_code == 200
        assert response.json() == {"helpful_count": 1, "viewer_helpful": True}
    switch_actor(client, forum, "other")
    assert client.put(url).json() == {"helpful_count": 2, "viewer_helpful": True}
    for actor, expected in (("owner", True), ("other", True), ("author", False)):
        switch_actor(client, forum, actor)
        comments = client.get(f"/api/posts/{forum['post']['id']}").json()["comments"]
        target = next(comment for comment in comments if comment["id"] == forum["first"]["id"])
        assert target["helpful_count"] == 2
        assert type(target["helpful_count"]) is int
        assert target["viewer_helpful"] is expected
        untouched = next(comment for comment in comments if comment["id"] == forum["second"]["id"])
        assert untouched["helpful_count"] == 0
        assert untouched["viewer_helpful"] is False
    switch_actor(client, forum, "owner")
    for _ in range(2):
        response = client.delete(url)
        assert response.status_code == 200
        assert response.json() == {"helpful_count": 1, "viewer_helpful": False}
    switch_actor(client, forum, "other")
    assert client.delete(url).json() == {"helpful_count": 0, "viewer_helpful": False}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0] == 0


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_cannot_vote_for_own_comment(client, forum, method):
    response = client.request(method, helpful_url(forum["own"]))
    assert response.status_code == 400
    assert response.json() == {"detail": "不能给自己的评论点有用"}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0] == 0


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("viewer", ["trial", "anonymous"])
def test_helpful_requires_logged_in_non_trial_account(client, forum, method, viewer):
    client.cookies.clear()
    if viewer == "trial":
        assert client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).status_code == 201
    response = client.request(method, helpful_url(forum["first"]))
    assert response.status_code == (403 if viewer == "trial" else 401)
    assert response.json() == {"detail": "体验账号不支持点有用" if viewer == "trial" else "请先登录"}


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
@pytest.mark.parametrize("hidden", ["comment", "post", "missing"])
def test_helpful_hides_invisible_comments_and_posts(client, forum, method, hidden):
    with connect(write=True) as conn:
        if hidden == "comment":
            conn.execute("UPDATE post_comments SET deleted_at = ? WHERE id = ?", (NOW, forum["first"]["id"]))
        elif hidden == "post":
            conn.execute("UPDATE posts SET deleted_at = ? WHERE id = ?", (NOW, forum["post"]["id"]))
    comment_id = 999999 if hidden == "missing" else forum["first"]["id"]
    response = client.request(method, f"/api/comments/{comment_id}/helpful")
    assert response.status_code == 404
    assert response.json() == {"detail": "评论不存在"}
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0] == 0


def test_helpful_limit_is_per_user_shared_between_put_and_delete(client, forum, monkeypatch):
    current_time = main.time.time()
    monkeypatch.setattr(main.time, "time", lambda: current_time)
    main.reset_rate_limits()
    url = helpful_url(forum["first"])
    for index in range(60):
        response = client.request("PUT" if index % 2 == 0 else "DELETE", url)
        assert response.status_code == 200, response.text
    before = client.get(f"/api/posts/{forum['post']['id']}").json()["comments"]
    for method in ("PUT", "DELETE"):
        response = client.request(method, url)
        assert response.status_code == 429
        assert response.json() == {"detail": "操作过于频繁，请稍后再试"}
    assert client.get(f"/api/posts/{forum['post']['id']}").json()["comments"] == before
    switch_actor(client, forum, "other")
    assert client.put(url).status_code == 200
    switch_actor(client, forum, "owner")
    current_time += 60.01
    assert client.put(url).status_code == 200


def test_account_deletion_removes_votes_given_to_other_comments(client, forum):
    assert client.put(helpful_url(forum["first"])).json() == {"helpful_count": 1, "viewer_helpful": True}
    switch_actor(client, forum, "other")
    assert client.put(helpful_url(forum["first"])).json() == {"helpful_count": 2, "viewer_helpful": True}
    assert client.put(helpful_url(forum["own"])).status_code == 200
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    with connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM comment_votes WHERE user_id = ?", (forum["actors"]["other"]["id"],)
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0] == 1
    switch_actor(client, forum, "owner")
    comments = client.get(f"/api/posts/{forum['post']['id']}").json()["comments"]
    first = next(comment for comment in comments if comment["id"] == forum["first"]["id"])
    assert first["helpful_count"] == 1
    assert first["viewer_helpful"] is True
    own = next(comment for comment in comments if comment["id"] == forum["own"]["id"])
    assert own["helpful_count"] == 0


@pytest.mark.parametrize("method,endpoint", [
    ("PUT", "accepted"), ("DELETE", "accepted"),
    ("PUT", "helpful"), ("DELETE", "helpful"),
])
def test_new_writes_recheck_account_inside_write_transaction(client, forum, monkeypatch, method, endpoint):
    calls = []

    def account_deleted_after_authentication(conn, user_id, expected_hash=None):
        assert conn.in_transaction
        calls.append(user_id)
        raise HTTPException(401, "登录已过期，请重新登录")

    monkeypatch.setattr(main, "recheck_account", account_deleted_after_authentication)
    if endpoint == "accepted":
        kwargs = {"json": {"comment_id": forum["first"]["id"]}} if method == "PUT" else {}
        url = accepted_url(forum)
    else:
        kwargs = {}
        url = helpful_url(forum["first"])
    response = client.request(method, url, **kwargs)
    assert response.status_code == 401
    assert response.json() == {"detail": "登录已过期，请重新登录"}
    assert calls == [forum["actors"]["owner"]["id"]]
    with connect() as conn:
        assert conn.execute("SELECT accepted_comment_id FROM posts").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM comment_votes").fetchone()[0] == 0


def test_get_post_uses_one_grouped_vote_query_with_bounded_sql(client, forum, monkeypatch):
    post_id = forum["post"]["id"]
    voter_id = forum["actors"]["owner"]["id"]
    author_id = forum["actors"]["author"]["id"]
    assert client.put(helpful_url(forum["first"])).status_code == 200
    statements = []

    @contextmanager
    def traced_connect(write=False):
        with connect(write=write) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(main, "connect", traced_connect)
    small = client.get(f"/api/posts/{post_id}")
    assert small.status_code == 200
    small_statements = statements[:]
    statements.clear()
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO post_comments(post_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            [(post_id, author_id, f"补充评论 {index}", NOW) for index in range(300)],
        )
        extra_ids = [row[0] for row in conn.execute(
            "SELECT id FROM post_comments WHERE post_id = ? AND id > ? ORDER BY id", (post_id, forum["second"]["id"])
        )]
        conn.executemany(
            "INSERT INTO comment_votes(comment_id, user_id, created_at) VALUES (?, ?, ?)",
            [(comment_id, voter_id, NOW) for comment_id in extra_ids],
        )
    large = client.get(f"/api/posts/{post_id}")
    assert large.status_code == 200
    assert len(large.json()["comments"]) == len(small.json()["comments"]) + 300

    def reads(trace):
        return [statement for statement in trace if statement.lstrip().upper().startswith("SELECT")]

    assert len(reads(statements)) == len(reads(small_statements)), statements
    assert len(reads(statements)) <= 6, statements
    for trace in (small_statements, statements):
        vote_reads = [statement for statement in reads(trace) if "comment_votes" in statement.lower()]
        assert len(vote_reads) == 1, trace
        assert "group by" in vote_reads[0].lower(), vote_reads
    by_id = {comment["id"]: comment for comment in large.json()["comments"]}
    for comment_id in extra_ids:
        assert by_id[comment_id]["helpful_count"] == 1
        assert by_id[comment_id]["viewer_helpful"] is True


def test_list_posts_computes_solved_in_one_query(client, forum, monkeypatch):
    accept_first(client, forum)
    statements = []

    @contextmanager
    def traced_connect(write=False):
        with connect(write=write) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(main, "connect", traced_connect)
    response = client.get("/api/posts")
    assert response.status_code == 200
    assert response.json()["posts"][0]["solved"] is True
    # 列表现在一次读取同时拿统计（含 solved）：读取 accepted_comment_id 的语句
    # 数量固定，不随帖子数增长（那种逐帖查询在 test_forum_board 里另有计数测试）。
    solved_reads = [s for s in statements if "accepted_comment_id" in s]
    assert 1 <= len(solved_reads) <= 3, statements


class RejectingWriteConnection:
    """Only canned SELECT results: these tests never create or open a database."""

    in_transaction = True

    def __init__(self, post_author_id=1, comment_author_id=2):
        self.post_author_id = post_author_id
        self.comment_author_id = comment_author_id
        self.statements = []

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select * from posts"):
            result = {"id": 10, "user_id": self.post_author_id, "deleted_at": None}
        elif normalized.startswith("select user_id from post_comments") or normalized.startswith("select c.user_id from post_comments"):
            result = {"user_id": self.comment_author_id}
        else:
            pytest.fail(f"拒绝分支不应继续查询或写入：{statement}")

        class Result:
            def fetchone(self):
                return result

        return Result()


def unit_write_connection(monkeypatch, *, post_author_id=1, comment_author_id=2):
    conn = RejectingWriteConnection(post_author_id, comment_author_id)

    @contextmanager
    def fake_connect(write=False):
        assert write is True
        yield conn

    monkeypatch.setattr(main, "connect", fake_connect)
    monkeypatch.setattr(main, "recheck_account", lambda connection, user_id: {"id": user_id})
    return conn


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_unit_accept_rejects_non_author_without_database(monkeypatch, method):
    unit_write_connection(monkeypatch, post_author_id=2)
    with pytest.raises(HTTPException) as error:
        if method == "PUT":
            main.accept_comment(10, main.AcceptedComment(comment_id=20), {"id": 1, "is_trial": False})
        else:
            main.unaccept_comment(10, {"id": 1, "is_trial": False})
    assert error.value.status_code == 404
    assert error.value.detail == "帖子不存在"


def test_unit_accept_rejects_own_comment_without_database(monkeypatch):
    unit_write_connection(monkeypatch, comment_author_id=1)
    with pytest.raises(HTTPException) as error:
        main.accept_comment(10, main.AcceptedComment(comment_id=20), {"id": 1, "is_trial": False})
    assert error.value.status_code == 400
    assert error.value.detail == "不能采纳自己的评论"


@pytest.mark.parametrize("helpful", [True, False])
def test_unit_helpful_rejects_self_vote_without_database(monkeypatch, helpful):
    unit_write_connection(monkeypatch, comment_author_id=1)
    with pytest.raises(HTTPException) as error:
        main.change_comment_helpful(20, {"id": 1, "is_trial": False}, helpful=helpful)
    assert error.value.status_code == 400
    assert error.value.detail == "不能给自己的评论点有用"


@pytest.mark.parametrize("endpoint", ["accept", "unaccept", "helpful", "unhelpful"])
def test_unit_new_writes_reject_trial_before_database(monkeypatch, endpoint):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("体验账号不能进入数据库写流程")

    monkeypatch.setattr(main, "connect", unexpected_connection)
    user = {"id": 1, "is_trial": True}
    with pytest.raises(HTTPException) as error:
        if endpoint == "accept":
            main.accept_comment(10, main.AcceptedComment(comment_id=20), user)
        elif endpoint == "unaccept":
            main.unaccept_comment(10, user)
        else:
            main.change_comment_helpful(20, user, helpful=endpoint == "helpful")
    assert error.value.status_code == 403
    action = "采纳" if endpoint in ("accept", "unaccept") else "点有用"
    assert error.value.detail == f"体验账号不支持{action}"


@pytest.mark.parametrize("comment_id", [2**63, 10**100])
def test_unit_accept_overflow_is_missing_without_database(monkeypatch, comment_id):
    conn = unit_write_connection(monkeypatch)
    with pytest.raises(HTTPException) as error:
        main.accept_comment(10, main.AcceptedComment(comment_id=comment_id), {"id": 1, "is_trial": False})
    assert error.value.status_code == 400
    assert error.value.detail == "这条评论不存在或已删除"
    assert len(conn.statements) == 1


@pytest.mark.parametrize("helpful", [True, False])
def test_unit_helpful_rate_limit_exact_contract_without_database(monkeypatch, helpful):
    unit_write_connection(monkeypatch)
    calls = []

    def limited(key, limit, seconds):
        calls.append((key, limit, seconds))
        return True

    monkeypatch.setattr(main, "rate_limited", limited)
    with pytest.raises(HTTPException) as error:
        main.change_comment_helpful(20, {"id": 1, "is_trial": False}, helpful=helpful)
    assert error.value.status_code == 429
    assert error.value.detail == "操作过于频繁，请稍后再试"
    assert calls == [("helpful:1", 60, 60)]


@pytest.mark.parametrize("path", [
    "/api/posts/not-an-id/accepted", "/api/posts/10/accepted",
    "/api/posts/not-an-id/summary", "/api/comments/not-an-id/helpful",
])
def test_unit_new_route_validation_errors_are_chinese_without_database(path):
    request = SimpleNamespace(url=SimpleNamespace(path=path))
    response = asyncio.run(main.forum_action_validation_error(
        request, main.RequestValidationError([]),
    ))
    assert response.status_code == 422
    assert json.loads(response.body) == {"detail": "请求参数不正确，请检查后重试"}
