"""离线评分补交的三项变异检查：改动源码副本后，tests/test_offline_review_server.py 必须失败。

副本建在系统临时目录（不是工作区），原仓库源码不会被修改。
运行：python -B tests/offline_review_mutations.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_offline_review_server.py"
MUTATIONS = [
    ("去掉幂等判断", "            if replay is not None:\n", "            if False:\n"),
    ("去掉 reviewed_at 上界", "    if given > server_now + REVIEW_FUTURE_SLACK:\n", "    if False:\n"),
    ("去掉早于上次评分的检查",
     "    if last is not None and given < datetime.fromisoformat(last):\n", "    if False:\n"),
]


def run_in_copy(before, after):
    with tempfile.TemporaryDirectory(prefix="oy-mutation-") as scratch:
        copy = Path(scratch) / "repo"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        source = (copy / "main.py").read_text(encoding="utf-8")
        assert source.count(before) == 1, f"变异目标不唯一或不存在：{before!r}"
        (copy / "main.py").write_text(source.replace(before, after), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", TARGET, "-q", "-p", "no:cacheprovider"],
            cwd=copy, capture_output=True, text=True,
        )
        return result


def main():
    survived = []
    for name, before, after in MUTATIONS:
        result = run_in_copy(before, after)
        failed = [line for line in result.stdout.splitlines() if line.startswith("FAILED")]
        if result.returncode == 0:
            survived.append(name)
            print(f"SURVIVED {name}")
        else:
            print(f"KILLED   {name}: {len(failed)} 个测试失败，例如 {failed[0].split(' - ')[0][7:] if failed else '(收集/运行错误)'}")
    if survived:
        raise SystemExit(f"存活的变异：{survived}")
    print(f"{len(MUTATIONS)}/{len(MUTATIONS)} 变异全部被杀死；源码未改动")


if __name__ == "__main__":
    main()
