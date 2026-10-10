"""新手指南页（static/help.html）的静态资产校验。

约束：
- 无行内 style 属性、无 <style> 块、无内联事件、无外部 CDN/字体/图片；
- 目录锚点与章节 id 一一对应；
- 页面引用的本地文件都存在；
- AI 额度数字以后端口径（main.py / seed_plans.py）为准：免费 20、体验 4、
  标准版 50、进阶版 120；当 static/ai-billing.html 上线后，额外与该页严格比对，
  该页尚未提交时跳过比对并明确提示（不伪装成已验证）。
"""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
HELP_HTML = STATIC / "help.html"
HELP_CSS = STATIC / "help.css"
HELP_JS = STATIC / "help.js"
AI_BILLING = STATIC / "ai-billing.html"

EXPECTED_QUOTA = {
    "free": ("免费", 20),
    "trial": ("体验", 4),
    "standard": ("标准版", 50),
    "pro": ("进阶版", 120),
}


class _HelpMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.violations = []
        self.anchor_targets = []
        self.section_ids = set()
        self.referenced_files = []
        self.external_urls = []
        self.quota_rows = {}
        self._toc_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if "style" in attrs:
            self.violations.append(f"<{tag}> 上存在行内 style 属性")
        if tag == "style":
            self.violations.append("不允许出现 <style> 块，样式必须放在 help.css")
        if any(name.startswith("on") for name in attrs):
            self.violations.append(f"<{tag}> 上存在内联事件属性（on*）")

        if tag == "nav" and attrs.get("aria-label") == "本页目录":
            self._toc_depth = 1
        elif self._toc_depth and tag == "nav":
            self._toc_depth += 1
        if tag == "a" and attrs.get("href", "").startswith("#") and self._toc_depth:
            self.anchor_targets.append(attrs["href"][1:])
        if tag == "section" and attrs.get("id"):
            self.section_ids.add(attrs["id"])

        for attr in ("href", "src"):
            url = attrs.get(attr)
            if not url:
                continue
            if url.startswith(("http://", "https://", "//")):
                self.external_urls.append(f"<{tag} {attr}={url}>")
                continue
            if url.startswith("#") or url.startswith(("mailto:", "tel:", "data:")):
                continue
            self.referenced_files.append(url.split("#", 1)[0])

        if tag == "tr" and attrs.get("data-plan"):
            self.quota_rows[attrs["data-plan"]] = attrs.get("data-limit")

    def handle_endtag(self, tag):
        if tag == "nav" and self._toc_depth:
            self._toc_depth -= 1


@pytest.fixture(scope="module")
def markup():
    assert HELP_HTML.exists(), "static/help.html 不存在"
    parser = _HelpMarkup()
    parser.feed(HELP_HTML.read_text(encoding="utf-8"))
    return parser


def test_help_page_has_basic_document_chrome():
    html = HELP_HTML.read_text(encoding="utf-8")
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert 'lang="zh-CN"' in html
    assert 'name="viewport"' in html
    assert 'charset="utf-8"' in html
    assert "<title>" in html


def test_no_inline_style_or_handlers(markup):
    assert markup.violations == []


def test_no_external_resources(markup):
    assert markup.external_urls == [], "帮助页不得引用任何外部 CDN、字体或图片"


def test_toc_anchors_match_section_ids(markup):
    assert markup.anchor_targets, "目录里至少要有一个锚点链接"
    missing = [target for target in markup.anchor_targets if target not in markup.section_ids]
    assert missing == [], f"目录锚点找不到对应章节 id：{missing}"
    # 每个章节都应能从目录到达。
    linked = set(markup.anchor_targets)
    unlinked = markup.section_ids - linked
    assert unlinked == set(), f"以下章节没有目录入口：{sorted(unlinked)}"


