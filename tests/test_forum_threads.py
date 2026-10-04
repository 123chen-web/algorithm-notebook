"""Forum floors and reply references, including legacy database upgrades."""

import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from db import connect, init_db
from test_app import register
from test_forum import create_post


CREATED_AT = "2026-09-21T10:00:00+00:00"
COMMENT_FIELDS = {
    "id", "post_id", "user_id", "username", "avatar_version", "has_avatar",
    "body", "created_at", "updated_at", "deleted_at", "reply_to_id",
    "floor", "is_op", "reply_to",
    "helpful_count", "viewer_helpful",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    # Keep avatar checks away from the developer's real avatar directory.
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 9, 19))
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def create_comment(client, post_id, body="一条评论", **overrides):
    response = client.post(
        f"/api/posts/{post_id}/comments", json={"body": body, **overrides}
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_comments(client, post_id):
    response = client.get(f"/api/posts/{post_id}")
    assert response.status_code == 200
    return response.json()["comments"]


@pytest.mark.parametrize("legacy", [True, False], ids=["legacy", "new"])
def test_schema_is_nullable_self_reference_and_upgrade_preserves_data(
    tmp_path, monkeypatch, legacy
):
    path = tmp_path / "forum.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if legacy:
        with closing(sqlite3.connect(path)) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, timezone TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT, deleted_at TEXT
                );
                CREATE TABLE post_comments (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    body TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT, deleted_at TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO users VALUES (7, '旧用户', 'original-hash', "
                "'Asia/Shanghai', ?)", (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO posts VALUES (13, 7, '旧帖子', '保留原文', ?, NULL, NULL)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO post_comments VALUES (31, 13, 7, '旧评论', ?, NULL, NULL)",
                (CREATED_AT,),
            )
            conn.commit()
    else:
        init_db()
        with connect(write=True) as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, timezone, created_at) "
                "VALUES (7, '旧用户', 'original-hash', 'Asia/Shanghai', ?)",
                (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO posts(id, user_id, title, body, created_at) "
                "VALUES (13, 7, '旧帖子', '保留原文', ?)", (CREATED_AT,),
            )
            conn.execute(
                "INSERT INTO post_comments(id, post_id, user_id, body, created_at) "
                "VALUES (31, 13, 7, '旧评论', ?)", (CREATED_AT,),
            )

    init_db()
    init_db()
    with connect(write=True) as conn:
        columns = {
            row["name"]: row for row in conn.execute("PRAGMA table_info(post_comments)")
        }
        assert columns["reply_to_id"]["type"] == "INTEGER"
        assert columns["reply_to_id"]["notnull"] == 0
        foreign_keys = list(conn.execute("PRAGMA foreign_key_list(post_comments)"))
        assert any(
            row["from"] == "reply_to_id" and row["table"] == "post_comments"
            and row["to"] == "id" and row["on_delete"] == "SET NULL"
            for row in foreign_keys
        )
        indexes = {
            row["name"] for row in conn.execute("PRAGMA index_list(post_comments)")
        }
        assert "idx_post_comments_reply_to" in indexes
        assert [
            row["name"]
            for row in conn.execute("PRAGMA index_info(idx_post_comments_reply_to)")
        ] == ["reply_to_id"]
        query_plan = conn.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM post_comments WHERE reply_to_id = ?",
            (31,),
        ).fetchall()
        assert any("idx_post_comments_reply_to" in row["detail"] for row in query_plan)
        comment = dict(conn.execute("SELECT * FROM post_comments WHERE id = 31").fetchone())
        assert comment == {
            "id": 31, "post_id": 13, "user_id": 7, "body": "旧评论",
            "created_at": CREATED_AT, "updated_at": None, "deleted_at": None,
            "reply_to_id": None,
        }
        user = conn.execute("SELECT * FROM users WHERE id = 7").fetchone()
        assert user["password_hash"] == "original-hash"
        post = conn.execute("SELECT * FROM posts WHERE id = 13").fetchone()
        assert post["title"] == "旧帖子"
        assert post["body"] == "保留原文"
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, 7, ?)",
            (main.token_hash("legacy-session"), int(time.time()) + 3600),
        )

    # Exercise the real API against both the migrated and fresh schemas.
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as api:
        api.cookies.set("session", "legacy-session")
        old_comment = get_comments(api, 13)[0]
        assert old_comment["body"] == "旧评论"
        assert old_comment["floor"] == 1
        assert old_comment["reply_to"] is None
        reply = create_comment(api, 13, "升级后回复", reply_to_id=31)
        assert reply["floor"] == 2
        assert reply["reply_to"]["excerpt"] == "旧评论"
        assert api.put(
            f"/api/comments/{reply['id']}", json={"body": "升级后编辑"}
        ).status_code == 200
    with connect(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE post_comments SET reply_to_id = 999999 WHERE id = ?",
                (reply["id"],),
            )
        conn.execute("DELETE FROM post_comments WHERE id = 31")
        assert conn.execute(
            "SELECT reply_to_id FROM post_comments WHERE id = ?", (reply["id"],)
        ).fetchone()[0] is None


