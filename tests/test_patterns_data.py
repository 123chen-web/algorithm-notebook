"""套路库数据的自动质量关。

- 结构校验：必填字段、id 唯一、category/level 合法、related 引用存在、
  examples 来源与题号格式合法。
- 运行校验：对每个套路，真的用 subprocess 运行 Python 模板，
  输入 sample_input，输出必须与 sample_output 一致（忽略行尾空白）；
  机器上有 g++ 时同样编译（-O2 -std=c++17）运行 C++ 模板，没有则跳过。
- 不联网；临时文件放系统临时目录。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
DATA = json.loads((STATIC / "data" / "patterns.json").read_text(encoding="utf-8"))

REQUIRED_FIELDS = {
    "id", "title", "category", "level", "signals", "idea",
    "template_python", "template_cpp", "complexity", "pitfalls",
    "confusions", "examples", "related", "sample_input", "sample_output",
}
ALLOWED_CATEGORIES = {
    "数组", "双指针", "前缀和与差分", "二分", "哈希", "单调结构", "栈", "堆",
    "链表", "树", "回溯", "搜索", "图遍历", "图论", "最短路", "动态规划",
    "分治", "分治与倍增", "字符串", "数据结构", "数学", "几何", "贪心",
    "位运算", "设计题", "图", "技巧", "实现陷阱", "工程陷阱",
}
ALLOWED_LEVELS = {"入门", "进阶", "竞赛"}
ALLOWED_SOURCES = {"luogu", "leetcode-cn"}
LUOGU_ID = re.compile(r"^[PB]\d{4,5}$")

PATTERNS = {item["id"]: item for item in DATA}


def normalize(text):
    """按行比较，忽略行尾空白与末尾空行。"""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def test_structure_fields_complete_and_ids_unique():
    ids = [item["id"] for item in DATA]
    assert len(ids) == len(set(ids)), "id 必须唯一"
    for item in DATA:
        assert REQUIRED_FIELDS <= item.keys(), item["id"]
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", item["id"]), item["id"]
        assert item["category"] in ALLOWED_CATEGORIES, item["id"]
        assert item["level"] in ALLOWED_LEVELS, item["id"]
        assert isinstance(item["signals"], list) and len(item["signals"]) >= 3, item["id"]
        assert isinstance(item["pitfalls"], list) and len(item["pitfalls"]) >= 2, item["id"]
        assert isinstance(item["examples"], list) and len(item["examples"]) >= 3, item["id"]
        assert item["sample_input"] != "" and item["sample_output"] != "", item["id"]


def test_related_references_exist():
    for item in DATA:
        for ref in item["related"]:
            assert ref in PATTERNS, f"{item['id']} 引用了不存在的 related: {ref}"


def test_examples_source_and_id_format():
    for item in DATA:
        for ex in item["examples"]:
            assert ex["source"] in ALLOWED_SOURCES, (item["id"], ex)
            assert ex["id"] and ex["title"], (item["id"], ex)
            if ex["source"] == "luogu":
                assert LUOGU_ID.match(ex["id"]), (item["id"], ex)
            else:
                assert re.fullmatch(r"\d+", ex["id"]), (item["id"], ex)


@pytest.mark.parametrize("pattern_id", sorted(PATTERNS), ids=sorted(PATTERNS))
def test_python_template_passes_sample(pattern_id):
    item = PATTERNS[pattern_id]
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "main.py"
        script.write_text(item["template_python"], encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(script)],
            input=item["sample_input"] + "\n",
            capture_output=True, text=True, timeout=5,
        )
    assert result.returncode == 0, f"{pattern_id} 运行失败: {result.stderr[-500:]}"
    assert normalize(result.stdout) == normalize(item["sample_output"]), (
        f"{pattern_id} 输出与 sample_output 不一致: {result.stdout!r}"
    )


GPP = shutil.which("g++")

cpp_ready = pytest.mark.skipif(GPP is None, reason="本机没有 g++，跳过 C++ 模板运行（CI 的 Linux 有 g++）")


@cpp_ready
@pytest.mark.parametrize("pattern_id", sorted(PATTERNS), ids=sorted(PATTERNS))
def test_cpp_template_passes_sample(pattern_id):
    item = PATTERNS[pattern_id]
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "main.cpp"
        exe = Path(tmp) / ("main.exe" if sys.platform == "win32" else "main")
        src.write_text(item["template_cpp"], encoding="utf-8")
        build = subprocess.run(
            [GPP, "-O2", "-std=c++17", str(src), "-o", str(exe)],
            capture_output=True, text=True, timeout=120,
        )
        assert build.returncode == 0, f"{pattern_id} 编译失败: {build.stderr[-500:]}"
        result = subprocess.run(
            [str(exe)],
            input=item["sample_input"] + "\n",
            capture_output=True, text=True, timeout=5,
        )
    assert result.returncode == 0, f"{pattern_id} 运行失败: {result.stderr[-500:]}"
    assert normalize(result.stdout) == normalize(item["sample_output"]), (
        f"{pattern_id} C++ 输出与 sample_output 不一致: {result.stdout!r}"
    )
