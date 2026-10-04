"""无需浏览器的主题正文颜色对比度检查。"""

from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
BACKGROUNDS = ("--paper", "--paper-2", "--surface")
MINIMUMS = {"--ink": 4.5, "--ink-2": 4.5, "--muted": 4.8}
# Forum controls, accepted answers, terminal code, and mention highlights all use
# these existing semantic tokens. Keep the original body-text matrix intact.
THREAD_TEXT_PAIRS = (
    ("--azurite", "--surface"),
    ("--azurite", "--paper"),
    ("--azurite", "--paper-2"),
    ("--azurite", "--success-soft"),
    ("--ink", "--success-soft"),
    ("--ink-2", "--success-soft"),
    ("--muted", "--success-soft"),
    ("--success-ink", "--success-soft"),
    ("--success-ink", "--surface"),
    ("--success-ink", "--paper"),
    ("--success-muted", "--success-soft"),
    ("--danger", "--surface"),
    ("--danger", "--paper"),
    ("--danger", "--paper-2"),
    ("--danger", "--success-soft"),
    ("--danger-hover", "--danger-soft"),
    ("--code-ink", "--code-surface"),
)
COLOR_TOKENS = set(BACKGROUNDS) | MINIMUMS.keys() | {
    token for pair in THREAD_TEXT_PAIRS for token in pair
}


def css_declarations(source):
    """按现有静态检查的方式保留声明所属的选择器和嵌套块。"""
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    blocks = []
    for match in re.finditer(r"([^{};]*)([{};])", source):
        text, delimiter = match.groups()
        text = text.strip()
        if delimiter == "{":
            blocks.append(text)
        else:
            if text:
                yield tuple(blocks), text
            if delimiter == "}":
                assert blocks, "CSS 出现多余的闭合括号"
                blocks.pop()
    assert not blocks, "CSS 块未闭合"


def css_selectors(selector_list):
    depth = 0
    start = 0
    for index, character in enumerate(selector_list):
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            yield selector_list[start:index].strip()
            start = index + 1
    yield selector_list[start:].strip()


def theme_context(selector):
    attributes = {}
    for match in re.finditer(
        r"\[\s*(data-theme|data-scene-tone)\s*=\s*"
        r"(?:\"([^\"]+)\"|'([^']+)'|([\w-]+))\s*\]",
        selector,
    ):
        name, double_quoted, single_quoted, unquoted = match.groups()
        attributes[name] = double_quoted or single_quoted or unquoted
    return attributes.get("data-theme"), attributes.get("data-scene-tone")


def theme_tokens(stylesheets):
    overrides = {}
    contexts = {(None, None)}
    for source in stylesheets:
        for blocks, declaration in css_declarations(source):
            if not blocks or blocks[-1].startswith("@"):
                continue
            selectors = tuple(css_selectors(blocks[-1]))
            for selector in selectors:
                theme, tone = theme_context(selector)
                if theme is not None or tone is not None:
                    contexts.add((theme, tone))
                    contexts.add((theme, None))
            name, separator, value = declaration.partition(":")
            name, value = name.strip(), value.strip()
            if not separator or name not in COLOR_TOKENS:
                continue
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (
                f"请扩展颜色解析以支持 {name}: {value}；不能跳过令牌"
            )
            assert len(blocks) == 1, f"请扩展解析以检查条件覆盖：{blocks}"
            for selector in selectors:
                assert re.fullmatch(r"(?:html|:root)(?:\[[^\]]+\])*", selector), (
                    f"请扩展解析以检查颜色覆盖：{selector}"
                )
                context = theme_context(selector)
                assert selector == ":root" or context != (None, None), (
                    f"无法识别颜色令牌所属主题或场景：{selector}"
                )
                contexts.add(context)
                overrides.setdefault(context, {})[name] = value
    root = overrides.get((None, None), {})
    assert COLOR_TOKENS <= root.keys(), "根主题缺少正文颜色或底色令牌"
    global_tones = {tone for theme, tone in contexts if theme is None and tone}
    for theme, _ in tuple(contexts):
        if theme is not None:
            contexts.update((theme, tone) for tone in global_tones)
    resolved = {}
    for theme, tone in contexts:
        tokens = dict(root)
        if tone is not None:
            tokens.update(overrides.get((None, tone), {}))
        if theme is not None:
            tokens.update(overrides.get((theme, None), {}))
        if tone is not None and theme is not None:
            tokens.update(overrides.get((theme, tone), {}))
        resolved[(theme, tone)] = tokens
    return resolved


