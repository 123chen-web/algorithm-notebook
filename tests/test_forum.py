"""针对讨论区（发帖 + 评论 + 封禁）相关接口的测试。"""
from db import connect
from test_app import client, register


PASSWORD = "a-test-password-123"


def create_post(client, title="讨论标题", body="讨论正文", **overrides):
    payload = {"title": title, "body": body}
    payload.update(overrides)
    response = client.post("/api/posts", json=payload)
    assert response.status_code == 201
    return response.json()


def create_comment(client, post_id, body="一条评论"):
    response = client.post(f"/api/posts/{post_id}/comments", json={"body": body})
    assert response.status_code == 201
    return response.json()


def ban(user_id):
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))


def test_create_post_returns_post_with_username(client):
    register(client, "alice")
    post = create_post(client, "标题", "正文")
    assert post["title"] == "标题"
    assert post["body"] == "正文"
    assert post["username"] == "alice"
    assert post["updated_at"] is None
    assert post["deleted_at"] is None


def test_list_posts_orders_newest_first_with_comment_count(client):
    register(client)
    first = create_post(client, "第一条")
    second = create_post(client, "第二条")
    create_comment(client, first["id"], "评论一")
    create_comment(client, first["id"], "评论二")

    posts = client.get("/api/posts").json()["posts"]
    assert [p["id"] for p in posts] == [second["id"], first["id"]]
    counts = {p["id"]: p["comment_count"] for p in posts}
    assert counts[first["id"]] == 2
    assert counts[second["id"]] == 0
    # 列表不带正文，避免响应过大。
    assert "body" not in posts[0]


def test_get_post_detail_includes_comments_with_username(client):
    register(client, "alice")
    post = create_post(client)
    create_comment(client, post["id"], "第一条评论")

    detail = client.get(f"/api/posts/{post['id']}").json()
    assert detail["title"] == post["title"]
    assert len(detail["comments"]) == 1
    assert detail["comments"][0]["body"] == "第一条评论"
    assert detail["comments"][0]["username"] == "alice"


def test_edit_post_updates_body_and_updated_at(client):
    register(client)
    post = create_post(client, "旧标题", "旧正文")
    assert post["updated_at"] is None

    response = client.put(
        f"/api/posts/{post['id']}",
        json={"title": "新标题", "body": "新正文"},
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["title"] == "新标题"
    assert updated["body"] == "新正文"
    assert updated["updated_at"] is not None


def test_delete_post_soft_deletes_and_hides_from_list_and_detail(client):
    register(client)
    post = create_post(client)

    response = client.delete(f"/api/posts/{post['id']}")
    assert response.status_code == 200

    assert client.get("/api/posts").json()["posts"] == []
    assert client.get(f"/api/posts/{post['id']}").status_code == 404
    # 软删不是物理删除，数据仍在库里。
    with connect() as conn:
        row = conn.execute(
            "SELECT deleted_at FROM posts WHERE id = ?", (post["id"],)
        ).fetchone()
    assert row["deleted_at"] is not None


def test_cannot_comment_on_deleted_post(client):
    register(client)
    post = create_post(client)
    client.delete(f"/api/posts/{post['id']}")

    response = client.post(
        f"/api/posts/{post['id']}/comments", json={"body": "还能评论吗"}
    )
    assert response.status_code == 404


def test_edit_comment_updates_body_and_updated_at(client):
    register(client)
    post = create_post(client)
    comment = create_comment(client, post["id"], "原始评论")
    assert comment["updated_at"] is None

    response = client.put(
        f"/api/comments/{comment['id']}", json={"body": "改过的评论"}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["body"] == "改过的评论"
    assert updated["updated_at"] is not None


def test_delete_comment_soft_deletes_and_excluded_from_count_and_detail(client):
    register(client)
    post = create_post(client)
    comment = create_comment(client, post["id"])

    response = client.delete(f"/api/comments/{comment['id']}")
    assert response.status_code == 200

    detail = client.get(f"/api/posts/{post['id']}").json()
    assert detail["comments"] == []
    posts = client.get("/api/posts").json()["posts"]
    assert posts[0]["comment_count"] == 0


def test_cannot_edit_or_delete_others_post(client):
    alice_post = None
    register(client, "alice")
    alice_post = create_post(client, "alice 的帖子")
    client.post("/api/auth/logout")

    register(client, "bob")
    assert client.put(
        f"/api/posts/{alice_post['id']}",
        json={"title": "改标题", "body": "改正文"},
    ).status_code == 404
    assert client.delete(f"/api/posts/{alice_post['id']}").status_code == 404
    # 帖子本身对 bob 仍然可见（未被改动），只是编辑/删除被拒绝。
    assert client.get(f"/api/posts/{alice_post['id']}").json()["title"] == "alice 的帖子"


def test_cannot_edit_or_delete_others_comment(client):
    register(client, "alice")
    post = create_post(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    comment = create_comment(client, post["id"], "bob 的评论")
    client.post("/api/auth/logout")

    register(client, "carol")
    assert client.put(
        f"/api/comments/{comment['id']}", json={"body": "改评论"}
    ).status_code == 404
    assert client.delete(f"/api/comments/{comment['id']}").status_code == 404


def test_trial_account_can_browse_but_not_write(client):
    register(client, "alice")
    post = create_post(client)
    client.post("/api/auth/logout")

    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    # 浏览不受限。
    assert client.get("/api/posts").status_code == 200
    assert client.get(f"/api/posts/{post['id']}").status_code == 200
    # 发帖、评论、编辑、删除全部被拒绝。
    assert client.post(
        "/api/posts", json={"title": "体验账号标题", "body": "体验账号正文"}
    ).status_code == 403
    assert client.post(
        f"/api/posts/{post['id']}/comments", json={"body": "体验账号评论"}
    ).status_code == 403
    assert client.put(
        f"/api/posts/{post['id']}",
        json={"title": "改标题", "body": "改正文"},
    ).status_code == 403
    assert client.delete(f"/api/posts/{post['id']}").status_code == 403


def test_post_and_comment_show_real_username_not_anonymized(client):
    register(client, "realname")
    post = create_post(client)
    comment = create_comment(client, post["id"])

    assert post["username"] == "realname"
    assert comment["username"] == "realname"
    listed = client.get("/api/posts").json()["posts"][0]
    assert listed["username"] == "realname"
    # 跟排行榜的匿名标识规则刚好相反，这里不应该出现 "用户 #" 这种格式。
    assert not listed["username"].startswith("用户 #")


def test_banned_account_cannot_login(client):
    user = register(client, "alice")
    client.post("/api/auth/logout")
    ban(user["id"])

    response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
    )
    assert response.status_code == 403


def test_account_banned_mid_session_is_rejected_on_next_request(client):
    user = register(client, "alice")
    # 会话仍然有效（没有登出、没有过期），期间被封禁。
    ban(user["id"])

    response = client.get("/api/me")
    assert response.status_code == 401
    assert "封禁" in response.json()["detail"]
