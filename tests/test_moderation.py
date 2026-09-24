"""针对举报 + 管理后台（单管理员账号）的测试。"""
import main
from db import connect
from test_app import client, register
from test_forum import create_comment, create_post


def report_post(client, post_id, reason="内容不合适"):
    return client.post(f"/api/posts/{post_id}/report", json={"reason": reason})


def report_comment(client, comment_id, reason="内容不合适"):
    return client.post(f"/api/comments/{comment_id}/report", json={"reason": reason})


def become_admin(client, monkeypatch, username="alice"):
    monkeypatch.setenv("ADMIN_USERNAME", username)


def test_report_post_creates_pending_report_visible_to_admin(client, monkeypatch):
    register(client, "alice")
    post = create_post(client, "被举报的帖子", "正文")
    client.post("/api/auth/logout")

    register(client, "bob")
    response = report_post(client, post["id"], reason="骚扰内容")
    assert response.status_code == 201
    client.post("/api/auth/logout")

    register(client, "carol")
    become_admin(client, monkeypatch, "carol")
    reports = client.get("/api/admin/reports").json()["reports"]
    assert len(reports) == 1
    assert reports[0]["reason"] == "骚扰内容"
    assert reports[0]["post_id"] == post["id"]
    assert reports[0]["post_title"] == "被举报的帖子"
    assert reports[0]["post_author_username"] == "alice"
    assert reports[0]["reporter_username"] == "bob"


def test_report_comment_creates_pending_report(client, monkeypatch):
    register(client, "alice")
    post = create_post(client)
    comment = create_comment(client, post["id"], "评论正文")
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_comment(client, comment["id"]).status_code == 201
    become_admin(client, monkeypatch, "bob")

    reports = client.get("/api/admin/reports").json()["reports"]
    assert len(reports) == 1
    assert reports[0]["comment_id"] == comment["id"]
    assert reports[0]["comment_body"] == "评论正文"
    assert reports[0]["comment_author_username"] == "alice"


def test_cannot_report_own_post(client):
    register(client)
    post = create_post(client)
    assert report_post(client, post["id"]).status_code == 400


def test_duplicate_pending_report_rejected(client):
    register(client, "alice")
    post = create_post(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_post(client, post["id"]).status_code == 201
    assert report_post(client, post["id"]).status_code == 409


def test_report_rate_limited(client, monkeypatch):
    monkeypatch.setattr(main, "REPORT_LIMIT", 1)
    register(client, "alice")
    first = create_post(client, "第一条")
    second = create_post(client, "第二条")
    client.post("/api/auth/logout")

    register(client, "bob")
    assert report_post(client, first["id"]).status_code == 201
    assert report_post(client, second["id"]).status_code == 429


def test_trial_account_cannot_report(client):
    register(client, "alice")
    post = create_post(client)
    client.post("/api/auth/logout")

    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert report_post(client, post["id"]).status_code == 403


def test_non_admin_cannot_access_admin_endpoints(client):
    register(client, "alice")
    post = create_post(client)

    assert client.get("/api/admin/reports").status_code == 403
    assert client.delete(f"/api/admin/posts/{post['id']}").status_code == 403
    assert client.post("/api/admin/reports/1/resolve").status_code == 403
    assert client.post("/api/admin/users/1/ban").status_code == 403


def test_admin_resolve_report_removes_it_from_queue_without_deleting(
    client, monkeypatch
):
    register(client, "alice")
    post = create_post(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    report_post(client, post["id"])
    become_admin(client, monkeypatch, "bob")
    report_id = client.get("/api/admin/reports").json()["reports"][0]["id"]

    response = client.post(f"/api/admin/reports/{report_id}/resolve")
    assert response.status_code == 200
    assert client.get("/api/admin/reports").json()["reports"] == []
    # 忽略举报不影响内容本身。
    assert client.get(f"/api/posts/{post['id']}").status_code == 200


def test_admin_delete_post_soft_deletes_and_auto_resolves_report(client, monkeypatch):
    register(client, "alice")
    post = create_post(client)
    client.post("/api/auth/logout")

    register(client, "bob")
    report_post(client, post["id"])
    become_admin(client, monkeypatch, "bob")

    response = client.delete(f"/api/admin/posts/{post['id']}")
    assert response.status_code == 200
    assert client.get(f"/api/posts/{post['id']}").status_code == 404
    assert client.get("/api/admin/reports").json()["reports"] == []


def test_admin_delete_comment_soft_deletes_and_auto_resolves_report(
    client, monkeypatch
):
    register(client, "alice")
    post = create_post(client)
    comment = create_comment(client, post["id"])
    client.post("/api/auth/logout")

    register(client, "bob")
    report_comment(client, comment["id"])
    become_admin(client, monkeypatch, "bob")

    response = client.delete(f"/api/admin/comments/{comment['id']}")
    assert response.status_code == 200
    assert client.get(f"/api/posts/{post['id']}").json()["comments"] == []
    assert client.get("/api/admin/reports").json()["reports"] == []


def test_admin_ban_and_unban_user(client, monkeypatch):
    target = register(client, "alice")
    client.post("/api/auth/logout")

    register(client, "bob")
    become_admin(client, monkeypatch, "bob")

    assert client.post(f"/api/admin/users/{target['id']}/ban").status_code == 200
    with connect() as conn:
        assert conn.execute(
            "SELECT is_banned FROM users WHERE id = ?", (target["id"],)
        ).fetchone()["is_banned"] == 1

    assert client.post(f"/api/admin/users/{target['id']}/unban").status_code == 200
    with connect() as conn:
        assert conn.execute(
            "SELECT is_banned FROM users WHERE id = ?", (target["id"],)
        ).fetchone()["is_banned"] == 0


def test_admin_cannot_ban_self(client, monkeypatch):
    admin = register(client, "alice")
    become_admin(client, monkeypatch, "alice")

    response = client.post(f"/api/admin/users/{admin['id']}/ban")
    assert response.status_code == 400
