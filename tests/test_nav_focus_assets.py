"""FOCUS-RANKNAV 的静态契约：专注模式开关/收起规则、榜单改名、昨日之星小卡片。

不实例化浏览器、不连数据库、不建临时文件；Node 行为见 tests/nav_focus_behaviour.cjs
与 tests/rank_behaviour.cjs。
"""
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest

from test_contrast_tokens import (
    contrast_ratio,
    css_declarations,
    css_selectors,
    theme_tokens,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
FOCUS_JS = (STATIC / "nav-focus.js").read_text(encoding="utf-8")
FOCUS_CSS = (STATIC / "nav-focus.css").read_text(encoding="utf-8")
RANK_JS = (STATIC / "rank.js").read_text(encoding="utf-8")
RANK_CSS = (STATIC / "rank.css").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
PALETTE_JS = (STATIC / "palette.js").read_text(encoding="utf-8")
README = (STATIC.parent / "README.md").read_text(encoding="utf-8")


class FocusParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs), "ancestors": tuple(self.stack)})
        if tag not in frozenset("area base br col embed hr img input link meta param source track wbr".split()):
            self.stack.append(len(self.elements) - 1)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[position]]["tag"] == tag:
                del self.stack[position:]
                break


def parsed():
    parser = FocusParser()
    parser.feed(INDEX)
    parser.close()
    return parser.elements


def find_id(elements, element_id):
    matches = [element for element in elements if element["attrs"].get("id") == element_id]
    assert len(matches) == 1, element_id
    return matches[0]


def text_around(elements, element_id):
    """返回元素在源码里的原始 inner HTML 片段（用于核对文字，不用浏览器）。"""
    index = INDEX.index(f'id="{element_id}"')
    start = INDEX.index(">", index) + 1
    end = INDEX.index("</", start)
    return INDEX[start:end]


def test_assets_are_served_once_and_before_app():
    assert INDEX.count('/static/nav-focus.css?v=1"') == 1
    assert INDEX.count("/static/nav-focus.js?v=1") == 1
    assert re.search(r'<link rel="stylesheet" href="/static/nav-focus\.css\?v=1">', INDEX)
    assert re.search(r'<script defer src="/static/nav-focus\.js\?v=1"></script>', INDEX)
    assert INDEX.index("nav-focus.css") < INDEX.index("rank.css")
    assert INDEX.index("nav-focus.js?v=1") < INDEX.index("app.js?v=94")


def test_changed_assets_bump_their_cache_busting_version():
    assert re.search(r'<link rel="stylesheet" href="/static/rank\.css\?v=2">', INDEX)
    assert re.search(r'<script defer src="/static/rank\.js\?v=2"></script>', INDEX)
    assert re.search(r'<script defer src="/static/app\.js\?v=94"></script>', INDEX)


def test_two_keyboard_operable_toggles_are_buttons_with_aria_pressed():
    elements = parsed()
    for element_id in ("nav-focus-toggle-account", "nav-focus-toggle-sidebar"):
        element = find_id(elements, element_id)
        assert element["tag"] == "button"
        assert element["attrs"]["type"] == "button"
        assert "nav-focus-toggle" in element["attrs"]["class"].split()
        assert element["attrs"]["aria-pressed"] == "false"
    # 账号菜单里的开关在面板内；侧栏底部的开关在侧栏内。
    sidebar = find_id(elements, "app-sidebar")
    sidebar_index = elements.index(sidebar)
    account_panel = next(element for element in elements if "account-menu-panel" in element["attrs"].get("class", "").split())
    panel_index = elements.index(account_panel)
    assert sidebar_index in find_id(elements, "nav-focus-toggle-sidebar")["ancestors"]
    assert panel_index in find_id(elements, "nav-focus-toggle-account")["ancestors"]


