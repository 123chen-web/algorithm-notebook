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


# 首访开场短片（static/intro-film.css）新增的文字 / 底色配对，两个主题、所有场景色调都算。
INTRO_FILM_TEXT_PAIRS = (
    ("--paper", "--ink"),       # 第 1 幕黑场上的字
    ("--ink", "--paper"),       # 大字幕首行、品牌名
    ("--ink-2", "--paper"),     # 字幕次行、副标题、“跳过”、文字版、时间轴刻度
    ("--azurite", "--paper"),   # 等宽小标签（记忆 / 时间 / 间隔复习）
    ("--ink", "--paper-2"),     # 草稿纸上的代码、错题纸条
    ("--azurite", "--paper-2"), # 草稿纸标签
    ("--ink-2", "--paper-2"),   # 欢迎页底部“重看开场”
    ("--ink", "--surface"),     # “再看一遍”按钮
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
@pytest.mark.parametrize("foreground,background", INTRO_FILM_TEXT_PAIRS)
def test_intro_film_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"开场短片 主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )


def intro_film_accents():
    """--accent 不是固定色：qixi 跟随当前场景（scenes.js），所以逐个场景展开。"""
    style = (STATIC / "style.css").read_text(encoding="utf-8")
    scenes = (STATIC / "scenes.js").read_text(encoding="utf-8")

    def block(selector):
        found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", style)
        assert found, selector
        return found.group(1)

    def full_hex(value):
        return "#" + "".join(ch * 2 for ch in value[1:]) if len(value) == 4 else value

    on_accent = full_hex(re.search(r"--on-accent:\s*(#[0-9a-fA-F]{3,6})\b", block(":root")).group(1))
    ink = re.search(r"--accent:\s*(#[0-9a-fA-F]{6})\b", block('html[data-theme="ink"]')).group(1)
    qixi_rule = re.search(r"--accent:\s*([^;]+);", block('html[data-theme="qixi"]')).group(1)
    fallback = re.fullmatch(r"var\(--scene-accent,\s*(#[0-9a-fA-F]{6})\)", qixi_rule.strip())
    assert fallback, f"请扩展解析以支持 qixi 的 --accent：{qixi_rule}"
    scene_accents = re.findall(r'accent:\s*"(#[0-9a-fA-F]{6})"', scenes)
    assert scene_accents, "scenes.js 里找不到场景强调色"
    variants = [("ink", None, ink, on_accent)]
    variants += [("qixi", None, fallback.group(1), on_accent)]
    variants += [("qixi", accent, accent, on_accent) for accent in scene_accents]
    return variants


@pytest.mark.parametrize(
    "theme,scene,accent,on_accent",
    [pytest.param(*variant, id=f"{variant[0]}-{variant[1] or 'default'}") for variant in intro_film_accents()],
)
@pytest.mark.parametrize("pair", [
    ("--accent", "--paper"),      # 印章字
    ("--accent", "--paper-2"),    # 草稿纸上的朱批
    ("--on-accent", "--accent"),  # “继续探索”主按钮
])
def test_intro_film_accent_pairs_meet_contrast(theme, scene, accent, on_accent, pair):
    tokens = dict(THEMES[(theme, None)], **{"--accent": accent, "--on-accent": on_accent})
    foreground, background = pair
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"开场短片 {theme}/{scene or '默认'}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )
