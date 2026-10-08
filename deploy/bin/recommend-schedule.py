#!/usr/bin/env python3
"""Manage the two OY recommendation units; every action is a dry run by default."""
import argparse
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile


SERVICE = "algorithm-notebook-recommend.service"
TIMER = "algorithm-notebook-recommend.timer"
NAMES = (SERVICE, TIMER)
MARKER = "# Managed-By: OY recommend-schedule.py v1\n"
CALENDAR = "*-*-* 04:00:00 Asia/Taipei"
UNIT_DIRECTORY = PurePosixPath("/etc/systemd/system")
SEARCH_DIRECTORIES = tuple(Path(path) for path in (
    "/etc/systemd/system", "/run/systemd/system",
    "/etc/systemd/system.control", "/run/systemd/system.control",
    "/run/systemd/transient", "/run/systemd/generator.early",
    "/run/systemd/generator", "/run/systemd/generator.late",
    "/usr/local/lib/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system",
))
TEMPLATES = Path(__file__).resolve().parents[1] / "systemd"
MAX_UNIT_BYTES = 65536


class ScheduleError(Exception):
    pass


def repository(value):
    """Only an absolute, canonical POSIX path without systemd expansion syntax."""
    if not re.fullmatch(r"/[A-Za-z0-9._/-]+", value):
        raise ScheduleError("仓库路径必须是安全的 Linux 绝对路径。")
    path = PurePosixPath(value)
    if str(path) != value or any(part in (".", "..") for part in value.split("/")):
        raise ScheduleError("仓库路径不能包含相对段、重复分隔符或末尾分隔符。")
    return path


def render(repo):
    return {
        SERVICE: (TEMPLATES / (SERVICE + ".in")).read_text(encoding="utf-8").replace("@REPO@", str(repo)),
        TIMER: (TEMPLATES / TIMER).read_text(encoding="utf-8"),
    }


def unit_directory():
    return Path(str(UNIT_DIRECTORY))


def check_host():
    if not sys.platform.startswith("linux") or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ScheduleError("实际操作只支持 Linux systemd 主机的 root；Windows 可以预览。")
    if not Path("/run/systemd/system").is_dir():
        raise ScheduleError("当前 Linux 主机没有运行 systemd。")


def check_directory(path):
    """Root commands must not follow writable or symlinked destination ancestors."""
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ScheduleError("管理路径不能经过符号链接。")
        info = part.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ScheduleError("管理路径必须由 root 所有，且不能允许组或其他用户写入。")


def check_repository(repo):
    path = Path(str(repo))
    check_directory(path)
    for relative in ("deploy/bin/refresh-recommend.py", "deploy/docker-compose.prod.yml"):
        source = path / relative
        check_directory(source.parent)
        if source.is_symlink():
            raise ScheduleError("任务入口或生产配置不能是符号链接。")
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ScheduleError("任务入口和生产配置必须是 root 所有的普通文件，且不可被其他用户修改。")


def dropin_names(name):
    stem, suffix = name.rsplit(".", 1)
    prefixes = []
    for index, character in enumerate(stem):
        if character == "-":
            prefixes.append(stem[:index + 1] + "." + suffix + ".d")
    return (name + ".d", *prefixes, suffix + ".d")


def present(path):
    return path.exists() or path.is_symlink()


def trusted_file(info):
    return stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022 and info.st_size <= MAX_UNIT_BYTES


def read_owned(path, name):
    if path.is_symlink():
        raise ScheduleError("拒绝操作符号链接形式的任务文件。")
    info = path.stat()
    if not trusted_file(info):
        raise ScheduleError("现有任务文件的类型、归属、权限或大小不符合要求。")
    content = path.read_text(encoding="utf-8")
    if name == TIMER:
        if content != render(repository("/srv/algorithm-notebook"))[TIMER]:
            raise ScheduleError("拒绝覆盖或操作外来定时任务。")
    else:
        match = re.match(re.escape(MARKER) + r"# Repository: ([^\n]+)\n", content)
        if not match or content != render(repository(match.group(1)))[SERVICE]:
            raise ScheduleError("拒绝覆盖或操作外来服务任务。")
    return content


def preflight(directory, expected, required):
    check_directory(directory)
    for base in SEARCH_DIRECTORIES:
        for name in NAMES:
            for dropin in dropin_names(name):
                if present(base / dropin):
                    raise ScheduleError("这两条任务存在额外配置；请先由站长手工审查，工具不会覆盖。")
            candidate = base / name
            if base != directory and present(candidate):
                raise ScheduleError("其他 systemd 搜索目录存在同名任务，拒绝接管。")
    wants = directory / "timers.target.wants"
    if present(wants):
        check_directory(wants)
        link = wants / TIMER
        if present(link) and (not link.is_symlink() or link.resolve(strict=True) != directory / TIMER):
            raise ScheduleError("定时任务的启用链接由外来配置占用。")
    for name in NAMES:
        destination = directory / name
        if present(destination):
            content = read_owned(destination, name)
            if required and content != expected[name]:
                raise ScheduleError("已安装任务对应另一仓库；请先检查路径，再显式安装更新。")
        elif required:
            raise ScheduleError("任务尚未完整安装；先预览 install，再按计划安装。")


