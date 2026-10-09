"""Flow 6: 讨论区 —— 发帖、评论、回复；评论回复输入框能出现并提交成功。"""
from conftest import goto_view, random_username, register_user


def test_forum_post_comment_reply(page, base_url):
    register_user(page, base_url)
    goto_view(page, "forum")

    # 发帖
    title = "帖子-" + random_username("p")
    page.locator("#forum-new-post-btn").click()
    page.locator("#forum-compose-title-input").fill(title)
    page.locator("#forum-compose-body").fill("这是 e2e 冒烟测试的帖子正文。")
    page.locator("#forum-compose-send").click()
    # 发布后进入帖子详情
    page.locator("#forum-detail").wait_for(timeout=10000)
    assert title in page.inner_text("body")

    # 评论
    comment_text = "评论-" + random_username("c")
    page.locator("#forum-comment-body").fill(comment_text)
    page.locator("#forum-comment-send").click()
    page.wait_for_timeout(1500)
    assert comment_text in page.inner_text("body"), "评论未出现在帖子下"

    # 回复：点第一条评论的"回复"按钮，回复输入框应出现
    page.locator("#forum-comments").get_by_role("button", name="回复").first.click()
    assert page.locator("#forum-reply-target").is_visible(), "点击回复后回复目标提示未出现"
    reply_text = "回复-" + random_username("r")
    page.locator("#forum-comment-body").fill(reply_text)
    page.locator("#forum-comment-send").click()
    page.wait_for_timeout(1500)
    assert reply_text in page.inner_text("body"), "回复未出现在帖子下"
