"""Flow 2: 记第一道错题（完整方式）+ 速记方式再记一道；确认列表里出现。"""
from conftest import goto_view, random_username, register_user


def _fill_full_mistake(page, title):
    goto_view(page, "new")
    page.locator("#problem-quick").select_option("full")
    page.locator("#problem-form input[name=title]").fill(title)
    page.locator("#problem-zone").select_option(label="算法")
    page.locator('#problem-form textarea[name="code"]').fill("def f(n):\n    return n // 2\n")
    # 思路折叠区是 inert 的，需要先展开
    page.locator('button[aria-controls="problem-thinking-content"]').click()
    page.locator('#problem-form textarea[name="thinking"]').fill("当时以为整除没问题")
    # 易错点折叠区同样需要展开
    page.locator('button[aria-controls="problem-mistakes-content"]').click()
    page.locator('#mistake-inputs textarea[name="mistake"]').first.fill("边界条件漏了 n=0")
    page.locator("#problem-save").click()


def test_full_then_quick_mistake(page, base_url):
    register_user(page, base_url)
    title_full = "完整记录-" + random_username("t")
    title_quick = "速记-" + random_username("t")

    # 完整方式
    _fill_full_mistake(page, title_full)
    # 保存成功后应有提示（toast/状态文案），且表单被清空或跳转
    page.wait_for_timeout(1500)

    # 速记方式：只填题目名和分区
    goto_view(page, "new")
    page.locator("#problem-quick").select_option("quick")
    page.locator("#problem-form input[name=title]").fill(title_quick)
    page.locator("#problem-zone").select_option(label="算法")
    page.locator("#problem-save").click()
    page.wait_for_timeout(1500)

    # 去"全部记录"确认两道题都在
    goto_view(page, "all")
    page.wait_for_timeout(1000)
    body = page.inner_text("body")
    assert title_full in body, "完整方式记录的题目未出现在列表"
    assert title_quick in body, "速记方式记录的题目未出现在列表"