def test_floor_is_per_post_id_order_and_stable_after_middle_soft_delete(client):
    register(client)
    post = create_post(client)
    other = create_post(client)
    first = create_comment(client, post["id"])
    unrelated = create_comment(client, other["id"])
    middle = create_comment(client, post["id"])
    last = create_comment(client, post["id"])
    assert unrelated["floor"] == 1
    assert [first["floor"], middle["floor"], last["floor"]] == [1, 2, 3]
    # Deliberately make timestamps disagree with IDs; they never define a floor.
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE post_comments SET created_at = '2025-01-01T00:00:00+00:00' "
            "WHERE id = ?", (last["id"],),
        )
    ordered = get_comments(client, post["id"])
    assert [c["id"] for c in ordered] == [last["id"], first["id"], middle["id"]]
    before = {c["id"]: c["floor"] for c in ordered}
    assert before == {first["id"]: 1, middle["id"]: 2, last["id"]: 3}
    assert client.delete(f"/api/comments/{middle['id']}").status_code == 200
    after = {c["id"]: c["floor"] for c in get_comments(client, post["id"])}
    assert after == {first["id"]: 1, last["id"]: 3}
    assert create_comment(client, post["id"])["floor"] == 4


def test_is_op_and_existing_fields_are_consistent_for_create_get_edit(client):
    author = register(client, "周知远")
    post = create_post(client)
    op_comment = create_comment(client, post["id"], "楼主评论")
    assert op_comment["is_op"] is True
    assert op_comment["user_id"] == author["id"]
    client.post("/api/auth/logout")
    reader = register(client, "苏晚")
    comment = create_comment(client, post["id"], "读者评论")
    assert comment["is_op"] is False
    assert comment["user_id"] == reader["id"]
    assert COMMENT_FIELDS <= comment.keys()
    assert comment["post_id"] == post["id"]
    assert comment["username"] == "苏晚"
    assert comment["avatar_version"] == 0
    assert comment["has_avatar"] is False
    assert comment["helpful_count"] == 0
    assert comment["viewer_helpful"] is False
    assert get_comments(client, post["id"])[1] == comment
    updated = client.put(
        f"/api/comments/{comment['id']}", json={"body": "编辑后的读者评论"}
    ).json()
    assert COMMENT_FIELDS <= updated.keys()
    assert updated["body"] == "编辑后的读者评论"
    assert updated["updated_at"] is not None
    assert updated["floor"] == 2
    assert updated["is_op"] is False
    assert updated["helpful_count"] == 0
    assert updated["viewer_helpful"] is False
    assert get_comments(client, post["id"])[1] == updated


