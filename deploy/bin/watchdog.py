#!/usr/bin/env python3
"""守夜人：宿主机上由 cron 运行的小脚本，只用标准库。

    watchdog.py check       每分钟：网站健康、磁盘、备份新鲜度；状态变化时微信通知
    watchdog.py selfcheck   每周：只检查自己的网站（静态资源、条款页、证书有效期）

通知复用 push_channels（Server酱 / PushPlus），密钥在 /etc/oy-watchdog.env
（由 watchdog-setup.sh 从管理员账号的微信提醒配置里取出，权限 600，不会打印）。
公开状态页 /status/ 只显示"正常 / 异常"和检查时间，不含任何细节。
"""
import json
import os
import re
import shutil
import ssl
import socket
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

ENV_FILE = Path(os.getenv("WATCHDOG_ENV", "/etc/oy-watchdog.env"))
STATE_DIR = Path(os.getenv("WATCHDOG_STATE", "/var/lib/oy-watchdog"))
STATUS_DIR = Path(os.getenv("WATCHDOG_STATUS", str(REPO / "status")))
DATA_DIR = Path(os.getenv("DATA_DIR", str(REPO / "data")))

REALERT_SECONDS = 6 * 3600
# 连续失败几次才报警：网站健康检查 2 次（约 2 分钟，避开更新时的短暂重启），其余 1 次。
THRESHOLDS = {"health": 2}
DISK_LIMIT_PERCENT = 90
BACKUP_MAX_AGE_HOURS = 30
CERT_MIN_DAYS = 14

LABELS = {
    "health": "网站健康检查",
    "disk": "磁盘空间",
    "backup": "每日备份",
    "assets": "静态资源",
    "pages": "条款与隐私页",
    "cert": "HTTPS 证书有效期",
}


