"""总览页「目标」卡的静态契约：文件存在、CSP 安全写法、主题令牌、动效门控、
手机宽度与触控目标、迟到响应守卫、体积上限。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
CSS = (STATIC / "goal-card.css").read_text(encoding="utf-8")
JS = (STATIC / "goal-card.js").read_text(encoding="utf-8")


def test_files_exist_and_stay_small():
    assert (STATIC / "goal-card.js").is_file()
    assert (STATIC / "goal-card.css").is_file()
    assert len(JS.encode("utf-8")) <= 24 * 1024, "goal-card.js 超过 24KB"
    assert len(CSS.encode("utf-8")) <= 12 * 1024, "goal-card.css 超过 12KB"


def test_public_contract_is_exported():
    assert "window.GoalCard" in JS
    for name in ("configure", "mount", "refresh", "reset", "helpers"):
        assert re.search(rf"\b{name}\b", JS), name
    for method in ('"/api/goal"',):
        assert method in JS
    assert '"PUT"' in JS and '"DELETE"' in JS


def test_csp_safe_script():
    for banned in ("innerHTML", "insertAdjacentHTML", "eval(", "outerHTML", "document.write"):
        assert banned not in JS, banned
    assert not re.search(r"\.style\.\w+\s*=", JS), "样式只能走 element.style.setProperty"
    assert 'setAttribute("style"' not in JS
    assert "localStorage" not in JS and "sessionStorage" not in JS
    # 请求只能走宿主传入的 api()，不直接 fetch。
    assert "fetch(" not in JS.replace("hooks.api", "")


def test_user_controlled_text_uses_textcontent():
    assert "textContent" in JS
    # 用户可控字段（目标名称）出现的地方都用 textContent 写入。
    assert re.search(r'node\("p", "gc-name", String\(goal\.name', JS)


def test_late_response_guards_are_in_place():
    assert "request.epoch === hooks.getEpoch()" in JS
    assert "request.userId === user.id" in JS
    assert "request.sequence === sequence[request.kind]" in JS
    assert "request.generation === generation" in JS
    # reset() 清掉计时器、序号和缓存。
    reset = re.search(r"function reset\(\) \{(.*?)\n  \}", JS, re.S).group(1)
    assert "clearTimeout(midnightTimer)" in reset
    assert "sequence[kind] += 1" in reset
    assert 'mode = "idle"' in reset


def test_progressbar_and_status_are_accessible():
    assert 'setAttribute("role", "progressbar")' in JS
    for attribute in ('"aria-valuemin"', '"aria-valuemax"', '"aria-valuenow"', '"aria-label"'):
        assert attribute in JS, attribute
    assert '"alert"' in JS and '"status"' in JS  # 错误用 role=alert，加载用 role=status
    assert "aria-busy" in JS


def test_css_uses_theme_tokens_only():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS), "不写十六进制颜色"
    assert "!important" not in CSS
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", CSS)
    for match in re.finditer(r"(?<![\w-])(color|background|border-color|fill|stroke)\s*:\s*([^;}]+)", CSS):
        value = match.group(2).strip()
        assert value.startswith("var(") or value in {"transparent", "none", "inherit"}, match.group(0)


def test_css_motion_is_gated_and_no_horizontal_scroll_risk():
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside)
    assert "overflow-x" not in CSS
    assert not re.search(r"(?<![\w-])width:\s*\d{3,}px", CSS), "不写会撑出手机宽度的固定宽度"
    assert "min-width: 0" in CSS and "overflow-wrap: anywhere" in CSS and "max-width: 100%" in CSS
    assert "@media (max-width: 480px)" in CSS


def test_css_touch_targets_and_phone_layout():
    assert CSS.count("min-height: 40px") >= 4, "按钮触控目标 ≥ 40px"
    assert "flex-wrap: wrap" in CSS
    assert "font-variant-numeric: tabular-nums" in CSS


def test_css_text_colors_are_checked_token_pairs():
    """goal-card.css 里出现的文字 / 底色令牌必须落在 INTEGRATION.md 登记的配对集合里。"""
    checked = {
        ("--ink", "--surface"), ("--ink-2", "--surface"), ("--azurite", "--surface"),
        ("--success-ink", "--surface"), ("--danger", "--surface"),
        ("--error-ink", "--error-surface"), ("--ink", "--paper-2"), ("--ink", "--danger-soft"),
    }
    foregrounds = {fg for fg, _ in checked}
    backgrounds = {bg for _, bg in checked} | {"--azurite", "--line"}  # azurite/line 只做装饰与描边
    used_foregrounds, used_backgrounds = set(), set()
    source = re.sub(r"/\*[\s\S]*?\*/", "", CSS)
    for match in re.finditer(r"(?<![\w-])(color|background|background-color)\s*:\s*([^;}]+)", source):
        tokens = set(re.findall(r"var\((--[\w-]+)\)", match.group(2)))
        if match.group(1) == "color":
            used_foregrounds |= tokens
        else:
            used_backgrounds |= tokens
    assert used_foregrounds, "没有解析到任何文字颜色，检查方式失效了"
    assert used_foregrounds <= foregrounds, used_foregrounds - foregrounds
    assert used_backgrounds <= backgrounds, used_backgrounds - backgrounds
