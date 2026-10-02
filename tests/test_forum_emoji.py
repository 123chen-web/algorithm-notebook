"""讨论区的表情：帖子、评论、回复摘要、搜索都按原样保存和返回，不过滤、不改写。"""
import pytest

from test_app import client, register


# 带变体选择符（❤️）、零宽连接符（👨‍💻）、肤色（👍🏽）、国旗和键帽的复合表情。
COMPOSITE = "❤️ 👨‍💻 👍🏽 🇨🇳 1️⃣ 🏳️‍🌈"
PLAIN = "👍 🎉 🤔"


def create_post(client, title, body):
    response = client.post("/api/posts", json={"title": title, "body": body})
    assert response.status_code == 201, response.text
    return response.json()


def create_comment(client, post_id, body, **extra):
    response = client.post(f"/api/posts/{post_id}/comments", json={"body": body, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_emoji_round_trip_in_post_and_comment_unchanged(client):
    register(client)
    post = create_post(client, f"二分查找 {PLAIN}", f"今天终于搞懂了 {COMPOSITE}")
    assert post["title"] == f"二分查找 {PLAIN}"
    assert post["body"] == f"今天终于搞懂了 {COMPOSITE}"
    comment = create_comment(client, post["id"], f"同感 {COMPOSITE}")
    assert comment["body"] == f"同感 {COMPOSITE}"

    detail = client.get(f"/api/posts/{post['id']}").json()
    assert detail["title"] == post["title"]
    assert detail["body"] == post["body"]
    assert [item["body"] for item in detail["comments"]] == [f"同感 {COMPOSITE}"]
    listed = client.get("/api/posts").json()["posts"]
    assert [item["title"] for item in listed] == [f"二分查找 {PLAIN}"]


def test_emoji_survive_editing(client):
    register(client)
    post = create_post(client, "标题", "正文")
    comment = create_comment(client, post["id"], "原评论")
    edited_post = client.put(
        f"/api/posts/{post['id']}", json={"title": f"改过 {PLAIN}", "body": f"新正文 {COMPOSITE}"},
    )
    assert edited_post.status_code == 200
    assert edited_post.json()["body"] == f"新正文 {COMPOSITE}"
    edited_comment = client.put(f"/api/comments/{comment['id']}", json={"body": f"改过 {COMPOSITE}"})
    assert edited_comment.status_code == 200
    assert edited_comment.json()["body"] == f"改过 {COMPOSITE}"


def test_reply_excerpt_keeps_emoji_whole(client):
    register(client)
    post = create_post(client, "标题", "正文")
    parent = create_comment(client, post["id"], f"{COMPOSITE} 这是被引用的楼层")
    reply = create_comment(client, post["id"], "回复一下 🙌", reply_to_id=parent["id"])
    quoted = reply["reply_to"]
    assert quoted["excerpt"].startswith(COMPOSITE)
    detail = client.get(f"/api/posts/{post['id']}").json()
    assert detail["comments"][1]["reply_to"]["excerpt"].startswith(COMPOSITE)


def test_long_quote_excerpt_of_plain_emoji_is_cut_at_sixty_characters(client):
    register(client)
    post = create_post(client, "标题", "正文")
    parent = create_comment(client, post["id"], "👍" * 200)
    reply = create_comment(client, post["id"], "好", reply_to_id=parent["id"])
    assert reply["reply_to"]["excerpt"] == "👍" * 60 + "…"


@pytest.mark.parametrize("tail,kept", [
    ("👍🏽", 59),                      # 肤色修饰符属于前面的手势
    ("👩‍💻", 59),                # 连接序列整体不拆
    ("🇨🇳" + "bb", 59),                # 国旗是一对区域指示符
    ("1️⃣", 59),             # 键帽 = 数字 + 变体选择符 + 组合键帽
    ("é", 59),                    # 字母 + 组合重音
    ("👩‍" + "💻", 58),            # 连接符落在边界上：连同前面的表情一起留给后面
])
def test_quote_excerpt_never_splits_a_composite_emoji_or_combining_mark(client, tail, kept):
    from main import truncate_text

    text = "a" * kept + tail
    assert len(text) > 60
    shortened, was_cut = truncate_text(text, 60)
    assert was_cut and shortened == "a" * kept
    register(client)
    post = create_post(client, "标题", "正文")
    parent = create_comment(client, post["id"], "a" * kept + tail)
    reply = create_comment(client, post["id"], "好", reply_to_id=parent["id"])
    assert reply["reply_to"]["excerpt"] == "a" * kept + "…"


def test_truncate_text_leaves_short_and_plain_text_alone():
    from main import truncate_text

    assert truncate_text("短文本", 60) == ("短文本", False)
    assert truncate_text("a" * 60, 60) == ("a" * 60, False)
    assert truncate_text("a" * 61, 60) == ("a" * 60, True)
    # 整个表情刚好放得下时保留。
    assert truncate_text("a" * 58 + "🇨🇳" + "b", 60) == ("a" * 58 + "🇨🇳", True)


def test_search_finds_posts_by_emoji(client):
    register(client)
    create_post(client, "带表情的帖子", f"这条有 {PLAIN}")
    create_post(client, "普通帖子", "什么表情都没有")
    found = client.get("/api/posts", params={"q": "🤔"}).json()["posts"]
    assert [item["title"] for item in found] == ["带表情的帖子"]
    assert client.get("/api/posts", params={"q": "🎊"}).json()["posts"] == []


@pytest.mark.parametrize("count,status", [(2000, 201), (2001, 422)])
def test_comment_length_limit_counts_characters_not_bytes(client, count, status):
    register(client)
    post = create_post(client, "标题", "正文")
    response = client.post(f"/api/posts/{post['id']}/comments", json={"body": "😀" * count})
    assert response.status_code == status
