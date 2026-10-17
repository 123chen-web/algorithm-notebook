"""“我的对策”：从用户自己的“我的老毛病：错因”笔记里读出“## 我的对策”那一节。

笔记由错因标签下的“汇成笔记”生成（标题“# 我的老毛病：边界/特例”，最后一节“## 我的对策”留给用户写），
用户写的对策会在复习同类错因的题时、以及考前一页纸上出现——只是把自己写过的话串回来，不加任务。

纯函数 + 一个只读查询；SQL 只用 ? 占位符。编辑器保存时会把 [ ] ( ) * 等转义成 \\[，这里读的时候还原。
"""

import re

import error_taxonomy

TITLE_PREFIX = "我的老毛病："
_TITLE = re.compile(r"^\s*#\s*我的老毛病[：:]\s*(.+?)\s*$")
_SECTION = re.compile(r"^\s*#{2,3}\s*我的对策\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~=])")
PLACEHOLDER = "……"  # 模板里的“下次看到……，先……”没改过就不算
MAX_LINES = 8
MAX_LINE_LENGTH = 200


def _unescape(text):
    return _ESCAPE.sub(r"\1", text)


def parse(content):
    """返回 (错因名, [对策行]) 或 None（不是老毛病笔记、或还没写对策）。"""
    lines = str(content or "").replace("\r\n", "\n").split("\n")
    label = None
    for line in lines:
        match = _TITLE.match(_unescape(line))
        if match:
            label = match.group(1).strip()
            break
    if not label:
        return None
    result = []
    inside = False
    for raw in lines:
        line = _unescape(raw)
        if _SECTION.match(line):
            inside = True
            continue
        if inside and _HEADING.match(line):
            break
        if not inside:
            continue
        text = _BULLET.sub("", line).strip()
        if not text or PLACEHOLDER in text:
            continue
        result.append(text[:MAX_LINE_LENGTH])
        if len(result) >= MAX_LINES:
            break
    return (label, result) if result else None


def _code_of(label):
    for code, name, _group, _hint in error_taxonomy.CAUSES:
        if name == label:
            return code
    return None


def for_user(conn, user_id):
    """这个用户所有写了对策的老毛病笔记：[{note_id, label, tag, lines}]，最近改过的在前。"""
    rows = conn.execute(
        "SELECT id, content FROM notes WHERE user_id = ? AND deleted_at IS NULL AND content LIKE ? "
        "ORDER BY updated_at DESC, id DESC",
        (user_id, f"%{TITLE_PREFIX}%"),
    ).fetchall()
    found = []
    seen = set()
    for row in rows:
        parsed = parse(row["content"])
        if not parsed:
            continue
        label, lines = parsed
        if label in seen:  # 同一个错因写了好几篇：用最近改过的那篇
            continue
        seen.add(label)
        found.append({"note_id": row["id"], "label": label, "tag": _code_of(label), "lines": lines})
    return found