def relative_luminance(color):
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return sum(
        weight * channel
        for weight, channel in zip((0.2126, 0.7152, 0.0722), linear)
    )


def contrast_ratio(foreground, background):
    dark, light = sorted((
        relative_luminance(foreground), relative_luminance(background),
    ))
    return (light + 0.05) / (dark + 0.05)


THEMES = theme_tokens([
    (STATIC / filename).read_text(encoding="utf-8")
    for filename in ("style.css", "themes.css")
])


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,minimum", MINIMUMS.items())
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_theme_text_tokens_meet_contrast(context, tokens, foreground, minimum, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= minimum, (
        f"主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 {minimum}:1"
    )


@pytest.mark.parametrize("tokens", list(THEMES.values()))
@pytest.mark.parametrize("selector,foreground,background", [
    (".redeem-plaintext", "--ink", "--paper-2"),
    (".redeem-code-row", "--ink", "--surface"),
    (".manual-qr-dialog", "--ink", "--surface"),
    ('.redeem-filters button[aria-pressed="true"]', "--ink", "--paper-2"),
])
def test_manual_payment_text_pairs_meet_contrast(tokens, selector, foreground, background):
    source = (STATIC / "redeem.css").read_text(encoding="utf-8")
    rule = next(
        body for blocks, body in css_declarations(source)
        if blocks and selector in tuple(css_selectors(blocks[-1]))
        and body.startswith("color:")
    )
    assert f"var({foreground})" in rule
    backgrounds = [
        body for blocks, body in css_declarations(source)
        if blocks and selector in tuple(css_selectors(blocks[-1]))
        and body.startswith("background:")
    ]
    assert any(f"var({background})" in body for body in backgrounds)
    assert contrast_ratio(tokens[foreground], tokens[background]) >= 4.5


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,background", THREAD_TEXT_PAIRS)
def test_thread_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"论坛主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )


def mix_srgb(foreground, background, weight):
    """Match the terminal's opaque color-mix(in srgb, ...) label colors."""
    return "#" + "".join(
        f"{round(int(foreground[index:index + 2], 16) * weight + int(background[index:index + 2], 16) * (1 - weight)):02x}"
        for index in (1, 3, 5)
    )


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("selector,weight", (
    ("#forum-page .thread-code figcaption", .72),
    ("#forum-page .thread-code .l::before", .62),
))
def test_thread_terminal_labels_and_line_numbers_meet_contrast(context, tokens, selector, weight):
    stylesheet = (STATIC / "forum.css").read_text(encoding="utf-8")
    expressions = [
        declaration.partition(":")[2].strip()
        for blocks, declaration in css_declarations(stylesheet)
        if blocks == (selector,) and declaration.partition(":")[0].strip() == "color"
    ]
    assert expressions == [
        f"color-mix(in srgb, var(--code-ink) {round(weight * 100)}%, var(--code-surface))"
    ], "Code-label contrast checks must match the rendered CSS expression"
    label = mix_srgb(tokens["--code-ink"], tokens["--code-surface"], weight)
    ratio = contrast_ratio(label, tokens["--code-surface"])
    assert ratio >= 4.5, (
        f"论坛主题/场景 {context}: {selector}={label} 对代码底色为 "
        f"{ratio:.6f}:1，要求至少 4.5:1"
    )


def test_thread_copy_text_inherits_checked_terminal_label_color():
    declarations = list(css_declarations((STATIC / "forum.css").read_text(encoding="utf-8")))
    assert any(
        blocks == ("#forum-page .thread-copy",) and declaration == "color: inherit"
        for blocks, declaration in declarations
    )
    assert any(
        blocks == ("#forum-page .thread-copy:hover:not(:disabled)",)
        and declaration == "color: var(--code-ink)"
        for blocks, declaration in declarations
    )


# 总览「趋势」区：指标块、图上的文字、时间范围按钮、悬停提示。
# (选择器, 属性, 前景令牌, 它可能落在的底色令牌)。文字落在卡片 --surface 上；选中的指标块与
# 时间范围外框是 --paper，悬停是 --paper-2；选中的时间范围按钮与悬停提示是反色（--ink 底）。
TREND_TEXT_RULES = (
    (".ov-trend-label", "color", "--ink-2", ("--surface", "--paper")),
    (".ov-trend-value small", "color", "--ink-2", ("--surface", "--paper")),
    (".ov-trend-sub", "color", "--ink-2", ("--surface", "--paper")),
    (".ov-trend-explain", "color", "--ink-2", ("--surface", "--paper")),
    (".ov-trend-delta.is-up", "color", "--success-ink", ("--surface", "--paper")),
    (".ov-trend-delta.is-down", "color", "--danger", ("--surface", "--paper")),
    (".ov-trend-delta.is-flat", "color", "--ink-2", ("--surface", "--paper")),
    (".ov-trend-range-button", "color", "--ink-2", ("--paper", "--paper-2")),
    (".ov-trend-range-button:hover:not(:disabled)", "color", "--ink", ("--paper-2",)),
    ('.ov-trend-range-button[aria-pressed="true"]', "color", "--paper", ("--ink",)),
    (".ov-chart-tick", "fill", "--ink-2", ("--surface",)),
    (".ov-chart-axis", "fill", "--ink-2", ("--surface",)),
    (".ov-chart-axis.is-today", "fill", "--ink", ("--surface",)),
    (".ov-chart-value", "fill", "--ink", ("--surface",)),
    (".ov-chart-note", "fill", "--ink-2", ("--surface",)),
    (".ov-hover-text", "fill", "--paper", ("--ink",)),
    (".ov-trend-note", "color", "--ink-2", ("--surface",)),
    (".ov-trend-empty", "color", "--ink-2", ("--surface",)),
    (".ov-trend-how summary", "color", "--azurite", ("--surface",)),
    (".ov-trend-how p", "color", "--ink-2", ("--surface",)),
    (".ov-trend-table th, .ov-trend-table td", "color", "--ink", ("--surface",)),
    (".ov-trend-table thead th", "color", "--ink-2", ("--surface",)),
)
TREND_TEXT_PAIRS = tuple(
    sorted({(foreground, background) for _, _, foreground, backgrounds in TREND_TEXT_RULES for background in backgrounds})
)


@pytest.mark.parametrize("selector,prop,foreground,backgrounds", TREND_TEXT_RULES)
def test_trend_css_really_uses_the_checked_text_colors(selector, prop, foreground, backgrounds):
    source = (STATIC / "overview.css").read_text(encoding="utf-8")
    wanted = {part.strip() for part in selector.split(", ")}
    values = [
        body.partition(":")[2].strip()
        for blocks, body in css_declarations(source)
        if blocks and wanted <= set(css_selectors(blocks[-1])) and body.partition(":")[0].strip() == prop
    ]
    assert values == [f"var({foreground})"], f"{selector} 的 {prop} 必须是 var({foreground})，才能沿用下面的对比度检查：{values}"


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,background", TREND_TEXT_PAIRS)
def test_trend_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"趋势区主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )
