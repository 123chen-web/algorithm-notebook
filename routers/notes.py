"""notes routes: 记笔记（Memos 式时间线，可关联题目）。

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

import re
from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import StringConstraints
from tags import normalize_tag
from typing import Annotated
from fastapi import APIRouter


router = APIRouter()

# 笔记标签独立于易错点标签体系：只借用 normalize_tag 做空白折叠，
# 去重/数量上限在这里单独实现，互不干扰。
NOTE_TAG_MAX = 10
NOTE_CONTENT_MAX = 20000
NOTE_COUNT_MAX = 2000
NoteTitle = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
NoteContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NOTE_CONTENT_MAX)
]
NoteTag = Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)]


def normalize_note_tags(values):
    """规范化笔记标签：去逗号（逗号是存储分隔符）、去重（不分大小写）、最多 10 个。"""
    seen = set()
    result = []
    for value in values or []:
        tag = normalize_tag(value).replace(",", "").replace("，", "")
        tag = " ".join(tag.split())
        if not tag:
            continue
        if len(tag) > 20:
            raise HTTPException(422, "标签最多 20 个字")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
    if len(result) > NOTE_TAG_MAX:
        raise HTTPException(422, f"每条笔记最多 {NOTE_TAG_MAX} 个标签")
    return result


def _escape_like(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ───────────── N1 笔记双向链接：解析与落库 ─────────────
# [[标题]] 链接本人笔记；[[题:标题]] 链接本人题目；[[标题|别名]] 渲染时显示别名。
# 围栏代码块（``` / ~~~）与行内代码（`...`）里的 [[ ]] 一律不解析。
NOTE_LINK_MAX = 50        # 单篇笔记最多识别/存储的链接数，超出截断并给中文 warning
RENAME_CASCADE_MAX = 200   # 改标题联动改写的笔记上限，超出截断并给中文 warning
GRAPH_NODE_MAX = 300      # 关系图谱节点上限，按度数截断，中心节点必保留
NOTE_REF_PER_NOTE_MAX = 20  # 单篇笔记正文引用附件 ![](attachment:ID) 上限，超出 422

# 先抓出所有"代码区间"，链接匹配落在代码区间内的一律忽略。
_CODE_SPAN_RE = re.compile(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`")
_LINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_ATTACHMENT_REF_RE = re.compile(r"attachment:(\d+)")


def _count_attachment_refs(content):
    """统计正文里 attachment:ID 引用个数；代码块/行内代码内的一律不计。"""
    ranges = [(m.start(), m.end()) for m in _CODE_SPAN_RE.finditer(content or "")]

    def in_code(start, end):
        return any(not (end <= cs or start >= ce) for cs, ce in ranges)

    return sum(
        1 for m in _ATTACHMENT_REF_RE.finditer(content or "")
        if not in_code(m.start(), m.end())
    )


def _iter_link_tokens(content):
    """按出现顺序产出正文中的 [[...]] 链接 token（已排除代码块/行内代码内的）。

    每项：{start, end, kind('note'|'problem'), target_text, alias}。
    target_text 已去掉"题:"前缀与"|别名"，并做 strip；alias 为可空字符串。
    """
    code_ranges = [(m.start(), m.end()) for m in _CODE_SPAN_RE.finditer(content)]

    def in_code(start, end):
        return any(not (end <= cs or start >= ce) for cs, ce in code_ranges)

    tokens = []
    for match in _LINK_RE.finditer(content):
        if in_code(match.start(), match.end()):
            continue
        inner = match.group(1)
        if "|" in inner:
            target_text, alias = inner.split("|", 1)
        else:
            target_text, alias = inner, ""
        if target_text.startswith("题:"):
            kind = "problem"
            target_text = target_text[2:]
        else:
            kind = "note"
        target_text = target_text.strip()
        alias = alias.strip()
        if not target_text:
            continue
        tokens.append({
            "start": match.start(), "end": match.end(), "kind": kind,
            "target_text": target_text, "alias": alias,
        })
    return tokens


def _display_title(title, content):
    """与前端 notes.js noteTitle 对齐：有 title 用 title，否则首非空行前 30 字。

    Memos 式 composer 只发 content、title 列存空，[[ ]] 链接按这个"显示标题"匹配。
    """
    text = (title or "").strip()
    if text:
        return text
    for line in (content or "").split("\n"):
        line = line.strip()
        if line:
            return line[:30]
    return ""


def _resolve_target(conn, user_id, token):
    """把一个 token 解析成目标 id（越权/不存在/已删除一律为 None=悬空）。"""
    if token["kind"] == "note":
        rows = conn.execute(
            "SELECT id, title, content, updated_at FROM notes "
            "WHERE user_id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchall()
        best = None
        for row in rows:
            if _display_title(row["title"], row["content"]) != token["target_text"]:
                continue
            if best is None or (row["updated_at"] or "", row["id"]) > (best["updated_at"] or "", best["id"]):
                best = row
        return (best["id"], None) if best else (None, None)
    row = conn.execute(
        "SELECT id FROM problems WHERE user_id = ? AND title = ? LIMIT 1",
        (user_id, token["target_text"]),
    ).fetchone()
    return (None, row["id"]) if row else (None, None)


def _reparse_links(conn, note_id, user_id, content):
    """与写笔记同一事务内"先删后插"重解析该笔记的全部出链（重复保存幂等）。

    返回中文 warnings 列表（超 50 截断等）。同一篇里的重复 token 按
    (kind, target_text, alias) 去重存储；图谱层面另行去重。
    """
    tokens = _iter_link_tokens(content)
    truncated = len(tokens) > NOTE_LINK_MAX
    kept = tokens[:NOTE_LINK_MAX]
    conn.execute("DELETE FROM note_links WHERE source_note_id = ?", (note_id,))
    seen = set()
    now = main.utc_now()
    for token in kept:
        dedupe_key = (token["kind"], token["target_text"], token["alias"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        target_note_id, target_problem_id = _resolve_target(conn, user_id, token)
        conn.execute(
            """
            INSERT INTO note_links(user_id, source_note_id, link_kind, target_note_id,
                                   target_problem_id, target_text, alias_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, note_id, token["kind"], target_note_id, target_problem_id,
             token["target_text"], token["alias"], now),
        )
    warnings = []
    if truncated:
        warnings.append(
            f"这篇笔记的链接超过 {NOTE_LINK_MAX} 个，已只识别前 {NOTE_LINK_MAX} 个，其余忽略。"
        )
    return warnings


