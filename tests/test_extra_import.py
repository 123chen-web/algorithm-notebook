"""import_notes.py 与导入向导的异常输入分支补充测试。"""
import json
from pathlib import Path

import pytest

import import_notes
import main
from db import connect
from test_app import client, register


# ---------------- import_notes.scan / parse_question 纯函数异常分支 ----------------

def test_scan_rejects_unclosed_fence():
    text = "## 题目 1：x\n\n```python\npass\n"
    with pytest.raises(ValueError, match="未闭合"):
        import_notes.scan(text)


def test_parse_question_requires_mistake_list():
    lines = import_notes.scan(
        "**代码**\n\n```python\npass\n```\n\n**思路**\n\n只有正文，没有列表。\n\n"
        "**易错点**\n\n这一段没有 bullet。\n"
    )
    with pytest.raises(ValueError, match="非空 Markdown 列表"):
        import_notes.parse_question("标题", lines)


def test_parse_question_rejects_text_before_bullets():
    lines = import_notes.scan(
        "## 题目 1：x\n\n**代码**\n\n```python\npass\n```\n\n"
        "**思路**\n\n思路。\n\n**易错点**\n\n\n突兀正文\n- 真正的一条\n"
    )
    with pytest.raises(ValueError, match="无法识别的正文"):
        import_notes.parse_question("标题", lines)


def test_parse_question_rejects_duplicate_field():
    text = (
        "## 题目 1：x\n\n**代码**\n\n```python\npass\n```\n\n**思路**\n\n思路。\n\n"
        "**易错点**\n\n- 一条\n\n**思路**\n\n重复的思路字段。\n"
    )
    # 走 parse_file 的 section 切分，等价于 CLI 解析一个真实文件。
    lines = import_notes.scan(text)
    sections, current = [], None
    for item in lines:
        heading = import_notes.HEADING.fullmatch(item[0]) if item[1] == "text" else None
        if heading:
            current = (heading[1], [])
            sections.append(current)
        elif current:
            current[1].append(item)
    with pytest.raises(ValueError, match="字段重复"):
        import_notes.parse_question(sections[0][0], sections[0][1])


def test_parse_question_requires_exactly_one_mistake_field():
    lines = import_notes.scan(
        "**代码**\n\n```python\npass\n```\n\n**思路**\n\n思路。\n"
    )
    with pytest.raises(ValueError, match="易错点"):
        import_notes.parse_question("标题", lines)


def test_parse_question_requires_thinking_or_fallback_description():
    lines = import_notes.scan(
        "**代码**\n\n```python\npass\n```\n\n**易错点**\n\n- 一条\n"
    )
    with pytest.raises(ValueError, match="缺少思路"):
        import_notes.parse_question("标题", lines)


def test_parse_question_rejects_oversized_title():
    lines = import_notes.scan(
        "**代码**\n\n```python\npass\n```\n\n**思路**\n\n思路。\n\n**易错点**\n\n- 一条\n"
    )
    with pytest.raises(ValueError, match="title"):
        import_notes.parse_question("x" * 201, lines)


def test_parse_question_rejects_too_many_mistakes():
    bullets = "\n".join(f"- 易错点 {i}" for i in range(11))
    lines = import_notes.scan(
        f"**代码**\n\n```python\npass\n```\n\n**思路**\n\n思路。\n\n**易错点**\n\n{bullets}\n"
    )
    with pytest.raises(ValueError, match="1–10"):
        import_notes.parse_question("标题", lines)


# ---------------- cuoti 格式异常分支 ----------------

def test_parse_cuoti_requires_h1_title():
    text = "## 错误原因分析\n\n没有一级标题。\n"
    with pytest.raises(ValueError, match="一级标题"):
        import_notes.parse_cuoti(text)


def test_parse_cuoti_requires_code_fence():
    text = "# 迷宫寻路\n\n## 我的错误代码\n\n没有代码块。\n\n## 错误原因分析\n\n错了。\n\n## 关键收获\n\n收获。\n"
    with pytest.raises(ValueError, match="代码围栏"):
        import_notes.parse_cuoti(text)


