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
    ("--ink", "--soft"),
    ("--danger", "--danger-soft"),
    ("--azurite", "--surface"),
    ("--muted", "--surface"),
    ("--ink-2", "--surface"),
    # 讨论区主页（static/forum.css 的 .board-*）：「刚刚」胶囊是 --on-accent 字压在 --azurite 底上；
    # 选中标签上的数字徽标是 --ink 字压在 --soft 底上。其余配对（--ink / --ink-2 / --muted / --azurite /
    # --success-* / --danger 压在 --paper / --paper-2 / --surface 上）已被上面的矩阵覆盖。
    ("--on-accent", "--azurite"),
)
# 管理后台“运营概览”（static/admin-metrics.css）用到的文字 / 底色配对。
ADMIN_METRICS_TEXT_PAIRS = (
    ("--ink", "--surface"),
    ("--ink-2", "--surface"),
    ("--muted", "--surface"),
    ("--muted", "--paper"),
    ("--success-ink", "--surface"),
    ("--danger", "--surface"),
    ("--danger", "--paper"),
    ("--azurite", "--surface"),
    ("--ink", "--tile-azurite"),
    ("--ink", "--danger-soft"),
    ("--ink-2", "--danger-soft"),
    ("--danger", "--danger-soft"),
    ("--danger-hover", "--danger-soft"),
)
# AN：掌握度四档等级胶囊、错因专题徽标用到的文字 / 底色配对（static/mastery.css、static/clusters.css）。
# 下面的 test_an_tier_and_trend_pairs_come_from_the_stylesheets 会核对样式表里真的是这些配对。
AN_TEXT_PAIRS = (
    ("--danger", "--danger-soft"),          # 陌生 / 仍在反复
    ("--reward-ink", "--reward-soft"),      # 熟悉
    ("--group-ink", "--group-soft"),        # 熟练
    ("--success-ink", "--success-soft"),    # 精通 / 已改善
    ("--ink-2", "--paper"),                 # 没有记录 / 样本不足
    ("--ink", "--surface"),                 # 优先标记、提示气泡
    ("--ink", "--paper-2"),                 # 选中行
    ("--azurite", "--paper-2"),             # 去看看这一块的记录（链接）
    ("--azurite", "--surface"),
    ("--azurite", "--paper"),
)
# 收录（粘贴链接 / 书签小工具）：书签按钮（深色代码块配色）、顶部“已从书签带入”提示条。
CAPTURE_TEXT_PAIRS = (
    ("--code-ink", "--code-surface"),
    ("--success-ink", "--success-soft"),
    ("--ink", "--surface"),
    ("--muted", "--surface"),
)
# 首次清单（static/onboarding.css）用到的文字 / 底色配对；两个主题都要 ≥ 4.5:1。
ONBOARDING_TEXT_PAIRS = (
    ("--ink", "--surface"),
    ("--ink", "--paper-2"),
    ("--ink-2", "--surface"),
    ("--ink-2", "--paper-2"),
    ("--azurite", "--surface"),
    ("--azurite", "--paper-2"),
    ("--success-ink", "--success-soft"),
    ("--danger", "--surface"),
    ("--error-ink", "--error-surface"),
)
# 榜单页新区块（static/rank.css）：今日一条、昨日之星、本周热门题目、账号菜单开关、总览小卡片、管理后台卡片。
# 红笔圈只是边框（--accent 不当文字色用）；文字都落在这些底色上，两个主题都要 ≥ 4.5:1。
RANK_TEXT_PAIRS = (
    ("--ink", "--surface"),
    ("--ink-2", "--surface"),
    ("--muted", "--surface"),
    ("--ink", "--soft"),
    ("--ink-2", "--soft"),
    ("--ink", "--paper-2"),
    ("--ink", "--tile-azurite"),
    ("--azurite", "--surface"),
    ("--danger", "--surface"),
    # 总览“昨日之星”小卡片的实心按钮（默认 azurite，悬停 accent）。
    ("--on-accent", "--azurite"),
    ("--on-accent", "--accent"),
)
# RVF：详情回忆输入、评分间隔、更多菜单、撤销提示及上限工具栏。
REVIEW_TEXT_PAIRS = (
    ("--ink", "--surface"),
    ("--ink", "--paper"),
    ("--ink-2", "--surface"),
    ("--ink-2", "--paper"),
    ("--azurite", "--surface"),
    ("--azurite", "--paper"),
)
COLOR_TOKENS = set(BACKGROUNDS) | MINIMUMS.keys() | {
    token for pair in THREAD_TEXT_PAIRS + ADMIN_METRICS_TEXT_PAIRS + AN_TEXT_PAIRS + CAPTURE_TEXT_PAIRS + ONBOARDING_TEXT_PAIRS + RANK_TEXT_PAIRS + REVIEW_TEXT_PAIRS for token in pair
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
            if re.fullmatch(r"#[0-9a-fA-F]{3}", value):  # #fff → #ffffff
                value = "#" + "".join(character * 2 for character in value[1:])
            # 运行时按场景覆盖的令牌（如 --scene-accent）静态分析取不到，按声明里的兜底色算。
            scene_fallback = re.fullmatch(
                r"var\(\s*--[\w-]+\s*,\s*(#[0-9a-fA-F]{6})\s*\)", value
            )
            if scene_fallback:
                value = scene_fallback.group(1)
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
        pytest.param(context, tokens, id="/".join(part for part in context if part) or "root")
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,background", REVIEW_TEXT_PAIRS)
def test_review_feel_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"复习主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
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


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,background", ADMIN_METRICS_TEXT_PAIRS)
def test_admin_metrics_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"运营概览主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
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
@pytest.mark.parametrize("foreground,background", CAPTURE_TEXT_PAIRS)
def test_capture_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"收录主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )


def test_admin_metrics_css_text_colors_match_the_checked_pairs():
    """运营概览里每个带文字色的规则，实际用的令牌必须与上面检查过的配对一致。"""
    expected = {
        ".am-metric": ("--ink", "--surface"),
        ".am-metric-label": ("--ink-2", "--surface"),
        ".am-metric-sub": ("--ink-2", "--surface"),
        ".am-metric-value": ("--ink", "--surface"),
        '.am-delta[data-trend="up"]': ("--success-ink", "--surface"),
        '.am-delta[data-trend="down"]': ("--danger", "--surface"),
        '.am-delta[data-trend="flat"]': ("--muted", "--surface"),
        '.am-delta[data-trend="new"]': ("--azurite", "--surface"),
        '.am-range button[aria-pressed="true"]': ("--ink", "--tile-azurite"),
        '.am-metric[data-alert="true"] .am-metric-value': ("--danger", "--danger-soft"),
        ".am-link": ("--danger-hover", "--danger-soft"),
        ".am-status.error": ("--danger", "--paper"),
    }
    source = (STATIC / "admin-metrics.css").read_text(encoding="utf-8")
    colors = {}
    for blocks, declaration in css_declarations(source):
        if blocks and not blocks[-1].startswith("@") and declaration.startswith("color:"):
            colors[blocks[-1]] = declaration.partition(":")[2].strip()
    assert set(colors) >= set(expected), set(expected) - set(colors)
    allowed = set(ADMIN_METRICS_TEXT_PAIRS) | {(fg, bg) for fg in MINIMUMS for bg in BACKGROUNDS}
    for selector, pair in expected.items():
        assert colors[selector] == f"var({pair[0]})", selector
        assert pair in allowed, f"{selector}: {pair} 没有对比度检查"
    for selector, color in colors.items():
        assert re.fullmatch(r"var\(--[\w-]+\)", color), f"{selector}: 文字色必须是主题令牌"


def test_capture_stylesheet_uses_the_checked_pairs():
    source = (STATIC / "capture.css").read_text(encoding="utf-8")
    assert re.search(r"\.capture-bookmarklet \{[^}]*background: var\(--code-surface\); color: var\(--code-ink\)", source)
    assert re.search(r"\.capture-banner \{[^}]*background: var\(--success-soft\); color: var\(--success-ink\)", source)


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
@pytest.mark.parametrize("foreground,background", AN_TEXT_PAIRS)
def test_an_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"AN 主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
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


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,background", ONBOARDING_TEXT_PAIRS)
def test_onboarding_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"首次清单 主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )


@pytest.mark.parametrize("filename,selector", [
    ("mastery.css", ".mastery-tier.is-new"),
    ("mastery.css", ".mastery-tier.is-familiar"),
    ("mastery.css", ".mastery-tier.is-proficient"),
    ("mastery.css", ".mastery-tier.is-mastered"),
    ("mastery.css", ".mastery-tier.is-none"),
    ("clusters.css", ".clusters-trend.is-improved"),
    ("clusters.css", ".clusters-trend.is-repeating"),
    ("clusters.css", ".clusters-trend.is-sparse"),
])
def test_an_tier_and_trend_pairs_come_from_the_stylesheets(filename, selector):
    """样式表里每个等级 / 徽标的文字色与底色必须是上面逐主题检查过的配对。"""
    source = (STATIC / filename).read_text(encoding="utf-8")
    declarations = [
        body for blocks, body in css_declarations(source)
        if blocks == (selector,)
    ]
    color = next(body for body in declarations if body.startswith("color:"))
    background = next(body for body in declarations if body.startswith("background:"))
    pair = (
        re.fullmatch(r"color: var\((--[\w-]+)\)", color).group(1),
        re.fullmatch(r"background: var\((--[\w-]+)\)", background).group(1),
    )
    assert pair in AN_TEXT_PAIRS, f"{filename} {selector} 用了没有检查过的配对 {pair}"


def test_onboarding_stylesheet_only_uses_checked_text_and_background_tokens():
    """onboarding.css 里出现的每个 color / background 令牌都必须落在上面检查过的配对里。"""
    source = (STATIC / "onboarding.css").read_text(encoding="utf-8")
    foregrounds = {pair[0] for pair in ONBOARDING_TEXT_PAIRS} | {"--muted"}
    backgrounds = {pair[1] for pair in ONBOARDING_TEXT_PAIRS} | {"--paper"}
    used_foregrounds, used_backgrounds = set(), set()
    for blocks, declaration in css_declarations(source):
        name, _, value = declaration.partition(":")
        name = name.strip()
        tokens = set(re.findall(r"var\((--[\w-]+)\)", value))
        if name == "color":
            used_foregrounds |= tokens
        elif name == "background" or name == "background-color":
            used_backgrounds |= tokens
    assert used_foregrounds, "没有解析到任何文字颜色，检查方式失效了"
    assert used_foregrounds <= foregrounds, used_foregrounds - foregrounds
    assert used_backgrounds <= backgrounds, used_backgrounds - backgrounds


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


@pytest.mark.parametrize(
    "context,tokens",
    [
        pytest.param(
            context, tokens, id="/".join(part for part in context if part) or "root",
        )
        for context, tokens in sorted(THEMES.items(), key=lambda item: str(item[0]))
    ],
)
@pytest.mark.parametrize("foreground,background", RANK_TEXT_PAIRS)
def test_rank_text_pairs_meet_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, (
        f"榜单页 主题/场景 {context}: {foreground}={tokens[foreground]} 对 "
        f"{background}={tokens[background]} 为 {ratio:.6f}:1，要求至少 4.5:1"
    )


def test_rank_stylesheet_only_uses_checked_text_and_background_tokens():
    """rank.css 里每个 color / background 令牌都必须落在上面逐主题检查过的配对里。"""
    source = (STATIC / "rank.css").read_text(encoding="utf-8")
    checked = set(RANK_TEXT_PAIRS)
    foregrounds = {fg for fg, _ in checked}
    backgrounds = {bg for _, bg in checked}
    used_foregrounds, used_backgrounds = set(), set()
    for blocks, declaration in css_declarations(source):
        name, _, value = declaration.partition(":")
        name = name.strip()
        tokens = set(re.findall(r"var\((--[\w-]+)\)", value))
        if name == "color":
            used_foregrounds |= tokens
        elif name in ("background", "background-color"):
            used_backgrounds |= tokens
    assert used_foregrounds, "没有解析到任何文字颜色，检查方式失效了"
    assert used_foregrounds <= foregrounds, used_foregrounds - foregrounds
    # --azurite 只用来画 7px 的装饰圆点（.rank-tag::before），上面没有文字。
    assert used_backgrounds <= backgrounds | {"--azurite"}, used_backgrounds - backgrounds


def test_rank_css_text_color_and_background_pairs_are_the_checked_ones():
    """逐条规则核对：文字色 + 它落在的底色必须是 RANK_TEXT_PAIRS 里的一对。"""
    expected = {
        ".rank-notice": ("--ink", "--soft"),
        ".rank-tag": ("--ink-2", "--surface"),
        # .rank-notice .rank-tag 继承 .rank-tag 的 --ink-2，落在 --soft 上：(--ink-2, --soft) 已在 RANK_TEXT_PAIRS。
        ".rank-me": ("--ink", "--soft"),
        ".rank-row.is-me": (None, "--soft"),
        ".rank-row.is-first .rank-no": (None, "--soft"),
        ".rank-name": ("--ink", "--surface"),
        ".rank-praise": ("--ink-2", "--surface"),
        ".rank-count": ("--ink", "--surface"),
        ".rank-streak": ("--ink-2", "--surface"),
        ".rank-source": ("--ink-2", "--surface"),
        ".rank-hot-users": ("--ink", "--surface"),
        ".rank-admin-badge": ("--ink", "--surface"),
        ".rank-admin-item-link": ("--azurite", "--surface"),
        ".rank-admin-count[data-over=\"true\"]": ("--danger", "--surface"),
        ".account-rank-pref-note": ("--muted", "--surface"),
        # 总览页“昨日之星”小卡片（.rank-mini-card）。
        ".rank-mini-card": ("--ink", "--surface"),
        ".rank-mini-title": ("--ink", "--surface"),
        ".rank-mini-text": ("--ink-2", "--surface"),
        ".rank-mini-link": ("--on-accent", "--azurite"),
    }
    source = (STATIC / "rank.css").read_text(encoding="utf-8")
    colors, backgrounds = {}, {}
    for blocks, declaration in css_declarations(source):
        if not blocks or blocks[-1].startswith("@"):
            continue
        name, _, value = declaration.partition(":")
        if name.strip() == "color":
            colors[blocks[-1]] = value.strip()
        elif name.strip() == "background":
            backgrounds[blocks[-1]] = value.strip()
    allowed = set(RANK_TEXT_PAIRS) | {(fg, bg) for fg in MINIMUMS for bg in BACKGROUNDS}
    for selector, (foreground, background) in expected.items():
        if foreground:
            assert colors.get(selector) == f"var({foreground})", selector
        # 规则自己写了底色就必须等于这里登记的；没写底色的文字落在外层容器（--surface / --soft）上。
        if selector in backgrounds:
            assert backgrounds[selector] == f"var({background})", selector
        if foreground and background:
            assert (foreground, background) in allowed, f"{selector}: {(foreground, background)} 没有对比度检查"
    for selector, value in colors.items():
        assert re.fullmatch(r"var\(--[\w-]+\)", value), f"{selector}: 文字色必须是主题令牌"
