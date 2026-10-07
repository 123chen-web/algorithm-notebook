"""“讲给小黄鸭听”面板的静态契约：文件存在、CSP 安全写法、只用主题令牌、迟到响应守卫、
手机宽度与触控目标、动效只挂在 prefers-reduced-motion 下、体积上限。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "duck-panel.js").read_text(encoding="utf-8")
CSS = (STATIC / "duck-panel.css").read_text(encoding="utf-8")


def test_files_exist_and_stay_small():
    assert (STATIC / "duck-panel.js").stat().st_size <= 20_000, "JS 体积上限 20KB"
    assert (STATIC / "duck-panel.css").stat().st_size <= 10_000, "CSS 体积上限 10KB"


def test_js_is_csp_safe_and_uses_textcontent_only():
    for banned in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval("):
        assert banned not in JS, banned
    assert not re.search(r"\.style\.\w+\s*=", JS), "不写内联样式属性"
    assert 'setAttribute("style"' not in JS
    assert "localStorage" not in JS and "sessionStorage" not in JS, "对话只存内存"
    assert "textContent" in JS


def test_js_never_fetches_directly_and_posts_the_contract_body():
    assert "fetch(" not in JS, "请求只走宿主传入的 api()"
    assert "hooks.api" in JS
    assert '/api/mistakes/${panel.mistakeId}/duck' in JS
    assert '"POST"' in JS and "finish" in JS and "turns" in JS


def test_js_guards_late_responses_with_epoch_user_generation_and_sequence():
    assert "request.epoch === hooks.getEpoch()" in JS
    assert "request.userId === hooks.getUser()?.id" in JS
    assert "request.generation === generation" in JS
    assert "request.sequence === sendSeq" in JS
    assert "request.mistakeId === panel.mistakeId" in JS
    assert "panel.root.isConnected" in JS, "关闭面板后迟到响应无处可写"


def test_js_keyboard_ime_and_live_region_contract():
    assert "compositionstart" in JS and "compositionend" in JS and "event.isComposing" in JS
    assert "event.shiftKey" in JS and '"Enter"' in JS
    assert 'setAttribute("aria-live", "polite")' in JS
    assert 'setAttribute("role", "alert")' in JS
    assert "input.maxLength = MAX_CHARS" in JS and "MAX_CHARS = 600" in JS
    assert "MAX_TURNS = 12" in JS
    for label in ("发送", "结束并总结", "重新开始", "重试"):
        assert f'"{label}"' in JS, label
    assert "小黄鸭在想…" in JS and "额度用完" in JS and "刷新或离开就没了" in JS
    assert "我" in JS and "小黄鸭" in JS, "说话人用文字标明，不只靠颜色"


def test_js_exposes_the_public_interface():
    for name in ("configure", "mount", "unmount", "reset"):
        assert re.search(rf"\b{name}\b", JS)
    assert "window.DuckPanel = {" in JS


def test_css_uses_theme_tokens_only():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS), "不写十六进制颜色"
    assert "!important" not in CSS
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", CSS)
    for match in re.finditer(r"(?<![\w-])(color|background|border-color)\s*:\s*([^;}]+)", CSS):
        value = match.group(2).strip()
        assert value.startswith("var(") or value in {"transparent", "none", "inherit"}, match.group(0)


def test_css_text_colors_land_on_checked_pairs():
    """文字色 / 底色配对必须都在 test_contrast_tokens.py 已经逐主题检查过的矩阵里
    （ink / ink-2 / muted × paper / paper-2 / surface，外加 --soft、--error-*、--success-*）。"""
    checked = {
        ("--ink", "--surface"), ("--ink-2", "--surface"), ("--muted", "--surface"),
        ("--ink", "--soft"), ("--ink-2", "--soft"),
        ("--ink", "--paper-2"), ("--ink-2", "--paper-2"),
        ("--error-ink", "--error-surface"),
        ("--success-ink", "--success-soft"),
    }
    declarations = re.findall(r"\.duck-[\w.-]*[^{]*\{([^}]*)\}", CSS)
    assert declarations, "没有解析到任何规则，检查方式失效了"
    used = set()
    for body in declarations:
        color = re.search(r"(?<![\w-])color:\s*var\((--[\w-]+)\)", body)
        background = re.search(r"(?<![\w-])background:\s*var\((--[\w-]+)\)", body)
        if color and background:
            used.add((color.group(1), background.group(1)))
        elif color:
            used.add((color.group(1), "--surface"))  # 不写底色的文字落在 .duck-panel 的 --surface 上
    assert ("--error-ink", "--error-surface") in used
    assert used <= checked, used - checked


def test_css_motion_is_gated_and_hover_free():
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside)
    assert ":hover" not in CSS, "状态不靠悬停揭示"
    assert "backdrop-filter" not in CSS


def test_css_layout_is_phone_safe_and_touch_friendly():
    assert "@media (max-width: 520px)" in CSS
    assert "overflow-wrap: anywhere" in CSS and "min-width: 0" in CSS
    assert "overflow-x" not in CSS
    assert "min-height: 40px" in CSS, "触控目标 ≥ 40px"
    assert not re.search(r"(?<![\w-])(?<!max-)width:\s*\d{3,}px", CSS), "不写会撑出手机宽度的固定宽度"
    assert not re.search(r"(?<!\()max-width:\s*(?:[4-9]\d{2}|\d{4,})px", CSS), "不写超过手机宽度的固定 max-width（媒体查询除外）"
    assert "font-variant-numeric: tabular-nums" in CSS
