"""讨论区冻结开关（FORUM_ENABLED）的测试。

开/关两种配置各一组：
- 关闭时：所有论坛接口统一 404（中文 detail“讨论区暂未开放”）；
  /api/me 返回 features.forum=false；总览 hot_post 为空；/api/search 不含帖子；
  导出、注销等非论坛功能不受影响；已有数据不删，重开即回来。
- 开启时（默认）：一切照旧。
"""
import pytest

from core import config
from db import connect
from test_app import client, register


@pytest.fixture
def forum_off(monkeypatch):
    monkeypatch.setattr(config, "FORUM_ENABLED", False)


@pytest.fixture
def forum_on(monkeypatch):
    monkeypatch.setattr(config, "FORUM_ENABLED", True)


def _forum_routes():
    return [
        ("GET", "/api/posts", None),
        ("POST", "/api/posts", {"title": "t", "body": "b"}),
        ("GET", "/api/posts/1", None),
        ("PUT", "/api/posts/1", {"title": "t"}),
        ("DELETE", "/api/posts/1", None),
        ("POST", "/api/posts/1/comments", {"body": "c"}),
        ("PUT", "/api/comments/1", {"body": "c"}),
        ("DELETE", "/api/comments/1", None),
        ("PUT", "/api/posts/1/accepted", {"comment_id": 1}),
        ("DELETE", "/api/posts/1/accepted", None),
        ("PUT", "/api/comments/1/helpful", None),
        ("DELETE", "/api/comments/1/helpful", None),
        ("GET", "/api/posts/1/summary", None),
        ("POST", "/api/posts/1/summary", {}),
        ("POST", "/api/posts/1/report", {"reason": "spam"}),
        ("POST", "/api/comments/1/report", {"reason": "spam"}),
    ]


@pytest.mark.parametrize("method,path,body", _forum_routes())
def test_forum_routes_all_404_when_disabled(client, forum_off, method, path, body):
    register(client, "alice")
    response = client.request(method, path, json=body)
    assert response.status_code == 404
    assert response.json()["detail"] == "讨论区暂未开放"


def test_me_reports_forum_feature_flag(client, forum_off):
    register(client, "alice")
    me = client.get("/api/me").json()
    assert me["features"] == {"forum": False}


def test_me_reports_forum_enabled_by_default(client, forum_on):
    register(client, "alice")
    me = client.get("/api/me").json()
    assert me["features"] == {"forum": True}


def test_forum_routes_work_when_enabled(client, forum_on):
    register(client, "alice")
    response = client.get("/api/posts")
    assert response.status_code == 200
    created = client.post("/api/posts", json={"title": "t", "body": "b"})
    assert created.status_code == 201


def test_overview_hot_post_hidden_when_disabled(client, forum_off):
    register(client, "alice")
    overview = client.get("/api/overview").json()
    assert overview["hot_post"] is None


def test_search_returns_no_posts_when_disabled(client, forum_off):
    register(client, "alice")
    result = client.get("/api/search", params={"q": "二分"}).json()
    assert result["posts"] == []
    assert result["truncated"]["posts"] is False


def test_search_returns_posts_when_enabled(client, forum_on):
    register(client, "alice")
    client.post("/api/posts", json={"title": "二分查找讨论", "body": "正文"})
    result = client.get("/api/search", params={"q": "二分"}).json()
    assert len(result["posts"]) == 1


def test_export_still_works_when_disabled(client, forum_off):
    register(client, "alice")
    response = client.get("/api/export")
    assert response.status_code == 200


def test_delete_account_route_not_gated_when_disabled(client, forum_off):
    register(client, "alice")
    # 空 body 会触发 422 校验，关键是不能是 404（论坛门控没误伤）。
    response = client.post("/api/me/delete-account", json={})
    assert response.status_code != 404


def test_forum_data_kept_and_back_after_reopen(client, forum_on, monkeypatch):
    register(client, "alice")
    post = client.post("/api/posts", json={"title": "保留", "body": "正文"}).json()
    post_id = post["id"]
    # 关闭：接口 404，但数据还在库里。
    monkeypatch.setattr(config, "FORUM_ENABLED", False)
    assert client.get(f"/api/posts/{post_id}").status_code == 404
    with connect() as conn:
        row = conn.execute("SELECT title FROM posts WHERE id = ?", (post_id,)).fetchone()
    assert row["title"] == "保留"
    # 重新打开：原样回来。
    monkeypatch.setattr(config, "FORUM_ENABLED", True)
    fetched = client.get(f"/api/posts/{post_id}")
    assert fetched.status_code == 200
    assert fetched.json()["title"] == "保留"


def test_config_parses_env_flag():
    assert config._parse_env_flag(None, True) is True
    assert config._parse_env_flag("1", True) is True
    assert config._parse_env_flag("0", True) is False
    assert config._parse_env_flag("false", True) is False
    assert config._parse_env_flag("False", True) is False
    assert config._parse_env_flag("no", True) is False
    assert config._parse_env_flag("off", True) is False
    assert config._parse_env_flag("", True) is True
    assert config._parse_env_flag("yes", False) is True
