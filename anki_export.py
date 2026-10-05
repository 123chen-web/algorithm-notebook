"""把错题记录列表转换成 Anki 可导入的制表符分隔文本。

仅使用标准库，不联网，不读写文件。
对外暴露的唯一接口是 build_anki_text(records)。
"""

import re
import unicodedata

# Anki 文件头指令，原样输出
_HEADER_LINES = [
    "#separator:tab",
    "#html:true",
    "#guid column:1",
    "#tags column:4",
]

_GUID_PREFIX = "oy-"
_ALWAYS_TAG = "oy-export"
_TAB_REPLACEMENT = "    "  # 制表符替换成 4 个空格
_WHITESPACE_RE = re.compile(r"\s")
_HTML_SPECIAL_IN_TAG_RE = re.compile(r"[&<>\"']")


def _escape_html(text: str) -> str:
    """HTML 转义 5 个特殊字符：& < > " '（& 必须最先替换）。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _clean_text(text) -> str:
    """用户文字清洗：HTML 转义 -> 换行变 <br> -> 制表符变 4 空格 -> 删除其它控制字符。"""
    if not text:
        return ""
    out = _escape_html(str(text))
    out = out.replace("\n", "<br>").replace("\t", _TAB_REPLACEMENT)
    # 删除剩余的其它控制字符（如 \r、\x00、\x07 等），保证每条记录恰好一行
    return "".join(ch for ch in out if unicodedata.category(ch) != "Cc")


def _clean_tag(tag) -> str:
    """标签清洗：空白字符换成下划线，去掉 HTML 特殊字符。"""
    if not tag:
        return ""
    cleaned = _WHITESPACE_RE.sub("_", str(tag))
    cleaned = _HTML_SPECIAL_IN_TAG_RE.sub("", cleaned)
    return cleaned


def _build_front(record: dict) -> str:
    """正面：题名 + 分区 + （合法的）链接。"""
    parts = [
        "<div><b>{}</b></div>".format(_clean_text(record.get("title"))),
        "<div>分区：{}</div>".format(_clean_text(record.get("zone"))),
    ]
    url = record.get("url") or ""
    if url.lower().startswith(("http://", "https://")):
        parts.append("<div>链接：{}</div>".format(_clean_text(url)))
    return "".join(parts)


def _build_back(record: dict) -> str:
    """背面：错因 / 当时的思路 / 错误代码 / 修正代码，有内容才输出，顺序固定。"""
    parts = []
    cause = _clean_text(record.get("cause"))
    if cause:
        parts.append("<div><b>错因</b>：{}</div>".format(cause))
    notes = _clean_text(record.get("notes"))
    if notes:
        parts.append("<div><b>当时的思路</b>：{}</div>".format(notes))
    code = _clean_text(record.get("code"))
    if code:
        parts.append("<div><b>错误代码</b></div><pre><code>{}</code></pre>".format(code))
    fixed = _clean_text(record.get("fixed_code"))
    if fixed:
        parts.append("<div><b>修正代码</b></div><pre><code>{}</code></pre>".format(fixed))
    return "".join(parts)


def _build_tags(record: dict) -> str:
    """标签列：清洗、去空、追加 oy-export、按出现顺序去重、空格分隔。"""
    raw_tags = record.get("tags") or []
    cleaned = []
    for tag in raw_tags:
        t = _clean_tag(tag)
        if t:
            cleaned.append(t)
    cleaned.append(_ALWAYS_TAG)
    seen = set()
    ordered = []
    for t in cleaned:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return " ".join(ordered)


def build_anki_text(records: list[dict]) -> str:
    """把错题记录列表转换成 Anki 制表符分隔导入文本。

    前 4 行为文件头，之后每条记录一行（GUID、正面、背面、标签共 4 列，
    制表符分隔），保持输入顺序，输出以换行结尾。
    """
    lines = list(_HEADER_LINES)
    for record in records:
        guid = _GUID_PREFIX + str(record["id"])
        lines.append(
            "\t".join(
                (guid, _build_front(record), _build_back(record), _build_tags(record))
            )
        )
    return "\n".join(lines) + "\n"
