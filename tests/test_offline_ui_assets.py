"""离线复习界面接线的静态契约：index.html 标签、脚本顺序、版本号、CSP 安全写法、app.js 最小接线。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
REVIEW_UI = (STATIC / "offline-review.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
SYNC = (STATIC / "offline-sync.js").read_text(encoding="utf-8")
REGISTER = (STATIC / "pwa-register.js").read_text(encoding="utf-8")

SIZE_LIMITS = {
    "static/offline-review.js": 14_000,
}


def test_new_widget_stays_small():
    for relative, limit in SIZE_LIMITS.items():
        size = (ROOT / relative).stat().st_size
        assert size <= limit, f"{relative} 有 {size} 字节，超过上限 {limit}"


def test_index_declares_manifest_and_theme_color():
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in INDEX
    assert '<meta name="theme-color" content="#c23a2b">' in INDEX
    assert INDEX.index('rel="manifest"') > INDEX.index('rel="icon"')


def test_index_loads_pwa_css_between_plan_and_print():
    plan_css = INDEX.index('href="/static/plan.css?v=1"')
    pwa_css = INDEX.index('href="/static/pwa.css?v=1"')
    print_css = INDEX.index('href="/static/print.css?v=2"')
    assert plan_css < pwa_css < print_css


def test_index_loads_offline_scripts_in_fixed_order_before_app_js():
    queue_js = INDEX.index('src="/static/offline-queue.js?v=1"')
    register_js = INDEX.index('src="/static/pwa-register.js?v=1"')
    sync_js = INDEX.index('src="/static/offline-sync.js?v=2"')
    review_ui_js = INDEX.index('src="/static/offline-review.js?v=1"')
    plan_js = INDEX.index('src="/static/plan.js?v=3"')
    app_js = INDEX.index('src="/static/app.js?v=85"')
    # offline-queue 必须在 offline-sync 前；UI 编排紧随其后；全部在 app.js 前。
    assert plan_js < queue_js < register_js < sync_js < review_ui_js < app_js
    assert INDEX.count('src="/static/offline-queue.js?v=1"') == 1
    assert INDEX.count('src="/static/offline-sync.js?v=2"') == 1


def test_app_js_bumped_to_78():
    assert 'src="/static/app.js?v=85"' in INDEX
    assert 'src="/static/app.js?v=77"' not in INDEX

def test_app_js_has_the_three_pwa_wiring_points():
    # 启动配置与注册。
    assert re.search(r"window\.OfflineSync\?\.configure\(\{ api, getUser: \(\) => user, getEpoch: \(\) => sessionEpoch \}\);", APP)
    assert 'window.PwaRegister?.configure({ version: "1" });' in APP
    assert "window.PwaRegister?.register();" in APP
    # 登录后重新配置并预取。
    assert re.search(r"window\.Onboarding\?\.reset\(user\);[\s\S]{0,400}?OfflineSync\?\.prefetch\(\)", APP)
    # 登出先提示保留、再 reset。
    assert re.search(r"OfflineReview\?\.onSignedOut\(\);[^\n]*\n\s*window\.OfflineSync\?\.reset\(\);", APP)


def test_app_js_review_page_uses_offline_widget():
    # 列表 / 详情请求遇到网络错误时走预取兜底。
    assert "OfflineReview?.todayFallback()" in APP
    assert "OfflineReview?.cachedItem(" in APP
    # 离线评分走入队；离线详情禁用联网功能。
    assert "OfflineReview?.grade(" in APP
    assert "OfflineReview?.restrictDetail(" in APP


def test_review_ui_js_is_csp_and_xss_safe():
    for forbidden in ("innerHTML", "insertAdjacentHTML", "outerHTML", "eval(", "document.write"):
        assert forbidden not in REVIEW_UI, forbidden
    assert not re.search(r"\.style\.\w+\s*=", REVIEW_UI)
    assert 'setAttribute("style"' not in REVIEW_UI
    assert "style=" not in REVIEW_UI
    # 用户可控文字只走 textContent（不允许 insertAdjacent/HTML 拼接）。
    assert "textContent" in REVIEW_UI


def test_review_ui_uses_existing_offline_sync_contract():
    assert "OfflineSync.enqueueGrade" in REVIEW_UI or "enqueueGrade(" in REVIEW_UI
    assert "readTodayQueue" in REVIEW_UI
    assert "flush" in REVIEW_UI and "summary" in REVIEW_UI
    for event in ("pwa:flush-result", "pwa:queue-dropped", "pwa:auth-expired"):
        assert event in REVIEW_UI


def test_offline_sync_announces_flush_result_and_rearms_failures_online():
    assert "pwa:flush-result" in SYNC, "flush 结束要播报结果，界面才能提示成功/跳过/失败"
    assert "retryFailed" in SYNC, "联网后要能把 failed 的评分重新排队（失败可重试）"


def test_pwa_register_offline_bar_mentions_pending_count():
    assert "条评分待同步" in REGISTER
