"""把错题记录列表转换成 Anki 可导入的制表符分隔文本。

仅使用标准库，不联网，不读写文件。
对外暴露的唯一接口是 build_anki_text(records)。
"""

import json
import re
import unicodedata
from urllib.parse import urlsplit

from tags import tags_for_mistakes
from mastery import AT_RISK_BELOW as MASTERY_AT_RISK_BELOW
from mastery import load_mistakes as mastery_load_mistakes
from mastery import retention_on as mastery_retention_on

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

# 与 static/capture.js 的 renderThinking 同一套识别规则：只认真正以
# “题目链接：”开头的行，取行内第一个 http(s) 链接，去掉句尾标点。
_ANKI_LINK_PREFIX = "题目链接："
_ANKI_URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_ANKI_URL_TAIL_RE = re.compile(r"[.,;:!?)\u3001\u3002\uff0c\uff1b\uff01\uff1f\uff09]+$")


from core import config
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


def anki_url_from_source(source_url, thinking):
    """优先用题目的来源链接字段；没有（旧数据）再从思路里的“题目链接：”行找。"""
    candidate = str(source_url or "").strip()
    if candidate:
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            parsed = None
        if parsed and parsed.scheme in ("http", "https") and parsed.netloc and not parsed.username:
            return candidate
    return anki_url_from_thinking(thinking)


def anki_url_from_thinking(thinking):
    """从思路文字的“题目链接：”行提取第一个安全 http(s) 链接；没有则返回空串。"""
    for line in str(thinking or "").split("\n"):
        if not line.startswith(_ANKI_LINK_PREFIX):
            continue
        for match in _ANKI_URL_RE.finditer(line):
            candidate = _ANKI_URL_TAIL_RE.sub("", match.group(0))
            try:
                parsed = urlsplit(candidate)
            except ValueError:
                continue
            if parsed.scheme in ("http", "https") and parsed.netloc and not parsed.username:
                return candidate
    return ""


def anki_mastery_sets(conn, user_id, timezone_name, today):
    """按 mastery 的单条保持率把易错点分成 (薄弱 id 集合, 已掌握 id 集合)。

    判定与 mastery.py 完全一致：retention_on(item, today) 低于 AT_RISK_BELOW
    算还没掌握（薄弱），否则算已掌握；当天还不存在的记录不参与。
    """
    weak, mastered = set(), set()
    items = mastery_load_mistakes(conn, user_id, timezone_name)
    for mistake_id, item in items.items():
        retention = mastery_retention_on(item, today)
        if retention is None:
            continue
        (weak if retention < MASTERY_AT_RISK_BELOW else mastered).add(mistake_id)
    return weak, mastered


def anki_export_records(conn, user_id, timezone_name, today, scope, zone=None):
    """按 scope 取该用户未删除的易错点，组装成 build_anki_text 需要的 record 字典。"""
    wanted_ids = None
    if scope in ("weak", "mastered"):
        weak, mastered = anki_mastery_sets(conn, user_id, timezone_name, today)
        wanted_ids = weak if scope == "weak" else mastered

    sql = [
        """
        SELECT m.id, p.title, p.zone, p.thinking, p.source_url, p.code, m.description AS cause,
               m.pending_reason,
               (
                   SELECT v.answer_code FROM variants v
                   WHERE v.mistake_id = m.id AND v.result = 'solved'
                     AND v.answer_code != '' AND v.result_updated_at IS NOT NULL
                   ORDER BY v.result_updated_at DESC, v.id DESC LIMIT 1
               ) AS fixed_code
        FROM mistakes m JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """
    ]
    params = [user_id]
    if scope == "zone":
        sql.append("AND p.zone = ?")
        params.append(zone)
    if wanted_ids is not None:
        sql.append("AND m.id IN (SELECT value FROM json_each(?))")
        params.append(json.dumps(sorted(wanted_ids)))
    sql.append("ORDER BY m.id")
    rows = conn.execute(" ".join(sql), params).fetchall()
    tags_by_id = tags_for_mistakes(conn, [row["id"] for row in rows])
    records = []
    for row in rows:
        records.append({
            "id": row["id"],
            "title": row["title"],
            "zone": row["zone"],
            "url": anki_url_from_source(row["source_url"], row["thinking"]),
            # 待补原因的卡片：背面原因处只显示"（待补）"，不泄露内部占位文案。
            "cause": config.PENDING_REASON_DISPLAY if row["pending_reason"] else row["cause"],
            "notes": row["thinking"],
            "code": row["code"],
            "fixed_code": row["fixed_code"] or "",
            "tags": tags_by_id[row["id"]],
        })
    return records

