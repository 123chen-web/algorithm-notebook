"""Systemd delivery tests never start systemd, Docker, or network requests."""
import importlib.util
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("recommend_schedule", ROOT / "deploy/bin/recommend-schedule.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("action", ["install", "verify", "enable", "disable", "run"])
def test_all_actions_are_side_effect_free_by_default(action, monkeypatch, capsys):
    module = load_script()
    def forbidden(*args, **kwargs):
        pytest.fail("Default schedule commands must not write or execute anything")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    monkeypatch.setattr(module, "apply", forbidden)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-must-not-appear")
    monkeypatch.setenv("SMTP_PASSWORD", "secret-must-not-appear")
    assert module.main([action]) == 0
    output = capsys.readouterr().out
    assert "/srv/algorithm-notebook" in output
    assert "04:00:00 Asia/Taipei" in output
    assert "--apply" in output
    assert "secret-must-not-appear" not in output


@pytest.mark.parametrize("repo", ["/", "relative", "C:\\website", "/srv/app%", "/srv/a b", "/srv/a/../b", "/srv/a;evil"])
def test_repository_path_cannot_inject_unit_directives(repo):
    module = load_script()
    with pytest.raises(module.ScheduleError):
        module.repository(repo)


def test_templates_keep_explicit_timezone_and_non_overlapping_oneshot():
    module = load_script()
    files = module.render(module.repository("/srv/notebook"))
    service = files[module.SERVICE]
    timer = files[module.TIMER]
    assert "Type=oneshot\n" in service
    assert "ExecStart=/usr/bin/python3 /srv/notebook/deploy/bin/refresh-recommend.py\n" in service
    assert "TimeoutStartSec=300\n" in service
    assert "RemainAfterExit=yes" not in service
    assert "OnCalendar=*-*-* 04:00:00 Asia/Taipei\n" in timer
    assert "Persistent=true\n" in timer
    assert "AccuracySec=1s\n" in timer
    assert "EnvironmentFile=" not in service


def configure_host(module, tmp_path, monkeypatch):
    units = tmp_path / "units"
    units.mkdir()
    monkeypatch.setattr(module, "unit_directory", lambda: units)
    monkeypatch.setattr(module, "SEARCH_DIRECTORIES", (units,))
    monkeypatch.setattr(module, "check_host", lambda: None)
    monkeypatch.setattr(module, "check_repository", lambda repo: None)
    monkeypatch.setattr(module, "check_directory", lambda path: None)
    # Windows cannot model Unix ownership/mode bits. Only these host checks are
    # stubbed; template ownership, symlinks, conflicts, and commands stay real.
    monkeypatch.setattr(module, "trusted_file", lambda info: stat.S_ISREG(info.st_mode) and info.st_size <= module.MAX_UNIT_BYTES)
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs.get("shell", False) is False
        assert kwargs["cwd"] == "/"
        assert kwargs["env"] == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
        output = ""
        if argv[1] == "show":
            output = "\n\n".join(f"Id={name}\nLoadState=loaded\nFragmentPath={units / name}\nDropInPaths=\nNeedDaemonReload=no" for name in module.NAMES)
        return SimpleNamespace(returncode=0, stdout=output, stderr="")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return units, calls


@pytest.mark.parametrize("obstacle", ["foreign", "symlink", "dropin", "prefix-dropin", "global-dropin", "wants-foreign"])
def test_install_refuses_conflicts_before_any_write(tmp_path, monkeypatch, obstacle):
    module = load_script()
    units, calls = configure_host(module, tmp_path, monkeypatch)
    service = units / module.SERVICE
    if obstacle == "foreign":
        service.write_text("[Service]\nExecStart=/foreign\n", encoding="utf-8")
    elif obstacle == "symlink":
        outside = tmp_path / "foreign.service"
        outside.write_text("foreign", encoding="utf-8")
        # Avoid requiring Windows symlink privileges; simulate lstat evidence.
        original = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == service or original(path))
    elif obstacle in ("dropin", "prefix-dropin", "global-dropin"):
        dirname = {"dropin": module.SERVICE + ".d", "prefix-dropin": "algorithm-.service.d", "global-dropin": "service.d"}[obstacle]
        (units / dirname).mkdir()
    else:
        wants = units / "timers.target.wants"
        wants.mkdir()
        (wants / module.TIMER).write_text("foreign", encoding="utf-8")
    with pytest.raises(module.ScheduleError):
        module.apply("install", module.repository("/srv/notebook"))
    assert calls == []
    assert not (units / module.TIMER).exists()
    assert not list(units.glob("*.tmp"))
    if obstacle == "foreign":
        assert service.read_text(encoding="utf-8") == "[Service]\nExecStart=/foreign\n"