def commands(action, directory=UNIT_DIRECTORY):
    if action == "install":
        return [["/usr/bin/systemctl", "daemon-reload"]]
    if action == "verify":
        return [
            ["/usr/bin/systemd-analyze", "verify", str(directory / SERVICE), str(directory / TIMER)],
            ["/usr/bin/systemd-analyze", "calendar", CALENDAR],
            ["/usr/bin/systemctl", "show", "--property=Id,LoadState,FragmentPath,DropInPaths,NeedDaemonReload,UnitFileState,ActiveState,SubState,Result,ExecMainStatus,NextElapseUSecRealtime", SERVICE, TIMER],
        ]
    return {
        "enable": [["/usr/bin/systemctl", "enable", "--now", TIMER]],
        "disable": [["/usr/bin/systemctl", "disable", "--now", TIMER]],
        "run": [["/usr/bin/systemctl", "start", "--no-block", SERVICE]],
    }[action]


def write_atomic(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix="." + path.name + ".", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_loaded_units(output, directory):
    blocks = [dict(line.split("=", 1) for line in block.splitlines() if "=" in line) for block in output.strip().split("\n\n")]
    found = {block.get("Id"): block for block in blocks}
    for name in NAMES:
        block = found.get(name, {})
        if block.get("LoadState") != "loaded" or block.get("FragmentPath") != str(directory / name) or block.get("DropInPaths") != "" or block.get("NeedDaemonReload") != "no":
            raise ScheduleError("systemd 的有效任务来源与预期不一致、存在额外配置，或需要先重载配置。")


def execute(argv, timeout=30):
    # Do not inherit credentials or systemd client override variables from the
    # administrator's shell. These tools operate on the local system manager.
    result = subprocess.run(argv, check=False, capture_output=True, text=True,
        timeout=timeout, shell=False, cwd="/", env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"})
    if result.returncode != 0:
        raise ScheduleError("systemd 操作失败；请在服务器本地检查任务状态和 journal。")
    return result.stdout


def apply(action, repo):
    check_host()
    check_repository(repo)
    directory = unit_directory()
    expected = render(repo)
    preflight(directory, expected, required=action != "install")
    if action == "install":
        for name, content in expected.items():
            path = directory / name
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                write_atomic(path, content)
    elif action != "verify":
        check_loaded_units(execute(commands("verify", directory)[-1]), directory)
    for argv in commands(action, directory):
        output = execute(argv)
        if action == "verify":
            if argv[0] == "/usr/bin/systemctl":
                check_loaded_units(output, directory)
            print(output.rstrip())


def preview(action, repo):
    print("预览模式：不会写文件、执行 systemd/Docker、读 .env 或联网；显式 --apply 才执行。")
    print("仓库：", str(repo))
    print("每日计划：", CALENDAR, "；Persistent=true，错过后补一次；单个 oneshot 不重叠。")
    print("install 只写两个受管理文件并重载配置；不会启用任务。")
    if action == "enable":
        print("enable 会启用计时，Persistent 可能立即触发一次缓存刷新和今日一条发布。")
    if action == "run":
        print("run 会立即请求官方 Codeforces 元信息，并在成功后发布今日一条。")
    if action == "install":
        for name, content in render(repo).items():
            print("目标：", str(UNIT_DIRECTORY / name))
            print(content.rstrip())
    for argv in commands(action):
        print("固定命令参数：", argv)
    if action in ("enable", "disable", "run"):
        print("执行动作前还会只读核查当前 systemd 加载的两个任务来源及额外配置。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "verify", "enable", "disable", "run"))
    parser.add_argument("--repo", default="/srv/algorithm-notebook", help="Canonical absolute POSIX repository path on the future server")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Actually perform this action on a Linux systemd host as root")
    mode.add_argument("--dry-run", action="store_true", help="Explicit preview; this is also the default")
    args = parser.parse_args(argv)
    try:
        repo = repository(args.repo)
        if args.apply:
            apply(args.action, repo)
            if args.action == "run":
                print("运行请求已提交；请用 verify 或 journal 查看实际结果。")
            else:
                print("操作完成：", args.action)
        else:
            preview(args.action, repo)
        return 0
    except ScheduleError as error:
        print(str(error))
    except (OSError, UnicodeError, subprocess.SubprocessError):
        print("无法完成任务操作；检查路径、权限和 systemd。不会回显命令原始错误或配置内容。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