def test_referenced_local_files_exist(markup):
    missing = []
    billing_link_seen = False
    for url in sorted(set(markup.referenced_files)):
        url = url.split("?", 1)[0].split("#", 1)[0]  # 资源版本号 ?v=N 不属于文件名
        if url.startswith("/static/"):
            path = ROOT / url.lstrip("/")
        elif url.startswith("/"):
            # 应用路由（如 /、/terms、/privacy），不是仓库里的静态文件。
            continue
        else:
            # 相对路径以 static/help.html 所在目录为基准。
            path = STATIC / url
        if path.name == "ai-billing.html":
            billing_link_seen = True
            if not path.exists():
                # 该页由用户确认稍后提交；缺失时跳过，不把链接改成假页面。
                pytest.skip("static/ai-billing.html 尚未提交，其链接存在性与额度比对暂时跳过")
        if not path.exists():
            missing.append(f"{url} -> {path.relative_to(ROOT)}")
    assert billing_link_seen, "帮助页必须链接到 ai-billing.html"
    assert missing == [], f"页面引用了不存在的本地文件：{missing}"


def test_help_js_has_no_html_injection_or_network_calls():
    js = HELP_JS.read_text(encoding="utf-8")
    forbidden = ["innerHTML", "outerHTML", "document.write", "insertAdjacentHTML", "fetch(", "XMLHttpRequest"]
    hits = [token for token in forbidden if token in js]
    assert hits == [], f"help.js 不允许出现 {hits}"


def test_help_css_uses_theme_tokens_only():
    css = HELP_CSS.read_text(encoding="utf-8")
    # 允许 url() 里不出现任何外部/本地图片；颜色一律走主题变量，不写死十六进制色值。
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "help.css 不得写死十六进制颜色，应使用主题令牌"
    assert "url(" not in css, "帮助页不引用任何图片/字体文件"
    assert "@media (prefers-color-scheme: dark)" in css, "需要跟随系统暗色模式"


def test_quota_table_matches_backend_numbers(markup):
    for plan, (_label, limit) in EXPECTED_QUOTA.items():
        assert markup.quota_rows.get(plan) == str(limit), f"{plan} 额度应为 {limit}"
    assert set(markup.quota_rows) == set(EXPECTED_QUOTA)


def test_quota_numbers_consistent_with_ai_billing_when_present():
    if not AI_BILLING.exists():
        pytest.skip("static/ai-billing.html 尚未提交；上线后本测试会自动开始严格比对额度数字")
    raw = AI_BILLING.read_text(encoding="utf-8")
    # 计费页用表格列出每档次数：先去掉标签，再按"档位名 + 数字"匹配。
    text = re.sub(r"<[^>]+>", " ", raw)
    patterns = {
        "free": r"免费账号\s+(\d+)",
        "trial": r"体验账号\s+(\d+)",
        "standard": r"标准版\s+(\d+)",
        "pro": r"进阶版\s+(\d+)",
    }
    for plan, (_label, expected) in EXPECTED_QUOTA.items():
        match = re.search(patterns[plan], text)
        assert match, f"ai-billing.html 中没有找到「{_label}」的额度数字，请调整测试正则"
        assert int(match.group(1)) == expected, (
            f"ai-billing.html 中{_label}额度为 {match.group(1)}，与帮助页/后端口径 {expected} 不一致"
        )


def test_help_page_covers_required_topics():
    html = HELP_HTML.read_text(encoding="utf-8")
    for text in (
        "三分钟上手",
        "速记", "一句话", "完整记录",
        "间隔达到 21 天", "取平均",
        "20 次 / 天", "4 次 / 天", "50 次 / 天", "120 次 / 天",
        "自动退还",
        "导入预览",
        "/static/extension.html",
        "参与公开榜单", "榜单显示名",
        "忘记密码", "改邮箱", "收不到邮件", "添加到主屏幕",
        "导出我的数据", "注销账号",
        "Ctrl", "F5",
        "在哪个页面",
    ):
        assert text in html, f"帮助页缺少必要内容：{text}"


def test_search_box_is_present_and_wired():
    html = HELP_HTML.read_text(encoding="utf-8")
    assert 'id="help-search"' in html
    js = HELP_JS.read_text(encoding="utf-8")
    assert "help-search" in js
    assert "addEventListener" in js
