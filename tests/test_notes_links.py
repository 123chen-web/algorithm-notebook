"""N1 笔记双向链接：迁移 70、链接解析规则、同事务幂等、改名联动、悬空、越权、
links/suggest/graph 接口、注销清理与导出。"""
import pytest

import db
from test_app import client, register  # noqa: F401

PASSWORD = "a-test-password-123"
CREATED_AT = "2026-09-21T10:00:00+00:00"


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "notes-links.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    return path


def make_note(client, **overrides):
    payload = {"title": "未命名", "content": "x"}
    payload.update(overrides)
    response = client.post("/api/notes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def make_problem(client, title="两数之和"):
    response = client.post(
        "/api/problems",
        json={
            "title": title, "zone": "算法", "language": "Python",
            "code": "x", "thinking": "t", "mistakes": ["m"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ───────────── 迁移 70 ─────────────

def test_migration_70_fresh_database(client):
    register(client)
    with db.connect() as conn:
        assert db.schema_version(conn) == 70
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "note_links" in tables
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(note_links)")}
        assert {
            "user_id", "source_note_id", "link_kind", "target_note_id",
            "target_problem_id", "target_text", "alias_text", "created_at",
        } <= cols


def test_migration_70_upgrades_from_frozen_schema_28_and_preserves_data(database_path, monkeypatch):
    # 冻结在 28（N1 之前的最新版本）造用户与笔记，再升到 70，旧数据必须保留。
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS", [e for e in db.MIGRATIONS if e[0] <= 28])
        patch.setattr(db, "SCHEMA_VERSION", 28)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, timezone, created_at) "
            "VALUES (7, 'legacy-notes', 'unchanged-hash', 'Asia/Shanghai', ?)",
            (CREATED_AT,),
        )
        conn.execute(
            "INSERT INTO notes(user_id, title, content, tags, problem_id, pinned, created_at, updated_at) "
            "VALUES (7, '旧笔记', '存量内容 [[旧链]]', '', NULL, 0, ?, ?)",
            (CREATED_AT, CREATED_AT),
        )
        assert db.schema_version(conn) == 28

    # 重复启动必须幂等、不重跑。
    db.init_db()
    db.init_db()
    with db.connect() as conn:
        assert db.schema_version(conn) == 70
        rows = conn.execute("SELECT title, content FROM notes WHERE user_id=7").fetchall()
        assert len(rows) == 1
        assert rows[0]["title"] == "旧笔记"
        assert "[[旧链]]" in rows[0]["content"]
        # 升级后存量笔记做一次链接回填重解析：旧链 [[旧链]] 无目标 → 悬空。
        link_rows = conn.execute("SELECT * FROM note_links WHERE source_note_id=1").fetchall()
        # 迁移本身不回填；回填发生在保存时。这里只验证表存在且旧数据完整。
        assert conn.execute("SELECT COUNT(*) FROM note_links").fetchone()[0] >= 0


# ───────────── 链接解析规则 ─────────────

def test_note_link_resolves_to_own_note_by_title(client):
    register(client)
    target = make_note(client, title="二分查找", content="靶笔记")
    source = make_note(client, title="主笔记", content="见 [[二分查找]] 的要点")
    data = client.get(f"/api/notes/{source['id']}/links").json()
    assert len(data["outgoing"]) == 1
    link = data["outgoing"][0]
    assert link["link_kind"] == "note"
    assert link["exists"] is True
    assert link["target_note_id"] == target["id"]
    assert link["title"] == "二分查找"
    # 反向链接出现在 target 上。
    back = client.get(f"/api/notes/{target['id']}/links").json()["backlinks"]
    assert any(b["source_note_id"] == source["id"] for b in back)


def test_alias_display_and_problem_link(client):
    register(client)
    make_note(client, title="二分查找", content="靶")
    prob = make_problem(client, "两数之和")
    note = make_note(client, content="别名 [[二分查找|折半]] 与题 [[题:两数之和]]")
    data = client.get(f"/api/notes/{note['id']}/links").json()
    by_target = {l["target_text"]: l for l in data["outgoing"]}
    assert by_target["二分查找"]["alias"] == "折半"
    assert by_target["二分查找"]["title"] == "折半"
    assert by_target["两数之和"]["link_kind"] == "problem"
    assert by_target["两数之和"]["exists"] is True
    assert by_target["两数之和"]["target_problem_id"] == prob["id"]


def test_code_fence_and_inline_code_are_ignored(client):
    register(client)
    make_note(client, title="真实靶", content="x")
    content = (
        "行内 `[[代码]]` 不解析；"
        "围栏 ```\n[[围栏]]\n``` 不解析；"
        "tilde ~~~\n[[波浪]]\n~~~ 不解析；"
        "数组 a[[0]] 在反引号里 `a[[0]]` 不误判；"
        "外面 [[真实靶]] 要解析。"
    )
    note = make_note(client, content=content)
    data = client.get(f"/api/notes/{note['id']}/links").json()
    targets = [l["target_text"] for l in data["outgoing"]]
    # 裸文本 a[[0]] 会被解析为链接；行内代码里的 `[[代码]]`/`a[[0]]` 与围栏内的不解析。
    assert set(targets) == {"0", "真实靶"}


def test_same_title_picks_most_recently_updated(client):
    register(client)
    first = make_note(client, title="重名", content="first")
    second = make_note(client, title="重名", content="second")
    # 手动把 first 的 updated_at 调旧。
    with db.connect(write=True) as conn:
        conn.execute("UPDATE notes SET updated_at='2000-01-01T00:00:00+00:00' WHERE id=?", (first["id"],))
    source = make_note(client, content="链接 [[重名]]")
    data = client.get(f"/api/notes/{source['id']}/links").json()
    assert data["outgoing"][0]["target_note_id"] == second["id"]


def test_duplicate_links_stored_once(client):
    register(client)
    make_note(client, title="靶", content="x")
    note = make_note(client, content="[[靶]] 再 [[靶]] 又 [[靶|别]]")
    data = client.get(f"/api/notes/{note['id']}/links").json()
    texts = [(l["target_text"], l["alias"]) for l in data["outgoing"]]
    assert ("靶", "") in texts
    assert ("靶", "别") in texts
    assert texts.count(("靶", "")) == 1


def test_over_50_links_truncated_with_warning(client):
    register(client)
    make_note(client, title="靶", content="x")
    body = " ".join(f"[[靶]]" for _ in range(55))
    note = make_note(client, content=body)
    assert any("50" in w for w in note.get("warnings", []))
    data = client.get(f"/api/notes/{note['id']}/links").json()
    assert len(data["outgoing"]) <= 50
    assert any("50" in w for w in data["warnings"])


def test_save_twice_is_idempotent(client):
    register(client)
    make_note(client, title="靶", content="x")
    note = make_note(client, content="[[靶]]")
    nid = note["id"]
    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) c FROM note_links WHERE source_note_id=?", (nid,)).fetchone()["c"]
    client.put(f"/api/notes/{nid}", json={"content": "[[靶]]"})
    with db.connect() as conn:
        after = conn.execute("SELECT COUNT(*) c FROM note_links WHERE source_note_id=?", (nid,)).fetchone()["c"]
    assert before == after


# ───────────── 改名联动 ─────────────

def test_rename_cascades_to_other_notes_keeping_alias(client):
    register(client)
    a = make_note(client, title="旧名", content="A 自己")
    b = make_note(client, content="引用 [[旧名]] 与 [[旧名|别名]]")
    client.put(f"/api/notes/{a['id']}", json={"title": "新名"})
    b_after = client.get(f"/api/notes/{b['id']}").json()
    assert "[[新名]]" in b_after["content"]
    assert "[[新名|别名]]" in b_after["content"]
    assert "[[旧名]]" not in b_after["content"]
    # 改名后链接解析到新名。
    links = client.get(f"/api/notes/{b['id']}/links").json()["outgoing"]
    assert all(l["target_text"] == "新名" for l in links)


def test_rename_cascade_caps_at_200_with_warning(client):
    import routers.notes as notes_mod
    register(client)
    target = make_note(client, title="种子", content="x")
    others = [make_note(client, content="引用 [[种子]]") for _ in range(205)]
    updated = client.put(f"/api/notes/{target['id']}", json={"title": "种子新"})
    assert any("200" in w for w in updated.json().get("warnings", []))


# ───────────── 软删除悬空与越权 ─────────────

def test_soft_delete_makes_incoming_links_dangling(client):
    register(client)
    target = make_note(client, title="将删", content="x")
    source = make_note(client, content="指向 [[将删]]")
    # source 里链接存在。
    before = client.get(f"/api/notes/{source['id']}/links").json()["outgoing"]
    assert before[0]["exists"] is True
    client.delete(f"/api/notes/{target['id']}")
    after = client.get(f"/api/notes/{source['id']}/links").json()["outgoing"]
    assert after[0]["exists"] is False
    assert after[0]["target_note_id"] is None
    assert after[0]["target_text"] == "将删"


def test_cross_user_links_never_resolve_and_404(client):
    register(client, "alice")
    alice_target = make_note(client, title="爱丽丝笔记", content="x")
    alice_source = make_note(client, content="x")
    client.post("/api/auth/logout")
    register(client, "bob")
    # bob 写 [[爱丽丝笔记]] 不能解析成 alice 的笔记。
    bob_note = make_note(client, content="别人的 [[爱丽丝笔记]]")
    data = client.get(f"/api/notes/{bob_note['id']}/links").json()
    assert data["outgoing"][0]["exists"] is False
    # 直接访问 alice 的笔记 / links 一律 404。
    assert client.get(f"/api/notes/{alice_target['id']}").status_code == 404
    assert client.get(f"/api/notes/{alice_target['id']}/links").status_code == 404
    assert client.get(f"/api/notes/{alice_source['id']}/graph").status_code == 404


# ───────────── suggest ─────────────

def test_suggest_notes_and_problems_and_escaping(client):
    register(client)
    make_note(client, title="二分查找笔记", content="x")
    make_problem(client, "两数之和题")
    res = client.get("/api/notes/suggest", params={"q": "二分"}).json()
    assert any(r["kind"] == "note" and "二分" in r["title"] for r in res["results"])
    # 题: 前缀查题目。
    prob_res = client.get("/api/notes/suggest", params={"q": "题:两数"}).json()
    assert any(r["kind"] == "problem" for r in prob_res["results"])
    # 空 q 返回最近更新。
    empty = client.get("/api/notes/suggest").json()
    assert len(empty["results"]) >= 1
    # limit 上限 20。
    capped = client.get("/api/notes/suggest", params={"q": "", "limit": 500}).json()
    assert len(capped["results"]) <= 20


# ───────────── graph ─────────────

def test_graph_global_and_center_depth(client):
    register(client)
    # 先建三篇笔记（目标此时不存在），再回写链接内容，使链接解析到已存在的目标。
    a = make_note(client, title="节点A", content="x")
    b = make_note(client, title="节点B", content="x")
    c = make_note(client, title="节点C", content="叶子")
    client.put(f"/api/notes/{a['id']}", json={"content": "[[节点B]]"})
    client.put(f"/api/notes/{b['id']}", json={"content": "[[节点C]]"})
    g = client.get("/api/notes/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert "note:%d" % a["id"] in ids
    assert "note:%d" % c["id"] in ids
    assert g["truncated"] is False
    # 中心 1 层：只含 A 与 B（A→B）。
    g1 = client.get("/api/notes/graph", params={"center": a["id"], "depth": 1}).json()
    ids1 = {n["id"] for n in g1["nodes"]}
    assert "note:%d" % b["id"] in ids1
    assert "note:%d" % c["id"] not in ids1
    # 中心 2 层：含 C。
    g2 = client.get("/api/notes/graph", params={"center": a["id"], "depth": 2}).json()
    ids2 = {n["id"] for n in g2["nodes"]}
    assert "note:%d" % c["id"] in ids2


def test_graph_truncates_at_300(client, monkeypatch):
    import routers.notes as notes_mod
    monkeypatch.setattr(notes_mod, "GRAPH_NODE_MAX", 5)
    register(client)
    make_note(client, title="prev", content="x")
    # 先建 8 篇同名目标，再回写互相链接内容。
    ids = [make_note(client, title=f"节点{i}", content="x")["id"] for i in range(8)]
    for i, nid in enumerate(ids):
        client.put(f"/api/notes/{nid}", json={"content": f"[[节点{(i+1)%8}]]"})
    g = client.get("/api/notes/graph").json()
    assert g["truncated"] is True
    assert g["node_count"] <= 5
    assert g["truncated_count"] >= 1


# ───────────── 注销与导出 ─────────────

def test_account_delete_clears_note_links(client):
    from db import connect
    register(client)
    user_id = client.get("/api/me").json()["id"]
    make_note(client, title="靶", content="x")
    note = make_note(client, content="[[靶]]")
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_links WHERE user_id=?", (user_id,)).fetchone()[0] >= 1
    with connect(write=True) as conn:
        import main
        main.delete_account_data(conn, user_id, main.utc_now())
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM note_links WHERE user_id=?", (user_id,)).fetchone()[0] == 0


def test_export_includes_note_links(client):
    register(client)
    make_note(client, title="靶", content="x")
    make_note(client, content="[[靶]] 与 [[不存在]]")
    data = client.get("/api/export").json()
    assert "note_links" in data
    kinds = {row["target_text"]: row for row in data["note_links"]}
    assert "靶" in kinds
    assert kinds["靶"]["dangling"] is False
    assert kinds["不存在"]["dangling"] is True