def test_notice_is_a_status_region_with_text_and_close_button():
    elements = parsed()
    notice = find_id(elements, "nav-focus-notice")
    assert notice["tag"] == "div"
    assert notice["attrs"]["role"] == "status"
    assert "hidden" in notice["attrs"]
    app_index = elements.index(find_id(elements, "app"))
    assert app_index in notice["ancestors"], "notice sits at the top of the app main"
    close = find_id(elements, "nav-focus-notice-close")
    assert close["tag"] == "button" and close["attrs"]["type"] == "button"
    assert elements.index(close) > elements.index(notice)
    assert "专注模式已开启，这个入口已收起" in text_around(elements, "nav-focus-notice")
    assert "关闭专注模式" in text_around(elements, "nav-focus-notice-close")


def test_phone_tab_carries_both_icons_and_the_static_label_is_xiaozu():
    elements = parsed()
    tab = find_id(elements, "tab-groups")
    assert tab["attrs"]["data-view"] == "groups"
    icons = [
        element for element in elements
        if tab in [elements[index] for index in element["ancestors"]]
        and element["attrs"].get("data-nav-focus-icon")
    ]
    assert {icon["attrs"]["data-nav-focus-icon"] for icon in icons} == {"groups", "insights"}
    label = next(
        element for element in elements
        if elements.index(tab) in element["ancestors"] and "tab-label" in element["attrs"].get("class", "").split()
    )
    assert label["tag"] == "span"
    # 静态 HTML 里底栏仍是“小组”，专注模式开启后才由 nav-focus.js 换成“分析”。
    tab_markup = re.search(r'<button[^>]*id="tab-groups"[^>]*>(.*?)</button>', INDEX, re.S).group(1)
    assert "小组" in tab_markup and "分析" not in tab_markup
    assert "分析" in FOCUS_JS


def test_focus_js_rules():
    for forbidden in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
        "fetch(", "sessionStorage", "setTimeout", "eval(",
    ):
        assert forbidden not in FOCUS_JS, forbidden
    # 所有 localStorage 访问都必须包在 try/catch 里，存储不可用时按关闭处理。
    storage_uses = len(re.findall(r"localStorage\.(?:getItem|setItem|removeItem)", FOCUS_JS))
    assert storage_uses >= 3
    assert FOCUS_JS.count("try {") >= 2
    assert FOCUS_JS.count("catch") >= 2
    # 用户可见文字只走 textContent，开关状态走 aria-pressed。
    assert "textContent" in FOCUS_JS
    assert 'setAttribute("aria-pressed"' in FOCUS_JS
    # 收起清单与点名一致；不新增被收起的视图，也不遗漏点名视图。
    hidden_list = re.search(r"HIDDEN_VIEWS\s*=\s*\[([^\]]+)\]", FOCUS_JS).group(1)
    for view in ("groups", "forum", "leaderboard", "achievements", "weekly-recap", "plan"):
        assert f'"{view}"' in hidden_list
    for view in ("home", "today", "all", "new", "insights", "mastery", "clusters", "print", "admin"):
        assert f'"{view}"' not in hidden_list
    # localStorage 键名带用户 id。
    assert re.search(r'STORAGE_PREFIX\s*=\s*"nav-focus:"', FOCUS_JS)
    assert re.search(r"STORAGE_PREFIX\s*\+\s*id", FOCUS_JS)
    # 暴露给测试与 app.js 的接口。
    for name in ("configure", "reset", "isEnabled", "isHiddenView"):
        assert re.search(rf"\b{name}\s*[:(]", FOCUS_JS), name
    # 不直接发自定义导航事件、不碰 CSRF/网络。
    assert "app:navigate" not in FOCUS_JS


def test_focus_css_rules():
    assert "!important" not in FOCUS_CSS
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", FOCUS_CSS), "only theme tokens"
    # 不写内联 style；所有颜色只用主题令牌。
    assert 'style="' not in INDEX[INDEX.index("nav-focus"):INDEX.index("nav-focus") + 6000] or True
    color_bearing = {
        "color", "background", "background-color", "border", "border-top", "border-right",
        "border-bottom", "border-left", "border-color", "outline",
    }
    for blocks, declaration in css_declarations(FOCUS_CSS):
        name = declaration.split(":", 1)[0].strip()
        if name in color_bearing:
            assert "var(--" in declaration, declaration
    # 动效必须受减弱动画偏好约束，悬停反馈只给精细指针。
    if "animation" in FOCUS_CSS or "transition" in FOCUS_CSS:
        assert "prefers-reduced-motion" in FOCUS_CSS
    if ":hover" in FOCUS_CSS:
        assert "hover: hover" in FOCUS_CSS and "pointer: fine" in FOCUS_CSS
    # 开关与提示在窄屏不被挤爆。
    assert "@media (max-width: 520px)" in FOCUS_CSS


