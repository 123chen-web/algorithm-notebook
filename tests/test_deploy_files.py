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
    assert development[0] == "-r requirements.txt"
    assert "pytest>=8.0,<10.0" in development
    assert "pip-audit>=2.7,<3.0" in development


def test_dockerfile_runs_as_non_root_and_checks_health():
    dockerfile = source("Dockerfile")
    user = re.search(r"(?m)^USER\s+(\S+)", dockerfile)
    assert user and user[1] not in {"root", "0", "0:0"}
    assert "--uid 10001" in dockerfile
    assert re.search(r"(?m)^HEALTHCHECK\s", dockerfile)
    assert "/healthz" in dockerfile
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
