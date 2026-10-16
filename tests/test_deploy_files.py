"""部署配置静态检查，不使用临时目录或数据库。"""

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_runtime_and_development_dependencies_are_separate():
    runtime = source("requirements.txt")
    development = source("requirements-dev.txt").splitlines()
    assert not re.search(r"(?mi)^\s*pytest(?:\s|[<>=!~]|$)", runtime)
    assert development[0] == "-r requirements.lock", "开发环境也装钉死的版本"
    assert "pytest>=8.0,<10.0" in development
    assert "pip-audit>=2.7,<3.0" in development


def test_dockerfile_runs_as_non_root_and_checks_health():
    dockerfile = source("Dockerfile")
    user = re.search(r"(?m)^USER\s+(\S+)", dockerfile)
    assert user and user[1] not in {"root", "0", "0:0"}
    assert "--uid 10001" in dockerfile
    assert re.search(r"(?m)^HEALTHCHECK\s", dockerfile)
    assert "/healthz" in dockerfile
    assert "pip install --no-cache-dir -r requirements.lock" in dockerfile, "镜像从锁文件装依赖"
    assert not re.search(r"(?mi)^COPY\s+[^\n]*\.env(?:\s|$)", dockerfile)
    command = re.search(r"(?m)^CMD\s+(\[[^\n]+\])$", dockerfile)
    assert command
    assert json.loads(command[1])[:2] == ["uvicorn", "main:app"]


def test_docker_context_excludes_secrets_and_data_but_keeps_assets():
    patterns = {
        line.strip().strip("/")
        for line in source(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".env", "data", ".git"} <= patterns
    assert not ({"assets", "static", "assets/**", "static/**"} & patterns)


def test_ci_covers_tests_syntax_and_container_health_without_secrets():
    workflow = source(".github/workflows/ci.yml")
    for expected in ("pytest", "node --check", "requirements-dev.txt", "/healthz"):
        assert expected in workflow
    assert not re.search(r"sk-[A-Za-z0-9_-]+", workflow)
    assert "secrets." not in workflow
    assert "contents: read" in workflow
    assert "cancel-in-progress: true" in workflow


def test_compose_persists_data_on_host():
    assert "./data:/app/data" in source("docker-compose.yml")


def test_image_contains_every_local_package_the_app_imports(tmp_path):
    """回归：领域拆分新增了 core/、models/ 两个包，Dockerfile 没跟着 COPY，镜像里 import main 就报
    “No module named 'core'”，更新后网站起不来。这里按 Dockerfile 的 COPY 规则搭一份最小镜像目录，
    在里面真的 import 一遍 main。"""
    import shutil
    import subprocess
    import sys

    dockerfile = source("Dockerfile")
    copied_dirs = set(re.findall(r"(?m)^COPY\s+([A-Za-z0-9_\-]+)/\s+\./\1/\s*$", dockerfile))
    # 应用代码里 import 到的、仓库里真实存在的本地目录，必须都被 COPY。
    local_dirs = {
        path.name for path in ROOT.iterdir()
        if path.is_dir() and path.name not in {"tests", "tools", "docs", "deploy", "static", "assets", "data"}
        and not path.name.startswith(".") and any(path.glob("*.py"))
    }
    assert local_dirs <= copied_dirs, f"Dockerfile 没有 COPY 这些包：{sorted(local_dirs - copied_dirs)}"

    image = tmp_path / "app"
    image.mkdir()
    for module in ROOT.glob("*.py"):
        shutil.copy(module, image / module.name)
    for name in copied_dirs - {"static", "assets"}:  # 静态资源很大，镜像里放个空目录就够 import 了
        shutil.copytree(ROOT / name, image / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("static", "assets"):
        (image / name).mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(image)!r}); import main"], cwd=image, capture_output=True, text=True, timeout=120,
        env={"PATH": "", "PYTHONUTF8": "1", "DATABASE_PATH": str(tmp_path / "t.db"), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )
    assert result.returncode == 0, result.stderr[-1500:]