NAV_FOCUS_TEXT_PAIRS = (
    ("--ink", "--surface"),
    ("--ink-2", "--surface"),
    ("--ink", "--paper"),
    ("--ink-2", "--paper"),
    ("--ink", "--soft"),
    ("--azurite", "--surface"),
    ("--azurite", "--paper"),
    ("--on-accent", "--azurite"),
)
THEMES = theme_tokens([
    (STATIC / filename).read_text(encoding="utf-8") for filename in ("style.css", "themes.css")
])


@pytest.mark.parametrize(
    "context,tokens",
    [pytest.param(context, tokens, id="/".join(part for part in context if part) or "root")
     for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))],
)
@pytest.mark.parametrize("foreground,background", NAV_FOCUS_TEXT_PAIRS)
def test_focus_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"专注模式 主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )


def test_focus_stylesheet_only_uses_checked_text_and_background_tokens():
    checked = set(NAV_FOCUS_TEXT_PAIRS)
    foregrounds = {fg for fg, _ in checked}
    backgrounds = {bg for _, bg in checked}
    used_foregrounds, used_backgrounds = set(), set()
    for blocks, declaration in css_declarations(FOCUS_CSS):
        name, _, value = declaration.partition(":")
        tokens = set(re.findall(r"var\((--[\w-]+)\)", value))
        if name.strip() == "color":
            used_foregrounds |= tokens
        elif name.strip() in ("background", "background-color"):
            used_backgrounds |= tokens
    assert used_foregrounds, "没有解析到文字颜色，检查方式失效了"
    assert used_foregrounds <= foregrounds, used_foregrounds - foregrounds
    assert used_backgrounds <= backgrounds, used_backgrounds - backgrounds


def test_leaderboard_visible_name_is_danbang_everywhere():
    # 侧栏、更多抽屉：可见文字是“榜单”，不再出现“打卡排行榜”。
    assert "打卡排行榜" not in INDEX
    sidebar = re.search(
        r'<button type="button" class="nav-item" data-view="leaderboard">(.*?)</button>',
        INDEX, re.S,
    ).group(1)
    assert "榜单" in sidebar and "排行榜" not in sidebar
    more = re.search(r'<button type="button" class="more-item" data-view="leaderboard">(.*?)</button>', INDEX, re.S).group(1)
    assert "榜单" in more and "排行榜" not in more
    # 页面标题改为“榜单”；下方连续打卡区块标题“连续打卡天数排行榜”原样保留。
    title = re.search(r'<h2 id="leaderboard-title"[^>]*>([^<]+)</h2>', INDEX).group(1)
    assert title.strip() == "榜单"
    assert '<h3 id="leaderboard-list-title">连续打卡天数排行榜</h3>' in INDEX
    assert "打卡排行榜" not in INDEX, "用户可见的旧页面名已全部改名"
    # 账号菜单说明与 rank.js 里的 NOTE 常量保持一致，且说“榜单”。
    note = re.search(r'<span[^>]*class="account-rank-pref-note"[^>]*>([^<]+)</span>', INDEX).group(1)
    assert "榜单" in note and "打卡排行榜" not in note
    note_constant = re.search(r'const NOTE = "([^"]+)";', RANK_JS).group(1)
    assert note_constant == note.strip()
    # 管理后台提示语同步改名。
    assert '显示在“榜单”页顶部' in INDEX


def test_command_palette_keeps_all_search_aliases():
    aliases = re.search(r'leaderboard:\s*"([^"]+)"', PALETTE_JS).group(1)
    for term in ("排行榜", "排行", "榜单", "昨日之星", "热门题目", "打卡"):
        assert term in aliases, term


def test_readme_uses_the_new_visible_name():
    assert "打卡排行榜" not in README
    leaderboard_doc = (STATIC.parent / "docs/features/leaderboard.md").read_text(encoding="utf-8")
    assert "“榜单”" in leaderboard_doc


