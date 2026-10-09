"""SSH 加固与异地加密备份脚本的行为测试。

不依赖真实的 sshd / fail2ban / rclone / age / docker：全部用临时目录里的假命令代替，
只验证脚本自身的逻辑（生成的配置内容、dry-run 输出、拒绝条件、保留份数、状态文件格式）。
另外对三个 shell 脚本做 bash -n 语法检查（没有 bash 的环境自动跳过）。
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SSH_HARDEN = ROOT / "deploy" / "bin" / "ssh-harden.sh"
OFFSITE = ROOT / "deploy" / "bin" / "offsite-backup.sh"
DRILL = ROOT / "deploy" / "bin" / "restore-drill.sh"
BACKUP_PY = ROOT / "backup.py"


def find_bash():
    override = os.environ.get("OY_TEST_BASH")
    if override and Path(override).exists():
        return override
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


BASH = find_bash()
requires_bash = pytest.mark.skipif(BASH is None, reason="环境中没有 bash，无法运行 shell 脚本测试")

if BASH:
    _uname = subprocess.run([BASH, "-c", "uname -s"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    IS_MINGW = "MINGW" in _uname or "MSYS" in _uname
else:
    IS_MINGW = False


def bp(path: Path) -> str:
    """转成 bash 能稳定使用的路径形式（Git Bash 下用 C:/... 混合路径作参数）。"""
    text = str(path.resolve())
    if IS_MINGW:
        out = subprocess.run(
            [BASH, "-c", 'cygpath -m "$1"', "_", text], capture_output=True, text=True, encoding="utf-8", errors="replace"
        ).stdout.strip()
        return out or text.replace("\\", "/")
    return text


def up(path: Path) -> str:
    """PATH 环境变量必须用 /c/... 形式 Git Bash 才会识别。"""
    text = str(path.resolve())
    if IS_MINGW:
        out = subprocess.run(
            [BASH, "-c", 'cygpath -u "$1"', "_", text], capture_output=True, text=True, encoding="utf-8", errors="replace"
        ).stdout.strip()
        return out or text.replace("\\", "/")
    return text


# ---- 假命令（fake bin）----

FAKE_ID = """#!/usr/bin/env bash
echo "${FAKE_UID:-0}"
"""

FAKE_SSHD = """#!/usr/bin/env bash
echo "sshd $*" >> "$FAKE_SSHD_LOG"
[ "${FAKE_SSHD_T:-0}" = "1" ] && exit 1
exit 0
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE_SYSTEMCTL_LOG"
case "$1" in
  cat) exit 0 ;;
  is-active) [ "${FAKE_F2B_ACTIVE:-active}" = "active" ] && exit 0 || exit 3 ;;
  *) exit 0 ;;
esac
"""

FAKE_APT_GET = """#!/usr/bin/env bash
echo "apt-get $*" >> "$FAKE_APT_LOG"
exit 0
"""

FAKE_FAIL2BAN = """#!/usr/bin/env bash
echo "fail2ban-client $*" >> "$FAKE_F2B_LOG"
if [ "${FAKE_F2B_T_FAIL:-0}" = "1" ] && [ "$1" = "-t" ]; then exit 1; fi
[ "$1" = "status" ] && echo "Jail list: sshd"
exit 0
"""

FAKE_SSH_KEYGEN = """#!/usr/bin/env bash
# 只支持 `ssh-keygen -l -f -`：从标准输入读一行公钥，看前缀是否合法。
if [ "$1" = "-l" ]; then
  line="$(cat)"
  case "$line" in
    ssh-rsa\\ *|ssh-ed25519\\ *|ecdsa-sha2-*\\ *|sk-*\\ *) exit 0 ;;
    *) exit 1 ;;
  esac
fi
exit 0
"""

FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$DOCKER_FAKE_LOG"
[ "${DOCKER_FAKE_FAIL:-0}" = "1" ] && exit 1
case "$*" in
  *"backup.py verify"*) exit 0 ;;
  *) exit 0 ;;
esac
"""

