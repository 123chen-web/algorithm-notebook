"""付款登记（manual-claims）前端静态契约：文件存在、体积上限、CSP 安全、只用主题令牌、
状态不只靠颜色、触控目标与窄屏不横向滚动、动效只在 prefers-reduced-motion: no-preference 下。"""
import re
from pathlib import Path

import pytest

from test_contrast_tokens import THEMES, contrast_ratio, css_declarations, css_selectors

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "manual-claims.js").read_text(encoding="utf-8")
CSS = (STATIC / "manual-claims.css").read_text(encoding="utf-8")


def test_manual_claims_files_exist_and_stay_within_size_budget():
    assert (STATIC / "manual-claims.js").stat().st_size <= 32_000
    assert (STATIC / "manual-claims.css").stat().st_size <= 8_000   # 当前约 4 KB


def test_manual_claims_public_interface_is_exported():
    assert "window.ManualClaims = ManualClaims" in JS
    assert "window.ManualClaimsAdmin = ManualClaimsAdmin" in JS
    # 两个部件各有一套 configure / mount / refresh / reset。
    assert JS.count("configure: (value)") == 2
    assert JS.count("return { configure:") == 2
    for name in ("mount", "refresh", "reset"):
        assert len(re.findall(rf"^    function {name}\(", JS, flags=re.M)) == 2


def test_manual_claims_js_obeys_csp_and_text_only_contract():
    assert "innerHTML" not in JS
    assert "insertAdjacentHTML" not in JS
    assert "eval(" not in JS
    assert "document.write" not in JS
    assert not re.search(r"""\bstyle\s*=\s*["']""", JS), "不允许内联 style 属性"
    # 服务器与用户可控文字只走 textContent。
    assert ".textContent" in JS


def test_manual_claims_js_has_late_response_guards():
    assert "request.epoch === hooks.getEpoch()" in JS
    assert "request.userId === hooks.getUser()?.id" in JS
    assert "request.sequence === listSeq" in JS
    assert "container?.isConnected" in JS
    assert "pending.has(id)" in JS, "管理端处理中防重复点击"
    assert "sessionEpoch" in (STATIC / "app.js").read_text(encoding="utf-8")


def test_manual_claims_status_is_never_color_only():
    for text in (
        "待确认", "已开通", "已驳回",
        "确认已在微信/支付宝账单里核对到这笔款项？",
        "已收到，站长确认后会自动开通。",
        "这条登记已被处理，列表已刷新。",
    ):
        assert text in JS
    for symbol in ("⏳", "✓", "✕"):
        assert symbol in JS
    assert 'aria-hidden", "true"' in JS


def test_manual_claims_css_uses_theme_tokens_only():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS), "不允许十六进制颜色"
    assert "!important" not in CSS
    assert "backdrop-filter" not in CSS
    for _, _, declaration in re.findall(r"(?m)(^|;)\s*(color|background(?:-color)?)\s*:\s*([^;}]+)", CSS):
        assert declaration.startswith("var(") or declaration == "transparent", declaration


def test_manual_claims_css_mobile_and_touch_contracts():
    assert "min-height: 40px" in CSS
    assert "@media (max-width: 600px)" in CSS
    assert "minmax(0, 1fr)" in CSS
    assert "overflow-wrap: anywhere" in CSS
    # 隐藏的二次确认 / 驳回面板不能被 display 规则顶出来。
    assert ".mca-confirm[hidden]" in CSS and ".mca-reject-form[hidden]" in CSS


def test_manual_claims_motion_only_under_no_preference():
    head, marker, _tail = CSS.partition("@media (prefers-reduced-motion: no-preference)")
    assert marker, "动效必须只出现在 prefers-reduced-motion: no-preference 媒体查询里"
    assert "transition" not in head and "animation" not in head


def test_manual_claims_admin_requires_actual_account_receipt_and_preserves_frozen_snapshot():
    for field in ("amount_cents", "period_days", "plan_name_snapshot", "verified_amount_cents",
                  "receipt_reference", "legacy_reviewed", "legacy_period_days"):
        assert field in JS
    assert "Number.isSafeInteger(cents)" in JS
    assert "这条登记已经处理过了" in JS
    assert "原套餐名称、金额与周期未知" in JS
    assert "mca-confirm-error" in CSS


@pytest.mark.parametrize("context,tokens", list(THEMES.items()))
def test_actual_receipt_error_is_readable_in_every_theme(context, tokens):
    rules = [body for blocks, body in css_declarations(CSS)
             if blocks and ".mca-confirm-error" in tuple(css_selectors(blocks[-1]))]
    assert "color: var(--danger)" in rules
    assert contrast_ratio(tokens["--danger"], tokens["--surface"]) >= 4.5, context
