"""「手感层」（static/feel.css + feel.js）：资源接入、动效开关、安全规则，以及 Node 行为测试。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "feel.css").read_text(encoding="utf-8")
JS = (STATIC / "feel.js").read_text(encoding="utf-8")


def test_assets_are_wired_with_versions():
    assert '<link rel="stylesheet" href="/static/feel.css?v=1">' in INDEX
    assert '<script defer src="/static/feel.js?v=1"></script>' in INDEX
    # feel.css 要排在组件样式之后，才能覆盖它们的 transition。
    assert INDEX.index("feel.css") > INDEX.index("roadmap.css") > INDEX.index("overview.css")


def test_hover_effects_only_for_fine_pointer_with_motion_allowed():
    block = re.search(r"@media \(hover: hover\) and \(pointer: fine\) and \(prefers-reduced-motion: no-preference\) \{", CSS)
    assert block, "上浮/聚光灯必须只在有精细指针且允许动画时生效"
    before_block = CSS[:block.start()]
    # 在这个媒体块之前，不能有对卡片的 :hover 规则。
    assert ":hover" not in before_block


def test_every_animation_has_a_reduced_motion_escape():
    for keyword in ("feel-page-in", "feel-dialog-in", "feel-toast-in"):
        index = CSS.index(f"animation: {keyword}") if f"animation: {keyword}" in CSS else CSS.index(keyword)
        window = CSS[max(0, index - 700):index]
        assert "prefers-reduced-motion" in window, keyword
    assert "@media (prefers-reduced-motion: reduce) { .feel-skeleton > i { animation: none; } }" in CSS


def test_spring_has_a_fallback_for_browsers_without_linear_easing():
    assert "@supports not (transition-timing-function: linear(0, 1))" in CSS


def test_only_transform_like_properties_are_animated():
    for banned in ("transition: width", "transition: top", "transition: left", "backdrop-filter: blur(12px)"):
        assert banned not in CSS
    # <details> 折叠用 height 补间，只在支持 ::details-content 的浏览器里启用。
    assert "@supports selector(::details-content)" in CSS


def test_feel_js_is_csp_safe_and_idle_when_nothing_moves():
    code = re.sub(r"/\*[\s\S]*?\*/", "", JS)
    for banned in ("innerHTML", "insertAdjacentHTML", "eval(", "new Function", "setAttribute(\"style\""):
        assert banned not in code
    # 没有 setInterval：静止时不能有任何周期性任务。
    assert "setInterval" not in code
    assert code.count('addEventListener("pointermove"') == 1, "只允许一个全局 pointermove 监听"


def test_magnetic_buttons_are_marked_in_markup():
    assert re.search(r'id="topbar-new"[^>]*data-magnetic', INDEX)
    assert re.search(r'id="tile-review-start"[^>]*data-magnetic', INDEX)


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_feel_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/feel_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 9, result.stdout[-1500:]
