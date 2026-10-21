"""Lint gate: no unused imports / undefined names / shadowed imports in the app sources.

Runs pyflakes over the non-test Python modules (top level, core/, models/, routers/).
If an import is kept on purpose (re-export that tests or other modules reach through
the module), add it to ALLOWED with the reason instead of leaving a silent warning.
"""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

# Only these message kinds fail the gate.
CHECKED = (
    re.compile(r"^'(?P<name>[^']+)' imported but unused$"),
    re.compile(r"^undefined name '(?P<name>[^']+)'$"),
    re.compile(r"^redefinition of unused '(?P<name>[^']+)' from line \d+$"),
)

# (file, name) -> why the finding is accepted. Keep this list short.
ALLOWED = {
    ("ai.py", "openai.InternalServerError"):
        "re-export: tests build upstream 5xx errors via ai.InternalServerError (the noqa comment is on the "
        "continuation line, which pyflakes does not read)",
}


def _sources():
    files = sorted(p.name for p in ROOT.glob("*.py"))
    for folder in ("core", "models", "routers"):
        files += sorted(f"{folder}/{p.name}" for p in (ROOT / folder).glob("*.py"))
    return files


@pytest.mark.skipif(importlib.util.find_spec("pyflakes") is None, reason="pyflakes not installed")
def test_no_unused_or_undefined_names_in_app_sources():
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", *_sources()],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    problems = []
    for line in result.stdout.splitlines():
        match = re.match(r"^(?P<file>.+?):\d+:\d+:? (?P<msg>.*)$", line)
        if not match:
            problems.append(line)  # syntax errors etc. also fail
            continue
        message = match["msg"]
        found = next((pattern.match(message) for pattern in CHECKED if pattern.match(message)), None)
        if not found:
            continue
        path = match["file"].replace("\\", "/")
        if (path, found["name"]) in ALLOWED:
            continue
        problems.append(line)
    assert not problems, "pyflakes findings (remove the import, or allow-list it with a reason):\n" + "\n".join(problems)
