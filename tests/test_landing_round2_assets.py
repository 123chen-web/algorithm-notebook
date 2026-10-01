"""Round two UI regressions without temporary files or browser dependencies."""

from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "static"


class AccountDocument(HTMLParser):
    VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self):
        super().__init__()
        self.stack = []
        self.trial_badges = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "trial-banner":
            self.trial_badges.append((attrs, tuple(self.stack)))
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


def test_trial_status_is_one_account_badge_with_the_retention_explanation():
    document = AccountDocument()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    assert len(document.trial_badges) == 1
    badge, ancestors = document.trial_badges[0]
    assert badge.get("role") == "status"
    assert "hidden" in badge
    assert any(attrs.get("id") == "my-avatar-wrap" for _, attrs in ancestors)
    for attribute in ("title", "aria-label"):
        assert "体验账号" in badge.get(attribute, "")
        assert "数据可能会被定期清理" in badge[attribute]
        assert "注册正式账号才能长期保存" in badge[attribute]


def test_trial_signin_does_not_repeat_the_account_status_in_a_notice():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    trial = re.search(r"(?m)^function startTrial\(\) \{([\s\S]*?)^\}", source)
    assert trial
    assert "await enterApp()" in trial[1]
    assert not re.search(r"\bmessage\(\s*[\"']", trial[1])
    assert '$("#trial-banner").hidden = !user.is_trial' in source
    assert '$("#trial-banner").hidden = true' in source