def read_env(path=ENV_FILE):
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def site_url(repo=REPO):
    for line in (repo / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("SITE_DOMAIN="):
            domain = line.partition("=")[2].strip()
            if domain:
                return f"https://{domain}"
    raise SystemExit("没有在 .env 里找到 SITE_DOMAIN")


def fetch(url, timeout=10, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, headers={"User-Agent": "oy-watchdog/1"})
    with opener(request, timeout=timeout) as response:
        return response.status, response.read(2_000_000)


# ---- 单项检查：返回 (是否正常, 给自己看的说明) ----

def check_health(base, get=fetch):
    try:
        status, body = get(base + "/healthz")
        ok = status == 200 and json.loads(body).get("status") == "ok"
        return ok, f"HTTP {status}"
    except Exception as exc:  # noqa: BLE001 - 任何失败都算异常
        return False, type(exc).__name__


def check_disk(path=DATA_DIR, limit=DISK_LIMIT_PERCENT, usage=shutil.disk_usage):
    total, used, _ = usage(path)[:3]
    percent = used * 100 // total
    return percent < limit, f"已用 {percent}%"


def check_backup(directory=DATA_DIR / "backups", max_age_hours=BACKUP_MAX_AGE_HOURS, now=None):
    now = time.time() if now is None else now
    files = sorted(Path(directory).glob("backup-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    if not files:
        return False, "没有找到备份文件"
    age = (now - files[-1].stat().st_mtime) / 3600
    return age <= max_age_hours, f"最新备份是 {age:.0f} 小时前"


def check_assets(base, get=fetch, limit=40):
    status, body = get(base + "/")
    page = body.decode("utf-8", "replace")
    paths = sorted(set(re.findall(r'(?:src|href)="(/static/[^"]+)"', page)))[:limit]
    bad = []
    for path in paths:
        time.sleep(0.2)  # 对自己的网站也放慢，别当压力测试
        try:
            code, _ = get(urljoin(base, path))
            if code != 200:
                bad.append(path)
        except Exception:  # noqa: BLE001
            bad.append(path)
    return not bad, f"检查 {len(paths)} 个，异常 {len(bad)} 个" + (f"：{bad[0]}" if bad else "")


def check_pages(base, get=fetch):
    bad = []
    for path in ("/terms", "/privacy", "/sw.js", "/manifest.webmanifest"):
        try:
            code, _ = get(base + path)
            if code != 200:
                bad.append(path)
        except Exception:  # noqa: BLE001
            bad.append(path)
    return not bad, "正常" if not bad else "异常：" + "、".join(bad)


def check_cert(base, min_days=CERT_MIN_DAYS, now=None):
    host = urlparse(base).hostname
    try:
        with socket.create_connection((host, 443), timeout=10) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
                expires = ssl.cert_time_to_seconds(tls.getpeercert()["notAfter"])
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
    days = (expires - (time.time() if now is None else now)) / 86400
    return days >= min_days, f"还剩 {days:.0f} 天"


# ---- 状态机：只在"变坏""恢复""持续坏超过 6 小时"时通知 ----

def evaluate(state, results, now):
    """state: {名字: {fails, alerted_at}}；results: {名字: (ok, detail)}。返回 (新 state, 通知列表)。"""
    new_state, messages = {}, []
    for name, (ok, detail) in results.items():
        entry = dict(state.get(name) or {"fails": 0, "alerted_at": None})
        label = LABELS.get(name, name)
        if ok:
            if entry["alerted_at"] is not None:
                messages.append((f"✅ 已恢复：{label}", detail))
            entry = {"fails": 0, "alerted_at": None}
        else:
            entry["fails"] += 1
            due = entry["alerted_at"] is None or now - entry["alerted_at"] >= REALERT_SECONDS
            if entry["fails"] >= THRESHOLDS.get(name, 1) and due:
                messages.append((f"⚠️ 异常：{label}", detail))
                entry["alerted_at"] = now
        new_state[name] = entry
    return new_state, messages


def load_state(name):
    try:
        return json.loads((STATE_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(name, data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / name).write_text(json.dumps(data), encoding="utf-8")


def write_status(results, now):
    """公开状态页只给"正常/异常"，不暴露具体数值。"""
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": all(ok for ok, _ in results.values()),
        "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "checks": {LABELS.get(name, name): ok for name, (ok, _) in results.items()},
    }
    tmp = STATUS_DIR / "status.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATUS_DIR / "status.json")


def notify(messages, env=None, send=None):
    env = read_env() if env is None else env
    if not messages or not env.get("KEY"):
        return False
    if send is None:
        import push_channels
        send = push_channels.send
    body = "\n".join(f"- {title}：{detail}" for title, detail in messages)
    try:
        send(env.get("CHANNEL", "serverchan"), env["KEY"], messages[0][0], body)
    except Exception as exc:  # noqa: BLE001 - 通知失败不能让脚本崩溃
        print(f"通知发送失败：{type(exc).__name__}", file=sys.stderr)
        return False
    return True


def run(mode, base=None, now=None):
    base = base or site_url()
    now = time.time() if now is None else now
    if mode == "check":
        results = {
            "health": check_health(base),
            "disk": check_disk(),
            "backup": check_backup(),
        }
        state_name = "state-check.json"
    else:
        results = {
            "assets": _safe(check_assets, base),
            "pages": _safe(check_pages, base),
            "cert": check_cert(base),
        }
        state_name = "state-selfcheck.json"
    state, messages = evaluate(load_state(state_name), results, now)
    save_state(state_name, state)
    if mode == "check":
        write_status(results, now)
    notify(messages)
    for name, (ok, detail) in results.items():
        if not ok:
            print(f"{datetime.fromtimestamp(now).isoformat(timespec='seconds')} {name} 异常：{detail}")
    return results


def _safe(func, *args):
    try:
        return func(*args)
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("check", "selfcheck"):
        raise SystemExit("用法：watchdog.py check|selfcheck")
    run(sys.argv[1])
