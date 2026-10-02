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
print(json.dumps({
    "groups": [(g["name"], g["member_count"], g["level"]["number"]) for g in mine.get("/api/groups").json()["groups"]],
    "analysis": mine.get("/api/insights/weakness-analysis").json()["status"],
    "ai_attempt": mine.post("/api/insights/weakness-analysis").status_code,
    "newcomer_analysis": newcomer.get("/api/insights/weakness-analysis").json()["status"],
    "newcomer_groups": len(newcomer.get("/api/groups").json()["groups"]),
    "full_group_join": join.status_code,
    "main_is_admin": mine.get("/api/me").json()["is_admin"],
    "admin_is_admin": admin.get("/api/me").json()["is_admin"],
    "admin_reports": len(admin.get("/api/admin/reports").json()["reports"]),
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
        mistake_id: (problem_id, title, zone, description, due_date)
        for mistake_id, problem_id, title, zone, description, due_date in query(
            folder, "SELECT m.id, p.id, p.title, p.zone, m.description, m.due_date "
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
                    member["description"], member["due_date"]) == source[mistake_id]
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
    assert query(folder, "SELECT COUNT(*) FROM posts") == [(3,)]
    assert query(folder, "SELECT COUNT(*) FROM post_comments WHERE post_id = "
                         "(SELECT id FROM posts WHERE title LIKE '二分查找的边界%')") == [(8,)]


def test_accounts_log_in_with_the_shared_password_and_nothing_can_spend_money(world):
    folder, _ = world
    result = run_python(folder, "-c", LOGIN_AND_PROBE)
    assert result.returncode == 0, result.stdout + result.stderr
    facts = json.loads(result.stdout.strip().splitlines()[-1])
    assert len(facts["groups"]) == 4
    assert {name: level for name, _, level in facts["groups"]}["满分俱乐部"] == 8
    assert facts["analysis"] == "ready"
    assert facts["ai_attempt"] == 503, "the AI key must be blank in the sample world"
    assert facts["newcomer_analysis"] == "insufficient_data"
    assert facts["newcomer_groups"] == 0
    assert facts["full_group_join"] == 403
    assert facts["main_is_admin"] is False and facts["admin_is_admin"] is True
    assert facts["admin_reports"] == 1


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
