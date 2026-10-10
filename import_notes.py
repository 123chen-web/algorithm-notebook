"""按 day 笔记格式导入：每个题目小节一条记录，每个文件一个事务。"""
import argparse
import glob
import os
import re
import sqlite3
import sys
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from pathlib import Path

from db import ROOT, connect
from scheduler import today_in_timezone

HEADING = re.compile(r"^ {0,3}##(?!#)\s+(.+?)\s*$")
QUESTION = re.compile(r"^题目\s*\d+\s*[:：]\s*(.+)$")
KNOWLEDGE = re.compile(r"^知识点(?:\s*\d+)?\s*[:：]")
FIELD = re.compile(r"^\*\*([^*]+)\*\*\s*[:：]?\s*(.*)$")
BULLET = re.compile(r"^([ \t]*)(?:[-+*]|\d+[.)])[ \t]+(.+)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ESCAPED = re.compile(r"^( {0,3})((?:\\`){3,})([A-Za-z0-9_+.-]*[ \t]*)$")


def fence_line(line):
    match = ESCAPED.fullmatch(line)
    if match:
        return match[1] + match[2].replace("\\`", "`") + match[3]
    return line


def scan(text):
    """只识别围栏外的 Markdown；不修改代码正文。"""
    result, fence = [], None
    for line in text.splitlines():
        candidate = fence_line(line)
        if fence:
            close = re.fullmatch(
                r" {0,3}" + re.escape(fence[0])
                + "{" + str(len(fence)) + r",}[ \t]*", candidate
            )
            result.append((line, "close" if close else "code", ""))
            if close:
                fence = None
        else:
            opening = FENCE.fullmatch(candidate)
            if opening:
                fence = opening[1]
                result.append((line, "open", opening[2].strip()))
            else:
                result.append((line, "text", ""))
    if fence:
        raise ValueError("存在未闭合的代码围栏")
    return result


def extract_mistakes(lines):
    bullets = [
        BULLET.match(line) for line, kind, _ in lines
        if kind == "text" and BULLET.match(line)
    ]
    if not bullets:
        raise ValueError("易错点必须包含非空 Markdown 列表")
    base = min(len(match[1].expandtabs(4)) for match in bullets)
    items = []
    for line, kind, _ in lines:
        if kind == "text" and line.lstrip().startswith(">"):
            continue
        match = BULLET.match(line) if kind == "text" else None
        if match and len(match[1].expandtabs(4)) == base:
            items.append([match[2]])
        elif items:
            items[-1].append(line)
        elif line.strip():
            raise ValueError("易错点列表前存在无法识别的正文")
    return ["\n".join(item).strip() for item in items]


def parse_question(title, lines):
    fields, active = {}, None
    for line, kind, info in lines:
        match = FIELD.fullmatch(line.strip()) if kind == "text" else None
        if match:
            active = match[1].strip()
            if active in fields:
                raise ValueError(f"字段重复：{active}")
            fields[active] = []
            if match[2]:
                fields[active].append((match[2], "text", ""))
        elif active:
            fields[active].append((line, kind, info))

    def body(name):
        return "\n".join(line for line, _, _ in fields.get(name, [])).strip()

    code_lines = fields.get("代码", [])
    openings = [item for item in code_lines if item[1] == "open"]
    if len(openings) != 1 or not openings[0][2]:
        raise ValueError("代码字段必须有一个带语言标签的代码围栏")
    code = "\n".join(line for line, kind, _ in code_lines if kind == "code")
    language = openings[0][2]
    language = {"cpp": "C++", "c++": "C++", "python": "Python"}.get(
        language.lower(), language
    )
    keys = [
        name for name in fields
        if re.sub(r"\s+", "", name) in {"易错点", "易错点/注意点"}
    ]
    if len(keys) != 1:
        raise ValueError("必须有一个易错点或易错点 / 注意点字段")
    mistakes = extract_mistakes(fields[keys[0]])
    thinking = body("思路")
    fallback = not thinking
    if fallback:
        description = body("题目描述")
        if not description:
            raise ValueError("缺少思路，且没有可代填的题目描述")
        thinking = "[导入说明：原笔记未记录思路；以下为题目描述。]\n" + description
    record = {
        "title": title.strip(), "language": language, "code": code + "\n",
        "thinking": thinking, "mistakes": mistakes, "fallback": fallback,
    }
    for name, limit in (
        ("title", 200), ("language", 40), ("code", 40000), ("thinking", 8000)
    ):
        if not record[name].strip() or len(record[name]) > limit:
            raise ValueError(f"{name} 为空或超过 {limit} 字符")
    if not 1 <= len(mistakes) <= 10 or any(
        not text or len(text) > 2000 for text in mistakes
    ):
        raise ValueError("易错点须为 1–10 条非空内容，每条最多 2000 字符")
    return record


CUOTI_TITLE = re.compile(r"^# (.+?)\s*$")
CUOTI_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LANGUAGE_NAMES = {"cpp": "C++", "c++": "C++", "python": "Python"}


def is_cuoti_format(text):
    """错题本（cuoti）格式：一个文件一道题，H1 标题 + 固定的二级小节。"""
    return bool(
        re.search(r"^# \S", text, re.M)
        and re.search(r"^## 错误原因分析\s*$", text, re.M)
    )


def first_fence(items):
    """返回该小节第一个代码围栏的 (语言, 代码)；没有围栏返回 None。"""
    language, body, inside = None, [], False
    for line, kind, info in items:
        if kind == "open" and not inside:
            language, inside = info, True
        elif kind == "code" and inside:
            body.append(line)
        elif kind == "close" and inside:
            return language, "\n".join(body)
    return None


def parse_cuoti(text):
    title, header, sections, current = None, [], {}, None
    for line, kind, info in scan(text):
        if kind == "text" and title is None and CUOTI_TITLE.fullmatch(line):
            title = CUOTI_TITLE.fullmatch(line)[1]
            continue
        heading = HEADING.fullmatch(line) if kind == "text" else None
        if heading:
            current = heading[1].strip()
            sections[current] = []
        elif current is None:
            header.append((line, kind, info))
        else:
            sections[current].append((line, kind, info))
    if not title:
        raise ValueError("缺少一级标题")

    def section(prefix):
        for name, items in sections.items():
            if name.startswith(prefix):
                return items
        return []

    def body(prefix):
        return "\n".join(line for line, _, _ in section(prefix)).strip()

    fields = {}
    for line, kind, _ in header:
        match = FIELD.fullmatch(line.strip()) if kind == "text" else None
        if match and match[2]:
            fields[match[1].strip()] = match[2].strip()

    wrong, right = first_fence(section("我的错误代码")), first_fence(section("正确代码"))
    chosen = wrong or right
    if not chosen or not chosen[0] or not chosen[1].strip():
        raise ValueError("缺少带语言标签的代码围栏（我的错误代码或正确代码）")
    language = LANGUAGE_NAMES.get(chosen[0].lower(), chosen[0])

    parts = []
    if fields.get("标签"):
        parts.append("标签：" + fields["标签"])
    for label, prefix in (
        ("题目大意", "题目大意"), ("来源", "题目来源"),
        ("当时的思路", "我当时的思路"), ("错误原因", "错误原因分析"),
    ):
        text_part = body(prefix)
        if text_part:
            parts.append(f"【{label}】\n{text_part}")
    takeaway = body("关键收获")
    if not takeaway:
        raise ValueError("缺少关键收获（它会作为复习用的易错点）")
    record = {
        "title": title.strip(), "language": language, "code": chosen[1] + "\n",
        "thinking": "\n\n".join(parts), "mistakes": [takeaway], "fallback": False,
        "correct_code": right[1] + "\n" if wrong and right else "",
    }
    date = fields.get("记录日期", "")
    if CUOTI_DATE.fullmatch(date):
        record["created_at"] = f"{date}T12:00:00+00:00"
    for name, limit in (
        ("title", 200), ("language", 40), ("code", 40000), ("thinking", 8000)
    ):
        if not record[name].strip() or len(record[name]) > limit:
            raise ValueError(f"{name} 为空或超过 {limit} 字符")
    if len(takeaway) > 2000:
        raise ValueError("关键收获超过 2000 字符")
    return record


def parse_file(path):
    text = path.read_text(encoding="utf-8-sig")
    if is_cuoti_format(text):
        return [parse_cuoti(text)], []
    sections, current = [], None
    for item in scan(text):
        heading = HEADING.fullmatch(item[0]) if item[1] == "text" else None
        if heading:
            current = (heading[1], [])
            sections.append(current)
        elif current:
            current[1].append(item)
    records, skipped = [], []
    for heading, lines in sections:
        question = QUESTION.fullmatch(heading)
        if question:
            try:
                records.append(parse_question(question[1], lines))
            except ValueError as exc:
                raise ValueError(f"{heading}：{exc}") from exc
        elif KNOWLEDGE.match(heading):
            skipped.append(heading)
    if not records and not skipped:
        raise ValueError("未找到题目或知识点小节，请检查格式")
    return records, skipped


def expand_paths(inputs):
    paths = set()
    for value in inputs:
        direct = Path(value).expanduser()
        matches = [direct] if direct.exists() else [
            Path(match) for match in glob.glob(str(direct), recursive=True)
        ]
        if not matches:
            raise ValueError(f"路径或模式没有匹配文件：{value}")
        for match in matches:
            files = match.rglob("*.md") if match.is_dir() else [match]
            for path in files:
                if path.is_file() and path.suffix.lower() == ".md":
                    paths.add(path.resolve())
    if not paths:
        raise ValueError("没有找到 Markdown 文件")
    return sorted(paths, key=lambda path: str(path).casefold())


def existing_titles(conn, user_id):
    return {
        row[0] for row in conn.execute(
            "SELECT title FROM problems WHERE user_id = ?", (user_id,)
        )
    }


def insert_record(conn, record, user_id, day, now):
    cursor = conn.execute(
        "INSERT INTO problems(user_id,title,language,code,thinking,created_at,correct_code) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, record["title"], record["language"], record["code"],
         record["thinking"], record.get("created_at", now), record.get("correct_code", "")),
    )
    conn.executemany(
        "INSERT INTO mistakes(problem_id,description,due_date) VALUES (?,?,?)",
        [(cursor.lastrowid, text, day) for text in record["mistakes"]],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="已存在的目标用户名")
    parser.add_argument("--database", help="可选：指定已存在的 SQLite 文件")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写入")
    parser.add_argument("paths", nargs="+", help="文件、目录或带引号的 glob 模式")
    args = parser.parse_args(argv)
    try:
        files = expand_paths(args.paths)
        if args.database:
            os.environ["DATABASE_PATH"] = str(Path(args.database).expanduser().resolve())
        db_path = Path(os.getenv("DATABASE_PATH", "data/notebook.db")).expanduser()
        db_path = (db_path if db_path.is_absolute() else ROOT / db_path).resolve()
        if not db_path.is_file():
            raise ValueError(f"数据库不存在，请先启动应用并注册账号：{db_path}")
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as ro:
            user = ro.execute(
                "SELECT id,timezone,deleted_at FROM users WHERE username = ?",
                (args.username.strip().lower(),),
            ).fetchone()
            if user is None:
                raise ValueError("目标账号不存在，请先在应用中注册")
            if user[2] is not None:
                raise ValueError("账号已注销，不能导入学习记录")
            simulated_titles = existing_titles(ro, user[0])
        now = datetime.now(timezone.utc)
        day = today_in_timezone(user[1], now).isoformat()
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2

    imported = duplicates = errors = skipped_count = 0
    for path in files:
        try:
            # 整个文件解析成功后才开启写事务。
            records, skipped = parse_file(path)
            messages, added, repeated = [], 0, 0
            context = nullcontext(None) if args.dry_run else connect(write=True)
            with context as conn:
                if not args.dry_run:
                    current = conn.execute(
                        "SELECT deleted_at FROM users WHERE id = ?", (user[0],)
                    ).fetchone()
                    if current is None or current["deleted_at"] is not None:
                        raise ValueError("账号已注销，不能导入学习记录")
                titles = (simulated_titles.copy() if args.dry_run
                          else existing_titles(conn, user[0]))
                for record in records:
                    title = record["title"]
                    if title in titles:
                        repeated += 1
                        messages.append(f"SKIP 已存在：{title}")
                        continue
                    if not args.dry_run:
                        insert_record(conn, record, user[0], day, now.isoformat())
                    titles.add(title)
                    added += 1
                    label = "WOULD IMPORT" if args.dry_run else "IMPORTED"
                    messages.append(f"{label} {title}（{len(record['mistakes'])} 条易错点）")
                    if record["fallback"]:
                        messages.append("  NOTE 原文缺思路，已标注以题目描述代填")
            if args.dry_run:
                simulated_titles = titles
            imported += added
            duplicates += repeated
            skipped_count += len(skipped)
            print(f"\n{path}")
            for heading in skipped:
                print(f"SKIP 知识点：{heading}")
            for message in messages:
                print(message)
        except (OSError, ValueError, sqlite3.Error) as exc:
            errors += 1
            print(f"ERROR {path}：{exc}；此文件未导入", file=sys.stderr)
    label = "预计新增" if args.dry_run else "已新增"
    print(f"\n{label} {imported} 题；重复 {duplicates} 题；"
          f"跳过知识点 {skipped_count} 个；失败文件 {errors} 个。")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
