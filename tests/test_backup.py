import hashlib
import io
import json
import os
import sqlite3
import tarfile
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pytest

import backup


@pytest.fixture
def backup_data(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    database = source / "notebook.db"
    avatars = source / "avatars"
    output = tmp_path / "backups"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("AVATAR_DIR", str(avatars))
    monkeypatch.setenv("BACKUP_DIR", str(output))
    monkeypatch.setenv("BACKUP_KEEP", "0")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    import db

    db.init_db()
    with db.connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO users(username,password_hash,timezone,created_at) "
            "VALUES (?, 'test-password-hash', 'Asia/Shanghai', '2026-10-03')",
            [("备份用户甲",), ("备份用户乙",)],
        )
        user_ids = [row[0] for row in conn.execute("SELECT id FROM users ORDER BY id")]
        for user_id in user_ids:
            problem = conn.execute(
                "INSERT INTO problems(user_id,title,language,code,thinking,created_at) "
                "VALUES (?, '两数之和', 'Python', 'pass', '先看边界', '2026-10-03')",
                (user_id,),
            )
            mistake = conn.execute(
                "INSERT INTO mistakes(problem_id,description,due_date) "
                "VALUES (?, '漏了边界', '2026-10-04')",
                (problem.lastrowid,),
            )
            conn.execute(
                "INSERT INTO reviews(mistake_id,quality,reviewed_at,next_due_date) "
                "VALUES (?, 4, '2026-10-03T00:00:00+00:00', '2026-10-04')",
                (mistake.lastrowid,),
            )
        conn.execute("PRAGMA user_version = 37")

    avatar_bytes = {
        "1.png": b"\x89PNG\r\n\x1a\nfirst-avatar",
        "nested/2.jpg": b"\xff\xd8\xffsecond-avatar",
    }
    for name, content in avatar_bytes.items():
        path = avatars / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {
        "database": database,
        "avatars": avatars,
        "output": output,
        "avatar_bytes": avatar_bytes,
    }


def read_archive(path):
    with tarfile.open(path, "r:gz") as archive:
        manifest = json.loads(archive.extractfile("MANIFEST.json").read())
        database = archive.extractfile("notebook.db").read()
        names = archive.getnames()
    return manifest, database, names


def rewrite_archive(source, target, transform=None, extra=None):
    with tarfile.open(source, "r:gz") as original:
        with tarfile.open(target, "w:gz") as changed:
            for member in original.getmembers():
                content = original.extractfile(member).read() if member.isfile() else None
                if transform is not None and content is not None:
                    content = transform(member.name, content)
                    member.size = len(content)
                changed.addfile(member, io.BytesIO(content) if content is not None else None)
            if extra is not None:
                member, content = extra
                changed.addfile(member, io.BytesIO(content) if content is not None else None)


def database_counts(path):
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("users", "problems", "mistakes", "reviews")
        }


def test_backup_verify_restore_round_trip(backup_data, tmp_path, capsys):
    archive = backup.create_backup()
    manifest, snapshot, names = read_archive(archive)
    assert archive.parent == backup_data["output"]
    assert {
        "format", "created_at", "database_sha256", "database_bytes",
        "schema_version", "tables", "avatar_files",
    } <= manifest.keys()
    assert manifest["format"] == 1
    assert datetime.fromisoformat(manifest["created_at"]).utcoffset().total_seconds() == 0
    assert manifest["database_sha256"] == hashlib.sha256(snapshot).hexdigest()
    assert manifest["database_bytes"] == len(snapshot)
    with closing(sqlite3.connect(backup_data["database"])) as conn:
        assert manifest["schema_version"] == conn.execute("PRAGMA user_version").fetchone()[0]
    assert manifest["tables"] == database_counts(backup_data["database"])
    assert manifest["avatar_files"] == len(backup_data["avatar_bytes"])
    assert {"notebook.db", "MANIFEST.json", "avatars/1.png", "avatars/nested/2.jpg"} <= set(names)
    assert not any(name.startswith(".backup-tmp-") for name in names)
    assert backup.verify_archive(archive) == manifest
    assert backup.main(["verify", str(archive)]) == 0

    restored = tmp_path / "restored"
    assert backup.restore_archive(archive, restored) == restored.resolve()
    assert database_counts(restored / "notebook.db") == manifest["tables"]
    for name, content in backup_data["avatar_bytes"].items():
        assert (restored / "avatars" / name).read_bytes() == content
    assert not list(backup_data["output"].glob(".backup-tmp-*"))
    if os.name != "nt":
        assert archive.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert "备份用户甲" not in output
    assert "test-password-hash" not in output
    assert "两数之和" not in output


