"""全局搜索（Ctrl K）：只查自己的题目/错因 + 讨论区帖子，纯本地查询。"""

import pytest

import ai
import main
from db import connect
from search import like_pattern, snippet_around
from test_app import client, register


ENDPOINT = "/api/search"


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("搜索是纯本地查询，不能调用 AI")

    for name in ("generate", "analyze_weaknesses", "recognize_photo"):
        monkeypatch.setattr(ai, name, unexpected_call)


def user_id(client):
    return client.get("/api/me").json()["id"]


def add_problem(client, title, descriptions=("遗漏边界条件",), zone="算法", language="Python",
                code="pass", thinking="先想想"):
    response = client.post("/api/problems", json={
        "title": title, "zone": zone, "language": language, "code": code,
        "thinking": thinking, "mistakes": list(descriptions),
    })
    assert response.status_code == 201, response.text
    return response.json()["mistake_ids"]


def search(client, q, **params):
    response = client.get(ENDPOINT, params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


def make_post(owner_id, title, body="正文", deleted=False):
    with connect(write=True) as conn:
        return conn.execute(
            "INSERT INTO posts(user_id, title, body, created_at, deleted_at) VALUES (?, ?, ?, ?, ?)",
            (owner_id, title, body, main.utc_now(), main.utc_now() if deleted else None),
        ).lastrowid


def test_requires_login(client):
    assert client.get(ENDPOINT, params={"q": "二分"}).status_code == 401


@pytest.mark.parametrize("q", ["", "   ", "\t\n"])
def test_blank_query_returns_empty_sections_without_touching_the_database(client, q):
    register(client)
    assert search(client, q) == {
        "q": "", "records": [], "posts": [], "truncated": {"records": False, "posts": False},
    }


def test_overlong_query_and_bad_limit_are_rejected(client):
    register(client)
    assert client.get(ENDPOINT, params={"q": "二" * 201}).status_code == 422
    for limit in ("0", "21", "x"):
        assert client.get(ENDPOINT, params={"q": "二", "limit": limit}).status_code == 422


def test_finds_records_by_title_description_zone_thinking_and_code(client):
    register(client)
    by_title = add_problem(client, "二分查找边界", ["循环条件写错"], thinking="先写区间")
    by_description = add_problem(client, "链表反转", ["忘记处理二分之一的情况"], thinking="画图")
    by_zone = add_problem(client, "SQL 联表", ["漏了 ON"], zone="数据库")
    by_thinking = add_problem(client, "背包", ["状态定义错"], thinking="我想到可以二分答案再验证")
    by_code = add_problem(client, "栈", ["漏弹栈"], code="while left <= right:\n    pass")

    result = search(client, "二分")
    assert [item["matched"] for item in result["records"]] == ["title", "description", "thinking"]
    assert [item["mistake_id"] for item in result["records"]] == [by_title[0], by_description[0], by_thinking[0]]
    assert result["records"][2]["snippet"] == "我想到可以二分答案再验证"
    assert result["records"][0]["snippet"] is None

    assert [item["mistake_id"] for item in search(client, "数据库")["records"]] == [by_zone[0]]
    code_hit = search(client, "left <=")["records"]
    assert [item["mistake_id"] for item in code_hit] == [by_code[0]]
    assert code_hit[0]["matched"] == "code" and "left <= right" in code_hit[0]["snippet"]


def test_each_mistake_is_its_own_result_with_the_fields_the_palette_needs(client):
    register(client)
    ids = add_problem(client, "区间 DP", ["状态转移写反", "边界没初始化"])
    records = search(client, "区间")["records"]
    assert [item["mistake_id"] for item in records] == ids
    first = records[0]
    assert set(first) == {
        "mistake_id", "problem_id", "title", "zone", "description", "due_date", "matched", "snippet",
    }
    assert first["description"] == "状态转移写反"
    assert first["zone"] == "算法"


def test_ascii_search_ignores_case(client):
    register(client)
    add_problem(client, "Binary Search", ["off by one"])
    assert len(search(client, "binary")["records"]) == 1
    assert len(search(client, "OFF BY")["records"]) == 1


@pytest.mark.parametrize("q,expected", [("100%", ["100% 通过率"]), ("a_b", ["a_b 命名"]), ("!", ["别漏了!"])])
def test_like_metacharacters_are_matched_literally(client, q, expected):
    register(client)
    for title in ("100% 通过率", "a_b 命名", "aXb 命名", "100 通过率", "别漏了!", "普通题"):
        add_problem(client, title, ["x"])
    assert [item["title"] for item in search(client, q)["records"]] == expected


def test_percent_and_underscore_alone_do_not_match_everything(client):
    register(client)
    for title in ("一二三", "四五六"):
        add_problem(client, title, ["x"])
    for q in ("%", "_"):
        assert search(client, q)["records"] == []


def test_limit_and_truncation_flags(client):
    register(client)
    for index in range(5):
        add_problem(client, f"二分 {index}", ["x"])
    owner = user_id(client)
    for index in range(4):
        make_post(owner, f"二分帖 {index}")
    result = search(client, "二分", limit=3)
    assert len(result["records"]) == 3 and len(result["posts"]) == 3
    assert result["truncated"] == {"records": True, "posts": True}
    everything = search(client, "二分", limit=20)
    assert len(everything["records"]) == 5 and len(everything["posts"]) == 4
    assert everything["truncated"] == {"records": False, "posts": False}


def test_other_users_records_are_never_returned_but_forum_posts_are_shared(client):
    register(client, "alice")
    add_problem(client, "alice 的二分笔记", ["私有错因"])
    alice = user_id(client)
    make_post(alice, "公开的二分讨论")
    client.post("/api/auth/logout")
    register(client, "bob")
    result = search(client, "二分")
    assert result["records"] == []
    assert [item["title"] for item in result["posts"]] == ["公开的二分讨论"]
    assert result["posts"][0]["username"] == "alice"


def test_posts_skip_deleted_ones_count_only_visible_comments_and_prefer_title_matches(client):
    register(client)
    owner = user_id(client)
    body_only = make_post(owner, "别的标题", body="里面提到二分")
    title_hit = make_post(owner, "二分专题")
    make_post(owner, "二分已删除", deleted=True)
    with connect(write=True) as conn:
        for index in range(3):
            conn.execute(
                "INSERT INTO post_comments(post_id, user_id, body, created_at, deleted_at) VALUES (?, ?, '评论', ?, ?)",
                (title_hit, owner, main.utc_now(), main.utc_now() if index == 0 else None),
            )
    posts = search(client, "二分")["posts"]
    assert [item["id"] for item in posts] == [title_hit, body_only]
    assert posts[0]["comment_count"] == 2
    assert set(posts[0]) == {"id", "title", "created_at", "username", "comment_count"}


def test_emoji_and_trial_accounts_work(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    assert search(client, "🎉")["posts"] == []
    owner = user_id(client)
    make_post(owner, "庆祝 🎉 一下")
    assert [item["title"] for item in search(client, "🎉")["posts"]] == ["庆祝 🎉 一下"]


def test_snippet_and_pattern_helpers():
    assert like_pattern("50%_!") == "%50!%!_!!%"
    text = "x" * 60 + "目标词" + "y" * 60
    snippet = snippet_around(text, "目标词")
    assert snippet.startswith("…") and snippet.endswith("…") and "目标词" in snippet
    assert snippet_around("没有", "目标词") is None
    assert snippet_around("", "x") is None
    assert snippet_around("a\n  b\tTARGET   c", "target") == "a b TARGET c"


def test_snippet_is_found_even_when_the_match_sits_deep_in_a_long_text(client):
    register(client)
    owner = user_id(client)
    mistake = add_problem(client, "背包", ["状态定义错"], thinking="先想想", code="pass")[0]
    deep = "a" * 6000 + "目标词" + "b" * 50
    with connect(write=True) as conn:
        conn.execute("UPDATE problems SET thinking = ? WHERE id = (SELECT problem_id FROM mistakes WHERE id = ?)", (deep, mistake))
    hit = search(client, "目标词")["records"][0]
    assert hit["matched"] == "thinking" and "目标词" in hit["snippet"]
    assert hit["snippet"].startswith("…") and hit["snippet"].endswith("…")
    with connect(write=True) as conn:
        conn.execute("UPDATE problems SET code = ? WHERE id = (SELECT problem_id FROM mistakes WHERE id = ?)", ("x" * 5000 + "TargetWord", mistake))
    code_hit = search(client, "targetword")["records"][0]
    assert code_hit["matched"] == "code" and "TargetWord" in code_hit["snippet"]
