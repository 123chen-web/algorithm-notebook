"""扩展构建：extension/build.py 的输出与仓库内提交的 zip 逐字节一致。

防止源码（extension/ouye-extension/）和下载包（static/downloads/…zip）漂移。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_build_output_matches_committed_zip():
    result = subprocess.run(
        [sys.executable, str(ROOT / "extension" / "build.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "extension/build.py --check 失败：源码与 static/downloads/ouye-companion-extension.zip 已漂移\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n"
        "请改完源码后运行 python3 extension/build.py 重新生成 zip 并提交。"
    )


def test_build_is_deterministic():
    """连跑两次构建，输出逐字节相同。"""
    build_py = str(ROOT / "extension" / "build.py")
    import importlib.util

    spec = importlib.util.spec_from_file_location("ext_build", build_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    first = mod.build_zip_bytes()
    second = mod.build_zip_bytes()
    assert first == second
    assert len(first) > 0
