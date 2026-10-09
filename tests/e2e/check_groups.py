"""Flow 5: 小组 —— 创建小组、复制邀请码、另一个账号用邀请码加入；
非成员访问详情得到 404。"""
import pytest

from conftest import goto_view, random_username, register_user


def _create_group(page, name):
    goto_view(page, "groups")
    page.locator("#groups-name").fill(name)
    page.locator("#groups-create-form button[type=submit]").click()
    # 创建成功后进入详情页，邀请码可见
    page.locator("#groups-invite-code").wait_for(timeout=10000)
    return page.locator("#groups-invite-code").inner_text().strip()


def _group_id_via_api(page, base_url):
    return page.evaluate(
        """async () => {
          const r = await fetch('/api/groups', {headers: {'X-CSRF-Protection': '1'}});
          const j = await r.json();
          return j.groups[0].id;
        }"""
    )


def test_group_create_join_and_nonmember_404(page, pw, base_url):
    # A 创建小组
    register_user(page, base_url)
    group_name = "小组-" + random_username("g")
    invite_code = _create_group(page, group_name)
    assert len(invite_code) == 8, f"邀请码应为 8 位，实际 {invite_code!r}"
    group_id = _group_id_via_api(page, base_url)

    # B 用邀请码加入（全新浏览器上下文 = 另一个账号，独立 IP 避免限流）
    browser_b = pw.chromium.launch()
    ctx_b = browser_b.new_context(extra_http_headers={"X-Forwarded-For": "10.200.1.11"})
    page_b = ctx_b.new_page()
    try:
        register_user(page_b, base_url)
        goto_view(page_b, "groups")
        page_b.locator("#groups-invite-input").fill(invite_code)
        page_b.locator("#groups-join-form button[type=submit]").click()
        page_b.locator("#groups-invite-code").wait_for(timeout=10000)
        assert group_name in page_b.inner_text("body"), "B 加入后未看到小组详情"
    finally:
        browser_b.close()

    # C（从未加入）访问该小组详情 → 404
    browser_c = pw.chromium.launch()
    ctx_c = browser_c.new_context(extra_http_headers={"X-Forwarded-For": "10.200.1.12"})
    page_c = ctx_c.new_page()
    try:
        register_user(page_c, base_url)
        status = page_c.evaluate(
            """async (gid) => {
              const r = await fetch(`/api/groups/${gid}`,
                {headers: {'X-CSRF-Protection': '1'}});
              return r.status;
            }""",
            group_id,
        )
        assert status == 404, f"非成员访问小组详情应 404，实际 {status}"
    finally:
        browser_c.close()
