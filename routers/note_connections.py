"""笔记的“串联”：一篇笔记和别的知识点之间有什么关系（只读，不新增任何任务）。

GET /api/notes/{id}/connections 返回：
- note_mentions：这篇正文里提到了哪些别的笔记（按显示标题匹配，代码块里的不算）；
- mentioned_by：哪些别的笔记正文里提到了这篇的标题（不管有没有写成 [[链接]]）；
- patterns：正文里提到的套路（按 150 个套路的名称、括号里的别名和常见俗称匹配）；
- mistakes：相关的错题——关联的题、[[题:…]] 链接到的题、正文提到的题名、提到的套路下的题；
- same_tag：标签相同的其他笔记。
前端用这些在阅读视图里给正文加“提到了”的链接、在下方列出“串联”面板。只处理本人数据。
"""
import re

from fastapi import APIRouter
from fastapi import Depends

import db
import pattern_match
from core import security
from routers import notes
import tags

router = APIRouter()

MENTION_MAX = 30
MENTIONED_BY_MAX = 10
PATTERN_MAX = 8
MISTAKE_MAX = 8
SAME_TAG_MAX = 5
TITLE_MIN = 2
PROBLEM_TITLE_MIN = 4


def _plain(content):
    """去掉代码块和行内代码，只在正文文字里找提及。"""
    return notes._CODE_SPAN_RE.sub(" ", content or "")


def _snippet(text, term):
    index = text.find(term)
    if index < 0:
        return ""
    left = max(0, index - 30)
    right = min(len(text), index + len(term) + 30)
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _pattern_hits(text):
    """[(pattern_id, title, category, term)]，term 是正文里实际出现的那个词（前端拿它加下划线）。"""
    lowered = text.lower()
    hits = []
    seen = set()
    for entry in pattern_match._catalog():
        best = ""
        for term in entry["terms"]:
            if len(term) > len(best) and term in lowered:
                best = term
        if best:
            start = lowered.find(best)
            hits.append((entry["id"], entry["title"], entry["category"], text[start:start + len(best)]))
            seen.add(entry["id"])
    # 常见俗称（“二分”“单调栈”“dp”……）补充：只在没有直接命中同类套路时用来找相关错题。
    alias_prefixes = set()
    for alias, prefixes in pattern_match.ALIASES.items():
        if len(alias) >= 2 and alias in lowered:
            alias_prefixes.update(prefixes)
    hits.sort(key=lambda item: -len(item[3]))
    return hits[:PATTERN_MAX], seen, alias_prefixes


@router.get("/api/notes/{note_id}/connections")
def note_connections(note_id: int, user=Depends(security.current_user)):
    user_id = user["id"]
    with db.connect() as conn:
        note = notes.owned_note(conn, note_id, user_id)
        text = _plain(note["content"])
        lowered = text.lower()
        own_title = notes._display_title(note["title"], note["content"])

        others = conn.execute(
            "SELECT id, title, content, tags, updated_at FROM notes "
            "WHERE user_id = ? AND deleted_at IS NULL AND id != ? ORDER BY updated_at DESC",
            (user_id, note_id),
        ).fetchall()

        note_mentions = []
        mentioned_by = []
        same_tag = []
        my_tags = {tag.casefold() for tag in note["tags"]}
        for row in others:
            title = notes._display_title(row["title"], row["content"])
            if len(title) >= TITLE_MIN and title != own_title and title.lower() in lowered:
                note_mentions.append({"id": row["id"], "title": title})
            if len(own_title) >= TITLE_MIN and own_title in _plain(row["content"]):
                mentioned_by.append({
                    "id": row["id"], "title": title,
                    "snippet": _snippet(_plain(row["content"]), own_title),
                })
            shared = [tag for tag in row["tags"].split(",") if tag and tag.casefold() in my_tags]
            if shared:
                same_tag.append({"id": row["id"], "title": title, "shared": shared})
        # 长标题优先（“二分查找”和“二分”都命中时，先给更具体的那个），最多 30 个。
        note_mentions.sort(key=lambda item: -len(item["title"]))
        same_tag.sort(key=lambda item: -len(item["shared"]))

        pattern_hits, pattern_ids, alias_prefixes = _pattern_hits(text)

        # 相关错题：按“关系有多直接”排序，去重后最多 8 条。
        problem_ids = []
        if note["problem_id"]:
            problem_ids.append(note["problem_id"])
        for row in conn.execute(
            "SELECT target_problem_id FROM note_links WHERE source_note_id = ? AND target_problem_id IS NOT NULL",
            (note_id,),
        ):
            problem_ids.append(row["target_problem_id"])
        for row in conn.execute("SELECT id, title FROM problems WHERE user_id = ?", (user_id,)):
            if len(row["title"] or "") >= PROBLEM_TITLE_MIN and row["title"].lower() in lowered:
                problem_ids.append(row["id"])
        wanted_patterns = set(pattern_ids)
        if alias_prefixes:
            for entry in pattern_match._catalog():
                if any(entry["id"].startswith(prefix) for prefix in alias_prefixes):
                    wanted_patterns.add(entry["id"])
        if wanted_patterns:
            marks = ",".join("?" for _ in wanted_patterns)
            for row in conn.execute(
                f"SELECT DISTINCT pp.problem_id FROM problem_patterns pp JOIN problems p ON p.id = pp.problem_id "
                f"WHERE p.user_id = ? AND pp.pattern_id IN ({marks}) ORDER BY pp.problem_id DESC LIMIT 20",
                (user_id, *sorted(wanted_patterns)),
            ):
                problem_ids.append(row["problem_id"])

        mistakes = []
        seen_problems = set()
        for problem_id in problem_ids:
            if problem_id in seen_problems or len(mistakes) >= MISTAKE_MAX:
                continue
            seen_problems.add(problem_id)
            for row in conn.execute(
                "SELECT m.id, m.problem_id, m.due_date, m.suspended_at, p.title, p.zone FROM mistakes m "
                "JOIN problems p ON p.id = m.problem_id WHERE p.id = ? AND p.user_id = ? ORDER BY m.id",
                (problem_id, user_id),
            ):
                if len(mistakes) >= MISTAKE_MAX:
                    break
                mistakes.append({
                    "id": row["id"], "problem_id": row["problem_id"], "title": row["title"],
                    "zone": row["zone"], "due_date": row["due_date"], "suspended": bool(row["suspended_at"]),
                })
        tag_map = tags.tags_for_mistakes(conn, [item["id"] for item in mistakes])
        for item in mistakes:
            item["tags"] = tag_map.get(item["id"], [])

    return {
        "note_mentions": note_mentions[:MENTION_MAX],
        "mentioned_by": mentioned_by[:MENTIONED_BY_MAX],
        "patterns": [
            {"id": pid, "title": title, "category": category, "term": term}
            for pid, title, category, term in pattern_hits
        ],
        "mistakes": mistakes,
        "same_tag": same_tag[:SAME_TAG_MAX],
    }