def test_reply_to_same_post_and_self_is_returned_by_create_get_and_edit(client):
    register(client, "苏晚")
    post = create_post(client)
    target = create_comment(client, post["id"], "  第一行\n\t第二行   第三行  ")
    reply = create_comment(client, post["id"], "回复自己", reply_to_id=target["id"])
    expected = {
        "id": target["id"], "floor": 1, "username": "苏晚",
        "excerpt": "第一行 第二行 第三行", "deleted": False,
    }
    assert reply["reply_to_id"] == target["id"]
    assert reply["reply_to"] == expected
    assert reply["floor"] == 2
    assert get_comments(client, post["id"])[1] == reply
    response = client.put(f"/api/comments/{reply['id']}", json={"body": "编辑回复"})
    assert response.status_code == 200
    assert response.json()["reply_to"] == expected
    assert response.json()["floor"] == 2
    assert response.json()["is_op"] is True


def test_other_author_can_reply_to_op_and_non_op(client):
    register(client, "楼主")
    post = create_post(client)
    op = create_comment(client, post["id"], "楼主说明")
    client.post("/api/auth/logout")
    register(client, "读者甲")
    other = create_comment(client, post["id"], "读者甲补充", reply_to_id=op["id"])
    assert other["reply_to"]["username"] == "楼主"
    client.post("/api/auth/logout")
    register(client, "读者乙")
    reply = create_comment(client, post["id"], reply_to_id=other["id"])
    assert reply["is_op"] is False
    assert reply["reply_to"] == {
        "id": other["id"], "floor": 2, "username": "读者甲",
        "excerpt": "读者甲补充", "deleted": False,
    }


@pytest.mark.parametrize(
    "target_kind",
    ["other_post", "deleted", "missing", "max_sqlite_id", "overflow", "huge"],
)
def test_invalid_reply_target_returns_400_without_creating_comment(client, target_kind):
    register(client)
    post = create_post(client)
    if target_kind == "other_post":
        target = create_comment(client, create_post(client)["id"])
        target_id = target["id"]
    elif target_kind == "deleted":
        target = create_comment(client, post["id"])
        target_id = target["id"]
        assert client.delete(f"/api/comments/{target_id}").status_code == 200
    else:
        target_id = {
            "missing": 999999,
            "max_sqlite_id": 2**63 - 1,
            "overflow": 2**63,
            "huge": 10**100,
        }[target_kind]
    before = get_comments(client, post["id"])
    response = client.post(
        f"/api/posts/{post['id']}/comments",
        json={"body": "失败的回复", "reply_to_id": target_id},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "被回复的评论不存在或已删除"
    assert get_comments(client, post["id"]) == before


def test_soft_deleted_reference_redacts_author_and_body_on_get_and_edit(client):
    register(client, "敏感作者")
    post = create_post(client)
    target = create_comment(client, post["id"], "删除后不能泄露的正文")
    reply = create_comment(client, post["id"], "保留回复", reply_to_id=target["id"])
    assert client.delete(f"/api/comments/{target['id']}").status_code == 200
    expected = {"id": target["id"], "floor": 1, "deleted": True}
    remaining = get_comments(client, post["id"])
    assert len(remaining) == 1
    assert remaining[0]["floor"] == 2
    assert remaining[0]["reply_to"] == expected
    updated = client.put(
        f"/api/comments/{reply['id']}", json={"body": "编辑保留回复"}
    )
    assert updated.status_code == 200
    assert updated.json()["reply_to"] == expected
    assert "username" not in updated.json()["reply_to"]
    assert "excerpt" not in updated.json()["reply_to"]
    assert "body" not in updated.json()["reply_to"]


@pytest.mark.parametrize("deleted_by", ["author", "admin"])
def test_edit_reply_in_deleted_post_does_not_read_hidden_reference(
    client, monkeypatch, deleted_by
):
    register(client, "楼主")
    post = create_post(client)
    client.post("/api/auth/logout")
    register(client, "被引用甲")
    target = create_comment(client, post["id"], "父帖删除后不能返回的第三方正文")
    client.post("/api/auth/logout")
    register(client, "回复乙")
    reply = create_comment(client, post["id"], "乙的回复", reply_to_id=target["id"])
    client.post("/api/auth/logout")
    if deleted_by == "admin":
        monkeypatch.setenv("ADMIN_USERNAME", "moderator_user")
        register(client, "moderator_user")
        delete_url = f"/api/admin/posts/{post['id']}"
    else:
        assert client.post(
            "/api/auth/login",
            json={"username": "楼主", "password": "a-test-password-123"},
        ).status_code == 200
        delete_url = f"/api/posts/{post['id']}"
    assert client.delete(delete_url).status_code == 200
    assert client.get(f"/api/posts/{post['id']}").status_code == 404
    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login",
        json={"username": "回复乙", "password": "a-test-password-123"},
    ).status_code == 200
    response = client.put(
        f"/api/comments/{reply['id']}", json={"body": "乙仍然可以编辑自己的回复"}
    )
    assert response.status_code == 200
    assert response.json()["body"] == "乙仍然可以编辑自己的回复"
    assert response.json()["reply_to"] is None
    assert "被引用甲" not in response.text
    assert target["body"] not in response.text


