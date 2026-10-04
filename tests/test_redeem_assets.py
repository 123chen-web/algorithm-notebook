"""手动收款 UI 的 CSP、版本、可访问性与主题静态契约。"""
from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "redeem.css").read_text(encoding="utf-8")
JS = (STATIC / "redeem.js").read_text(encoding="utf-8")


def test_redeem_assets_are_versioned_and_loaded_before_app():
    assert '/static/redeem.css?v=1' in HTML
    assert '/static/redeem.js?v=1' in HTML
    version = re.search(r'/static/app.js\?v=(\d+)', HTML)
    assert version and int(version.group(1)) >= 58
    assert HTML.index('/static/redeem.js?') < HTML.index('/static/app.js?')


def test_redeem_markup_contains_required_controls():
    for name in (
        "redeem-panel", "redeem-form", "redeem-code", "redeem-submit", "redeem-status",
        "redeem-trial-note", "manual-payment-panel", "manual-payment-prices",
        "manual-payment-contact", "manual-payment-qrs", "manual-qr-dialog", "admin-redeem-card",
        "manual-settings-form", "manual-enabled", "manual-contact", "manual-alipay-file",
        "manual-wechat-file", "redeem-generate-form", "redeem-generated-codes", "redeem-copy",
        "redeem-status-filters", "redeem-code-list", "manual-grant-form", "manual-grant-status",
    ):
        assert f'id="{name}"' in HTML
    code = re.search(r'<input\b[^>]*id="redeem-code"[^>]*>', HTML).group()
    for required in ('maxlength="64"', 'autocomplete="off"', 'spellcheck="false"', 'inputmode="text"'):
        assert required in code
    assert re.search(r'id="redeem-status"[^>]*role="status"', HTML)
    assert '关闭后不能再看到明文，请现在复制发给对方' in HTML
    assert '注册正式账号后可兑换' in HTML


def test_redeem_ui_obeys_csp_and_plain_text_contract():
    # 全文检查覆盖嵌套卡片，SVG 展示属性不是内联 CSS。
    assert not re.search(r'\bstyle\s*=', HTML)
    assert not re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', HTML)
    assert 'innerHTML' not in JS
    assert '$("#manual-payment-contact").textContent = data.contact' in JS
    assert '"X-CSRF-Protection": "1"' in JS
    assert 'POST /api/orders' not in JS
    assert '"/api/orders"' not in JS
    assert 'sessionEpoch' in (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'request.epoch === hooks.getEpoch()' in JS


def test_redeem_css_uses_theme_tokens_and_mobile_safe_tracks():
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', CSS)
    assert '!important' not in CSS
    assert 'backdrop-filter' not in CSS
    for _, _, declaration in re.findall(r'(?m)(^|;)\s*(color|background(?:-color)?)\s*:\s*([^;}]+)', CSS):
        assert declaration.startswith('var(') or declaration == 'transparent'
    assert 'repeat(2, minmax(0, 1fr))' in CSS
    assert '@media (max-width: 600px)' in CSS
    assert 'grid-template-columns: minmax(0, 1fr)' in CSS
    assert 'min-height: 40px' in CSS
