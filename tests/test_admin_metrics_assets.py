"""运营概览的静态契约：关键 id、版本号、CSP、令牌与动效约束。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "admin-metrics.css").read_text(encoding="utf-8")
JS = (STATIC / "admin-metrics.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")

IDS = (
    "admin-metrics", "admin-metrics-title", "admin-metrics-status", "admin-metrics-retry",
    "admin-metrics-skeleton", "admin-metrics-body", "admin-metrics-strip", "admin-metrics-chart-wrap",
    "admin-metrics-chart", "admin-metrics-table", "admin-metrics-funnel", "admin-metrics-funnel-empty",
    "admin-metrics-ai-table", "admin-metrics-ai-empty", "admin-metrics-ai-split",
    "admin-metrics-other", "admin-metrics-updated", "admin-reports-heading",
)


def test_markup_has_the_required_ids_once_and_sits_above_the_dashboard():
    for name in IDS:
        assert len(re.findall(rf'\bid="{name}"', HTML)) == 1, name
    assert HTML.index('id="admin-metrics"') < HTML.index('id="admin-dashboard"')
    assert HTML.index('id="admin-metrics"') < HTML.index('id="admin-redeem-card"')
    assert re.search(r'id="admin-metrics"[^>]*\bdata-admin-only\b[^>]*\bhidden\b', HTML)
    for control in ('data-am-days="7"', 'data-am-days="30"', 'data-am-series="new_users"',
                    'data-am-series="active_users"', 'data-am-series="reviews"'):
        assert control in HTML
    assert re.search(r'id="admin-metrics-status"[^>]*role="status"', HTML)
    assert re.search(r'id="admin-metrics-chart"[^>]*role="img"', HTML)
    assert "<summary>查看数据表</summary>" in HTML and "<summary>怎么算</summary>" in HTML
    assert "令牌来自服务商返回的用量，仅供估算成本；单价请按你所用服务商的价目表自己换算。" in HTML
    assert not re.search(r"[¥￥$]|USD|RMB", HTML[HTML.index('id="admin-metrics"'):HTML.index('id="admin-dashboard"')])


def test_assets_are_versioned_and_loaded_in_order():
    assert '/static/admin-metrics.css?v=1' in HTML
    assert '/static/admin-metrics.js?v=1' in HTML
    assert HTML.index('/static/admin-metrics.js?') < HTML.index('/static/app.js?')
    version = re.search(r'/static/app.js\?v=(\d+)', HTML)
    assert version and int(version.group(1)) >= 59


def test_csp_safe_markup_and_script():
    assert not re.search(r'\bstyle\s*=', HTML)
    assert not re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', HTML)
    assert "innerHTML" not in JS and "insertAdjacentHTML" not in JS and "eval(" not in JS
    assert not re.search(r'\.style\.(?!setProperty)\w+\s*=', JS)
    assert "/api/admin/metrics?days=" in JS
    assert '"admin-metrics-days"' in JS
    assert "request.epoch === hooks.getEpoch()" in JS and "request.generation === generation" in JS
    assert not re.search(r"[¥￥$]\d|USD|RMB|price", JS, re.I)


def test_app_js_only_wires_load_configure_and_reset():
    assert "window.AdminMetrics?.load()" in APP
    assert "window.AdminMetrics?.reset()" in APP
    assert "window.AdminMetrics?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch, getView: () => view })" in APP
    assert len(re.findall(r"AdminMetrics", APP)) == 5


def test_css_uses_theme_tokens_only_and_respects_reduced_motion():
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', CSS)
    assert "!important" not in CSS
    assert "backdrop-filter" not in CSS
    assert not re.search(r'\b(?:rgb|rgba|hsl|hsla)\(', CSS)
    for match in re.finditer(r'(?<![\w-])(color|background|fill|stroke)\s*:\s*([^;}]+)', CSS):
        value = match.group(2).strip()
        assert value.startswith("var(") or value in {"transparent", "none", "inherit"}, match.group(0)
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*\n\}", CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside)


def test_css_layout_is_mobile_safe_and_touch_friendly():
    assert "scroll-snap-type: x mandatory" in CSS and "scroll-snap-align: start" in CSS
    assert "@media (max-width: 700px)" in CSS
    assert CSS.count("min-height: 40px") >= 4
    assert "font-variant-numeric: tabular-nums" in CSS
    assert ".am-table-scroll { max-width: 100%; overflow-x: auto; }" in CSS
    assert "min-width: 0" in CSS