FAKE_AGE = r"""#!/usr/bin/env bash
echo "age $*" >> "$AGE_FAKE_LOG"
[ "${AGE_FAKE_FAIL:-0}" = "1" ] && exit 1
out=""; input=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -r|-i) shift 2 ;;
    -d) shift ;;
    *) input="$1"; shift ;;
  esac
done
[ -n "$out" ] && [ -n "$input" ] || exit 2
cp "$input" "$out"
"""

FAKE_RCLONE = r"""#!/usr/bin/env bash
# 假 rclone：把 REMOTE:bucket/path 映射到 $RCLONE_FAKE_ROOT/bucket/path。
echo "rclone $*" >> "$RCLONE_FAKE_LOG"
is_remote() {
  # 远端形态为 name:rel 且冒号后不是 /；Windows 盘符 C:/... 冒号后是 /，不算远端。
  case "$1" in
    [A-Za-z]*:/*) return 1 ;;
    [A-Za-z]*:*) return 0 ;;
    *) return 1 ;;
  esac
}
resolve() {
  local spec="$1"
  if is_remote "$spec"; then
    printf '%s/%s\n' "$RCLONE_FAKE_ROOT" "${spec#*:}"
  else
    printf '%s\n' "$spec"
  fi
}
cmd="$1"; shift
case "$cmd" in
  lsf)
    [ "${RCLONE_FAKE_FAIL:-}" = "lsf" ] && exit 1
    d="$(resolve "$1")"
    [ -d "$d" ] || exit 0
    ls -1 "$d"
    ;;
  lsl)
    [ "${RCLONE_FAKE_FAIL:-}" = "lsl" ] && exit 1
    p="$(resolve "$1")"
    if [ -f "$p" ]; then
      printf '%s 2026-01-01 00:00:00.000000000 %s\n' "$(stat -c %s "$p" 2>/dev/null || stat -f %z "$p")" "$(basename "$p")"
    elif [ -d "$p" ]; then
      for f in "$p"/*; do
        [ -f "$f" ] || continue
        printf '%s 2026-01-01 00:00:00.000000000 %s\n' "$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f")" "$(basename "$f")"
      done
    else
      exit 1
    fi
    ;;
  copyto)
    [ "${RCLONE_FAKE_FAIL:-}" = "copyto" ] && exit 1
    src="$1"; dst="$2"
    if is_remote "$src"; then
      s="$(resolve "$src")"; d="$dst"; mkdir -p "$(dirname "$d")"; cp "$s" "$d"
    else
      d="$(resolve "$dst")"; mkdir -p "$(dirname "$d")"; cp "$src" "$d"
    fi
    ;;
  deletefile)
    [ "${RCLONE_FAKE_FAIL:-}" = "deletefile" ] && exit 1
    rm -f "$(resolve "$1")"
    ;;
  config) exit 0 ;;
  *) exit 0 ;;
esac
"""

FAKE_PYTHON3 = """#!/usr/bin/env bash
exec "{python}" "$@"
"""


