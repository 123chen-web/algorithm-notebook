"""静态页面无障碍审计（只读 HTML，用标准库 html.parser）。

用法：python tests/a11y_audit.py  → 打印每个页面的发现。
tests/test_a11y_static.py 复用 audit() 做回归守卫。
"""
import sys
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = ["index.html", "help.html", "patterns.html", "extension.html", "ai-billing.html"]

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []  # (tag, attrs, record)
        self.elements = []  # records
        self.lang = None
        self.line = 1

    def handle_starttag(self, tag, attrs):
        a = {k: (v if v is not None else "") for k, v in attrs}
        rec = {"tag": tag, "attrs": a, "text": "", "in_label": False,
               "in_hidden": False, "line": self.getpos()[0]}
        if tag == "html":
            self.lang = a.get("lang")
        for _t, _a, r in self.stack:
            if r["tag"] == "label":
                rec["in_label"] = True
            if "hidden" in r["attrs"] or r["attrs"].get("aria-hidden") == "true":
                rec["in_hidden"] = True
        self.elements.append(rec)
        if tag not in VOID:
            self.stack.append((tag, a, rec))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        txt = data.strip()
        if txt:
            for _t, _a, r in self.stack:
                r["text"] += txt + " "


def _named(rec):
    a = rec["attrs"]
    return bool(
        a.get("aria-label", "").strip()
        or a.get("aria-labelledby", "").strip()
        or a.get("title", "").strip()
        or rec["text"].strip()
    )


def audit(name):
    """返回 {规则名: [描述, ...]}。"""
    p = _Page()
    p.feed((STATIC / name).read_text(encoding="utf-8"))
    els = p.elements
    ids = {}
    for e in els:
        i = e["attrs"].get("id")
        if i:
            ids.setdefault(i, []).append(e["line"])
    label_for = {e["attrs"]["for"] for e in els if e["tag"] == "label" and e["attrs"].get("for")}
    out = {k: [] for k in (
        "html-lang", "skip-link", "unlabelled-control", "unnamed-button", "unnamed-link",
        "img-alt", "duplicate-id", "dangling-ref", "positive-tabindex", "landmark-label")}
    if not p.lang:
        out["html-lang"].append("<html> 缺 lang")
    if name == "index.html":
        if not any(e["tag"] == "a" and e["attrs"].get("href", "").startswith("#") and "skip" in
                   (e["attrs"].get("class", "") + e["attrs"].get("id", "")).lower() for e in els):
            out["skip-link"].append("没有跳到主内容的 skip link")
    for e in els:
        a, t, ln = e["attrs"], e["tag"], e["line"]
        desc = f"L{ln} <{t}{' id=' + a['id'] if a.get('id') else ''}{' name=' + a['name'] if a.get('name') else ''}>"
        if t in ("input", "select", "textarea"):
            if t == "input" and a.get("type", "text").lower() in SKIP_INPUT_TYPES:
                pass
            elif "hidden" in a and False:
                pass
            else:
                ok = (e["in_label"] or a.get("aria-label", "").strip() or a.get("aria-labelledby", "").strip()
                      or a.get("title", "").strip() or (a.get("id") and a["id"] in label_for))
                if not ok:
                    out["unlabelled-control"].append(desc)
        if t == "button" or a.get("role") == "button":
            if not _named(e) and not e["in_hidden"]:
                out["unnamed-button"].append(desc)
        if t == "a" and "href" in a:
            if not _named(e) and not e["in_hidden"]:
                out["unnamed-link"].append(desc)
        if t == "img" and "alt" not in a:
            out["img-alt"].append(desc)
        if t == "tbody":
            pass
        for ref_attr in ("aria-labelledby", "aria-controls", "aria-describedby"):
            for ref in a.get(ref_attr, "").split():
                if ref not in ids:
                    out["dangling-ref"].append(f"{desc} {ref_attr}→#{ref}")
        if t == "label" and a.get("for") and a["for"] not in ids:
            out["dangling-ref"].append(f"{desc} for→#{a['for']}")
        if a.get("tabindex", "").lstrip("-").isdigit() and int(a["tabindex"]) > 0:
            out["positive-tabindex"].append(f"{desc} tabindex={a['tabindex']}")
    for i, lines in ids.items():
        if len(lines) > 1:
            out["duplicate-id"].append(f"id={i} 出现在 L{lines}")
    for tag, role in (("main", "main"), ("nav", "navigation")):
        landmarks = [e for e in els if e["tag"] == tag or e["attrs"].get("role") == role]
        if len(landmarks) > 1:
            for e in landmarks:
                if not (e["attrs"].get("aria-label") or e["attrs"].get("aria-labelledby")):
                    out["landmark-label"].append(f"L{e['line']} <{e['tag']}> 同类地标不止一个却没有名字")
    if name == "index.html" and not any(e["tag"] == "main" or e["attrs"].get("role") == "main" for e in els):
        out["landmark-label"].append("没有 main 地标")
    return out


if __name__ == "__main__":
    for page in PAGES:
        res = audit(page)
        print(f"== {page}")
        for rule, items in res.items():
            if items:
                print(f"  [{rule}] {len(items)}")
                for it in items[:60]:
                    print("     ", it)
    sys.exit(0)