def test_get_post_uses_one_snapshot_during_physical_comment_delete(client, monkeypatch):
    register(client)
    post = create_post(client)
    first = create_comment(client, post["id"], "第一层")
    second = create_comment(client, post["id"], "第二层", reply_to_id=first["id"])
    third = create_comment(client, post["id"], "第三层")
    interleaved = False

    class InterleavedConnection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, statement, parameters=()):
            nonlocal interleaved
            if "FROM post_comments c" in statement and "WHERE c.post_id = ?" in statement:
                assert self.conn.in_transaction
                # The post SELECT has already established a WAL read snapshot.
                with connect(write=True) as writer:
                    writer.execute("DELETE FROM post_comments WHERE id = ?", (first["id"],))
                interleaved = True
            return self.conn.execute(statement, parameters)

    @contextmanager
    def interleaved_connect(write=False):
        with connect(write=write) as conn:
            yield InterleavedConnection(conn)

    monkeypatch.setattr(main, "connect", interleaved_connect)
    comments = get_comments(client, post["id"])
    assert interleaved
    assert [comment["id"] for comment in comments] == [first["id"], second["id"], third["id"]]
    assert [comment["floor"] for comment in comments] == [1, 2, 3]
    assert comments[1]["reply_to"] == second["reply_to"]
    with connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM post_comments WHERE id = ?", (first["id"],)
        ).fetchone() is None


def test_get_500_comments_has_bounded_sql_and_counts_soft_deleted_floors(client, monkeypatch):
    user = register(client)
    post = create_post(client)
    with connect(write=True) as conn:
        first_id = conn.execute(
            "INSERT INTO post_comments(post_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (post["id"], user["id"], "第一层引用摘要", CREATED_AT),
        ).lastrowid
        conn.executemany(
            "INSERT INTO post_comments(post_id, user_id, body, created_at, reply_to_id) "
            "VALUES (?, ?, ?, ?, ?)",
            [(post["id"], user["id"], f"第 {floor} 层", CREATED_AT, first_id)
             for floor in range(2, 501)],
        )
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM post_comments WHERE post_id = ? ORDER BY id", (post["id"],)
        )]
        conn.execute(
            "UPDATE post_comments SET deleted_at = ? WHERE id = ?", (CREATED_AT, ids[249])
        )
        conn.execute(
            "UPDATE post_comments SET reply_to_id = ? WHERE id = ?", (ids[249], ids[-1])
        )
    statements = []

    @contextmanager
    def traced_connect(write=False):
        with connect(write=write) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(main, "connect", traced_connect)
    comments = get_comments(client, post["id"])
    assert len(statements) <= 12, statements
    assert len(comments) == 499
    assert [comment["floor"] for comment in comments] == [
        floor for floor in range(1, 501) if floor != 250
    ]
    assert comments[1]["reply_to"] == {
        "id": ids[0], "floor": 1, "deleted": False,
        "username": user["username"], "excerpt": "第一层引用摘要",
    }
    assert comments[-1]["reply_to"] == {"id": ids[249], "floor": 250, "deleted": True}


