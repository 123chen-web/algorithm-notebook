"""检查一个套路批次文件（docs/pattern-batches/batch-XX.json）能不能并入正式清单。

用法：python tools/check_pattern_batch.py docs/pattern-batches/batch-01.json

做法：把批次里的套路合并到当前清单（static/data/patterns.json）的内存副本里，写到系统临时目录，
然后用同一套自动校验（tests/test_patterns_data.py）跑一遍：结构、例题格式、related 引用、
Python 模板真实运行、（本机有 g++ 时）C++ 模板编译运行。不会修改任何仓库文件。

额外规则（批次独立交付时用）：批次里的 id 不能和现有清单重复；related 只能引用
“现有清单里的 id”或“本批次里的 id”。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "static" / "data" / "patterns.json"


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    batch_path = Path(argv[1])
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    if not isinstance(batch, list) or not batch:
        print("批次文件必须是非空的 JSON 数组")
        return 2
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    current_ids = {item["id"] for item in current}
    batch_ids = [item.get("id") for item in batch]
    problems = []
    for item_id in batch_ids:
        if item_id in current_ids:
            problems.append(f"id 已经存在于现有清单：{item_id}")
    if len(batch_ids) != len(set(batch_ids)):
        problems.append("批次内 id 有重复")
    allowed_refs = current_ids | set(batch_ids)
    for item in batch:
        for ref in item.get("related", []):
            if ref not in allowed_refs:
                problems.append(f"{item.get('id')} 的 related 引用了不存在的 id：{ref}（只能引用现有清单或本批次的 id）")
    if problems:
        print("\n".join(problems))
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        merged = Path(tmp) / "patterns.json"
        merged.write_text(json.dumps(current + batch, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, PATTERN_DATA_PATH=str(merged), PYTHONUTF8="1")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_patterns_data.py", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT, env=env,
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))
