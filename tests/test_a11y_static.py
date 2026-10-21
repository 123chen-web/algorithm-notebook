"""静态页面无障碍回归守卫：新增违规会让这里失败。

审计逻辑在 tests/a11y_audit.py（标准库 html.parser，只读 HTML）。
规则：表单控件有名字、按钮/链接有名字、img 有 alt、id 不重复、
aria-labelledby/for/aria-controls 指向存在的 id、无正数 tabindex、<html lang>、
有 skip link、同类地标多于一个时各自有名字。
"""
import pytest

import a11y_audit
from static_helpers import STATIC

# 由 JS 在运行时创建标题/元素的 id：静态 HTML 里引用它们是有意的。
# 下面的测试会校验每个 id 确实出现在某个 static/*.js 里，防止白名单悄悄变成死引用。
JS_CREATED_REFS = {
    "ov-yesterday-card-title": "rank.js 建昨日卡标题",
    "forum-detail-title": "forum-post.js 建帖子标题",
    "forum-accepted-title": "forum-post.js 建已采纳回答标题",
    "forum-summary-title": "forum-post.js 建小结标题",
    "account-dialog-title": "account.js 建账号对话框标题",
    "notes-history-title": "notes-history.js 建历史对话框标题",
    "profile-title": "profile.js 建资料对话框标题",
}

STRICT_RULES = [
    "html-lang", "skip-link", "unlabelled-control", "unnamed-button", "unnamed-link",
    "img-alt", "duplicate-id", "positive-tabindex", "landmark-label",
]


@pytest.mark.parametrize("page", a11y_audit.PAGES)
@pytest.mark.parametrize("rule", STRICT_RULES)
def test_no_static_a11y_violations(page, rule):
    found = a11y_audit.audit(page)[rule]
    assert not found, f"{page} 违反 {rule}：\n" + "\n".join(found)


@pytest.mark.parametrize("page", a11y_audit.PAGES)
def test_dangling_aria_refs_are_only_known_js_created_ids(page):
    unknown = []
    for item in a11y_audit.audit(page)["dangling-ref"]:
        target = item.rsplit("→#", 1)[1]
        if target not in JS_CREATED_REFS:
            unknown.append(item)
    assert not unknown, f"{page} 引用了不存在的 id：\n" + "\n".join(unknown)


@pytest.mark.parametrize("ref_id", sorted(JS_CREATED_REFS))
def test_js_created_ref_ids_really_exist_in_scripts(ref_id):
    # TODO：这些标题靠 JS 创建，静态审计看不到；若 JS 改名，这里会提醒同步 HTML。
    sources = [p.read_text(encoding="utf-8") for p in STATIC.rglob("*.js")]
    assert any(ref_id in s for s in sources), f"{ref_id} 在任何 JS 里都找不到（{JS_CREATED_REFS[ref_id]}）"
