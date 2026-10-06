import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "deploy" / "bin" / "watchdog.py"


@pytest.fixture
def wd(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHDOG_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("WATCHDOG_STATUS", str(tmp_path / "status"))
    monkeypatch.setenv("WATCHDOG_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    spec = importlib.util.spec_from_file_location("watchdog_under_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ok(detail="好"):
    return True, detail


def bad(detail="坏"):
    return False, detail


def test_health_needs_two_failures_before_alerting(wd):
    state, messages = wd.evaluate({}, {"health": bad()}, now=1000)
    assert messages == []
    state, messages = wd.evaluate(state, {"health": bad("HTTP 502")}, now=1060)
    assert len(messages) == 1 and "网站健康检查" in messages[0][0] and "异常" in messages[0][0]


def test_other_checks_alert_on_first_failure(wd):
    _, messages = wd.evaluate({}, {"disk": bad("已用 95%")}, now=1000)
    assert len(messages) == 1 and "磁盘空间" in messages[0][0]


def test_no_repeat_alert_until_six_hours_then_again(wd):
    state, first = wd.evaluate({}, {"disk": bad()}, now=0)
    state, again = wd.evaluate(state, {"disk": bad()}, now=3600)
    state, later = wd.evaluate(state, {"disk": bad()}, now=6 * 3600 + 1)
    assert len(first) == 1 and again == [] and len(later) == 1


def test_recovery_message_only_after_an_alert(wd):
    state, _ = wd.evaluate({}, {"health": bad()}, now=0)
    state, _ = wd.evaluate(state, {"health": ok()}, now=60)  # 没报过警，恢复时不打扰
    state, _ = wd.evaluate(state, {"health": bad()}, now=120)
    state, alert = wd.evaluate(state, {"health": bad()}, now=180)
    state, recovery = wd.evaluate(state, {"health": ok()}, now=240)
    assert len(alert) == 1 and len(recovery) == 1 and "已恢复" in recovery[0][0]
    assert state["health"] == {"fails": 0, "alerted_at": None}


def test_check_health_requires_status_ok_json(wd):
    assert wd.check_health("https://x", get=lambda url: (200, b'{"status":"ok"}'))[0]
    assert not wd.check_health("https://x", get=lambda url: (200, b'{"status":"error"}'))[0]
    assert not wd.check_health("https://x", get=lambda url: (200, b"<html>"))[0]

    def boom(url):
        raise OSError("down")

    assert wd.check_health("https://x", get=boom) == (False, "OSError")


def test_check_disk_threshold(wd):
    full = lambda path: (100, 95, 5)
    roomy = lambda path: (100, 40, 60)
    assert not wd.check_disk(".", usage=full)[0]
    assert wd.check_disk(".", usage=roomy)[0]


def test_check_backup_age(wd, tmp_path):
    directory = tmp_path / "backups"
    directory.mkdir()
    assert wd.check_backup(directory) == (False, "没有找到备份文件")
    backup = directory / "backup-1.tar.gz"
    backup.write_bytes(b"x")
    now = time.time()
    os.utime(backup, (now - 3600, now - 3600))
    assert wd.check_backup(directory, now=now)[0]
    os.utime(backup, (now - 40 * 3600, now - 40 * 3600))
    assert not wd.check_backup(directory, now=now)[0]


def test_check_pages_lists_the_broken_ones(wd):
    def get(url):
        return (404 if url.endswith("/privacy") else 200), b""

    result = wd.check_pages("https://x", get=get)
    assert result[0] is False and "/privacy" in result[1]


def test_check_assets_follows_static_links_only(wd, monkeypatch):
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    page = b'<script src="/static/a.js?v=1"></script><link href="/static/b.css"><a href="/other">x</a>'
    seen = []

    def get(url):
        seen.append(url)
        return 200, page

    assert wd.check_assets("https://x", get=get)[0]
    assert sorted(seen) == ["https://x/", "https://x/static/a.js?v=1", "https://x/static/b.css"]


def test_status_file_hides_details(wd, tmp_path):
    wd.write_status({"health": ok("HTTP 200"), "disk": bad("已用 95%")}, now=1_700_000_000)
    data = json.loads((tmp_path / "status" / "status.json").read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert data["checks"] == {"网站健康检查": True, "磁盘空间": False}
    assert "95" not in json.dumps(data, ensure_ascii=False)


def test_notify_sends_once_and_survives_failure(wd):
    sent = []
    env = {"CHANNEL": "serverchan", "KEY": "SCTabcdef123456"}
    assert wd.notify([("⚠️ 异常：磁盘空间", "已用 95%")], env=env, send=lambda *a: sent.append(a))
    assert sent[0][0] == "serverchan" and "磁盘空间" in sent[0][3]

    def fail(*args):
        raise RuntimeError("network")

    assert wd.notify([("x", "y")], env=env, send=fail) is False
    assert wd.notify([("x", "y")], env={}, send=fail) is False  # 没配密钥时静默跳过
    assert wd.notify([], env=env, send=fail) is False


def test_read_env_ignores_comments(wd, tmp_path):
    path = tmp_path / "env"
    path.write_text("# c\nCHANNEL=serverchan\nKEY=abc=def\n", encoding="utf-8")
    assert wd.read_env(path) == {"CHANNEL": "serverchan", "KEY": "abc=def"}
