"""本周热门题目：近 7 个北京日内被足够多不同用户收录或复习过的公开题目。

题目来源只认思路文字里“题目链接：”开头的行，并用与 static/capture.js 的
Capture.parse 等价的规则识别站点和题目 id（parse() 是它的 Python 移植，
tests/test_hot_problems.py 用同一批用例同时喂两边保证一致）。
对外只给来源站点、由 id 生成的题名、人数和白名单站点的规范链接：
不含任何用户信息，也不含用户自己写的标题、思路或原始链接。
"""
import re
import unicodedata
from datetime import timedelta
from urllib.parse import unquote_to_bytes

import rank_board
import rank_cache
from db import connect

# 近 7 天内至少这么多个不同用户收录或复习过才进入热门；测试里可以调小。
HOT_MIN_USERS = 5
HOT_LIMIT = 10
WINDOW_DAYS = 7

ZONE = "算法"
MAX_URL = 2000
LINK_PREFIX = "题目链接："

HOSTS = {
    "leetcode.cn": "leetcode", "leetcode.com": "leetcode", "leetcode-cn.com": "leetcode",
    "luogu.com.cn": "luogu",
    "nowcoder.com": "nowcoder",
    "codeforces.com": "codeforces",
    "atcoder.jp": "atcoder",
}
SOURCE_LABELS = {
    "leetcode": "LeetCode", "luogu": "洛谷", "nowcoder": "牛客",
    "codeforces": "Codeforces", "atcoder": "AtCoder",
}
# 对外链接只允许这些站点，由 id 按模板生成，不回传用户写的原始链接。
ALLOWED_LINK_HOSTS = frozenset({
    "leetcode.cn", "www.luogu.com.cn", "www.nowcoder.com", "codeforces.com", "atcoder.jp",
})

# JS 的 \s（含 ﻿ 和各种 Unicode 空格，不含 Python 额外认作空白的 \x1c-\x1f）。
_JS_SPACE = "\t\n\u000b\u000c\r    -     　﻿"
_EDGE = _JS_SPACE + "“”‘’\"'`<>《》〈〉「」『』（）()\\[\\]【】，。、；：！？,;!?"
_TRIM_EDGE = re.compile(f"^[{_EDGE}]+|[{_EDGE}]+\\Z")
_HAS_SPACE = re.compile(f"[{_JS_SPACE}]")
_TRACKING = re.compile(
    r"(?:utm_[^\n\r  ]*|spm|from|fbclid|gclid|ref|referer|source|share_token|scm"
    r"|share_[^\n\r  ]*)",
    re.I | re.A,
)
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*", re.I | re.A)
_LUOGU = re.compile(r"[A-Za-z]{1,4}\d+[A-Za-z0-9_]*", re.A)
_ALNUM = re.compile(r"[0-9A-Za-z]+", re.A)
_DIGITS_1_7 = re.compile(r"\d{1,7}", re.A)
_CF_LETTER = re.compile(r"[A-Za-z]\d?", re.A)
_WORD_DASH = re.compile(r"[\w-]+", re.A)
_FORBIDDEN_HOST = set(" #%/:<>?@[\\]^|\x7f") | {chr(code) for code in range(0x20)}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _utf16_length(text):
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _title_case(slug):
    return " ".join(word[0].upper() + word[1:] for word in slug.split("-") if word)


def _identify(source, segments):
    """返回 (id, 标题, 规范链接) 或 None；规则与 capture.js 的 identify 逐条对应。"""
    first, second, third, fourth = (list(segments) + [None] * 4)[:4]
    if source == "leetcode":
        if first != "problems" or not (second and _SLUG.fullmatch(second)):
            return None
        slug = second.lower()
        return slug, f"LeetCode · {_title_case(slug)}", f"https://leetcode.cn/problems/{slug}/"
    if source == "luogu":
        if first != "problem" or not (second and _LUOGU.fullmatch(second)):
            return None
        ident = second.upper()
        return ident, f"洛谷 · {ident}", f"https://www.luogu.com.cn/problem/{ident}"
    if source == "nowcoder":
        if first not in ("practice", "questionTerminal") or not (second and _ALNUM.fullmatch(second)):
            return None
        return second, "牛客 · 题目", f"https://www.nowcoder.com/practice/{second}"
    if source == "codeforces":
        kind = None
        if first == "problemset" and second == "problem":
            contest, letter, kind = third, fourth, "problemset"
        elif first in ("contest", "gym") and third == "problem":
            contest, letter, kind = second, fourth, first
        else:
            return None
        if not (contest and _DIGITS_1_7.fullmatch(contest)) or not (letter and _CF_LETTER.fullmatch(letter)):
            return None
        ident = f"{contest}{letter.upper()}"
        # 赛题与 gym 的编号不会重叠（gym 从 100001 起），按编号选链接形式。
        if int(contest) >= 100000:
            link = f"https://codeforces.com/gym/{contest}/problem/{letter.upper()}"
        else:
            link = f"https://codeforces.com/problemset/problem/{contest}/{letter.upper()}"
        return ident, f"Codeforces · {ident}", link
    if source == "atcoder":
        if first != "contests" or not (second and _WORD_DASH.fullmatch(second)) or third != "tasks" \
                or not (fourth and _WORD_DASH.fullmatch(fourth)):
            return None
        return fourth, f"AtCoder · {fourth}", f"https://atcoder.jp/contests/{second}/tasks/{fourth}"
    return None


