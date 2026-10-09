"""Flow 7: 数据导出、账号注销（注销后无法登录）。"""
from conftest import DEFAULT_PASSWORD, login_user, random_username, register_user


def _open_account_menu(page):
    # 幂等：菜单已打开时不再点 summary（否则会 toggle 关闭）
    if not page.locator("#account-delete").is_visible():
        page.locator('summary[aria-label="账号菜单"]').click()
    page.locator("#account-delete").wait_for(timeout=10000)


def test_export_then_delete_account(page, base_url):
    username, password = register_user(page, base_url)
    _open_account_menu(page)

    # 数据导出：点击后应触发文件下载
    with page.expect_download() as dl_info:
        page.locator('a[href="/api/export"]').click()
    download = dl_info.value
    path = download.path()
    assert path is not None
    assert download.suggested_filename, "导出文件没有文件名"

    # 注销账号：对话框确认按钮文案为"永久注销账号"
    # （导出下载后账号菜单会自动收起，需要重新打开）
    _open_account_menu(page)
    page.locator("#account-delete").click()
    page.locator("#account-delete-password").fill(password)
    page.locator("#account-delete-confirm").check()
    page.get_by_role("button", name="永久注销账号").click()
    page.wait_for_url("**#/welcome", timeout=15000)

    # 注销后无法再登录
    login_user_expect_fail(page, base_url, username, password)


def login_user_expect_fail(page, base_url, username, password):
    page.goto(f"{base_url}/#/auth", wait_until="networkidle")
    page.locator("#login-form input[name=username]").fill(username)
    page.locator("#login-form input[name=password]").fill(password)
    page.locator("#login-form button[type=submit]").click()
    page.wait_for_timeout(2000)
    # 仍停留在登录页（未进入应用）
    assert "#/app" not in page.url, "已注销的账号竟然还能登录"