def test_parse_cuoti_requires_takeaway():
    text = (
        "# 迷宫寻路\n\n## 我的错误代码\n\n```python\npass\n```\n\n"
        "## 错误原因分析\n\n错了。\n"
    )
    with pytest.raises(ValueError, match="关键收获"):
        import_notes.parse_cuoti(text)


# ---------------- expand_paths ----------------

def test_expand_paths_raises_when_no_glob_match(tmp_path):
    with pytest.raises(ValueError, match="没有匹配文件"):
        import_notes.expand_paths([str(tmp_path / "no-such-*.md")])


def test_expand_paths_raises_when_directory_has_no_md(tmp_path):
    (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="没有找到 Markdown"):
        import_notes.expand_paths([str(tmp_path)])


# ---------------- 导入向导 HTTP 层异常分支 ----------------

def md_question(title="二分边界"):
    return (f"## 题目 1：{title}\n\n**代码**\n\n```python\npass\n```\n\n"
            "**思路**\n\n维护区间。\n\n**易错点**\n\n- 空数组。\n")


def preview(client, content=None, filename="notes.md", zone="算法", extra_fields=None):
    fields = {"zone": zone}
    if extra_fields:
        fields.update(extra_fields)
    return client.post(
        "/api/import/preview",
        data=fields,
        files={"file": (filename, content or md_question(), "application/octet-stream")},
    )


def exported_problem(**overrides):
    return {
        "title": "导出题", "language": "Python", "code": "pass\n",
        "thinking": "维护区间。",
        "created_at": "2026-09-01T12:00:00+00:00",
        "mistakes": [{"description": "（待补）", "pending_reason": False}],
        **overrides,
    }


def test_preview_rejects_invalid_zone(client):
    register(client)
    response = preview(client, zone="不存在的分区")
    assert response.status_code == 422


def test_preview_rejects_file_without_upload(client):
    register(client)
    response = client.post("/api/import/preview", data={"zone": "算法"})
    assert response.status_code == 422


def test_preview_rejects_extra_form_fields(client):
    register(client)
    # 恰好 1 个文件 + 1 个非 "zone" 的文本字段：走到我们自己的 422 分支。
    response = client.post(
        "/api/import/preview",
        data={"not_zone": "算法"},
        files={"file": ("notes.md", md_question(), "application/octet-stream")},
    )
    assert response.status_code == 422


def test_preview_rejects_empty_markdown(client):
    register(client)
    response = preview(client, "   \n  \n")
    assert response.status_code == 422


def test_preview_rejects_markdown_with_no_question_sections(client):
    register(client)
    response = preview(client, "# 随便写写\n\n没有题目小节。\n")
    assert response.status_code == 422


def test_preview_json_rejects_naive_created_at(client):
    register(client)
    payload = exported_problem(created_at="2026-09-01T12:00:00")  # 无时区
    response = preview(client, json.dumps({"problems": [payload]}), "export.json")
    assert response.status_code == 422


def test_preview_json_rejects_problem_not_dict(client):
    register(client)
    response = preview(client, json.dumps({"problems": ["just-a-string"]}), "export.json")
    assert response.status_code == 422


def test_preview_json_rejects_mistake_not_dict(client):
    register(client)
    payload = exported_problem(mistakes=["just-a-string"])
    response = preview(client, json.dumps({"problems": [payload]}), "export.json")
    assert response.status_code == 422


def test_preview_json_rejects_non_bool_pending_flag(client):
    register(client)
    payload = exported_problem(
        mistakes=[{"description": "x", "pending_reason": "yes"}]
    )
    response = preview(client, json.dumps({"problems": [payload]}), "export.json")
    assert response.status_code == 422


def test_preview_json_rejects_more_than_200_records(client):
    register(client)
    problems = [exported_problem(title=f"题{i}") for i in range(201)]
    response = preview(client, json.dumps({"problems": problems}), "export.json")
    assert response.status_code == 413


def test_preview_markdown_without_thinking_marks_fallback_warning(client):
    register(client)
    md = (
        "## 题目 1：无思路题\n\n**代码**\n\n```python\npass\n```\n\n"
        "**题目描述**\n\n这是一道二分题。\n\n**易错点**\n\n- 边界。\n"
    )
    response = preview(client, md)
    assert response.status_code == 200
    body = response.json()
    assert any("代填" in w for w in body["items"][0]["warnings"])
