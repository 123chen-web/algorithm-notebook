from html.parser import HTMLParser
from pathlib import Path
import re

import pytest

import legal


ROOT = Path(__file__).resolve().parents[1]


class _LegalMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inline_assets = []
        self.inside_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "style" in attrs or any(name.startswith("on") for name in attrs):
            self.inline_assets.append(tag)
        if tag == "style" or (tag == "script" and not attrs.get("src")):
            self.inline_assets.append(tag)
        self.inside_script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside_script = False

    def handle_data(self, data):
        if self.inside_script and data.strip():
            self.inline_assets.append("script body")


@pytest.mark.parametrize("kind,title", [("terms", "服务条款"), ("privacy", "隐私政策")])
def test_legal_pages_have_complete_safe_markup_and_current_styles(kind, title, monkeypatch):
    monkeypatch.delenv("LEGAL_OPERATOR_NAME", raising=False)
    monkeypatch.delenv("LEGAL_CONTACT_EMAIL", raising=False)
    page = legal.render_legal_page(kind)
    assert page.startswith("<!doctype html>")
    assert 'lang="zh-CN"' in page
    assert 'name="viewport"' in page
    assert f"<title>{title} · {legal.PRODUCT_NAME}</title>" in page
    assert '<a href="/">返回</a>' in page
    assert legal.TERMS_VERSION in page
    assert "本站管理员" in page
    assert "请通过站内渠道联系管理员" in page
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    version = re.search(r"/static/style\.css\?v=(\d+)", index).group(1)
    assert f'/static/style.css?v={version}' in page
    assert '/static/legal.css?v=1' in page
    markup = _LegalMarkup()
    markup.feed(page)
    assert markup.inline_assets == []


@pytest.mark.parametrize("kind", ["terms", "privacy"])
def test_legal_operator_and_contact_are_escaped(kind, monkeypatch):
    monkeypatch.setenv("LEGAL_OPERATOR_NAME", '<script>alert("operator")</script>')
    monkeypatch.setenv("LEGAL_CONTACT_EMAIL", '<img src=x onerror="alert(1)">')
    page = legal.render_legal_page(kind)
    assert '<script>alert(' not in page
    assert '<img src=x' not in page
    assert '&lt;script&gt;alert(&quot;operator&quot;)&lt;/script&gt;' in page
    assert '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;' in page
    markup = _LegalMarkup()
    markup.feed(page)
    assert markup.inline_assets == []


def test_privacy_contains_data_transfer_retention_and_rights():
    page = legal.render_legal_page("privacy")
    for text in (
        "收集哪些数据", "发给第三方的数据", "密码的哈希值", "不保存明文密码",
        "管理员未配置 AI 服务时", "支付宝", "微信支付", "SMTP", "登录会话 Cookie",
        "不记录题目、代码、对话或照片内容", "14 份", "已注销用户", "导出我的数据",
        "未成年人", "政策变更", "联系方式",
    ):
        assert text in page


def test_terms_contains_account_content_ai_and_payment_rules():
    page = legal.render_legal_page("terms")
    for text in (
        "邀请制内测", "一人一号", "体验账号", "到期会被清理", "使用规范",
        "你的内容", "版权", "必要的存储、展示和处理许可", "不保证正确",
        "付费与退款", "服务变更与终止", "责任限制", "运营者所在地法律", "联系方式",
    ):
        assert text in page


def test_legal_css_uses_tokens_with_mobile_spacing_and_dark_scheme():
    css = (ROOT / "static/legal.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "!important" not in css
    assert "max-width: 46rem" in css
    assert "padding-right: 16px" in css
    assert "padding-left: 16px" in css
    assert "prefers-color-scheme: dark" in css
    assert "background: var(--code-surface)" in css
    assert "color: var(--code-ink)" in css


def test_unknown_legal_page_is_rejected():
    with pytest.raises(ValueError, match="未知的法律页面"):
        legal.render_legal_page("<script>")