def _rewrite_title_links(content, old_title, new_title):
    """把正文里指向 old_title 的 [[ ]] 链接改成指向 new_title（别名部分保持不变）。

    代码块/行内代码内的不处理（沿用 _iter_link_tokens 的代码保护）。无改动时原样返回。
    """
    tokens = _iter_link_tokens(content)
    out = []
    cursor = 0
    changed = False
    for token in tokens:
        if token["kind"] == "note" and token["target_text"] == old_title:
            out.append(content[cursor:token["start"]])
            replacement = (
                f"[[{new_title}|{token['alias']}]]" if token["alias"] else f"[[{new_title}]]"
            )
            out.append(replacement)
            cursor = token["end"]
            changed = True
    out.append(content[cursor:])
    return "".join(out) if changed else content


def _rename_title_cascade(conn, user_id, note_id, old_title, new_title):
    """A 改名为 B：把其他笔记正文里的 [[A]] / [[A|别名]] 改写为 [[B]]（别名不变）。

    最多处理 RENAME_CASCADE_MAX 篇，超出截断并给中文 warning；改写后同事务重解析被改笔记的链接。
    """
    warnings = []
    if not old_title or old_title == new_title:
        return warnings
    like = f"%[[{_escape_like(old_title)}%"
    rows = conn.execute(
        "SELECT id, content FROM notes WHERE user_id = ? AND deleted_at IS NULL AND id != ? "
        "AND content LIKE ? ESCAPE '\\' ORDER BY id LIMIT ?",
        (user_id, note_id, like, RENAME_CASCADE_MAX + 1),
    ).fetchall()
    truncated = len(rows) > RENAME_CASCADE_MAX
    now = main.utc_now()
    for row in rows[:RENAME_CASCADE_MAX]:
        new_content = _rewrite_title_links(row["content"], old_title, new_title)
        if new_content == row["content"]:
            continue
        conn.execute(
            "UPDATE notes SET content = ?, updated_at = ? WHERE id = ?",
            (new_content, now, row["id"]),
        )
        _reparse_links(conn, row["id"], user_id, new_content)
    if truncated:
        warnings.append(
            f"标题变更联动改写超过 {RENAME_CASCADE_MAX} 篇笔记，已截断，请手动检查其余笔记。"
        )
    return warnings