def test_online_backup_is_consistent_while_wal_writer_is_active(backup_data):
    database = backup_data["database"]
    with closing(sqlite3.connect(database)) as conn, conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        conn.execute("CREATE TABLE backup_load(id INTEGER PRIMARY KEY, payload BLOB)")
        conn.executemany(
            "INSERT INTO backup_load(payload) VALUES (?)",
            [(bytes(range(256)) * 8,)] * 3000,
        )
    started = threading.Event()
    stop = threading.Event()
    write_times = []
    errors = []

    def write_continuously():
        try:
            deadline = time.monotonic() + 10
            with closing(sqlite3.connect(database, timeout=10)) as conn, conn:
                while not stop.is_set() and time.monotonic() < deadline:
                    conn.execute("INSERT INTO backup_load(payload) VALUES (?)", (b"concurrent",))
                    conn.commit()
                    write_times.append(time.monotonic())
                    started.set()
                    stop.wait(0.001)
        except Exception as error:
            errors.append(error)
            started.set()

    writer = threading.Thread(target=write_continuously)
    writer.start()
    try:
        assert started.wait(10), "并发写入线程没有启动"
        start = time.monotonic()
        archive = backup.create_backup()
        end = time.monotonic()
    finally:
        stop.set()
        writer.join(timeout=10)
    assert not writer.is_alive()
    assert not errors
    assert any(start <= timestamp <= end for timestamp in write_times)
    assert backup.verify_archive(archive)["schema_version"] == 37


def test_retention_only_removes_older_matching_files(backup_data):
    output = backup_data["output"]
    output.mkdir()
    old = []
    for index in range(4):
        path = output / f"backup-20000101-00000{index}.tar.gz"
        path.write_bytes(f"old-{index}".encode())
        os.utime(path, (946684800 + index, 946684800 + index))
        old.append(path)
    unrelated = output / "notes.tar.gz"
    unrelated.write_bytes(b"keep-me")
    directory = output / "backup-19990101-000000.tar.gz"
    directory.mkdir()
    (directory / "unchanged.txt").write_text("保留目录", encoding="utf-8")
    new = backup.create_backup(keep=2)
    archives = {path for path in output.glob("backup-*.tar.gz") if path.is_file()}
    assert archives == {old[-1], new}
    assert unrelated.read_bytes() == b"keep-me"
    assert (directory / "unchanged.txt").read_text(encoding="utf-8") == "保留目录"


def test_keep_zero_preserves_all_archives(backup_data):
    output = backup_data["output"]
    output.mkdir()
    old = output / "backup-20000101-000000.tar.gz"
    old.write_bytes(b"old")
    new = backup.create_backup(keep=0)
    assert old.read_bytes() == b"old"
    assert new.is_file()


def test_retention_preserves_archives_newer_than_current_backup(backup_data):
    output = backup_data["output"]
    output.mkdir()
    future = output / "backup-20990101-000000.tar.gz"
    future.write_bytes(b"future")
    os.utime(future, (time.time() + 86400, time.time() + 86400))
    new = backup.create_backup(keep=1)
    assert future.read_bytes() == b"future"
    assert new.is_file()