def _encode_path_segment(segment):
    out = []
    for byte in segment.encode("utf-8", "replace"):
        if byte <= 0x20 or byte >= 0x7F or chr(byte) in '"#<>?`{}':
            out.append(f"%{byte:02X}")
        else:
            out.append(chr(byte))
    return "".join(out)


def _normalize_path(path):
    """WHATWG 路径规范化：反斜杠当斜杠、处理 . / .. 段、按路径编码集转义。"""
    path = path.replace("\\", "/")
    segments = path.split("/")[1:] if path.startswith("/") else path.split("/")
    out = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        lowered = segment.lower()
        if lowered in ("..", ".%2e", "%2e.", "%2e%2e"):
            if out:
                out.pop()
            if last:
                out.append("")
        elif lowered in (".", "%2e"):
            if last:
                out.append("")
        else:
            out.append(_encode_path_segment(segment))
    return "/" + "/".join(out)


def _form_decode(text):
    return unquote_to_bytes(text.replace("+", " ")).decode("utf-8", "replace")


def _form_encode(text):
    out = []
    for byte in text.encode("utf-8", "replace"):
        char = chr(byte)
        if byte < 0x80 and (char.isalnum() or char in "*-._"):
            out.append(char)
        elif char == " ":
            out.append("+")
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def _clean_query(query):
    kept = []
    for pair in query.split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        name, value = _form_decode(name), _form_decode(value)
        if not _TRACKING.fullmatch(name):
            kept.append((name, value))
    if not kept:
        return ""
    return "?" + "&".join(f"{_form_encode(name)}={_form_encode(value)}" for name, value in kept)


def _parse_host(authority_host):
    host = unquote_to_bytes(authority_host).decode("utf-8", "replace")
    host = unicodedata.normalize("NFKC", host).lower()
    for dot in "。．｡":
        host = host.replace(dot, ".")
    host = re.sub("[­​⁠]", "", host)
    if not host or any(char in _FORBIDDEN_HOST for char in host):
        return None
    return host


def _split_url(text):
    """最小化的 WHATWG 解析（只处理 http/https）：返回 (scheme, host, pathname, query) 或 None。"""
    text = text.strip("".join(chr(code) for code in range(0x21))).replace("\t", "").replace("\n", "")
    match = re.match(r"(https?):", text, re.I)
    if not match:
        return None
    scheme = match.group(1).lower()
    rest = text[match.end():].lstrip("/\\")
    end = next((index for index, char in enumerate(rest) if char in "/\\?#"), len(rest))
    authority, rest = rest[:end], rest[end:]
    if "@" in authority:
        userinfo, _, authority = authority.rpartition("@")
        username, _, password = userinfo.partition(":")
        if username or password:
            return None
        if not authority:
            return None
    if "[" in authority:
        return None
    host_text, colon, port_text = authority.partition(":")
    if colon and port_text:
        if not port_text.isascii() or not port_text.isdigit() or int(port_text) > 65535:
            return None
        if int(port_text) != _DEFAULT_PORTS[scheme]:
            return None  # 非默认端口：capture.js 一律拒绝
    elif colon and ":" in port_text:
        return None
    host = _parse_host(host_text)
    if host is None:
        return None
    rest = rest.split("#", 1)[0]
    path, _, query = rest.partition("?")
    return scheme, host, _normalize_path(path or "/"), query


