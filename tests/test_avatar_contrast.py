"""The letter avatar (white initial on a hashed colour) stays readable for every possible hue."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_cursor_fx_assets import function_body


STATIC = Path(__file__).resolve().parents[1] / "static"

# 另写一套 HSL→RGB 和对比度算法来验算，避免和 app.js 里的实现犯同一个错。
HARNESS = r"""
const payload = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const avatarBackground = new Function(payload.source + "; return avatarBackground;")();
const linear = (value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
function toRgb(h, s, l) {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const sector = Math.floor(h / 60) % 6;
  const [r, g, b] = [[c, x, 0], [x, c, 0], [0, c, x], [0, x, c], [x, 0, c], [c, 0, x]][sector];
  const m = l - c / 2;
  return [r + m, g + m, b + m];
}
const rows = [];
for (let hue = 0; hue < 360; hue += 1) {
  const text = avatarBackground(hue);
  const match = /^hsl\((\d+), 55%, (\d+)%\)$/.exec(text);
  if (!match || Number(match[1]) !== hue) { rows.push({ hue, text, bad: true }); continue; }
  const [r, g, b] = toRgb(hue, 0.55, Number(match[2]) / 100);
  const luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
  rows.push({ hue, lightness: Number(match[2]), contrast: 1.05 / (luminance + 0.05) });
}
console.log(JSON.stringify(rows));
"""


def avatar_rows():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to run the avatar colour function")
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    function = "function avatarBackground(hue) {" + function_body(source, "avatarBackground") + "}"
    result = subprocess.run(
        [node, "-e", HARNESS], input=json.dumps({"source": function}), text=True,
        encoding="utf-8", capture_output=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_white_initial_has_at_least_4_5_to_1_on_every_hue():
    rows = avatar_rows()
    assert len(rows) == 360
    assert not [row for row in rows if row.get("bad")], "every hue must give hsl(<hue>, 55%, <n>%)"
    worst = min(rows, key=lambda row: row["contrast"])
    assert worst["contrast"] >= 4.5, worst


def test_only_the_bright_hues_are_darkened_and_never_below_the_floor():
    rows = avatar_rows()
    darkened = [row for row in rows if row["lightness"] != 45]
    assert darkened, "yellow / green / cyan hues need a darker background"
    assert len(darkened) < 200, "most hues keep the original colour"
    assert all(row["lightness"] >= 20 for row in rows)
    # 原来就够用的色相（红、蓝、紫、粉）完全不变。
    assert all(row["lightness"] == 45 for row in rows if row["hue"] < 20 or row["hue"] > 210)


def test_the_fallback_avatar_uses_that_function():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "fallback.style.background = avatarBackground(avatarHue(username || \"\"));" in source
    assert "55%, 45%" not in source.replace("\r\n", "\n").split("function avatarBackground")[0], "no hard-coded 45% left"
