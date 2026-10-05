"""微信提醒卡片的静态契约：文件存在且被 index.html / app.js 接线，
只用主题令牌，无内联样式 / HTML 注入面，密钥只走密码框、文字只走 textContent。"""
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_push_files_exist_and_stay_small():
    script = STATIC / "push-settings.js"
    styles = STATIC / "push-settings.css"
    assert script.is_file() and styles.is_file()
    assert script.stat().st_size <= 20 * 1024, "push-settings.js 不能超过 20 KB"
    assert styles.stat().st_size <= 10 * 1024, "push-settings.css 不能超过 10 KB"


def test_index_html_loads_the_card_assets_and_hosts_the_card():
    html = read("index.html")
    assert 'href="/static/push-settings.css?v=1"' in html
    assert 'src="/static/push-settings.js?v=1"' in html
    assert 'id="push-settings-host"' in html
    # 卡片挂在账号菜单面板里。
    host_pos = html.index('id="push-settings-host"')
    panel_pos = html.index('class="account-menu-panel"')
    menu_end = html.index("</details>", host_pos)
    assert panel_pos < host_pos < menu_end


def test_app_js_configures_mounts_and_resets_the_card():
    source = read("app.js")
    assert "window.PushSettings?.configure(" in source
    assert 'window.PushSettings?.mount($("#push-settings-host"))' in source
    assert "window.PushSettings?.reset()" in source


def test_stylesheet_follows_the_project_rules():
    css = read("push-settings.css")
    assert "!important" not in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "颜色必须来自主题令牌"
    assert "hsl(" not in css and not re.search(r"rgba?\(\s*\d", css), "不写字面量颜色函数"
    assert not re.search(r'style\s*=\s*"', css)
    # 动效只能出现在 prefers-reduced-motion: no-preference 媒体查询里。
    assert "animation" not in css
    transition_blocks = re.findall(
        r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n\}", css, re.S
    )
    assert transition_blocks and all("transition" in block for block in transition_blocks)
    assert "transition" not in css.replace("".join(transition_blocks), "")


def test_script_never_builds_html_or_inline_styles():
    source = read("push-settings.js")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "eval(", "new Function"):
        assert forbidden not in source, forbidden
    assert 'setAttribute("style"' not in source and ".style.cssText" not in source
    # 密钥输入框必须是密码型、不自动回填；所有动态文字走 textContent。
    assert 'secret.type = "password"' in source
    assert 'secret.autocomplete = "new-password"' in source
    assert "textContent" in source


def test_script_exposes_configure_mount_reset_and_guards_stale_responses():
    source = read("push-settings.js")
    assert "window.PushSettings = { configure, mount, reset }" in source
    # 迟到响应守卫三要素：代次、会话 epoch、用户 id。
    assert "generation" in source and "getEpoch" in source and "currentUser()" in source
    # 接口路径固定。
    assert '"/api/me/push"' in source and '"/api/me/push/test"' in source
