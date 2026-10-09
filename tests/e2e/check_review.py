"""Flow 3: 去"今日复习"评分一道题；确认进度/次数有变化。"""
from conftest import goto_view, random_username, register_user


def _create_due_mistake(page, title):
    goto_view(page, "new")
    page.locator("#problem-quick").select_option("full")
    page.locator("#problem-form input[name=title]").fill(title)
    page.locator("#problem-zone").select_option(label="算法")
    page.locator('#problem-form textarea[name="code"]').fill("x = 1\n")
    page.locator('button[aria-controls="problem-thinking-content"]').click()
    page.locator('#problem-form textarea[name="thinking"]').fill("思路")
    page.locator('button[aria-controls="problem-mistakes-content"]').click()
    page.locator('#mistake-inputs textarea[name="mistake"]').first.fill("易错点一")
    page.locator("#problem-save").click()
    page.wait_for_timeout(1500)


def test_review_one_and_count_changes(page, base_url):
    register_user(page, base_url)
    title = "待复习-" + random_username("t")
    _create_due_mistake(page, title)

    goto_view(page, "today")
    # 等待复习队列加载出卡片
    page.locator("#cards .problem-record-card").first.wait_for(timeout=10000)
    before = page.locator("#review-done-today").inner_text()

    # 打开第一张卡片的第一条易错点
    page.locator("#cards .problem-record-causes button").first.click()
    # 先点"显示错因"揭示，再评分
    page.locator("#review-reveal").click()
    page.locator('.review-grade[data-quality="4"]').click()
    # 评分后卡片应消失或"今天已复习"计数变化
    page.wait_for_timeout(1500)
    after = page.locator("#review-done-today").inner_text()
    assert before != after, f"评分前后计数无变化: {before!r} -> {after!r}"
