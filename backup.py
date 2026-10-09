"""在线备份 SQLite 与头像，并在独立目录校验、恢复。"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath


ROOT = Path(__file__).resolve().parent


class BackupError(Exception):
    pass


def _resolve_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _utc_now():
    return datetime.now(timezone.utc)


def _check_integrity(path):
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise BackupError("数据库完整性检查失败")
    except sqlite3.Error:
        raise BackupError("数据库完整性检查失败：快照无法正常读取") from None


def _database_metadata(path):
    _check_integrity(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables = {
            name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            for name in ("users", "problems", "mistakes", "reviews") if name in names
        }
    return version, tables


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prune_archives(output_dir, created, keep):
    if keep == 0:
        return 0
    created_time = created.stat().st_mtime_ns
    older = [
        path for path in output_dir.glob("backup-*.tar.gz")
        if path != created and path.is_file() and not path.is_symlink()
        and path.stat().st_mtime_ns <= created_time
    ]
    older.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    removed = 0
    for path in older[keep - 1:]:
        path.unlink()
        removed += 1
    return removed


EXTRA_DIRS = ("note_files", "drawings")  # 笔记图片附件、画板缩略图：与头像一起进入备份


def _extra_directory(name, override=None):
    if override is not None:
        return _resolve_path(override)
    if name == "note_files":
        return _resolve_path(os.getenv("NOTE_FILES_DIR", "data/note_files"))
    return _resolve_path(os.getenv("DRAWING_DIR", "data/drawings"))


def _collect_files(directory, label):
    paths = []
    if directory.exists():
        if not directory.is_dir():
            raise BackupError(f"{label}路径不是目录")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise BackupError(f"{label}目录含符号链接，请先改为普通文件")
            if path.is_file():
                paths.append(path)
            elif not path.is_dir():
                raise BackupError(f"{label}目录含不支持的特殊文件")
    return paths


def create_backup(output_dir=None, keep=None, *, database_path=None, avatar_dir=None,
                  note_files_dir=None, drawings_dir=None):
    database = _resolve_path(database_path if database_path is not None else os.getenv("DATABASE_PATH", "data/notebook.db"))
    avatars = _resolve_path(avatar_dir if avatar_dir is not None else os.getenv("AVATAR_DIR", "data/avatars"))
    output = _resolve_path(output_dir if output_dir is not None else os.getenv("BACKUP_DIR", "data/backups"))
    try:
        keep = int(keep if keep is not None else os.getenv("BACKUP_KEEP", "14"))
    except (TypeError, ValueError):
        raise BackupError("保留份数（BACKUP_KEEP/--keep）必须是大于或等于 0 的整数") from None
    if keep < 0:
        raise BackupError("保留份数（BACKUP_KEEP/--keep）必须是大于或等于 0 的整数")
    extra_dirs = {
        "note_files": _extra_directory("note_files", note_files_dir),
        "drawings": _extra_directory("drawings", drawings_dir),
    }
    if output == avatars or avatars in output.parents:
        raise BackupError("备份输出目录不能在头像目录内")
    for extra in extra_dirs.values():
        if output == extra or extra in output.parents:
            raise BackupError("备份输出目录不能在笔记图片或画板目录内")
    if not database.is_file():
        raise BackupError(f"数据库文件不存在：{database}")
    if avatars.exists() and not avatars.is_dir():
        raise BackupError("头像路径不是目录")

    output.mkdir(parents=True, exist_ok=True)
    temporary_paths = []
    try:
        descriptor, name = tempfile.mkstemp(prefix=".backup-tmp-", suffix=".db", dir=output)
        os.close(descriptor)
        snapshot = Path(name)
        temporary_paths.append(snapshot)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)) as source:
            with closing(sqlite3.connect(str(snapshot))) as destination:
                source.backup(destination)
        version, tables = _database_metadata(snapshot)

        avatar_paths = []
        if avatars.exists():
            for path in sorted(avatars.rglob("*")):
                if path.is_symlink():
                    raise BackupError("头像目录含符号链接，请先改为普通文件")
                if path.is_file():
                    avatar_paths.append(path)
                elif not path.is_dir():
                    raise BackupError("头像目录含不支持的特殊文件")
        extra_paths = {
            name: _collect_files(directory, "笔记图片" if name == "note_files" else "画板缩略图")
            for name, directory in extra_dirs.items()
        }
        now = _utc_now()
        manifest = {
            "format": 1,
            "created_at": now.isoformat(),
            "database_sha256": _sha256(snapshot),
            "database_bytes": snapshot.stat().st_size,
            "schema_version": version,
            "tables": tables,
            "avatar_files": len(avatar_paths),
            "extra_files": {name: len(paths) for name, paths in extra_paths.items()},
        }
        descriptor, name = tempfile.mkstemp(prefix=".backup-tmp-", suffix=".tar.gz", dir=output)
        os.close(descriptor)
        temporary_archive = Path(name)
        temporary_paths.append(temporary_archive)
        with tarfile.open(temporary_archive, "w:gz", dereference=True) as archive:
            archive.add(snapshot, arcname="notebook.db")
            directory = tarfile.TarInfo("avatars")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o700
            archive.addfile(directory)
            for path in avatar_paths:
                archive.add(path, arcname="avatars/" + path.relative_to(avatars).as_posix(), recursive=False)
            for name, paths in extra_paths.items():
                folder = tarfile.TarInfo(name)
                folder.type = tarfile.DIRTYPE
                folder.mode = 0o700
                archive.addfile(folder)
                for path in paths:
                    archive.add(path, arcname=name + "/" + path.relative_to(extra_dirs[name]).as_posix(), recursive=False)
            content = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            entry = tarfile.TarInfo("MANIFEST.json")
            entry.size = len(content)
            entry.mode = 0o600
            archive.addfile(entry, io.BytesIO(content))
        try:
            temporary_archive.chmod(0o600)
        except OSError:
            if os.name != "nt":
                raise

        stem = "backup-" + now.strftime("%Y%m%d-%H%M%S")
        created = output / (stem + ".tar.gz")
        suffix = 0
        while os.path.lexists(created):
            suffix += 1
            created = output / f"{stem}-{suffix}.tar.gz"
        os.replace(temporary_archive, created)
        removed = _prune_archives(output, created, keep)
        policy = "全部保留" if keep == 0 else f"保留最近 {keep} 份"
        print(f"备份完成：{created}；{created.stat().st_size} 字节；schema {version}；头像 {len(avatar_paths)} 个；笔记图片 {len(extra_paths["note_files"])} 个；画板缩略图 {len(extra_paths["drawings"])} 个；{policy}，删除旧归档 {removed} 份。")
        return created
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            if temporary_paths:
                Path(str(temporary_paths[0]) + suffix).unlink(missing_ok=True)


def _is_windows_device(part):
    stem = part.partition(".")[0].rstrip(" ").upper()
    return stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem) is not None


def _validate_member(member):
    name = member.name
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or PureWindowsPath(name).drive or ".." in path.parts or not path.parts:
        raise BackupError("归档含不安全的成员路径")
    # Windows 盘符、设备名和尾部归一化也可能绕过普通的相对路径检查。
    if any(
        ":" in part or "\x00" in part or part.endswith((" ", "."))
        or _is_windows_device(part)
        for part in path.parts
    ):
        raise BackupError("归档含不安全的成员路径")
    if not (member.isfile() or member.isdir()):
        raise BackupError("归档含符号链接、硬链接或不支持的特殊成员")
    if path not in (PurePosixPath("notebook.db"), PurePosixPath("MANIFEST.json")) and path.parts[0] not in ("avatars", *EXTRA_DIRS):
        raise BackupError("归档含不支持的成员路径")
    return path


def _read_manifest(archive, members):
    for name in ("notebook.db", "MANIFEST.json"):
        if name not in members or not members[name].isfile():
            raise BackupError(f"归档缺少普通文件 {name}")
    if members["MANIFEST.json"].size > 1024 * 1024:
        raise BackupError("MANIFEST.json 过大")
    try:
        with archive.extractfile(members["MANIFEST.json"]) as source:
            manifest = json.load(source)
    except (ValueError, UnicodeError):
        raise BackupError("MANIFEST.json 无法解析") from None
    required = {"format", "created_at", "database_sha256", "database_bytes", "schema_version", "tables", "avatar_files"}
    if not isinstance(manifest, dict) or not required <= manifest.keys():
        raise BackupError("MANIFEST.json 字段不完整")
    if type(manifest["format"]) is not int or manifest["format"] != 1:
        raise BackupError("不支持的备份格式")
    for field in ("database_bytes", "schema_version", "avatar_files"):
        if type(manifest[field]) is not int or manifest[field] < 0:
            raise BackupError(f"MANIFEST.json 的 {field} 无效")
    if not isinstance(manifest["database_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["database_sha256"]):
        raise BackupError("MANIFEST.json 的 database_sha256 无效")
    if not isinstance(manifest["tables"], dict) or any(type(count) is not int or count < 0 for count in manifest["tables"].values()):
        raise BackupError("MANIFEST.json 的 tables 无效")
    try:
        timestamp = datetime.fromisoformat(manifest["created_at"])
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError
    except (TypeError, ValueError):
        raise BackupError("MANIFEST.json 的 created_at 不是 UTC ISO 时间") from None
    count = sum(member.isfile() for name, member in members.items() if name.startswith("avatars/"))
    if manifest["avatar_files"] != count:
        raise BackupError("头像文件数量与 MANIFEST.json 不一致")
    extra = manifest.get("extra_files", {})
    if not isinstance(extra, dict) or set(extra) - set(EXTRA_DIRS) or any(
        type(value) is not int or value < 0 for value in extra.values()
    ):
        raise BackupError("MANIFEST.json 的 extra_files 无效")
    for name in EXTRA_DIRS:
        actual = sum(member.isfile() for member_name, member in members.items() if member_name.startswith(name + "/"))
        if extra.get(name, 0) != actual:
            raise BackupError("笔记图片或画板缩略图数量与 MANIFEST.json 不一致")
    return manifest


@contextmanager
def _verified_archive(archive_path):
    with tarfile.open(_resolve_path(archive_path), "r:gz") as archive:
        members = {}
        normalized_names = set()
        for member in archive.getmembers():
            name = _validate_member(member).as_posix()
            normalized = os.path.normcase(name)
            if normalized in normalized_names:
                raise BackupError("归档含重复成员路径")
            normalized_names.add(normalized)
            members[name] = member
        for name in members:
            for parent in PurePosixPath(name).parents:
                if parent.as_posix() in members and not members[parent.as_posix()].isdir():
                    raise BackupError("归档成员的父路径不是目录")
        for folder in ("avatars", *EXTRA_DIRS):
            if folder in members and not members[folder].isdir():
                raise BackupError(f"归档中的 {folder} 不是目录")
        manifest = _read_manifest(archive, members)
        if members["notebook.db"].size != manifest["database_bytes"]:
            raise BackupError("数据库大小与 MANIFEST.json 的 database_bytes 不一致")
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "notebook.db"
            with archive.extractfile(members["notebook.db"]) as source, database.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            if database.stat().st_size != manifest["database_bytes"]:
                raise BackupError("数据库大小与 MANIFEST.json 的 database_bytes 不一致")
            if _sha256(database) != manifest["database_sha256"]:
                raise BackupError("数据库 SHA-256 与 MANIFEST.json 的 database_sha256 不一致")
            version, tables = _database_metadata(database)
            if version != manifest["schema_version"] or tables != manifest["tables"]:
                raise BackupError("数据库 schema 版本或表行数与 MANIFEST.json 不一致")
            yield archive, members, manifest, database


def verify_archive(archive_path):
    with _verified_archive(archive_path) as (_, _, manifest, _):
        return manifest


def restore_archive(archive_path, into, force=False):
    target = _resolve_path(into)
    database_target = target / "notebook.db"
    avatars_target = target / "avatars"
    extra_targets = {name: target / name for name in EXTRA_DIRS}
    database = _resolve_path(os.getenv("DATABASE_PATH", "data/notebook.db"))
    avatars = _resolve_path(os.getenv("AVATAR_DIR", "data/avatars"))
    live_dirs = (database, avatars, _extra_directory("note_files"), _extra_directory("drawings"))
    destinations = (database_target.resolve(), avatars_target.resolve(),
                    *(path.resolve() for path in extra_targets.values()))
    if any(
        destination == live or destination in live.parents or live in destination.parents
        for destination in destinations for live in live_dirs
    ):
        raise BackupError("恢复目录与应用数据路径重合，请使用独立目录，再停服务手动替换")
    with _verified_archive(archive_path) as (archive, members, _, snapshot):
        if not force and any(os.path.lexists(path) for path in (database_target, avatars_target, *extra_targets.values())):
            raise BackupError("恢复目录已有 notebook.db、avatars、note_files 或 drawings，请换目录或使用 --force")
        if force:
            for path in (database_target, avatars_target, *extra_targets.values()):
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            for suffix in ("-wal", "-shm", "-journal"):
                path = Path(str(database_target) + suffix)
                if os.path.lexists(path):
                    path.unlink()
        elif any(os.path.lexists(Path(str(database_target) + suffix)) for suffix in ("-wal", "-shm", "-journal")):
            raise BackupError("恢复目录已有 SQLite 日志文件，请换目录或使用 --force")
        target.mkdir(parents=True, exist_ok=True)
        avatars_target.mkdir()
        for folder in extra_targets.values():
            folder.mkdir()
        shutil.copyfile(snapshot, database_target)
        database_target.chmod(0o600)
        for name, member in members.items():
            if not name.startswith(("avatars/", "note_files/", "drawings/")):
                continue
            destination = target.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", help="备份输出目录，默认 data/backups")
    parser.add_argument("--keep", type=int, help="保留份数，默认 14，0 表示全部保留")
    commands = parser.add_subparsers(dest="command")
    verify = commands.add_parser("verify", help="校验备份归档")
    verify.add_argument("archive")
    restore = commands.add_parser("restore", help="恢复到独立目录")
    restore.add_argument("archive")
    restore.add_argument("--into", required=True, help="独立的恢复目录")
    restore.add_argument("--force", action="store_true", help="覆盖恢复目录已有数据库和头像")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            verify_archive(args.archive)
            print(f"校验通过：{_resolve_path(args.archive)}；数据库大小、SHA-256 与完整性检查均通过。")
        elif args.command == "restore":
            target = restore_archive(args.archive, args.into, args.force)
            print(f"恢复完成：{target}")
            print("下一步：停止服务 → 手动替换应用配置的数据库与头像目录 → 启动服务 → 访问 /healthz。")
            print("替换前保留原数据副本；不要让旧的 notebook.db-wal、notebook.db-shm 与恢复出的数据库混用。")
        else:
            create_backup(args.output_dir, args.keep)
        return 0
    except (BackupError, OSError, sqlite3.Error, tarfile.TarError) as error:
        print(f"备份操作失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
