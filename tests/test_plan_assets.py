"""套餐页重做的静态契约：资源版本、关键 id、CSP 友好、令牌用色、迟到响应守卫仍在。"""
from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "plan.css").read_text(encoding="utf-8")
JS = (STATIC / "plan.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
PAGE = HTML[HTML.index('<section id="plan-page"'):HTML.index("</main>")]


def test_plan_assets_are_versioned_and_loaded_before_app():
    assert '/static/plan.css?v=1' in HTML
    assert '/static/plan.js?v=2' in HTML
    assert HTML.index('/static/plan.js?') < HTML.index('/static/app.js?')
    assert int(re.search(r'/static/app.js\?v=(\d+)', HTML).group(1)) >= 59


def test_plan_page_keeps_every_id_the_scripts_and_redeem_flow_rely_on():
    for name in (
        "plan-title", "plan-subscription", "plan-trial-note", "plan-catalog", "plan-list",
        "plan-order", "plan-order-details", "plan-payment", "plan-order-status", "plan-back",
        "plan-how", "plan-orders", "plan-orders-list", "redeem-panel", "redeem-form", "redeem-code",
        "redeem-submit", "redeem-status", "redeem-trial-note", "manual-payment-status",
        "manual-payment-panel", "manual-payment-prices", "manual-payment-qrs",
        "manual-payment-contact", "manual-qr-dialog",
    ):
        assert f'id="{name}"' in PAGE, name
    assert "选择套餐" in PAGE and "开通方式" in PAGE and "我的订单" in PAGE


def test_plan_page_sections_are_in_the_designed_order():
    order = [PAGE.index(f'id="{name}"') for name in ("plan-status", "plan-catalog", "plan-how", "plan-orders")]
    assert order == sorted(order)
    # 手动付款三步在启用的面板里，备用兑换码入口仍保留在其后。
    assert PAGE.index('id="manual-payment-panel"') < PAGE.index('id="redeem-panel"')
    assert PAGE.count('class="pl-steps"') == 1
    assert PAGE.index('class="pl-steps"') > PAGE.index('id="manual-payment-panel"')


def test_manual_payment_steps_match_registration_and_actual_amount_limits():
    for instruction in (
        '按套餐价格扫码付款，备注写用户名',
        '在下方「付款登记」提交「我已付款」',
        '站长核对金额与备注后确认开通',
        '网页无法锁定付款 App 的金额',
    ):
        assert instruction in PAGE
    assert '站长确认后发给你兑换码' not in PAGE


def test_plan_page_has_no_inline_style_or_script():
    assert not re.search(r'\bstyle\s*=', HTML)
    assert not re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', HTML)
    assert 'innerHTML' not in JS and 'insertAdjacentHTML' not in JS
    assert 'style.cssText' not in JS


def test_plan_css_uses_only_theme_tokens():
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', CSS)
    assert '!important' not in CSS
    assert 'backdrop-filter' not in CSS
    for _, _, value in re.findall(r'(?m)(^|[;{])\s*(color|background(?:-color)?|border-color)\s*:\s*([^;}]+)', CSS):
        assert value.strip().startswith('var(') or value.strip() in {'transparent', 'currentColor'}, value
    assert 'font-variant-numeric: tabular-nums' in CSS
    # 动效只在未要求减少动效时出现。
    outside = re.sub(r'@media \(prefers-reduced-motion: no-preference\) \{.*', '', CSS, flags=re.S)
    assert 'transition' not in outside and 'animation' not in outside


def test_plan_css_is_scoped_and_mobile_safe():
    assert '--pl-mono' in CSS
    assert not re.search(r'(?m)^:root', CSS)
    assert '@media (max-width: 700px)' in CSS
    assert 'attr(data-label)' in CSS
    assert 'min-width: 0' in CSS


def test_plan_app_still_guards_late_responses_and_keeps_payment_flow():
    for needle in (
        'if (user !== currentUser || !user || view !== "plan" || !isCurrent()) return false;',
        'planCatalog = [];',
        '"/api/orders"', 'JSON.stringify({ plan_id: plan.id, channel })', 'startOrderPolling', 'orderPollGeneration',
        'window.Redeem?.loadPlan(plans)', 'confirm(`确认申请退回',
    ):
        assert needle in APP, needle
    assert 'window.PlanView.renderOrders' in APP and 'window.PlanView.renderCards' in APP


def test_plan_view_exposes_the_pure_functions():
    assert 'window.PlanView = { model, formatPrice, daysLeft' in JS