def test_same_second_uses_incrementing_suffix(backup_data, monkeypatch):
    frozen = datetime(2026, 10, 3, 4, 5, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(backup, "_utc_now", lambda: frozen)
    archives = [backup.create_backup(keep=0) for _ in range(3)]
    assert [path.name for path in archives] == [
        "backup-20261003-040506.tar.gz",
        "backup-20261003-040506-1.tar.gz",
        "backup-20261003-040506-2.tar.gz",
    ]
    assert all(backup.verify_archive(path)["format"] == 1 for path in archives)


@pytest.mark.parametrize("child", ["", "backups"])
def test_output_cannot_be_inside_avatar_directory(backup_data, child):
    output = backup_data["avatars"] / child
    with pytest.raises(backup.BackupError):
        backup.create_backup(output_dir=output)
    assert backup.main(["--output-dir", str(output)]) == 1
    assert not list(backup_data["avatars"].rglob("backup-*.tar.gz"))


def test_missing_database_returns_one_without_creating_it(backup_data, tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("DATABASE_PATH", str(missing))
    assert backup.main([]) == 1
    assert not missing.exists()
    message = capsys.readouterr()
    assert "数据库" in message.out + message.err
    assert "不存在" in message.out + message.err


@pytest.mark.parametrize("kind", ["database_byte", "database_bytes", "database_sha256"])
def test_corruption_is_rejected_before_restore(backup_data, tmp_path, kind):
    archive = backup.create_backup()
    damaged = tmp_path / "damaged.tar.gz"

    def corrupt(name, content):
        if kind == "database_byte" and name == "notebook.db":
            changed = bytearray(content)
            changed[100] ^= 1
            return bytes(changed)
        if name == "MANIFEST.json" and kind != "database_byte":
            manifest = json.loads(content)
            if kind == "database_bytes":
                manifest["database_bytes"] += 1
            else:
                manifest["database_sha256"] = "0" * 64
            return json.dumps(manifest).encode()
        return content

    rewrite_archive(archive, damaged, transform=corrupt)
    assert backup.main(["verify", str(damaged)]) == 1
    target = tmp_path / "restore-damaged"
    assert backup.main(["restore", str(damaged), "--into", str(target)]) == 1
    assert not (target / "notebook.db").exists()
    assert not (target / "avatars").exists()


def test_matching_manifest_hash_does_not_skip_sqlite_integrity_check(backup_data, tmp_path):
    archive = backup.create_backup()
    damaged = tmp_path / "invalid-sqlite.tar.gz"
    _, snapshot, _ = read_archive(archive)
    changed = bytearray(snapshot)
    changed[0] ^= 1
    broken_database = bytes(changed)

    def corrupt(name, content):
        if name == "notebook.db":
            return broken_database
        if name == "MANIFEST.json":
            manifest = json.loads(content)
            manifest["database_sha256"] = hashlib.sha256(broken_database).hexdigest()
            manifest["database_bytes"] = len(broken_database)
            return json.dumps(manifest).encode()
        return content

    rewrite_archive(archive, damaged, transform=corrupt)
    assert backup.main(["verify", str(damaged)]) == 1
    target = tmp_path / "restore-invalid-sqlite"
    assert backup.main(["restore", str(damaged), "--into", str(target)]) == 1
    assert not (target / "notebook.db").exists()


@pytest.mark.parametrize("kind", [
    "parent", "absolute", "windows", "symlink", "hardlink",
    "embedded_drive", "drive_relative", "alternate_stream",
    "trailing_space", "trailing_dot", "reserved_device", "reserved_extension",
])
def test_archive_path_traversal_and_links_never_write_outside_target(backup_data, tmp_path, kind):
    archive = backup.create_backup()
    malicious = tmp_path / f"malicious-{kind}.tar.gz"
    evil = tmp_path / "evil"
    if kind == "parent":
        member = tarfile.TarInfo("../evil")
    elif kind == "absolute":
        member = tarfile.TarInfo(evil.as_posix())
    elif kind == "windows":
        member = tarfile.TarInfo("C:/evil")
    elif kind in {
        "embedded_drive", "drive_relative", "alternate_stream", "trailing_space",
        "trailing_dot", "reserved_device", "reserved_extension",
    }:
        names = {
            "embedded_drive": "avatars/D:/evil",
            "drive_relative": "avatars/D:evil",
            "alternate_stream": "avatars/file:ads",
            "trailing_space": "avatars/.. /evil",
            "trailing_dot": "avatars/name./evil",
            "reserved_device": "avatars/CON",
            "reserved_extension": "avatars/NUL.txt",
        }
        member = tarfile.TarInfo(names[kind])
    else:
        member = tarfile.TarInfo("avatars/evil")
        member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        member.linkname = "../../evil"
    content = b"must-not-extract" if member.isfile() else None
    member.size = len(content) if content is not None else 0
    rewrite_archive(archive, malicious, extra=(member, content))
    target = tmp_path / "restore-malicious"
    assert backup.main(["verify", str(malicious)]) == 1
    assert backup.main(["restore", str(malicious), "--into", str(target)]) == 1
    assert not evil.exists()
    assert not (target / "notebook.db").exists()
    assert not (target / "avatars").exists()


@pytest.mark.parametrize("existing", ["notebook.db", "avatars"])
def test_restore_requires_force_for_existing_content(backup_data, tmp_path, existing):
    archive = backup.create_backup()
    target = tmp_path / "existing"
    target.mkdir()
    if existing == "notebook.db":
        (target / existing).write_bytes(b"existing-database")
    else:
        (target / existing).mkdir()
        (target / existing / "stale.png").write_bytes(b"stale-avatar")
    assert backup.main(["restore", str(archive), "--into", str(target)]) == 1
    if existing == "notebook.db":
        assert (target / existing).read_bytes() == b"existing-database"
    else:
        assert (target / existing / "stale.png").read_bytes() == b"stale-avatar"
    assert backup.main(["restore", str(archive), "--into", str(target), "--force"]) == 0
    assert database_counts(target / "notebook.db") == database_counts(backup_data["database"])
    assert not (target / "avatars" / "stale.png").exists()


def test_restore_force_cannot_overwrite_configured_application_data(backup_data):
    archive = backup.create_backup()
    before = backup_data["database"].read_bytes()
    target = backup_data["database"].parent
    assert backup.main(["restore", str(archive), "--into", str(target), "--force"]) == 1
    assert backup_data["database"].read_bytes() == before
    for name, content in backup_data["avatar_bytes"].items():
        assert (backup_data["avatars"] / name).read_bytes() == content


@pytest.mark.parametrize("relationship", ["same", "parent", "child", "contains_database"])
@pytest.mark.parametrize("force", [False, True])
def test_restore_rejects_application_data_directory_overlap(
    backup_data, tmp_path, monkeypatch, relationship, force
):
    archive = backup.create_backup()
    target = tmp_path / "overlap-target"
    if relationship == "same":
        live_avatars = target / "avatars"
    elif relationship == "parent":
        live_avatars = target / "avatars" / "live"
    elif relationship == "child":
        live_avatars = tmp_path / "live-avatars"
        target = live_avatars / "restore-area"
    else:
        live_avatars = backup_data["avatars"]
        live_database = target / "avatars" / "application" / "notebook.db"
        live_database.parent.mkdir(parents=True)
        live_database.write_bytes(backup_data["database"].read_bytes())
        monkeypatch.setenv("DATABASE_PATH", str(live_database))
    live_avatars.mkdir(parents=True, exist_ok=True)
    marker = live_avatars / "must-preserve.png"
    marker.write_bytes(b"application-data")
    monkeypatch.setenv("AVATAR_DIR", str(live_avatars))
    arguments = ["restore", str(archive), "--into", str(target)]
    if force:
        arguments.append("--force")
    assert backup.main(arguments) == 1
    assert marker.read_bytes() == b"application-data"
    assert not (target / "notebook.db").exists()
    if relationship == "contains_database":
        assert database_counts(live_database) == database_counts(backup_data["database"])


def test_environment_paths_are_relative_to_script_and_cli_has_priority(backup_data, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "source/notebook.db")
    monkeypatch.setenv("AVATAR_DIR", "source/avatars")
    monkeypatch.setenv("BACKUP_DIR", "environment-backups")
    monkeypatch.setenv("BACKUP_KEEP", "1")
    first = backup.create_backup()
    second = backup.create_backup()
    assert first.parent == tmp_path / "environment-backups"
    assert not first.exists()
    assert second.is_file()
    assert backup.verify_archive(second)["avatar_files"] == 2

    cli_output = tmp_path / "cli-backups"
    cli_output.mkdir()
    old = cli_output / "backup-20000101-000000.tar.gz"
    old.write_bytes(b"keep-with-cli-zero")
    assert backup.main(["--output-dir", str(cli_output), "--keep", "0"]) == 0
    assert old.read_bytes() == b"keep-with-cli-zero"
    assert len(list(cli_output.glob("backup-*.tar.gz"))) == 2


def test_default_paths_and_fourteen_archive_retention(backup_data, tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    for name in ("DATABASE_PATH", "AVATAR_DIR", "BACKUP_DIR", "BACKUP_KEEP"):
        monkeypatch.delenv(name, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    (data / "notebook.db").write_bytes(backup_data["database"].read_bytes())
    for name, content in backup_data["avatar_bytes"].items():
        avatar = data / "avatars" / name
        avatar.parent.mkdir(parents=True, exist_ok=True)
        avatar.write_bytes(content)
    output = data / "backups"
    output.mkdir()
    old = []
    for index in range(15):
        path = output / f"backup-20000101-0000{index:02}.tar.gz"
        path.write_bytes(b"old")
        os.utime(path, (946684800 + index, 946684800 + index))
        old.append(path)
    archive = backup.create_backup()
    assert archive.parent == output
    assert set(output.glob("backup-*.tar.gz")) == {*old[-13:], archive}
    manifest = backup.verify_archive(archive)
    assert manifest["schema_version"] == 37
    assert manifest["avatar_files"] == 2


def test_source_database_connection_is_read_only(backup_data, monkeypatch):
    original_connect = sqlite3.connect
    calls = []

    def record_connect(database, *args, **kwargs):
        calls.append((str(database), kwargs))
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, "connect", record_connect)
    backup.create_backup()
    source_uri = backup_data["database"].resolve().as_uri()
    source_calls = [(database, kwargs) for database, kwargs in calls if database.split("?", 1)[0] == source_uri]
    assert source_calls
    assert all("mode=ro" in database and kwargs.get("uri") is True for database, kwargs in source_calls)


@pytest.mark.parametrize("avatars_present", [False, True])
def test_missing_or_empty_avatar_directory_is_allowed(backup_data, tmp_path, monkeypatch, avatars_present):
    avatars = tmp_path / "empty-avatars"
    if avatars_present:
        avatars.mkdir()
    monkeypatch.setenv("AVATAR_DIR", str(avatars))
    archive = backup.create_backup()
    assert backup.verify_archive(archive)["avatar_files"] == 0
    target = tmp_path / "restore-empty"
    backup.restore_archive(archive, target)
    assert (target / "avatars").is_dir()
    assert not list((target / "avatars").iterdir())


def test_absent_tables_are_omitted_from_manifest(backup_data, tmp_path, monkeypatch):
    database = tmp_path / "minimal.db"
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute("CREATE TABLE only_table(id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 9")
    monkeypatch.setenv("DATABASE_PATH", str(database))
    manifest = backup.verify_archive(backup.create_backup())
    assert manifest["tables"] == {}
    assert manifest["schema_version"] == 9


def test_archive_write_failure_cleans_snapshot_and_partial_archive(backup_data, monkeypatch):
    def fail_open(*args, **kwargs):
        raise OSError("模拟归档写入失败")

    monkeypatch.setattr(backup.tarfile, "open", fail_open)
    assert backup.main([]) == 1
    assert not list(backup_data["output"].glob(".backup-tmp-*"))
    assert not list(backup_data["output"].glob("backup-*.tar.gz"))


def test_failed_snapshot_integrity_check_leaves_no_archive(backup_data, monkeypatch):
    def fail_integrity(path):
        raise backup.BackupError("数据库完整性检查失败")

    monkeypatch.setattr(backup, "_check_integrity", fail_integrity)
    assert backup.main([]) == 1
    assert not list(backup_data["output"].glob(".backup-tmp-*"))
    assert not list(backup_data["output"].glob("backup-*.tar.gz"))


@pytest.mark.parametrize("name", [
    "../evil", "avatars/../evil", "/evil", "C:/evil", "C:evil",
    "\\\\server\\share\\evil", "avatars\\evil",
    "avatars/D:/evil", "avatars/D:evil", "avatars/file:ads",
    "avatars/.. /evil", "avatars/name./evil", "avatars/CON", "avatars/NUL.txt",
])
def test_validate_member_rejects_unsafe_names_without_temp(name):
    with pytest.raises(backup.BackupError):
        backup._validate_member(tarfile.TarInfo(name))


@pytest.mark.parametrize("kind", [
    tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE,
    tarfile.BLKTYPE, tarfile.FIFOTYPE,
])
def test_validate_member_rejects_special_types_without_temp(kind):
    member = tarfile.TarInfo("avatars/evil")
    member.type = kind
    member.linkname = "../../evil"
    with pytest.raises(backup.BackupError):
        backup._validate_member(member)


@pytest.mark.parametrize("name,kind", [
    ("notebook.db", tarfile.REGTYPE),
    ("MANIFEST.json", tarfile.REGTYPE),
    ("avatars/", tarfile.DIRTYPE),
    ("avatars/nested/头像.png", tarfile.REGTYPE),
])
def test_validate_member_accepts_regular_relative_paths_without_temp(name, kind):
    member = tarfile.TarInfo(name)
    member.type = kind
    assert backup._validate_member(member) == PurePosixPath(name)


def test_resolve_path_uses_script_root_and_expands_user_without_temp():
    assert backup._resolve_path("data/notebook.db") == (backup.ROOT / "data/notebook.db").resolve()
    assert backup._resolve_path("~/backup-test") == Path("~/backup-test").expanduser().resolve()


@pytest.mark.parametrize("args,expected", [
    (["verify", "example.tar.gz"], "verify"),
    (["restore", "example.tar.gz", "--into", "restored", "--force"], "restore"),
])
def test_main_dispatches_subcommands_without_temp(monkeypatch, capsys, args, expected):
    calls = []

    def verify(path):
        calls.append(("verify", str(path)))
        return {"schema_version": 37, "avatar_files": 2}

    def restore(path, into, force=False):
        calls.append(("restore", str(path), str(into), force))
        return Path(into).resolve()

    monkeypatch.setattr(backup, "verify_archive", verify)
    monkeypatch.setattr(backup, "restore_archive", restore)
    monkeypatch.setenv("BACKUP_KEEP", "invalid-create-only-option")
    assert backup.main(args) == 0
    assert calls[0][0] == expected
    if expected == "restore":
        assert calls[0][-1] is True
        output = capsys.readouterr().out
        assert "停" in output and "启动" in output and "/healthz" in output


def test_main_reports_business_failure_without_temp(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise backup.BackupError("模拟业务失败")

    monkeypatch.setattr(backup, "create_backup", fail)
    assert backup.main([]) == 1
    captured = capsys.readouterr()
    assert "模拟业务失败" in captured.out + captured.err


def test_invalid_keep_from_environment_is_rejected_without_temp(monkeypatch, capsys):
    monkeypatch.setenv("BACKUP_KEEP", "not-a-number")
    assert backup.main([]) == 1
    captured = capsys.readouterr()
    assert "BACKUP_KEEP" in captured.out + captured.err


class MemoryArchive:
    def __init__(self, members, content):
        self.members = members
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getmembers(self):
        return self.members

    def extractfile(self, member):
        name = member if isinstance(member, str) else member.name
        return io.BytesIO(self.content[name])


def memory_archive(manifest=None):
    database = b"sqlite-content-not-extracted"
    if manifest is None:
        manifest = {
            "format": 1,
            "created_at": "2026-10-03T00:00:00+00:00",
            "database_sha256": hashlib.sha256(database).hexdigest(),
            "database_bytes": len(database),
            "schema_version": 37,
            "tables": {"users": 2},
            "avatar_files": 0,
        }
    content = {
        "notebook.db": database,
        "MANIFEST.json": json.dumps(manifest).encode("utf-8"),
    }
    members = []
    for name, data in content.items():
        member = tarfile.TarInfo(name)
        member.size = len(data)
        members.append(member)
    directory = tarfile.TarInfo("avatars")
    directory.type = tarfile.DIRTYPE
    members.append(directory)
    return MemoryArchive(members, content), manifest


def test_read_manifest_accepts_complete_utc_metadata_without_temp():
    archive, manifest = memory_archive()
    members = {member.name: member for member in archive.members}
    assert backup._read_manifest(archive, members) == manifest


@pytest.mark.parametrize("field,value", [
    ("format", True),
    ("format", 2),
    ("created_at", "2026-10-03T00:00:00"),
    ("created_at", "2026-10-03T00:00:00+08:00"),
    ("database_bytes", True),
    ("schema_version", -1),
    ("avatar_files", 1),
    ("database_sha256", "not-a-sha256"),
    ("tables", {"users": True}),
])
def test_read_manifest_rejects_invalid_fields_without_temp(field, value):
    _, manifest = memory_archive()
    manifest[field] = value
    archive, _ = memory_archive(manifest)
    members = {member.name: member for member in archive.members}
    with pytest.raises(backup.BackupError):
        backup._read_manifest(archive, members)


@pytest.mark.parametrize("problem", ["missing_field", "not_object", "invalid_json", "oversized"])
def test_read_manifest_rejects_invalid_document_without_temp(problem):
    archive, manifest = memory_archive()
    if problem == "missing_field":
        manifest.pop("schema_version")
        archive, _ = memory_archive(manifest)
    elif problem == "not_object":
        archive, _ = memory_archive([])
    elif problem == "invalid_json":
        archive.content["MANIFEST.json"] = b"not-json"
    members = {member.name: member for member in archive.members}
    if problem == "oversized":
        members["MANIFEST.json"].size = 1024 * 1024 + 1
    with pytest.raises(backup.BackupError):
        backup._read_manifest(archive, members)


@pytest.mark.parametrize("operation", ["verify", "restore"])
@pytest.mark.parametrize("problem", [
    "parent", "symlink", "hardlink", "duplicate", "case_alias", "file_parent", "avatars_file",
    "embedded_drive", "alternate_stream", "trailing_space", "trailing_dot", "reserved_device",
])
def test_archive_preflight_rejects_all_members_before_extracting_without_temp(
    monkeypatch, operation, problem
):
    archive, _ = memory_archive()
    if problem in {"parent", "embedded_drive", "alternate_stream", "trailing_space", "trailing_dot", "reserved_device"}:
        names = {
            "parent": "../evil",
            "embedded_drive": "avatars/D:/evil",
            "alternate_stream": "avatars/photo.png:ads",
            "trailing_space": "avatars/.. /evil",
            "trailing_dot": "avatars/name./evil",
            "reserved_device": "avatars/NUL.txt",
        }
        archive.members.append(tarfile.TarInfo(names[problem]))
    elif problem in {"symlink", "hardlink"}:
        member = tarfile.TarInfo("avatars/evil")
        member.type = tarfile.SYMTYPE if problem == "symlink" else tarfile.LNKTYPE
        member.linkname = "../../evil"
        archive.members.append(member)
    elif problem == "duplicate":
        archive.members.extend([
            tarfile.TarInfo("avatars/photo.png"), tarfile.TarInfo("avatars/./photo.png"),
        ])
    elif problem == "case_alias":
        archive.members.extend([
            tarfile.TarInfo("avatars/photo.png"), tarfile.TarInfo("avatars/PHOTO.PNG"),
        ])
        monkeypatch.setattr(backup.os.path, "normcase", lambda name: os.fspath(name).casefold())
    elif problem == "file_parent":
        archive.members.extend([
            tarfile.TarInfo("avatars/parent"), tarfile.TarInfo("avatars/parent/photo.png"),
        ])
    else:
        archive.members[-1].type = tarfile.REGTYPE

    def forbidden(*args, **kwargs):
        pytest.fail("恶意归档必须在创建临时目录或读取任何成员内容之前被拒绝")

    archive.extractfile = forbidden
    monkeypatch.setattr(backup.tarfile, "open", lambda *args, **kwargs: archive)
    monkeypatch.setattr(backup.tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setenv("DATABASE_PATH", str(backup.ROOT / "unused-preflight-source/notebook.db"))
    monkeypatch.setenv("AVATAR_DIR", str(backup.ROOT / "unused-preflight-source/avatars"))
    if operation == "verify":
        with pytest.raises(backup.BackupError):
            backup.verify_archive("not-opened-on-disk.tar.gz")
    else:
        with pytest.raises(backup.BackupError):
            backup.restore_archive("not-opened-on-disk.tar.gz", backup.ROOT / "unused-preflight-target")
