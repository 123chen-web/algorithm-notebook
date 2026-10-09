"""Exercise cron planning with a fake uid; never install cron or invoke Docker."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/bin/recommend-cron-setup.sh"
BASH = shutil.which("bash")
if not BASH and os.name == "nt":
    candidate = Path("C:/Program Files/Git/usr/bin/bash.exe")
    if candidate.is_file():
        BASH = str(candidate)

pytestmark = pytest.mark.skipif(not BASH, reason="cron shell planning requires Bash")


def plan(tmp_path, *arguments, **overrides):
    env = os.environ.copy()
    env.update({
        "TMP": str(tmp_path), "TEMP": str(tmp_path), "TMPDIR": str(tmp_path),
        "APP_DIR": "/srv/algorithm-notebook", "RECOMMEND_LOG": "/var/log/oy-recommend.log",
        "FAKE_UID": "0", "LC_ALL": "C.UTF-8",
    })
    env.update(overrides)
    # Functions avoid needing an actual root account or executable stub files.
    # Every mutating dependency is a sentinel, even if dry-run accidentally regresses.
    wrapper = r'''
id() { printf '%s\n' "$FAKE_UID"; }
for forbidden in crontab docker timeout touch mkdir mktemp cp chmod awk; do
  eval "$forbidden() { printf 'UNEXPECTED MUTATION: $forbidden\\n' >&2; return 97; }"
done
script="$1"
shift
source "$script" "$@"
'''
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", wrapper, "cron-plan", SCRIPT.as_posix(), *arguments],
        env=env, cwd=tmp_path, text=True, encoding="utf-8", capture_output=True, timeout=10,
    )


def cron_line(output):
    return next(line for line in output.splitlines() if line.startswith("20 3 * * * "))


def test_default_dry_run_plans_container_refresh_and_never_writes(tmp_path):
    before = list(tmp_path.iterdir())
    result = plan(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN" in result.stdout
    assert "03:20" in result.stdout
    assert "/var/backups/oy-recommend-crontab." in result.stdout
    line = cron_line(result.stdout)
    assert line.endswith("# oy-recommend-refresh")
    assert " /usr/bin/timeout 180 /usr/bin/docker compose " in line
    assert " -f deploy/docker-compose.prod.yml exec -T app python cf_problems.py refresh " in line
    assert " >> /var/log/oy-recommend.log 2>&1 " in line
    assert "UNEXPECTED MUTATION" not in result.stderr
    assert list(tmp_path.iterdir()) == before
    repeated = plan(tmp_path)
    assert repeated.stdout == result.stdout


def test_remove_is_only_a_dry_run_for_its_own_marker(tmp_path):
    result = plan(tmp_path, "--remove")
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN" in result.stdout
    assert "# oy-recommend-refresh" in result.stdout
    assert "保留其余 cron" in result.stdout
    assert "20 3 * * * " not in result.stdout
    assert "UNEXPECTED MUTATION" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_cron_shell_quoting_preserves_paths_with_spaces_and_metacharacters(tmp_path):
    app = "/srv/OY app (friends)"
    logfile = "/var/log/OY refresh's log"
    result = plan(tmp_path, APP_DIR=app, RECOMMEND_LOG=logfile)
    assert result.returncode == 0, result.stderr
    tokens = shlex.split(cron_line(result.stdout)[len("20 3 * * * "):], comments=True)
    assert tokens[0:3] == ["cd", app, "&&"]
    assert tokens[tokens.index(">>") + 1] == logfile
    assert tokens[tokens.index("exec") + 1:tokens.index(">>")] == [
        "-T", "app", "python", "cf_problems.py", "refresh",
    ]


@pytest.mark.parametrize("arguments", [("--unknown",), ("--apply=yes",), ("--",), ("/tmp/extra",)])
def test_unknown_arguments_are_rejected_without_side_effects(tmp_path, arguments):
    result = plan(tmp_path, *arguments)
    assert result.returncode != 0
    assert "未知参数" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["APP_DIR", "RECOMMEND_LOG"])
@pytest.mark.parametrize("value", ["relative/path", "/srv/path%cron", "/srv/a\nb", "/srv/a\rb"])
def test_invalid_cron_paths_are_rejected_without_side_effects(tmp_path, name, value):
    result = plan(tmp_path, **{name: value})
    assert result.returncode != 0
    assert "绝对路径" in result.stderr
    assert "20 3 * * * " not in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_non_root_uid_is_rejected_even_for_planning(tmp_path):
    result = plan(tmp_path, FAKE_UID="1000")
    assert result.returncode != 0
    assert "root" in result.stderr
    assert "20 3 * * * " not in result.stdout

