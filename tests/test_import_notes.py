import sqlite3
from pathlib import Path

import pytest

import import_notes
from db import connect, init_db
from scheduler import today_in_timezone


def note(title="A + B", escaped=False):
    fence = "\\`\\`\\`" if escaped else "```"
    return (
        f"## 题目 1：{title}\n\n"
        "**题目描述**\n输入两个整数，输出它们的和。\n\n"
        "**思路**\n读取并相加。\n\n"
        f"**代码**\n{fence}cpp\n"
        "// ## 题目 99：代码里的内容不应被解析\n"
        "int main() {\n    return 0;\n}\n"
        f"{fence}\n\n**易错点 / 注意点**\n"
        "- 忘记多组输入。\n  注意循环读入。\n\n"
        "- 忽略整数溢出。\n"
    )


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    init_db()
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO users(username,password_hash,timezone,created_at) "
            "VALUES (?, 'unused', 'Asia/Shanghai', '2026-09-20')",
            [("alice",), ("bob",)],
        )
    return path


def test_actual_structure_and_escaped_fences(tmp_path):
    path = tmp_path / "day1.md"
    path.write_text(
        "# Day 1\n## 今日概览\n- 不导入\n" + note(escaped=True)
        + "## 知识点 2：列表\n**易错点**\n- 不导入\n"
        + "## 今日总结\n- 不导入\n> ## 题目 3：模板说明\n",
        encoding="utf-8",
    )
    records, skipped = import_notes.parse_file(path)
    assert len(records) == 1
    assert records[0]["title"] == "A + B"
    assert records[0]["language"] == "C++"
    assert "    return 0;" in records[0]["code"]
    assert records[0]["mistakes"] == [
        "忘记多组输入。\n  注意循环读入。", "忽略整数溢出。"
    ]
    assert skipped == ["知识点 2：列表"]


def test_dry_run_dedupe_and_user_isolation(tmp_path, database):
    path = tmp_path / "day1.md"
    path.write_text(note(), encoding="utf-8")
    args = ["--database", str(database), "--username", "alice", str(path)]
    assert import_notes.main(["--dry-run", *args]) == 0
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM problems").fetchone()[0] == 0
    assert import_notes.main(args) == 0
    assert import_notes.main(args) == 0
    args[3] = "bob"
    assert import_notes.main(args) == 0
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM problems").fetchone()[0] == 2
        rows = conn.execute("SELECT * FROM mistakes").fetchall()
        assert len(rows) == 4
        assert all(row["version"] == 0 for row in rows)
        assert all(row["ease_factor"] == 2.5 for row in rows)
        assert all(
            row["due_date"] == today_in_timezone("Asia/Shanghai").isoformat()
            for row in rows
        )


def test_bad_second_problem_rolls_back_whole_file(tmp_path, database):
    path = tmp_path / "day2.md"
    path.write_text(
        note() + "## 题目 2：缺字段\n**思路**\n没有代码。\n",
        encoding="utf-8",
    )
    assert import_notes.main(["--username", "alice", str(path)]) == 1
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM problems").fetchone()[0] == 0


def test_missing_thinking_is_explicit(tmp_path):
    path = tmp_path / "day1.md"
    path.write_text(
        note().replace("**思路**\n读取并相加。\n\n", ""),
        encoding="utf-8",
    )
    records, _ = import_notes.parse_file(path)
    assert records[0]["fallback"] is True
    assert "原笔记未记录思路" in records[0]["thinking"]


def test_dry_run_does_not_create_missing_database(tmp_path, monkeypatch):
    path = tmp_path / "day1.md"
    path.write_text(note(), encoding="utf-8")
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("DATABASE_PATH", str(missing))
    assert import_notes.main(
        ["--dry-run", "--username", "alice", str(path)]
    ) == 2
    assert not missing.exists()
