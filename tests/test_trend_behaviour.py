"""总览趋势区：Node 行为测试的包装 + 静态契约（关键 id、版本号、无十六进制色 / !important / 内联样式）。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
IDS = [
    "home-trend", "home-trend-range", "home-trend-skeleton", "home-trend-error", "home-trend-retry",
    "home-trend-body", "home-trend-metrics", "home-trend-panel", "home-trend-chart",
    "home-trend-readout", "home-trend-how", "home-trend-table-body",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_trend_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/trend_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 11, result.stdout[-1500:]


def test_trend_markup_has_required_ids_and_roles():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for identifier in IDS:
        assert f'id="{identifier}"' in html, identifier
    assert 'id="home-trend-metrics" class="ov-metrics" role="tablist"' in html
    for days in (7, 30, 90):
        assert f'data-days="{days}"' in html
    trend = html[html.index('<section id="home-trend"'):html.index('<div class="ov-columns">')]
    assert "style=" not in trend and "onclick" not in trend
    # 趋势区放在现有磁贴之后、待复习列表之前，不替换任何现有卡片。
    assert html.index('class="ov-tiles"') < html.index('id="home-trend"') < html.index('class="ov-columns"')
    for kept in ("tile-review", "overview-heatmap", "ov-due-list"):
        assert kept in html


def test_trend_assets_are_versioned_and_clean():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/static/overview.css?v=3" in html and "/static/overview.js?v=3" in html
    css = (STATIC / "overview.css").read_text(encoding="utf-8")
    js = (STATIC / "overview.js").read_text(encoding="utf-8")
    mine = css.split("趋势区：指标条 + 主图")[1]  # 只检查本区新增的样式
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", mine)
    assert "!important" not in mine
    assert "backdrop-filter" not in mine
    assert not re.search(r"\.style\.(cssText|color|background)", js)
    assert "innerHTML" not in js
    assert "window.Overview = { render, renderError, reset, renderTrend, trendModel, mountTrend }" in js
    # 动画只在 no-preference 下。
    assert re.search(r"@media \(prefers-reduced-motion: no-preference\)\s*\{[^}]*animation", css)
    assert "animation" not in re.sub(r"@media \(prefers-reduced-motion: no-preference\)[\s\S]*", "", mine)