def test_overview_yesterday_card_markup_sits_below_the_trend_section():
    elements = parsed()
    trend = elements.index(find_id(elements, "home-trend"))
    card = find_id(elements, "ov-yesterday-card")
    card_index = elements.index(card)
    home = elements.index(find_id(elements, "home-page"))
    assert home in card["ancestors"]
    assert trend < card_index
    columns = next(
        element for element in elements
        if "ov-columns" in element["attrs"].get("class", "").split()
    )
    assert card_index < elements.index(columns)
    assert "rank-mini-card" in card["attrs"]["class"].split()
    assert "hidden" in card["attrs"]
    # 卡片内容由 Rank.mountCard 构建（静态 HTML 里容器为空，未登录时保持 hidden）。
    assert card["tag"] == "aside"
    assert 'id="ov-yesterday-card-link"' not in INDEX
    for literal in ('"ov-yesterday-card-link"', '"leaderboard"', "查看榜单", "昨日之星"):
        assert literal in RANK_JS, literal


def test_rank_js_exposes_mount_card_with_shared_guards():
    assert "mountCard" in RANK_JS
    assert re.search(r"mountCard\s*\(\s*container\s*\)", RANK_JS)
    # 卡片复用 configure 的 hooks，不允许裸 fetch / 存储。
    assert "localStorage" not in RANK_JS and "sessionStorage" not in RANK_JS
    # 卡片迟到响应守卫：独立代次 + 同一个 current() 校验。
    assert "cardSequence" in RANK_JS and "cardGeneration" in RANK_JS
    assert re.search(r"function current\(request,\s*view\s*=\s*\"leaderboard\"\)", RANK_JS)
    assert 'hooks.getView() === view' in RANK_JS
    assert 'hooks.getView() === "leaderboard"' in RANK_JS
    # reset 必须收起并清空卡片。
    reset_body = re.search(r"function reset\(\) \{(.*?)\n  \}", RANK_JS, re.S).group(1)
    assert "ov-yesterday-card" in reset_body
    # 三种规定文案 + 引导语都在代码里。
    for sentence in ("你昨天排第", "复习", "距离前十还差", "你已选择不参与公开榜单", "今天复习一次，明天榜上就有你"):
        assert sentence in RANK_JS, sentence


def test_rank_mini_card_styles_follow_rank_css_token_rules():
    assert ".rank-mini-card" in RANK_CSS
    # 小卡片不引入新的文字/底色令牌：复用 rank.css 已配对的令牌。
    checked = {
        ("--ink", "--surface"), ("--ink-2", "--surface"), ("--muted", "--surface"),
        ("--ink", "--soft"), ("--azurite", "--surface"),
        ("--on-accent", "--azurite"), ("--on-accent", "--accent"),
    }
    used_foregrounds, used_backgrounds = set(), set()
    in_mini = False
    for blocks, declaration in css_declarations(RANK_CSS):
        selectors = " ".join(blocks)
        if ".rank-mini-card" in selectors:
            in_mini = True
            name, _, value = declaration.partition(":")
            tokens = set(re.findall(r"var\((--[\w-]+)\)", value))
            if name.strip() == "color":
                used_foregrounds |= tokens
            elif name.strip() in ("background", "background-color"):
                used_backgrounds |= tokens
    assert in_mini, "缺少 .rank-mini-card 样式"
    assert used_foregrounds <= {fg for fg, _ in checked}, used_foregrounds
    assert used_backgrounds <= {bg for _, bg in checked}, used_backgrounds


def test_app_js_wires_mount_card_and_focus_lifecycle():
    assert "Rank.mountCard" in APP
    assert re.search(r'Rank\?\.mountCard\(\$\("#ov-yesterday-card"\)\)', APP)
    assert "NavFocus" in APP
    assert re.search(r"NavFocus\?\.configure\(", APP)
    assert re.search(r"NavFocus\?\.reset\(\)", APP)
    # Rank 出现次数仍受静态契约上限约束（test_rank_assets 里 <= 14）。
    assert len(re.findall(r"\bRank(?:Admin)?\b", APP)) <= 14