def parse_detail(text):
    """与 Capture.parse 等价；多返回一个 canonical（热门列表用的规范链接）。认不出返回 None。"""
    if not isinstance(text, str) or _utf16_length(text) > MAX_URL * 2:
        return None
    trimmed = _TRIM_EDGE.sub("", text)
    if not trimmed or _utf16_length(trimmed) > MAX_URL or _HAS_SPACE.search(trimmed):
        return None
    if not re.match(r"https?://", trimmed, re.I):
        return None
    split = _split_url(trimmed)
    if split is None:
        return None
    scheme, hostname, pathname, query = split
    host = hostname[4:] if hostname.startswith("www.") else hostname
    source = HOSTS.get(host)
    if source is None:
        return None
    found = _identify(source, [segment for segment in pathname.split("/") if segment])
    if found is None:
        return None
    ident, title, canonical = found
    return {
        "source": source, "host": host, "id": ident, "title": title, "zone": ZONE,
        "url": f"{scheme}://{hostname}{pathname}{_clean_query(query)}",
        "canonical": canonical,
    }


def parse(text):
    """Capture.parse 的服务端等价实现：返回 source/host/id/title/zone/url 或 None。"""
    detail = parse_detail(text)
    if detail is None:
        return None
    return {key: value for key, value in detail.items() if key != "canonical"}


def problem_links(thinking):
    """思路文字里每个“题目链接：”行能识别出的 (source, id, 规范链接, 题名)；去重。"""
    found = {}
    for line in str(thinking or "").split("\n"):
        if not line.startswith(LINK_PREFIX):
            continue
        detail = parse_detail(line[len(LINK_PREFIX):])
        if detail:
            found.setdefault((detail["source"], detail["id"]), detail)
    return found


def display_name(source, ident, title):
    # 牛客的 id 是不透明的字符串，题名固定为“题目”，显示时带上 id 区分。
    if source == "nowcoder":
        return ident
    return title.split(" · ", 1)[1]


def _compute(day_from, day_to, min_users, limit):
    start, end = rank_board.day_bounds(day_from, day_to)
    params = {"start": start, "end": end}
    like = "p.thinking LIKE '%题目链接：%'"
    with connect() as conn:
        conn.execute("BEGIN")
        created = conn.execute(
            f"""
            SELECT p.user_id, p.thinking FROM problems p JOIN users u ON u.id = p.user_id
            WHERE p.created_at >= :start AND p.created_at < :end AND {like}
              AND {rank_board.ELIGIBLE_SQL}
            """,
            params,
        ).fetchall()
        reviewed = conn.execute(
            f"""
            SELECT DISTINCT p.user_id, p.id, p.thinking FROM reviews r
            JOIN mistakes m ON m.id = r.mistake_id
            JOIN problems p ON p.id = m.problem_id
            JOIN users u ON u.id = p.user_id
            WHERE r.reviewed_at >= :start AND r.reviewed_at < :end AND {like}
              AND {rank_board.ELIGIBLE_SQL}
            """,
            params,
        ).fetchall()
    people = {}
    details = {}
    for row in [*created, *reviewed]:
        for key, detail in problem_links(row["thinking"]).items():
            people.setdefault(key, set()).add(row["user_id"])
            # 同一题可能有不同写法的链接：取字典序最小的规范链接，结果稳定。
            if key not in details or detail["canonical"] < details[key]["canonical"]:
                details[key] = detail
    ranked = sorted(
        ((len(users), key) for key, users in people.items() if len(users) >= min_users),
        key=lambda item: (-item[0], item[1]),
    )[:limit]
    entries = []
    for count, key in ranked:
        detail = details[key]
        entries.append({
            "source": detail["source"],
            "source_label": SOURCE_LABELS[detail["source"]],
            "name": display_name(detail["source"], detail["id"], detail["title"]),
            "title": detail["title"],
            "users": count,
            "url": detail["canonical"],
        })
    return {
        "from": day_from.isoformat(), "to": day_to.isoformat(), "timezone": "Asia/Shanghai",
        "min_users": min_users, "entries": entries,
    }


def hot_problems_response(now=None, min_users=None):
    day_to = rank_board.yesterday(now)
    day_from = day_to - timedelta(days=WINDOW_DAYS - 1)
    threshold = HOT_MIN_USERS if min_users is None else min_users
    return rank_cache.get_or_compute(
        "hot", day_to, lambda: _compute(day_from, day_to, threshold, HOT_LIMIT), extra=(threshold,)
    )