def test_owned_install_update_and_commands_are_bounded(tmp_path, monkeypatch):
    module = load_script()
    units, calls = configure_host(module, tmp_path, monkeypatch)
    original = module.repository("/srv/original")
    module.apply("install", original)
    assert calls == [["/usr/bin/systemctl", "daemon-reload"]]
    repo = module.repository("/srv/notebook")
    module.apply("install", repo)
    module.apply("install", repo)
    expected = module.render(repo)
    assert all((units / name).read_text(encoding="utf-8") == content for name, content in expected.items())
    assert not list(units.glob("*.tmp"))
    for action in ("enable", "enable", "disable", "disable", "run"):
        module.apply(action, repo)
    assert calls[-10::2] == [module.commands("verify", units)[-1]] * 5
    assert calls[-9::2] == [
        ["/usr/bin/systemctl", "enable", "--now", module.TIMER],
        ["/usr/bin/systemctl", "enable", "--now", module.TIMER],
        ["/usr/bin/systemctl", "disable", "--now", module.TIMER],
        ["/usr/bin/systemctl", "disable", "--now", module.TIMER],
        ["/usr/bin/systemctl", "start", "--no-block", module.SERVICE],
    ]


def test_stale_manager_override_blocks_actions_before_mutation(tmp_path, monkeypatch):
    module = load_script()
    units, calls = configure_host(module, tmp_path, monkeypatch)
    repo = module.repository("/srv/notebook")
    module.apply("install", repo)
    calls.clear()
    def stale_manager(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="Id=" + module.TIMER + "\nLoadState=loaded\nFragmentPath=/foreign.timer\nDropInPaths=/foreign.conf", stderr="")
    monkeypatch.setattr(module.subprocess, "run", stale_manager)
    with pytest.raises(module.ScheduleError):
        module.apply("enable", repo)
    assert calls == [module.commands("verify", units)[-1]]


def test_claimed_marker_does_not_make_a_modified_unit_owned(tmp_path, monkeypatch):
    module = load_script()
    units, calls = configure_host(module, tmp_path, monkeypatch)
    repo = module.repository("/srv/notebook")
    expected = module.render(repo)
    (units / module.SERVICE).write_text(expected[module.SERVICE] + "Environment=EXTRA=1\n", encoding="utf-8")
    with pytest.raises(module.ScheduleError):
        module.apply("install", repo)
    assert calls == []


@pytest.mark.parametrize("owner,mode,size", [(1, stat.S_IFREG | 0o644, 10), (0, stat.S_IFREG | 0o664, 10),
    (0, stat.S_IFREG | 0o646, 10), (0, stat.S_IFDIR | 0o755, 10), (0, stat.S_IFREG | 0o644, 65537)])
def test_untrusted_unit_metadata_is_rejected(owner, mode, size):
    module = load_script()
    assert not module.trusted_file(SimpleNamespace(st_uid=owner, st_mode=mode, st_size=size))


def test_verify_validates_loaded_source_and_rejects_overrides():
    module = load_script()
    directory = module.UNIT_DIRECTORY
    blocks = [f"Id={name}\nLoadState=loaded\nFragmentPath={directory / name}\nDropInPaths=\nNeedDaemonReload=no\nActiveState=inactive" for name in module.NAMES]
    output = "\n\n".join(blocks)
    module.check_loaded_units(output, directory)
    for bad in (output.replace("LoadState=loaded", "LoadState=not-found", 1),
                output.replace("DropInPaths=", "DropInPaths=/foreign.conf", 1),
                output.replace(str(directory / module.SERVICE), "/foreign.service", 1),
                output.replace("NeedDaemonReload=no", "NeedDaemonReload=yes", 1)):
        with pytest.raises(module.ScheduleError):
            module.check_loaded_units(bad, directory)


def test_apply_requires_linux_root_before_reading_system_units(monkeypatch, capsys):
    module = load_script()
    monkeypatch.setattr(module.sys, "platform", "win32")
    def forbidden():
        pytest.fail("Unsupported hosts must not inspect system units")
    monkeypatch.setattr(module, "unit_directory", forbidden)
    assert module.main(["install", "--apply"]) == 2
    assert "Linux" in capsys.readouterr().out


def test_failed_command_returns_generic_error_without_output(tmp_path, monkeypatch, capsys):
    module = load_script()
    units, calls = configure_host(module, tmp_path, monkeypatch)
    repo = module.repository("/srv/notebook")
    module.apply("install", repo)
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="secret-output", stderr="secret-error"))
    assert module.main(["enable", "--repo", str(repo), "--apply"]) == 2
    output = capsys.readouterr().out
    assert "失败" in output and "secret-output" not in output and "secret-error" not in output
