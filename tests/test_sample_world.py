"""The local sample world: a rich, known account that never touches the real database.

The script is always run in a subprocess pointed at a temporary folder, so the
environment variables it sets (database path, blanked AI key, ...) cannot leak into
the other tests.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "sample_world.py"
MAIN, NEWCOMER, ADMIN = "样本同学", "样本新人", "样本管理员"
REAL_DATABASE = b"stand-in for the real notebook.db"

LOGIN_AND_PROBE = r'''
import json, sys
sys.path.insert(0, ".")
import sample_world as sw
sw.configure_environment()
import main
from fastapi.testclient import TestClient

password = sw.read_password()

def login(name):
    main.reset_rate_limits()
    client = TestClient(main.app, headers={"X-CSRF-Protection": "1"})
    assert client.post("/api/auth/login", json={"username": name, "password": password}).status_code == 200, name
    return client

mine, newcomer, admin = login(sw.MAIN), login(sw.NEWCOMER), login(sw.ADMIN)
full_code = next(line for line in open(sw.CREDENTIALS, encoding="utf-8") if sw.FULL_GROUP in line and "已满员" in line).split("：")[-1].strip()
join = newcomer.post("/api/groups/join", json={"invite_code": full_code})
posts = mine.get("/api/posts").json()["posts"]
thread = next(post for post in posts if post["title"].startswith("二分查找的边界"))
detail = mine.get(f"/api/posts/{thread['id']}").json()
board = mine.get("/api/posts", params={"limit": 50}).json()
print(json.dumps({
    "board": {
        "total": board["total"], "counts": board["counts"], "zone_counts": board["zone_counts"],
        "posts": [{key: post[key] for key in ("title", "zone", "solved", "hot", "has_code", "is_mine",
                                                "comment_count", "helpful_total", "participant_count")}
                  for post in board["posts"]],
        "unanswered": [post["title"] for post in mine.get("/api/posts", params={"filter": "unanswered"}).json()["posts"]],
    },
    "groups": [(g["name"], g["member_count"], g["level"]["number"]) for g in mine.get("/api/groups").json()["groups"]],
    "analysis": mine.get("/api/insights/weakness-analysis").json()["status"],
    "ai_attempt": mine.post("/api/insights/weakness-analysis").status_code,
    "newcomer_analysis": newcomer.get("/api/insights/weakness-analysis").json()["status"],
    "newcomer_groups": len(newcomer.get("/api/groups").json()["groups"]),
    "full_group_join": join.status_code,
    "main_is_admin": mine.get("/api/me").json()["is_admin"],
    "admin_is_admin": admin.get("/api/me").json()["is_admin"],
    "admin_reports": len(admin.get("/api/admin/reports").json()["reports"]),
    "thread_solved": thread["solved"],
    "accepted_floor": next(comment["floor"] for comment in detail["comments"]
                           if comment["id"] == detail["accepted_comment_id"]),
    "helpful": {comment["floor"]: [comment["helpful_count"], comment["viewer_helpful"]]
                for comment in detail["comments"]},
    "thread_summary": mine.get(f"/api/posts/{thread['id']}/summary").json()["summary"],
}))
'''


AI_SWITCH_PROBE = r'''
import json, os, sys
sys.path.insert(0, ".")
import sample_world as sw
sw.configure_environment(with_ai=True)
with_ai = {name: os.environ[name] for name in ("OPENAI_API_KEY", "SMTP_HOST", "ALIPAY_APP_ID")}
sw.configure_environment()
print(json.dumps({"with_ai": with_ai, "default": os.environ["OPENAI_API_KEY"]}))
'''


def run_python(folder, *arguments, extra_env=None):
    env = {**os.environ, "SAMPLE_WORLD_DIR": str(folder), "PYTHONIOENCODING": "utf-8", **(extra_env or {})}
    return subprocess.run(
        [sys.executable, "-B", *arguments], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )


def read_password(folder):
    text = (Path(folder) / "sample-account.txt").read_text(encoding="utf-8")
    return re.search(r"^密码[：:]\s*(\S+)", text, re.M).group(1)


def query(folder, sql, *params):
    database = (Path(folder) / "sample.db").as_uri() + "?mode=ro"
    with closing(sqlite3.connect(database, uri=True)) as conn:
        return conn.execute(sql, params).fetchall()


_PROBES = {}


def probe(folder):
    """Run the login probe once per sample world (it logs in as three accounts)."""
    if folder not in _PROBES:
        result = run_python(folder, "-c", LOGIN_AND_PROBE)
        assert result.returncode == 0, result.stdout + result.stderr
        _PROBES[folder] = json.loads(result.stdout.strip().splitlines()[-1])
    return _PROBES[folder]


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sample-world")
    (folder / "notebook.db").write_bytes(REAL_DATABASE)
    result = run_python(folder, str(SCRIPT), "--build-only")
    assert result.returncode == 0, result.stdout + result.stderr
    return folder, result


def test_password_lives_in_the_credentials_file_and_is_never_printed(world):
    folder, result = world
    password = read_password(folder)
    assert re.fullmatch(r"yangben-[a-z2-9]{6}", password)
    assert password not in result.stdout and password not in result.stderr
    text = (folder / "sample-account.txt").read_text(encoding="utf-8")
    for name in (MAIN, NEWCOMER, ADMIN, "http://127.0.0.1:8001", "sample-invite"):
        assert name in text


def test_notes_list_the_current_invite_codes_and_explain_the_ai_switch(world):
    folder, _ = world
    text = (folder / "sample-account.txt").read_text(encoding="utf-8")
    codes = dict(query(folder, "SELECT name, invite_code FROM study_groups WHERE name IN ('夜读小组', '满分俱乐部')"))
    assert set(codes) == {"夜读小组", "满分俱乐部"}
    for name, code in codes.items():
        assert re.search(rf"{name}.*：{code}\s*$", text, re.M), name
    assert "AI 分析：默认关闭" in text and "--with-ai" in text


def test_with_ai_keeps_the_key_while_mail_and_payments_stay_off(world):
    folder, _ = world
    result = run_python(folder, "-c", AI_SWITCH_PROBE, extra_env={
        "OPENAI_API_KEY": "fake-key-for-the-test", "SMTP_HOST": "smtp.example.com", "ALIPAY_APP_ID": "fake-app"})
    assert result.returncode == 0, result.stdout + result.stderr
    facts = json.loads(result.stdout.strip().splitlines()[-1])
    assert facts["with_ai"] == {"OPENAI_API_KEY": "fake-key-for-the-test", "SMTP_HOST": "", "ALIPAY_APP_ID": ""}
    assert facts["default"] == "", "without --with-ai the AI key is blanked, whatever .env says"


def test_the_real_database_is_not_touched(world):
    folder, _ = world
    assert (folder / "notebook.db").read_bytes() == REAL_DATABASE
    assert (folder / "sample.db").is_file()


def test_main_account_has_plenty_of_mistakes_and_four_groups(world):
    folder, _ = world
    mistakes = query(
        folder,
        "SELECT p.zone, m.due_date FROM mistakes m JOIN problems p ON p.id = m.problem_id "
        "JOIN users u ON u.id = p.user_id WHERE u.username = ?", MAIN)
    assert len(mistakes) >= 30
    assert len({zone for zone, _ in mistakes}) >= 7
    soon = (date.today() + timedelta(days=1)).isoformat()
    assert sum(1 for _, due in mistakes if due <= soon) >= 15, "the review page needs plenty of due cards"
    groups = query(
        folder,
        "SELECT g.name, (SELECT COUNT(*) FROM study_group_members x WHERE x.group_id = g.id) "
        "FROM study_groups g JOIN study_group_members m ON m.group_id = g.id "
        "JOIN users u ON u.id = m.user_id WHERE u.username = ?", MAIN)
    assert len(groups) == 4, "one of the five group slots must stay free for create / join tests"
    assert sorted(count for _, count in groups)[-1] == 10, "one group must be full"
    assert query(folder, "SELECT COUNT(*) FROM weakness_insights WHERE user_id = "
                         "(SELECT id FROM users WHERE username = ?)", MAIN) == [(1,)]


def test_main_account_has_saved_mistake_topics_with_real_members(world):
    folder, _ = world
    saved = query(folder, "SELECT content FROM mistake_clusters WHERE user_id = "
                          "(SELECT id FROM users WHERE username = ?)", MAIN)
    assert len(saved) == 1
    report = json.loads(saved[0][0])
    assert len(report["clusters"]) >= 3
    source = {
        mistake_id: (problem_id, title, zone, description, due_date, problem_created_at)
        for mistake_id, problem_id, title, zone, description, due_date, problem_created_at in query(
            folder, "SELECT m.id, p.id, p.title, p.zone, m.description, m.due_date, p.created_at "
                    "FROM mistakes m JOIN problems p ON p.id = m.problem_id "
                    "JOIN users u ON u.id = p.user_id WHERE u.username = ?", MAIN)
    }
    seen = set()
    for cluster in report["clusters"]:
        assert 2 <= len(cluster["members"]) <= 8
        for member in cluster["members"]:
            mistake_id = member["mistake_id"]
            assert mistake_id in source and mistake_id not in seen
            seen.add(mistake_id)
            assert (member["problem_id"], member["title"], member["zone"],
                    member["description"], member["due_date"], member["problem_created_at"]) == source[mistake_id]
    assert report["sample"]["mistake_count"] == len(source)


def test_main_account_has_tags_and_forum_emoji(world):
    folder, _ = world
    kinds = query(folder, "SELECT COUNT(DISTINCT t.tag) FROM mistake_tags t JOIN users u ON u.id = t.user_id "
                          "WHERE u.username = ?", MAIN)
    assert kinds[0][0] >= 5, "the tag filter needs several kinds to show"
    tagged = query(folder, "SELECT COUNT(DISTINCT t.mistake_id) FROM mistake_tags t JOIN users u ON u.id = t.user_id "
                           "WHERE u.username = ?", MAIN)
    assert tagged[0][0] >= 15
    assert query(folder, "SELECT COUNT(*) FROM post_comments WHERE body LIKE '%👍%' OR body LIKE '%🙌%'")[0][0] >= 2


def test_newcomer_and_admin_cover_the_empty_and_privileged_states(world):
    folder, _ = world
    assert query(folder, "SELECT COUNT(*) FROM mistakes m JOIN problems p ON p.id = m.problem_id "
                         "JOIN users u ON u.id = p.user_id WHERE u.username = ?", NEWCOMER) == [(4,)]
    assert query(folder, "SELECT COUNT(*) FROM study_group_members m JOIN users u ON u.id = m.user_id "
                         "WHERE u.username = ?", NEWCOMER) == [(0,)]
    assert query(folder, "SELECT COUNT(*) FROM users WHERE username = ?", ADMIN) == [(1,)]
    assert query(folder, "SELECT COUNT(*) FROM users") == [(25,)]


def test_forum_has_floors_quotes_a_deleted_floor_and_a_report(world):
    folder, _ = world
    comments = query(folder, "SELECT id, reply_to_id, deleted_at FROM post_comments ORDER BY id")
    assert any(reply is not None for _, reply, _ in comments)
    deleted = {comment_id for comment_id, _, gone in comments if gone}
    assert deleted, "a deleted floor shows how quotes of removed comments look"
    assert any(reply in deleted for _, reply, _ in comments)
    assert query(folder, "SELECT COUNT(*) FROM posts") == [(8,)]
    assert query(folder, "SELECT COUNT(*) FROM post_comments WHERE post_id = "
                         "(SELECT id FROM posts WHERE title LIKE '二分查找的边界%')") == [(8,)]


def test_forum_board_home_shows_every_state_and_zone(world):
    folder, _ = world
    zones = dict(query(folder, "SELECT title, zone FROM posts"))
    assert zones["二分查找的边界总写错，大家是怎么自查的？"] == "算法"
    assert zones["我整理了一份二分查找自查清单"] == "算法"
    assert zones["整理了一份数据库索引失效的清单"] == "数据库"
    assert {zones[title] for title in zones if title.startswith(("CSS Grid", "条件概率", "短链", "矩阵"))} == {
        "前端", "概率统计", "系统设计", "线性代数",
    }
    assert None not in zones.values()
    # 时间要有层次：几小时前到五周前
    ages = query(folder, "SELECT (julianday('now') - julianday(created_at)) * 24 FROM posts ORDER BY 1")
    assert ages[0][0] < 6 and ages[-1][0] > 24 * 30


def test_forum_board_api_view_of_the_sample_data(world):
    folder, _ = world
    facts = probe(folder)["board"]
    posts = {post["title"][:6]: post for post in facts["posts"]}
    assert facts["total"] == 8 and facts["counts"]["all"] == 8
    assert facts["zone_counts"] == {"算法": 3, "数据库": 1, "前端": 1, "概率统计": 1, "系统设计": 1, "线性代数": 1}
    window = next(post for post in facts["posts"] if post["title"].startswith("滑动窗口"))
    assert window["solved"] and window["hot"] and window["has_code"]
    assert window["comment_count"] >= 6 and window["helpful_total"] >= 5
    assert facts["unanswered"] and all(title for title in facts["unanswered"])
    assert any(post["title"].startswith("短链") for post in facts["posts"])
    assert next(p for p in facts["posts"] if p["title"].startswith("短链"))["comment_count"] == 0
    assert sum(post["is_mine"] for post in facts["posts"]) == 2   # 样本同学自己发的两个帖子
    assert facts["counts"]["mine"] == 2 and facts["counts"]["unanswered"] == len(facts["unanswered"])
    assert posts


def test_forum_has_an_accepted_checklist_votes_and_a_fresh_manual_summary(world):
    from thread_summary import thread_signature

    folder, _ = world
    with closing(sqlite3.connect((folder / "sample.db").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        post = dict(conn.execute("SELECT * FROM posts WHERE title LIKE '二分查找的边界%'").fetchone())
        comments = [
            {**dict(row), "floor": floor}
            for floor, row in enumerate(conn.execute(
                "SELECT c.*, u.username FROM post_comments c JOIN users u ON u.id = c.user_id "
                "WHERE c.post_id = ? ORDER BY c.id", (post["id"],),
            ), 1)
        ]
        assert post["accepted_comment_id"] == comments[1]["id"]
        assert comments[1]["username"] == "苏晚" and "检查清单" in comments[1]["body"]
        votes = conn.execute(
            "SELECT c.id, u.username FROM comment_votes v JOIN post_comments c ON c.id = v.comment_id "
            "JOIN users u ON u.id = v.user_id WHERE c.post_id = ? ORDER BY c.id, u.username",
            (post["id"],),
        ).fetchall()
        assert {comment["floor"]: sorted(row["username"] for row in votes if row["id"] == comment["id"])
                for comment in comments if any(row["id"] == comment["id"] for row in votes)} == {
            1: ["苏晚", "许朝"],
            2: ["周知远", "样本同学", "陈一鸣"],
            5: ["周知远"],
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM comment_votes v JOIN post_comments c ON c.id = v.comment_id "
            "WHERE v.user_id = c.user_id",
        ).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM post_summaries").fetchone()[0] == 1
        cached = conn.execute("SELECT * FROM post_summaries WHERE post_id = ?", (post["id"],)).fetchone()
        assert cached["signature"] == thread_signature(post, comments)
        assert cached["comment_count"] == 7
        content = json.loads(cached["content"])
        assert set(content) == {"tldr", "points", "open_questions"}
        assert content["tldr"].startswith("（样本）")
        assert [point["floors"] for point in content["points"]] == [[1, 2], [2], [2, 5, 6]]
        assert len(content["open_questions"]) == 1
        assert conn.execute("SELECT COUNT(*) FROM ai_calls WHERE feature = 'thread_summary'").fetchone()[0] == 0


def test_accounts_log_in_with_the_shared_password_and_nothing_can_spend_money(world):
    folder, _ = world
    facts = probe(folder)
    assert len(facts["groups"]) == 4
    assert {name: level for name, _, level in facts["groups"]}["满分俱乐部"] == 8
    assert facts["analysis"] == "ready"
    assert facts["ai_attempt"] == 503, "the AI key must be blank in the sample world"
    assert facts["newcomer_analysis"] == "insufficient_data"
    assert facts["newcomer_groups"] == 0
    assert facts["full_group_join"] == 403
    assert facts["main_is_admin"] is False and facts["admin_is_admin"] is True
    assert facts["admin_reports"] == 1
    assert facts["thread_solved"] is True and facts["accepted_floor"] == 2
    assert facts["helpful"] == {
        "1": [2, False], "2": [3, True], "4": [0, False], "5": [1, False],
        "6": [0, False], "7": [0, False], "8": [0, False],
    }
    summary = facts["thread_summary"]
    assert set(summary) == {"tldr", "points", "open_questions", "generated_at", "comment_count", "stale"}
    assert summary["stale"] is False and summary["comment_count"] == 7
    assert summary["tldr"].startswith("（样本）")
    assert [point["floors"] for point in summary["points"]] == [[1, 2], [2], [2, 5, 6]]
    assert len(summary["open_questions"]) == 1


def test_a_second_run_keeps_the_data_and_reset_keeps_the_password(world):
    folder, _ = world
    password = read_password(folder)
    before = (folder / "sample.db").stat().st_mtime_ns
    # Without --reset an existing sample world is left alone.
    assert run_python(folder, str(SCRIPT), "--build-only").returncode == 0
    assert (folder / "sample.db").stat().st_mtime_ns == before
    # With --reset everything is rebuilt, but both of us keep knowing the password.
    result = run_python(folder, str(SCRIPT), "--reset", "--build-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert read_password(folder) == password
    assert (folder / "sample.db").stat().st_mtime_ns != before
    assert (folder / "notebook.db").read_bytes() == REAL_DATABASE
    assert query(folder, "SELECT COUNT(*) FROM users") == [(25,)]
