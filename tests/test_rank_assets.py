"""榜单页新区块的静态契约：关键 id、版本号与加载顺序、CSP、令牌与动效、手机宽度、接线最小化。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "rank.css").read_text(encoding="utf-8")
RANK_JS = (STATIC / "rank.js").read_text(encoding="utf-8")
ADMIN_JS = (STATIC / "rank-admin.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")

IDS = (
    "rank-notice", "rank-notice-text", "rank-notice-link", "rank-yesterday", "rank-yesterday-title",
    "rank-yesterday-date", "rank-yesterday-status", "rank-yesterday-list", "rank-yesterday-me",
    "rank-yesterday-retry", "rank-hot", "rank-hot-title", "rank-hot-range", "rank-hot-min",
    "rank-hot-status", "rank-hot-list", "rank-hot-retry", "account-public-rank",
    "account-public-rank-label", "account-public-rank-status", "rank-admin-card", "rank-admin-form",
    "rank-admin-text", "rank-admin-count", "rank-admin-link", "rank-admin-start", "rank-admin-end",
    "rank-admin-active", "rank-admin-save", "rank-admin-cancel", "rank-admin-form-status",
    "rank-admin-status", "rank-admin-list",
)


def test_markup_has_each_required_id_exactly_once():
    for name in IDS:
        assert len(re.findall(rf'\bid="{name}"', HTML)) == 1, name


def test_blocks_sit_in_the_leaderboard_page_in_order_notice_yesterday_hot_then_streak():
    page = HTML[HTML.index('<section id="leaderboard-page"'):HTML.index('<section id="groups-page"')]
    order = [page.index(f'id="{name}"') for name in ("rank-notice", "rank-yesterday", "rank-hot")]
    assert order == sorted(order)
    assert order[-1] < page.index('class="panel leaderboard-personal"'), "榜单页原有的连续打卡区块保持在后面"
    assert "Asia/Shanghai" in page and "最多算 3 次" in page, "页面上写明北京时间与封顶规则"
    assert "取消勾选“参与公开榜单”" in page


def test_notice_link_markup_is_safe_by_default():
    tag = re.search(r'<a id="rank-notice-link"[^>]*>', HTML).group(0)
    assert 'rel="noopener noreferrer"' in tag and 'target="_blank"' in tag and "hidden" in tag
    assert "href=" not in tag, "href 只在通过 http(s) 校验后由脚本写入"
    assert re.search(r'id="rank-notice"[^>]*\bhidden\b', HTML)


def test_admin_card_is_admin_only_and_hidden_until_the_user_is_known():
    assert re.search(r'id="rank-admin-card"[^>]*\bdata-admin-only\b[^>]*\bhidden\b', HTML)
    assert HTML.index('id="rank-admin-card"') < HTML.index('id="admin-reports-heading"')
    assert re.search(r'id="rank-admin-text"[^>]*maxlength=', HTML)


def test_assets_are_versioned_and_loaded_before_app_js():
    assert '<link rel="stylesheet" href="/static/rank.css?v=2">' in HTML
    scripts = re.findall(r'<script defer src="/static/([\w.-]+\.js)\?v=(\d+)"></script>', HTML)
    names = [name for name, _ in scripts]
    assert names.count("rank.js") == names.count("rank-admin.js") == 1
    assert names.index("rank.js") < names.index("app.js") and names.index("rank-admin.js") < names.index("app.js")
    versions = dict(scripts)
    assert versions["rank.js"] == "2" and versions["rank-admin.js"] == "1"
    assert versions["app.js"] == "88", "app.js 改过，版本号随各任务递增（现为 85）"


def test_csp_safe_markup_and_scripts():
    assert not re.search(r'\bstyle\s*=', HTML)
    assert not re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', HTML)
    for source in (RANK_JS, ADMIN_JS):
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source and "eval(" not in source
        assert "outerHTML" not in source and "document.write" not in source
        assert not re.search(r"\.style\.\w+\s*=", source) and "setAttribute(\"style\"" not in source
        assert "localStorage" not in source and "sessionStorage" not in source


def test_scripts_guard_late_responses_with_epoch_and_sequence():
    assert "request.epoch === hooks.getEpoch()" in RANK_JS
    assert "request.sequence === sequence[request.name]" in RANK_JS
    assert "request.generation === generation" in RANK_JS and 'hooks.getView() === "leaderboard"' in RANK_JS
    assert "request.epoch === hooks.getEpoch()" in ADMIN_JS and "request.sequence === listSequence" in ADMIN_JS
    assert 'hooks.getView() === "admin"' in ADMIN_JS and "user.is_admin" in ADMIN_JS
    assert "fetch(" not in RANK_JS.replace("hooks.api", "") and "fetch(" not in ADMIN_JS


def test_links_are_always_rendered_with_noopener_and_new_tab():
    for source in (RANK_JS, ADMIN_JS):
        assert 'setAttribute("rel", "noopener noreferrer")' in source
        assert 'setAttribute("target", "_blank")' in source
    assert "HOT_HOSTS" in RANK_JS and "allowedHosts" in RANK_JS


def test_app_js_only_wires_load_configure_sync_and_reset():
    assert "window.Rank?.load();" in APP
    assert "window.Rank?.reset();" in APP and "window.RankAdmin?.reset();" in APP
    assert "window.Rank?.syncSetting(user);" in APP
    assert "window.RankAdmin?.load()" in APP
    assert "refreshPage: () => loadLeaderboard()" in APP
    assert "window.Rank?.configure(hooks);" in APP and "window.RankAdmin?.configure(hooks);" in APP
    assert len(re.findall(r"\bRank(?:Admin)?\b", APP)) <= 14


def test_css_uses_theme_tokens_only():
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', CSS), "不写十六进制颜色"
    assert "!important" not in CSS
    assert not re.search(r'\b(?:rgb|rgba|hsl|hsla)\(', CSS)
    assert not re.search(r'\bstyle\s*=', CSS)
    for match in re.finditer(r'(?<![\w-])(color|background|border-color|fill|stroke)\s*:\s*([^;}]+)', CSS):
        value = match.group(2).strip()
        assert value.startswith("var(") or value in {"transparent", "none", "inherit"}, match.group(0)
    for match in re.finditer(r'border(?:-left)?\s*:\s*[^;}]*', CSS):
        for token in re.findall(r'var\((--[\w-]+)\)', match.group(0)):
            assert token.startswith("--"), match.group(0)
        assert not re.search(r'#[0-9a-fA-F]{3,8}\b', match.group(0))


def test_css_backdrop_filter_absent_and_motion_is_gated():
    assert "backdrop-filter" not in CSS
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside)
    hover_blocks = re.findall(r"@media \(hover: hover\) and \(pointer: fine\) \{.*?\n\}", CSS, re.S)
    assert hover_blocks and all(":hover" in block for block in hover_blocks)
    assert CSS.count(":hover") == sum(block.count(":hover") for block in hover_blocks), ":hover 只能出现在 hover 媒体块里"


def test_css_layout_is_phone_safe_and_touch_friendly():
    assert "@media (max-width: 520px)" in CSS
    assert "minmax(0, 1fr)" in CSS and "overflow-wrap: anywhere" in CSS and "min-width: 0" in CSS
    assert CSS.count("min-height: 40px") >= 5 and "min-height: 48px" in CSS and "min-height: 56px" in CSS
    assert "overflow-x" not in CSS
    assert re.search(r"\.rank-row\.is-top \.rank-no \{[^}]*border: 2px solid var\(--accent\)", CSS), "前三名红笔圈"
    assert "font-variant-numeric: tabular-nums" in CSS
    assert not re.search(r"(?<![\w-])width:\s*\d{3,}px", CSS), "不写会撑出手机宽度的固定宽度"
