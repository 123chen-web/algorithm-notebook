"""error-report.js 必须是页面里第一个脚本（否则它之前的脚本报错抓不到），且带版本号。"""
import re

from static_helpers import INDEX_HTML, versioned_src


def test_error_report_is_the_first_script_and_versioned():
    html = INDEX_HTML.read_text(encoding="utf-8")
    srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', html)
    assert srcs[0] == versioned_src("error-report.js")
    assert re.fullmatch(r"/static/error-report\.js\?v=\d+", srcs[0])
    first_tag = re.search(r"<script[^>]*>", html).group(0)
    assert "defer" not in first_tag and "async" not in first_tag
    assert "<script>" not in html  # 严格 CSP：没有行内脚本
