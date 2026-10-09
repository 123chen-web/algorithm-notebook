"""Flow 1: 邀请码注册 → 登录 → 退出 → 再登录。"""
from conftest import login_user, logout, random_username, register_user


def test_register_login_logout_relogin(page, base_url):
    username, password = register_user(page, base_url)

    # 注册后应直接进入应用，导航可见
    assert page.locator('button.nav-item[data-view="home"]').first.is_visible()

    # 退出（退出后回到欢迎页）
    logout(page)
    page.wait_for_url("**#/welcome", timeout=10000)

    # 再登录
    login_user(page, base_url, username, password)
    assert page.locator('button.nav-item[data-view="home"]').first.is_visible()
    # 用户名应显示在界面上（顶栏）
    assert username in page.inner_text("body")
