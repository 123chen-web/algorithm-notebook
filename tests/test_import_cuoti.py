import pytest

import import_notes
from db import connect, init_db

FENCE = "```"


def cuoti(wrong=True, takeaway="多条语句要一起生效就必须加花括号。", date="2026-09-23"):
    wrong_block = (
        f"## 我的错误代码\n\n提交结果：TLE\n\n{FENCE}cpp\nint main() {{ return 1; }}\n{FENCE}\n\n"
        if wrong else "## 我当时的思路（卡住的地方）\n\n这题当时没做出来。\n\n"
    )
    takeaway_block = f"## 关键收获\n\n{takeaway}\n" if takeaway else ""
    return (
        "# B3625 迷宫寻路\n\n**标签**：BFS、语法坑\n\n"
        + (f"**记录日期**：{date}\n\n" if date else "")
        + "## 题目来源与链接\n\n洛谷 B3625：https://www.luogu.com.cn/problem/B3625\n\n"
        "## 题目大意\n\n问能不能从左上走到右下。\n\n"
        + wrong_block
        + "## 错误原因分析\n\n缺少花括号。\n\n"
        f"## 正确代码\n\n{FENCE}cpp\nint main() {{ return 0; }}\n{FENCE}\n\n"
        + takeaway_block
    )


def parse(tmp_path, text):
    path = tmp_path / "B3625.md"
    path.write_text(text, encoding="utf-8")
    return import_notes.parse_file(path)


def test_cuoti_file_becomes_one_record(tmp_path):
    records, skipped = parse(tmp_path, cuoti())
    assert skipped == [] and len(records) == 1
    record = records[0]
    assert record["title"] == "B3625 迷宫寻路"
    assert record["language"] == "C++"
    # 复习时看到的是"我当时写的"代码，正确代码单独放进 correct_code。
    assert "return 1;" in record["code"] and record["code"].endswith("\n")
    assert record["mistakes"] == ["多条语句要一起生效就必须加花括号。"]
    assert record["created_at"] == "2026-09-23T12:00:00+00:00"
    for expected in ("标签：BFS、语法坑", "【题目大意】", "【来源】", "【错误原因】"):
        assert expected in record["thinking"]
    assert "【正确代码】" not in record["thinking"]
    assert "return 0;" in record["correct_code"]
    assert record["fallback"] is False


def test_without_wrong_code_uses_correct_code_and_keeps_thinking(tmp_path):
    records, _ = parse(tmp_path, cuoti(wrong=False))
    record = records[0]
    assert "return 0;" in record["code"]
    assert "【当时的思路】" in record["thinking"]
    assert "【正确代码】" not in record["thinking"]


def test_missing_date_leaves_created_at_to_the_importer(tmp_path):
    records, _ = parse(tmp_path, cuoti(date=""))
    assert "created_at" not in records[0]


def test_missing_takeaway_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="关键收获"):
        parse(tmp_path, cuoti(takeaway=""))


def test_day_format_files_are_not_mistaken_for_cuoti(tmp_path):
    assert not import_notes.is_cuoti_format("## 题目 1：A + B\n\n**易错点**\n- x\n")


def test_import_writes_problem_with_note_date(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    init_db()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username,password_hash,timezone,created_at) "
            "VALUES ('alice', 'unused', 'Asia/Shanghai', '2026-09-20')"
        )
    path = tmp_path / "B3625.md"
    path.write_text(cuoti(), encoding="utf-8")
    assert import_notes.main(["--username", "alice", str(path)]) == 0
    assert import_notes.main(["--username", "alice", str(path)]) == 0  # 重复运行不重复导入
    with connect() as conn:
        rows = conn.execute("SELECT title, created_at FROM problems").fetchall()
        mistakes = conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0]
    assert [(r["title"], r["created_at"]) for r in rows] == [
        ("B3625 迷宫寻路", "2026-09-23T12:00:00+00:00")
    ]
    assert mistakes == 1
