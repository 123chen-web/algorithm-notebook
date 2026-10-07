"""The scheduler must remain inspectable without contacting production services."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/bin/refresh-recommend.py"
spec = importlib.util.spec_from_file_location("recommend_schedule", SCRIPT)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


def test_dry_run_does_not_inspect_configuration_or_spawn(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("dry run must have no side effects")
    monkeypatch.setattr(schedule.Path, "is_file", forbidden)
    monkeypatch.setattr(schedule.subprocess, "run", forbidden)
    assert schedule.main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "cf_problems.py" in output
    assert "no AI, email, or push" in output


def test_fixed_refresh_command_preserves_failure_exit_and_never_runs_a_shell(monkeypatch):
    monkeypatch.setattr(schedule.Path, "is_file", lambda self: True)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(schedule.subprocess, "run", run)
    assert schedule.main([]) == 7
    repo = SCRIPT.resolve().parents[2]
    assert calls == [(schedule.command(repo), {"cwd": repo, "check": False})]
    assert calls[0][0][-5:] == ["-T", "app", "python", "cf_problems.py", "refresh"]


def test_missing_configuration_and_docker_report_failure(monkeypatch):
    monkeypatch.setattr(schedule.Path, "is_file", lambda self: False)
    assert schedule.main([]) == 2
    monkeypatch.setattr(schedule.Path, "is_file", lambda self: True)
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(schedule.subprocess, "run", unavailable)
    assert schedule.main([]) == 2