class FakeEnv:
    """搭建假命令目录 + 假 /etc，并负责运行脚本。"""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.bin = tmp_path / "fakebin"
        self.bin.mkdir()
        self.etc_ssh = tmp_path / "etc" / "ssh"
        self.sshd_d = self.etc_ssh / "sshd_config.d"
        self.sshd_d.mkdir(parents=True)
        self.f2b_d = tmp_path / "etc" / "fail2ban" / "jail.d"
        self.f2b_d.mkdir(parents=True)
        self.backup_dir = tmp_path / "var-backups"
        self.state = tmp_path / "oy-state"
        self.log = tmp_path / "oy-offsite.log"
        self.home = tmp_path / "home" / "ubuntu"
        self.ssh_dir = self.home / ".ssh"
        self.ssh_dir.mkdir(parents=True)
        self.remote_root = tmp_path / "remote"
        self.remote_root.mkdir()
        self.logs = {}

    def write_fake(self, name, content):
        path = self.bin / name
        text = content.format(python=bp(Path(sys.executable))) if "{python}" in content else content
        path.write_text(text, encoding="utf-8", newline="\n")
        # Git Bash 不会仅凭文件内容赋予执行权限，显式 chmod（Linux 下同样无害）
        subprocess.run([BASH, "-c", 'chmod +x "$1"', "_", bp(path)], capture_output=True)

    def setup_all(self):
        for name, content in (
            ("id", FAKE_ID),
            ("sshd", FAKE_SSHD),
            ("systemctl", FAKE_SYSTEMCTL),
            ("apt-get", FAKE_APT_GET),
            ("fail2ban-client", FAKE_FAIL2BAN),
            ("ssh-keygen", FAKE_SSH_KEYGEN),
            ("docker", FAKE_DOCKER),
            ("age", FAKE_AGE),
            ("rclone", FAKE_RCLONE),
            ("python3", FAKE_PYTHON3),
        ):
            self.write_fake(name, content)
        # 各类假命令的调用日志
        for logname in (
            "sshd.log", "systemctl.log", "apt.log", "f2b.log", "docker.log", "age.log", "rclone.log"
        ):
            p = self.tmp / logname
            self.logs[logname] = p
        self.main_config.write_text(
            "Include /etc/ssh/sshd_config.d/*.conf\n", encoding="utf-8"
        )

    @property
    def main_config(self) -> Path:
        return self.etc_ssh / "sshd_config"

    @property
    def harden_conf(self) -> Path:
        return self.sshd_d / "99-oy-hardening.conf"

    @property
    def jail_file(self) -> Path:
        return self.f2b_d / "oy-sshd.local"

    @property
    def auth_keys(self) -> Path:
        return self.ssh_dir / "authorized_keys"

    def put_authorized_key(self, valid=True):
        key = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI0123456789abcdefghijklmnopqrstuvwxyzABCD test@laptop\n"
            if valid
            else "not-a-key-line\n"
        )
        self.auth_keys.write_text(key, encoding="utf-8")

    def base_env(self):
        env = dict(os.environ)
        env.update(
            {
                "PATH": up(self.bin) + os.pathsep + env["PATH"],
                "HOME": bp(self.home),
                "FAKE_SSHD_LOG": bp(self.logs["sshd.log"]),
                "FAKE_SYSTEMCTL_LOG": bp(self.logs["systemctl.log"]),
                "FAKE_APT_LOG": bp(self.logs["apt.log"]),
                "FAKE_F2B_LOG": bp(self.logs["f2b.log"]),
                "DOCKER_FAKE_LOG": bp(self.logs["docker.log"]),
                "AGE_FAKE_LOG": bp(self.logs["age.log"]),
                "RCLONE_FAKE_LOG": bp(self.logs["rclone.log"]),
                "RCLONE_FAKE_ROOT": bp(self.remote_root),
                "OY_SSHD_MAIN": bp(self.main_config),
                "OY_SSHD_CONFIG_D": bp(self.sshd_d),
                "OY_FAIL2BAN_JAIL_D": bp(self.f2b_d),
                "OY_HARDEN_BACKUP_DIR": bp(self.backup_dir),
                "OY_AUTHORIZED_KEYS": bp(self.auth_keys),
                "OY_OFFSITE_STATE_DIR": bp(self.state),
                "OY_OFFSITE_LOG": bp(self.log),
                "OY_OFFSITE_ENV": bp(self.tmp / "no-such-env-file"),
                "TMPDIR": bp(self.tmp),
            }
        )
        return env

    def run(self, script, *args, env_extra=None, check=False):
        env = self.base_env()
        if env_extra:
            env.update(env_extra)
        # Git Bash 启动时会强制把 /usr/bin 排到 PATH 最前面，导致假命令被真命令遮住；
        # 因此在 bash 内部再前置一次假命令目录（POSIX 路径），然后 exec 脚本。
        wrapper = 'export PATH="$1:$PATH"; shift; exec bash "$@"'
        proc = subprocess.run(
            [BASH, "-c", wrapper, "_", up(self.bin), bp(script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(ROOT),
        )
        if check and proc.returncode != 0:
            raise AssertionError(
                f"脚本 {script.name} 退出码 {proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        return proc

    def log_text(self, logname) -> str:
        p = self.logs[logname]
        return p.read_text(encoding="utf-8") if p.exists() else ""


@pytest.fixture
def fake(tmp_path):
    fe = FakeEnv(tmp_path)
    fe.setup_all()
    return fe


def make_real_backup(tmp_path: Path, rows=3) -> Path:
    """用仓库真实的 backup.py 造一份合法备份，返回 data 目录路径。"""
    data = tmp_path / "data"
    backups = data / "backups"
    backups.mkdir(parents=True)
    db = data / "notebook.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE problems (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE mistakes (id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE reviews (id INTEGER PRIMARY KEY)")
    for table in ("users", "problems", "mistakes", "reviews"):
        con.executemany(f"INSERT INTO {table} DEFAULT VALUES", [()] * rows)
    con.commit()
    con.close()
    avatars = tmp_path / "avatars-src"
    avatars.mkdir()
    subprocess.run(
        [
            sys.executable,
            str(BACKUP_PY),
            "--output-dir",
            str(backups),
            "--keep",
            "30",
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_PATH": str(db), "AVATAR_DIR": str(avatars)},
    )
    return data


# ---- bash -n 语法检查 ----

@requires_bash
@pytest.mark.parametrize("script", [SSH_HARDEN, OFFSITE, DRILL])
def test_scripts_pass_bash_syntax_check(script):
    proc = subprocess.run([BASH, "-n", bp(script)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr


# ---- ssh-harden.sh ----

@requires_bash
def test_ssh_harden_requires_root(fake):
    proc = fake.run(SSH_HARDEN, env_extra={"FAKE_UID": "1000"})
    assert proc.returncode != 0
    assert "root" in proc.stderr + proc.stdout


@requires_bash
def test_ssh_harden_refuses_without_authorized_keys(fake):
    proc = fake.run(SSH_HARDEN)
    assert proc.returncode != 0
    assert "authorized_keys" in proc.stderr
    assert "ssh-copy-id" in proc.stderr
    assert not fake.harden_conf.exists()
    assert not fake.jail_file.exists()


@requires_bash
def test_ssh_harden_refuses_with_invalid_key(fake):
    fake.put_authorized_key(valid=False)
    proc = fake.run(SSH_HARDEN)
    assert proc.returncode != 0
    assert "有效的公钥" in proc.stderr


@requires_bash
def test_ssh_harden_dry_run_changes_nothing_and_prints_plan(fake):
    fake.put_authorized_key()
    proc = fake.run(SSH_HARDEN, check=True)
    out = proc.stdout
    assert "[dry-run]" in out
    assert "PasswordAuthentication no" in out
    assert "maxretry = 5" in out
    assert "bantime = 1h" in out
    assert "另开一个新终端" in out
    assert not fake.harden_conf.exists()
    assert not fake.jail_file.exists()
    assert fake.log_text("sshd.log") == ""  # 演练不真正校验/重载
    assert fake.log_text("systemctl.log") == ""


@requires_bash
def test_ssh_harden_apply_writes_dropin_and_jail(fake):
    fake.put_authorized_key()
    proc = fake.run(SSH_HARDEN, "--apply", "--whitelist", "9.9.9.9,8.8.8.8", check=True)
    conf = fake.harden_conf.read_text(encoding="utf-8")
    for directive in (
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PermitRootLogin prohibit-password",
        "MaxAuthTries 3",
        "ClientAliveInterval 300",
    ):
        assert directive in conf
    jail = fake.jail_file.read_text(encoding="utf-8")
    assert "[sshd]" in jail
    assert "maxretry = 5" in jail
    assert "bantime = 1h" in jail
    assert "ignoreip = 127.0.0.1/8 ::1 9.9.9.9 8.8.8.8" in jail
    assert "sshd -t" in fake.log_text("sshd.log")
    assert "reload" in fake.log_text("systemctl.log")
    assert "fail2ban-client -t" in fake.log_text("f2b.log")
    assert "另开一个新终端" in proc.stdout


@requires_bash
def test_ssh_harden_rejects_bad_whitelist_ip(fake):
    fake.put_authorized_key()
    proc = fake.run(SSH_HARDEN, "--apply", "--whitelist", "999.1.1.1")
    assert proc.returncode != 0
    assert "白名单" in proc.stderr


@requires_bash
def test_ssh_harden_rolls_back_when_sshd_test_fails(fake):
    fake.put_authorized_key()
    proc = fake.run(SSH_HARDEN, "--apply", env_extra={"FAKE_SSHD_T": "1"})
    assert proc.returncode != 0
    assert "回滚" in proc.stderr + proc.stdout
    assert not fake.harden_conf.exists()
    assert "reload" not in fake.log_text("systemctl.log")


@requires_bash
def test_ssh_harden_is_idempotent_without_duplicate_backups(fake):
    fake.put_authorized_key()
    fake.run(SSH_HARDEN, "--apply", check=True)
    fake.run(SSH_HARDEN, "--apply", check=True)
    assert fake.harden_conf.exists()
    # 第一次是新增文件、第二次内容相同：不应产生任何备份文件
    assert not any(fake.backup_dir.rglob("*")) if fake.backup_dir.exists() else True


@requires_bash
def test_ssh_harden_revert_apply_removes_dropins(fake):
    fake.put_authorized_key()
    fake.run(SSH_HARDEN, "--apply", check=True)
    # 只给 --revert（演练）不应删除
    proc = fake.run(SSH_HARDEN, "--revert", check=True)
    assert "[dry-run]" in proc.stdout
    assert fake.harden_conf.exists() and fake.jail_file.exists()
    # --revert --apply 才真正回滚
    before = fake.log_text("systemctl.log")
    fake.run(SSH_HARDEN, "--revert", "--apply", check=True)
    assert not fake.harden_conf.exists()
    assert not fake.jail_file.exists()
    assert "reload" in fake.log_text("systemctl.log")[len(before):]


@requires_bash
def test_ssh_harden_requires_include_directive(fake):
    fake.put_authorized_key()
    fake.main_config.write_text("# 没有 Include\nPasswordAuthentication yes\n", encoding="utf-8")
    proc = fake.run(SSH_HARDEN)
    assert proc.returncode != 0
    assert "Include" in proc.stderr


@requires_bash
def test_ssh_harden_refuses_when_main_config_overrides_before_include(fake):
    fake.put_authorized_key()
    fake.main_config.write_text(
        "PasswordAuthentication yes\nInclude /etc/ssh/sshd_config.d/*.conf\n", encoding="utf-8"
    )
    proc = fake.run(SSH_HARDEN)
    assert proc.returncode != 0
    assert "覆盖" in proc.stderr


# ---- offsite-backup.sh ----

RECIPIENT = "age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290"
REMOTE = "oycos:oy-bucket/backups"


@pytest.fixture
def offsite(fake, tmp_path):
    data = make_real_backup(tmp_path)
    fake.env_data = data
    yield fake


def offsite_args(fake, *extra, env_extra=None, check=False):
    extra_env = {
        "OY_BACKUP_SRC_DIR": bp(fake.env_data / "backups"),
        "RCLONE_FAKE_ROOT": bp(fake.remote_root),
    }
    if env_extra:
        extra_env.update(env_extra)
    return fake.run(OFFSITE, "--recipient", RECIPIENT, "--remote", REMOTE, *extra, env_extra=extra_env, check=check)


@requires_bash
def test_offsite_dry_run_does_not_mutate(fake, tmp_path):
    data = make_real_backup(tmp_path)
    proc = fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE,
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
        check=True,
    )
    assert "[dry-run]" in proc.stdout
    assert "age -r" in proc.stdout
    assert "copyto" in proc.stdout
    rclone_log = fake.log_text("rclone.log")
    assert "copyto" not in rclone_log and "deletefile" not in rclone_log  # lsf 只读预览允许
    assert fake.log_text("age.log") == ""
    assert not (fake.state / "last-success.json").exists()


@requires_bash
def test_offsite_apply_encrypts_uploads_and_writes_status(offsite):
    proc = offsite_args(offsite, "--apply", check=True)
    remote_dir = offsite.remote_root / "oy-bucket" / "backups"
    ages = list(remote_dir.glob("backup-*.tar.gz.age"))
    assert len(ages) == 1
    status = json.loads((offsite.state / "last-success.json").read_text(encoding="utf-8"))
    assert status["status"] == "ok"
    assert status["archive"].endswith(".tar.gz")
    assert status["archive_bytes"] > 0
    assert status["encrypted_bytes"] == ages[0].stat().st_size
    assert status["remote"] == REMOTE
    assert status["kept_remote"] == 1
    assert status["deleted_remote"] == []
    assert status["finished_at"].endswith("Z")
    assert "backup.py verify" in offsite.log_text("docker.log")
    # 临时密文已清理
    assert not list((offsite.state / "staging").rglob("*.age"))
    assert "成功" in offsite.log.read_text(encoding="utf-8")


@requires_bash
def test_offsite_retention_keeps_only_newest_fourteen(offsite):
    remote_dir = offsite.remote_root / "oy-bucket" / "backups"
    remote_dir.mkdir(parents=True, exist_ok=True)
    older = [f"backup-202609{i:02d}-020000.tar.gz.age" for i in range(1, 16)]
    for name in older:
        (remote_dir / name).write_bytes(b"x")
    offsite_args(offsite, "--apply", "--keep", "14", check=True)
    remaining = sorted(p.name for p in remote_dir.glob("backup-*.tar.gz.age"))
    assert len(remaining) == 14
    # 最旧的两份被删，新上传的当天备份保留
    assert "backup-20260901-020000.tar.gz.age" not in remaining
    assert "backup-20260902-020000.tar.gz.age" not in remaining
    assert "backup-20260915-020000.tar.gz.age" in remaining
    status = json.loads((offsite.state / "last-success.json").read_text(encoding="utf-8"))
    assert status["kept_remote"] == 14
    assert len(status["deleted_remote"]) == 2


@requires_bash
def test_offsite_fails_on_corrupt_archive_and_writes_failure_status(fake, tmp_path):
    data = make_real_backup(tmp_path)
    archive = next((data / "backups").glob("backup-*.tar.gz"))
    archive.write_bytes(b"this is not gzip data")
    proc = fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE, "--apply",
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
    )
    assert proc.returncode != 0
    failure = json.loads((fake.state / "last-failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["stage"] == "verify"
    assert fake.log_text("rclone.log") == ""  # 校验没过，绝不上传
    assert fake.log_text("age.log") == ""


@requires_bash
def test_offsite_fails_on_upload_error_and_cleans_staging(fake, tmp_path):
    data = make_real_backup(tmp_path)
    proc = fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE, "--apply",
        env_extra={
            "OY_BACKUP_SRC_DIR": bp(data / "backups"),
            "RCLONE_FAKE_FAIL": "copyto",
        },
    )
    assert proc.returncode != 0
    failure = json.loads((fake.state / "last-failure.json").read_text(encoding="utf-8"))
    assert failure["stage"] == "upload"
    assert not list((fake.state / "staging").rglob("*.age"))
    remote_dir = fake.remote_root / "oy-bucket" / "backups"
    assert not remote_dir.exists() or not list(remote_dir.glob("*.age"))


@requires_bash
def test_offsite_requires_recipient_and_remote(fake, tmp_path):
    data = make_real_backup(tmp_path)
    proc = fake.run(
        OFFSITE, "--remote", REMOTE,
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
    )
    assert proc.returncode != 0 and "AGE_RECIPIENT" in proc.stderr
    proc = fake.run(
        OFFSITE, "--recipient", RECIPIENT,
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
    )
    assert proc.returncode != 0 and "RCLONE_REMOTE" in proc.stderr


@requires_bash
def test_offsite_without_backups_fails_at_find_stage(fake, tmp_path):
    empty = tmp_path / "empty-backups"
    empty.mkdir()
    proc = fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE, "--apply",
        env_extra={"OY_BACKUP_SRC_DIR": bp(empty)},
    )
    assert proc.returncode != 0
    failure = json.loads((fake.state / "last-failure.json").read_text(encoding="utf-8"))
    assert failure["stage"] == "find"


# ---- restore-drill.sh ----

def make_identity(tmp_path: Path) -> Path:
    key = tmp_path / "oy-age-key.txt"
    key.write_text(
        "# created: 2026-01-01\n# public key: age1xxx\nAGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ\n",
        encoding="utf-8",
    )
    return key


@requires_bash
def test_restore_drill_requires_identity(fake, tmp_path):
    make_real_backup(tmp_path)
    proc = fake.run(DRILL, "--remote", REMOTE)
    assert proc.returncode != 0
    assert "--identity" in proc.stderr


@requires_bash
def test_restore_drill_rejects_non_identity_file(fake, tmp_path):
    make_real_backup(tmp_path)
    fake_key = tmp_path / "wrong.txt"
    fake_key.write_text("age1public-key-only\n", encoding="utf-8")
    proc = fake.run(DRILL, "--identity", bp(fake_key), "--remote", REMOTE)
    assert proc.returncode != 0
    assert "AGE-SECRET-KEY-1" in proc.stderr


@requires_bash
def test_restore_drill_end_to_end_never_touches_live_data(fake, tmp_path):
    data = make_real_backup(tmp_path, rows=5)
    identity = make_identity(tmp_path)
    # 先正式上传一份
    fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE, "--apply",
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
        check=True,
    )
    # 造一个线上库（模拟 data/notebook.db），记录演练前的内容
    live_db = data / "notebook.db"
    before = live_db.read_bytes()
    drills = fake.state / "drills"
    proc = fake.run(
        DRILL,
        "--identity", bp(identity), "--remote", REMOTE, "--apply",
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
        check=True,
    )
    assert "integrity_check: ok" in proc.stdout
    assert "rows users: 5" in proc.stdout
    assert "rows problems: 5" in proc.stdout
    restored = list(drills.rglob("restored/notebook.db"))
    assert len(restored) == 1
    # 线上库字节不变，且恢复目录在线上数据目录之外
    assert live_db.read_bytes() == before
    assert data not in restored[0].parents
    assert "rm -rf" in proc.stdout and "rm -f" in proc.stdout  # 提示清理副本与私钥


@requires_bash
def test_restore_drill_dry_run_downloads_nothing(fake, tmp_path):
    data = make_real_backup(tmp_path)
    identity = make_identity(tmp_path)
    fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE, "--apply",
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
        check=True,
    )
    rclone_before = fake.log_text("rclone.log")
    proc = fake.run(
        DRILL,
        "--identity", bp(identity), "--remote", REMOTE,
        check=True,
    )
    assert "[dry-run]" in proc.stdout
    assert "copyto" in proc.stdout
    assert not (fake.state / "drills").exists()
    new_calls = fake.log_text("rclone.log")[len(rclone_before):]
    assert "copyto" not in new_calls and "deletefile" not in new_calls
    assert "lsf" in new_calls  # 演练只做只读列目录


@requires_bash
def test_restore_drill_failure_removes_temp_dir(fake, tmp_path):
    data = make_real_backup(tmp_path)
    identity = make_identity(tmp_path)
    fake.run(
        OFFSITE,
        "--recipient", RECIPIENT, "--remote", REMOTE, "--apply",
        env_extra={"OY_BACKUP_SRC_DIR": bp(data / "backups")},
        check=True,
    )
    proc = fake.run(
        DRILL,
        "--identity", bp(identity), "--remote", REMOTE, "--apply",
        env_extra={"AGE_FAKE_FAIL": "1"},
    )
    assert proc.returncode != 0
    assert "解密失败" in proc.stderr
    # 失败后临时目录被 trap 清掉
    assert not (fake.state / "drills").exists() or not list((fake.state / "drills").iterdir())


@requires_bash
def test_restore_drill_fails_when_remote_empty(fake, tmp_path):
    make_real_backup(tmp_path)
    identity = make_identity(tmp_path)
    proc = fake.run(
        DRILL,
        "--identity", bp(identity), "--remote", "oycos:oy-bucket/empty",
    )
    assert proc.returncode != 0
    assert "没有找到" in proc.stderr
