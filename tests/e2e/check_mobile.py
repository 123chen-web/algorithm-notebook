"""Flow 4: 手机宽度（375×812）下重复 1~3 的关键步骤。

断言：无横向滚动条、关键按钮可点。
"""
from conftest import random_username, register_user


def _goto_view_mobile(page, view):
    # 移动端用底部 tab 栏；tab 只有 today/all/new/groups，new/全部记录走 tab
    tab = page.locator(f'.tab-item[data-view="{view}"]')
    if tab.count():
        tab.first.click()
    else:
        # 其他视图走"更多"菜单
        page.locator('.tab-item[data-view="more"], button:has-text("更多")').first.click()
        page.locator(f'.more-item[data-view="{view}"]').first.click()
    page.wait_for_timeout(500)


def _assert_no_horizontal_scroll(page):
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"出现横向滚动条，溢出 {overflow}px"


def test_mobile_key_flows(mobile_page, base_url):
    page = mobile_page
    username, password = register_user(page, base_url)
    _assert_no_horizontal_scroll(page)

    # 新增一道题（完整方式）
    _goto_view_mobile(page, "new")
    title = "手机-" + random_username("t")
    page.locator("#problem-quick").select_option("full")
    page.locator("#problem-form input[name=title]").fill(title)
    page.locator("#problem-zone").select_option(label="算法")
    page.locator('#problem-form textarea[name="code"]').fill("x = 1\n")
    page.locator('button[aria-controls="problem-thinking-content"]').click()
    page.locator('#problem-form textarea[name="thinking"]').fill("思路")
    page.locator('button[aria-controls="problem-mistakes-content"]').click()
    page.locator('#mistake-inputs textarea[name="mistake"]').first.fill("易错点")
    save_btn = page.locator("#problem-save")
    assert save_btn.is_visible() and save_btn.is_enabled(), "保存按钮不可点"
    save_btn.click()
    page.wait_for_timeout(1500)
    _assert_no_horizontal_scroll(page)

    # 今日复习：打开卡片并评分
    _goto_view_mobile(page, "today")
    page.locator("#cards .problem-record-card").first.wait_for(timeout=10000)
    before = page.locator("#review-done-today").inner_text()
    page.locator("#cards .problem-record-causes button").first.click()
    page.locator("#review-reveal").click()
    grade = page.locator('.review-grade[data-quality="4"]')
    assert grade.is_visible() and grade.is_enabled(), "评分按钮不可点"
    grade.click()
    page.wait_for_timeout(1500)
    after = page.locator("#review-done-today").inner_text()
    assert before != after, "评分后计数无变化"
    _assert_no_horizontal_scroll(page)