def _snippet(content, start, end):
    """引用处前后各 40 字的纯文本片段；折叠空白，不产出 HTML。"""
    left = max(0, start - 40)
    right = min(len(content), end + 40)
    text = re.sub(r"\s+", " ", content[left:right]).strip()
    return text


class NoteCreate(BaseModel):
    title: NoteTitle = ""
    content: NoteContent
    tags: list[NoteTag] = []
    problem_id: int | None = None
    pinned: bool = False


class NoteUpdate(BaseModel):
    title: NoteTitle | None = None
    content: NoteContent | None = None
    tags: list[NoteTag] | None = None
    problem_id: int | None = None
    pinned: bool | None = None


def owned_note(conn, note_id, user_id):
    row = conn.execute(
        """
        SELECT n.*, p.title AS problem_title
        FROM notes n LEFT JOIN problems p ON p.id = n.problem_id
        WHERE n.id = ? AND n.user_id = ? AND n.deleted_at IS NULL
        """,
        (note_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "笔记不存在")
    return note_public(dict(row))


def note_public(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "content": row["content"],
        "tags": [tag for tag in row["tags"].split(",") if tag],
        "problem_id": row["problem_id"],
        "problem_title": row["problem_title"],
        "pinned": bool(row["pinned"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def check_problem_ownership(conn, problem_id, user_id):
    """problem_id 只能关联自己的题目；他人/不存在的题目一律 422。"""
    if problem_id is None:
        return
    row = conn.execute(
        "SELECT id FROM problems WHERE id = ? AND user_id = ?",
        (problem_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(422, "关联的题目不存在或不属于你")


@router.get("/api/notes")
def list_notes(
    q: str | None = None,
    tag: str | None = None,
    problem_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
    user=Depends(main.current_user),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    sql = (
        "SELECT n.*, p.title AS problem_title FROM notes n "
        "LEFT JOIN problems p ON p.id = n.problem_id "
        "WHERE n.user_id = ? AND n.deleted_at IS NULL"
    )
    count_sql = "SELECT COUNT(*) FROM notes n WHERE n.user_id = ? AND n.deleted_at IS NULL"
    params: list = [user["id"]]
    if q:
        like = f"%{_escape_like(q)}%"
        sql += " AND (n.title LIKE ? ESCAPE '\\' OR n.content LIKE ? ESCAPE '\\')"
        count_sql += " AND (n.title LIKE ? ESCAPE '\\' OR n.content LIKE ? ESCAPE '\\')"
        params += [like, like]
    if tag:
        like = f"%,{_escape_like(tag)},%"
        sql += " AND (',' || n.tags || ',') LIKE ? ESCAPE '\\'"
        count_sql += " AND (',' || n.tags || ',') LIKE ? ESCAPE '\\'"
        params.append(like)
    if problem_id is not None:
        sql += " AND n.problem_id = ?"
        count_sql += " AND n.problem_id = ?"
        params.append(problem_id)
    sql += " ORDER BY n.pinned DESC, n.updated_at DESC LIMIT ? OFFSET ?"
    with main.connect() as conn:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(sql, params + [limit, offset]).fetchall()
        # N2：回传本人全部附件 id，前端只把这些 id 的 attachment:ID 渲染成 <img>。
        attachment_ids = [
            row["id"] for row in conn.execute(
                "SELECT id FROM note_attachments WHERE user_id = ? ORDER BY id",
                (user["id"],),
            ).fetchall()
        ]
    return {
        "notes": [note_public(dict(row)) for row in rows],
        "total": total,
        "attachment_ids": attachment_ids,
    }


@router.get("/api/notes/suggest")
def suggest_notes(
    q: str = "",
    limit: int = 20,
    user=Depends(main.current_user),
):
    """供前端输入 [[ 时联想：q 以"题:"开头查本人题目，否则查本人笔记标题；空 q 返回最近更新。"""
    limit = max(1, min(limit, 20))
    q = (q or "").strip()
    with main.connect() as conn:
        if q.startswith("题:"):
            keyword = q[2:].strip()
            if keyword:
                like = f"%{_escape_like(keyword)}%"
                rows = conn.execute(
                    "SELECT id, title FROM problems WHERE user_id = ? AND title LIKE ? ESCAPE '\\' "
                    "ORDER BY id DESC LIMIT ?",
                    (user["id"], like, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title FROM problems WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user["id"], limit),
                ).fetchall()
            return {"results": [{"kind": "problem", "id": row["id"], "title": row["title"]} for row in rows]}
        if q:
            keyword = q.lower()
            rows = conn.execute(
                "SELECT id, title, content FROM notes WHERE user_id = ? AND deleted_at IS NULL "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (user["id"], limit * 8),
            ).fetchall()
            results = []
            for row in rows:
                dt = _display_title(row["title"], row["content"])
                # 在 Python 里做子串匹配，% _ \ 不构成 LIKE 通配，天然无注入。
                if dt and keyword in dt.lower():
                    results.append({"kind": "note", "id": row["id"], "title": dt})
                if len(results) >= limit:
                    break
            return {"results": results}
        rows = conn.execute(
            "SELECT id, title, content FROM notes WHERE user_id = ? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (user["id"], limit),
        ).fetchall()
    return {"results": [{"kind": "note", "id": row["id"],
                         "title": _display_title(row["title"], row["content"])} for row in rows]}


@router.get("/api/notes/graph")
def notes_graph(
    center: int | None = None,
    depth: int = 1,
    user=Depends(main.current_user),
):
    """关系图谱：无参数为全局图；center=笔记id 时为以它为中心 depth(1|2) 层的局部图。

    节点 {id(note:<id>|problem:<id>), type, title, pinned}；边 {source, target}。
    节点超过 GRAPH_NODE_MAX 时按度数（关联数）从高到低截断，中心节点必保留；
    超限时 truncated=true 并给出保留/截断数量。只含本人数据。
    """
    depth = 1 if depth != 2 else 2
    with main.connect() as conn:
        if center is not None:
            owned_note(conn, center, user["id"])
        link_rows = conn.execute(
            """
            SELECT source_note_id, target_note_id, target_problem_id
            FROM note_links
            WHERE user_id = ? AND (target_note_id IS NOT NULL OR target_problem_id IS NOT NULL)
            """,
            (user["id"],),
        ).fetchall()

    edges = set()
    degrees: dict[str, int] = {}
    note_node_ids: set[int] = set()
    problem_node_ids: set[int] = set()
    for row in link_rows:
        source = f"note:{row['source_note_id']}"
        note_node_ids.add(row["source_note_id"])
        targets = []
        if row["target_note_id"]:
            target = f"note:{row['target_note_id']}"
            note_node_ids.add(row["target_note_id"])
            targets.append(target)
        if row["target_problem_id"]:
            target = f"problem:{row['target_problem_id']}"
            problem_node_ids.add(row["target_problem_id"])
            targets.append(target)
        for target in targets:
            if source == target:
                continue  # 自环不画
            edge = (source, target)
            edges.add(edge)

    if center is not None:
        center_id = f"note:{center}"
        within = {center_id}
        frontier = {center_id}
        for _ in range(depth):
            nxt = set()
            for a, b in edges:
                if a in frontier and b not in within:
                    within.add(b)
                    nxt.add(b)
                elif b in frontier and a not in within:
                    within.add(a)
                    nxt.add(a)
            frontier = nxt
        edges = {(a, b) for a, b in edges if a in within and b in within}
        note_node_ids = {int(n.split(":")[1]) for n in within if n.startswith("note:")}
        problem_node_ids = {int(n.split(":")[1]) for n in within if n.startswith("problem:")}

    for a, b in edges:
        degrees[a] = degrees.get(a, 0) + 1
        degrees[b] = degrees.get(b, 0) + 1

    kept_nodes: set[str] = {f"note:{i}" for i in note_node_ids} | {
        f"problem:{i}" for i in problem_node_ids
    }
    truncated = False
    truncated_count = 0
    if len(kept_nodes) > GRAPH_NODE_MAX:
        forced = {f"note:{center}"} if center is not None else set()
        ranked = sorted(
            kept_nodes,
            key=lambda node: (-degrees.get(node, 0), node),
        )
        chosen = set(forced)
        for node in ranked:
            if len(chosen) >= GRAPH_NODE_MAX:
                break
            chosen.add(node)
        dropped = kept_nodes - chosen
        truncated = True
        truncated_count = len(dropped)
        kept_nodes = chosen
        edges = {
            (a, b) for a, b in edges if a in kept_nodes and b in kept_nodes
        }

    nodes: list[dict] = []
    if note_node_ids:
        marks = ",".join("?" for _ in note_node_ids)
        with main.connect() as conn:
            for row in conn.execute(
                f"SELECT id, title, content, pinned FROM notes WHERE user_id = ? AND id IN ({marks}) "
                "AND deleted_at IS NULL",
                (user["id"], *note_node_ids),
            ):
                node_id = f"note:{row['id']}"
                if node_id in kept_nodes:
                    nodes.append({
                        "id": node_id, "type": "note",
                        "title": _display_title(row["title"], row["content"]),
                        "pinned": bool(row["pinned"]),
                    })
    if problem_node_ids:
        marks = ",".join("?" for _ in problem_node_ids)
        with main.connect() as conn:
            for row in conn.execute(
                f"SELECT id, title FROM problems WHERE user_id = ? AND id IN ({marks})",
                (user["id"], *problem_node_ids),
            ):
                node_id = f"problem:{row['id']}"
                if node_id in kept_nodes:
                    nodes.append({"id": node_id, "type": "problem", "title": row["title"], "pinned": False})
    # 中心节点可能因为目标已删除而不在 nodes 里：强制补一个占位。
    if center is not None:
        center_id = f"note:{center}"
        if not any(node["id"] == center_id for node in nodes):
            with main.connect() as conn:
                row = conn.execute(
                    "SELECT id, title, content, pinned FROM notes WHERE user_id = ? AND id = ? AND deleted_at IS NULL",
                    (user["id"], center),
                ).fetchone()
            if row:
                nodes.append({
                    "id": center_id, "type": "note",
                    "title": _display_title(row["title"], row["content"]),
                    "pinned": bool(row["pinned"]),
                })
    return {
        "nodes": nodes,
        "edges": [{"source": a, "target": b} for a, b in edges],
        "truncated": truncated,
        "node_limit": GRAPH_NODE_MAX,
        "node_count": len(nodes),
        "truncated_count": truncated_count,
    }


@router.post("/api/notes", status_code=201)
def create_note(data: NoteCreate, user=Depends(main.current_user)):
    tags = normalize_note_tags(data.tags)
    if _count_attachment_refs(data.content) > NOTE_REF_PER_NOTE_MAX:
        raise HTTPException(422, f"每篇笔记最多引用 {NOTE_REF_PER_NOTE_MAX} 张图片附件")
    now = main.utc_now()
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        count = conn.execute("SELECT COUNT(*) FROM notes WHERE user_id = ?", (user["id"],)).fetchone()[0]
        if count >= NOTE_COUNT_MAX:
            raise HTTPException(422, f"每个账号最多保存 {NOTE_COUNT_MAX} 条笔记（含已删除笔记）")
        check_problem_ownership(conn, data.problem_id, user["id"])
        cursor = conn.execute(
            """
            INSERT INTO notes(user_id, title, content, tags, problem_id, pinned,
                              created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"], data.title, data.content, ",".join(tags),
                data.problem_id, 1 if data.pinned else 0, now, now,
            ),
        )
        note_id = cursor.lastrowid
        warnings = _reparse_links(conn, note_id, user["id"], data.content)
        row = conn.execute(
            """
            SELECT n.*, p.title AS problem_title
            FROM notes n LEFT JOIN problems p ON p.id = n.problem_id
            WHERE n.id = ?
            """,
            (note_id,),
        ).fetchone()
    result = note_public(dict(row))
    result["warnings"] = warnings
    return result


@router.get("/api/notes/{note_id}")
def get_note(note_id: int, user=Depends(main.current_user)):
    with main.connect() as conn:
        return owned_note(conn, note_id, user["id"])


@router.get("/api/notes/{note_id}/links")
def get_note_links(note_id: int, user=Depends(main.current_user)):
    """出链 outgoing 与反向链接 backlinks；每条附引用处前后 40 字的纯文本 snippet。"""
    with main.connect() as conn:
        note = owned_note(conn, note_id, user["id"])
        content = note["content"]
        tokens = _iter_link_tokens(content)
        # (kind, target_text, alias) -> 首个 token 的位置，用于切 snippet。
        token_pos: dict[tuple, dict] = {}
        for token in tokens:
            key = (token["kind"], token["target_text"], token["alias"])
            token_pos.setdefault(key, token)

        link_rows = conn.execute(
            "SELECT * FROM note_links WHERE source_note_id = ? ORDER BY id",
            (note_id,),
        ).fetchall()
        outgoing = []
        for row in link_rows:
            token = token_pos.get(
                (row["link_kind"], row["target_text"], row["alias_text"])
            )
            snippet = _snippet(content, token["start"], token["end"]) if token else ""
            outgoing.append({
                "link_kind": row["link_kind"],
                "target_note_id": row["target_note_id"],
                "target_problem_id": row["target_problem_id"],
                "target_text": row["target_text"],
                "alias": row["alias_text"],
                "exists": row["target_note_id"] is not None or row["target_problem_id"] is not None,
                "title": row["alias_text"] or row["target_text"],
                "snippet": snippet,
            })

        back_rows = conn.execute(
            """
            SELECT n.id, n.title, l.source_note_id, l.target_text, l.alias_text,
                   src.content AS src_content
            FROM note_links l
            JOIN notes n ON n.id = l.source_note_id
            JOIN notes src ON src.id = l.source_note_id
            WHERE l.target_note_id = ? AND n.user_id = ? AND n.deleted_at IS NULL
            AND n.id != ?
            ORDER BY n.updated_at DESC
            """,
            (note_id, user["id"], note_id),
        ).fetchall()
        backlinks = []
        self_title = _display_title(note["title"], note["content"])
        for row in back_rows:
            src_tokens = _iter_link_tokens(row["src_content"])
            match = next(
                (t for t in src_tokens if t["kind"] == "note" and t["target_text"] == self_title),
                None,
            )
            snippet = _snippet(row["src_content"], match["start"], match["end"]) if match else ""
            backlinks.append({
                "source_note_id": row["source_note_id"],
                "title": _display_title(row["title"], row["src_content"]),
                "alias": row["alias_text"],
                "snippet": snippet,
            })

        warnings: list[str] = []
        # 出链 warnings 与保存时一致：按当前正文中的 token 数量判断是否截断。
        if len(tokens) > NOTE_LINK_MAX:
            warnings.append(
                f"这篇笔记的链接超过 {NOTE_LINK_MAX} 个，已只识别前 {NOTE_LINK_MAX} 个，其余忽略。"
            )
    return {"outgoing": outgoing, "backlinks": backlinks, "warnings": warnings}


@router.put("/api/notes/{note_id}")
def update_note(note_id: int, data: NoteUpdate, user=Depends(main.current_user)):
    tags = normalize_note_tags(data.tags) if data.tags is not None else None
    now = main.utc_now()
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        before = owned_note(conn, note_id, user["id"])
        old_title = (before["title"] or "").strip()
        if data.problem_id is not None:
            check_problem_ownership(conn, data.problem_id, user["id"])
        updates: list[str] = []
        params: list = []
        if data.title is not None:
            updates.append("title = ?")
            params.append(data.title)
        if data.content is not None:
            updates.append("content = ?")
            params.append(data.content)
        if data.content is not None and _count_attachment_refs(data.content) > NOTE_REF_PER_NOTE_MAX:
            raise HTTPException(422, f"每篇笔记最多引用 {NOTE_REF_PER_NOTE_MAX} 张图片附件")
        if tags is not None:
            updates.append("tags = ?")
            params.append(",".join(tags))
        if "problem_id" in data.model_fields_set:
            updates.append("problem_id = ?")
            params.append(data.problem_id)
        if data.pinned is not None:
            updates.append("pinned = ?")
            params.append(1 if data.pinned else 0)
        updates.append("updated_at = ?")
        params.append(now)
        params.append(note_id)
        conn.execute(
            f"UPDATE notes SET {', '.join(updates)} WHERE id = ?", params
        )
        warnings: list[str] = []
        # 改标题联动：A→B 时改写其他笔记正文里的 [[A]]（别名不变），再同事务重解析。
        if data.title is not None:
            new_title = (data.title or "").strip()
            warnings.extend(_rename_title_cascade(conn, user["id"], note_id, old_title, new_title))
        # 内容可能变化（含标题联动改写他人笔记后自身链接也需重算）：先删后插重解析本笔记出链。
        fresh = owned_note(conn, note_id, user["id"])
        warnings.extend(_reparse_links(conn, note_id, user["id"], fresh["content"]))
    fresh["warnings"] = warnings
    return fresh


@router.delete("/api/notes/{note_id}")
def delete_note(note_id: int, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        owned_note(conn, note_id, user["id"])
        conn.execute(
            "UPDATE notes SET deleted_at = ? WHERE id = ?",
            (main.utc_now(), note_id),
        )
        # 软删除：别人笔记里指向它的链接正文保留、target 置空（显示为悬空/未创建）。
        conn.execute(
            "UPDATE note_links SET target_note_id = NULL WHERE target_note_id = ?",
            (note_id,),
        )
        # 本笔记自己的出链随软删除作废。
        conn.execute("DELETE FROM note_links WHERE source_note_id = ?", (note_id,))
    return {"ok": True}