def test_physically_deleted_reference_becomes_null(client):
    register(client)
    post = create_post(client)
    target = create_comment(client, post["id"])
    create_comment(client, post["id"], "留下回复", reply_to_id=target["id"])
    with connect(write=True) as conn:
        conn.execute("DELETE FROM post_comments WHERE id = ?", (target["id"],))
    remaining = get_comments(client, post["id"])
    assert len(remaining) == 1
    assert remaining[0]["reply_to_id"] is None
    assert remaining[0]["reply_to"] is None


@pytest.mark.parametrize("length", [59, 60, 61, 100])
def test_reply_excerpt_counts_unicode_characters_and_only_truncates_over_60(client, length):
    register(client)
    post = create_post(client)
    body = "界" * length
    target = create_comment(client, post["id"], body)
    reply = create_comment(client, post["id"], reply_to_id=target["id"])
    expected = body[:60] + ("…" if length > 60 else "")
    assert reply["reply_to"]["excerpt"] == expected
    assert len(reply["reply_to"]["excerpt"]) == min(length, 60) + (length > 60)


def test_reply_excerpt_normalizes_whitespace_before_truncation(client):
    register(client)
    post = create_post(client)
    target = create_comment(client, post["id"], "甲\n\t 乙\u3000丙  " + "丁" * 70)
    reply = create_comment(client, post["id"], reply_to_id=target["id"])
    normalized = "甲 乙 丙 " + "丁" * 70
    assert reply["reply_to"]["excerpt"] == normalized[:60] + "…"


def test_trial_account_remains_forbidden_with_or_without_reply(client):
    register(client)
    post = create_post(client)
    target = create_comment(client, post["id"])
    client.post("/api/auth/logout")
    assert client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).status_code == 201
    for payload in (
        {"body": "体验账号普通评论"},
        {"body": "体验账号回复", "reply_to_id": target["id"]},
    ):
        assert client.post(
            f"/api/posts/{post['id']}/comments", json=payload
        ).status_code == 403
    assert len(get_comments(client, post["id"])) == 1


@pytest.mark.parametrize("explicit_null", [False, True])
def test_omitted_or_null_reply_keeps_normal_comment_behavior(client, explicit_null):
    user = register(client)
    post = create_post(client)
    payload = {"reply_to_id": None} if explicit_null else {}
    comment = create_comment(client, post["id"], "  原有评论行为  ", **payload)
    assert comment["body"] == "原有评论行为"
    assert comment["user_id"] == user["id"]
    assert comment["post_id"] == post["id"]
    assert comment["updated_at"] is None
    assert comment["deleted_at"] is None
    assert comment["reply_to_id"] is None
    assert comment["reply_to"] is None
    assert comment["floor"] == 1
    assert get_comments(client, post["id"])[0] == comment


@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, 1.5, "1", "", [], {}])
def test_reply_to_id_model_requires_a_positive_strict_integer(value):
    with pytest.raises(ValidationError):
        main.NewComment(body="回复", reply_to_id=value)


@pytest.mark.parametrize("value", [None, 1, 2**63, 10**100])
def test_reply_to_id_model_accepts_null_or_positive_integer_without_id_lookup(value):
    assert main.NewComment(body="回复", reply_to_id=value).reply_to_id == value


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "1"])
def test_reply_to_id_api_validation_returns_422(client, value):
    register(client)
    post = create_post(client)
    response = client.post(
        f"/api/posts/{post['id']}/comments", json={"body": "回复", "reply_to_id": value}
    )
    assert response.status_code == 422
    assert get_comments(client, post["id"]) == []
