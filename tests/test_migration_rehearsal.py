"""升级演练脚本的端到端测试。

用真实迁移链造一个 v16 旧库（只跑 MIGRATIONS 中版本号 <= 16 的部分，
这是仓库自己的迁移代码，不是手写的假结构），打成 backup.py 格式的
tar.gz，再用子进程跑 tools/migration_rehearsal.py，断言：
- 退出码为 0，报告生成且结论为通过；
- 用户数/题目数/易错点数/复习记录数升级前后一致；
- 原备份文件字节没有任何变化（sha256 前后一致）。

子进程隔离：测试里用 monkeypatch 改了 db.MIGRATIONS，必须在子进程里
跑演练脚本，否则补丁会污染演练。
"""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

import db

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "migration_rehearsal.py"


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_v16_database(path, monkeypatch):
    """用真实迁移链（截断到 v16）造旧库，并写入几行数据。"""
    monkeypatch.setenv("DATABASE_PATH", str(path))
    old_migrations = [m for m in db.MIGRATIONS if m[0] <= 16]
    assert old_migrations, "MIGRATIONS 里没有 <= 16 的迁移"
    monkeypatch.setattr(db, "MIGRATIONS", old_migrations)
    monkeypatch.setattr(db, "SCHEMA_VERSION", 16)
    db.init_db()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
        conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at)"
            " VALUES ('rehearsal_alice', 'hash1', 'Asia/Shanghai',"
            " '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at)"
            " VALUES ('rehearsal_bob', 'hash2', 'Asia/Shanghai',"
            " '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO problems (user_id, title, language, code, thinking, created_at)"
            " VALUES (1, '二分查找', 'Python', 'pass', '边界', '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO problems (user_id, title, language, code, thinking, created_at)"
            " VALUES (2, '动态规划', 'Python', 'pass', '状态', '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO mistakes (problem_id, description, due_date)"
            " VALUES (1, '错因一', '2026-01-02')"
        )
        conn.execute(
            "INSERT INTO mistakes (problem_id, description, due_date)"
            " VALUES (2, '错因二', '2026-01-03')"
        )
        conn.execute(
            "INSERT INTO reviews (mistake_id, quality, reviewed_at, next_due_date)"
            " VALUES (1, 4, '2026-01-01T00:00:00+00:00', '2026-01-05')"
        )
        conn.commit()


def make_fake_backup(db_path, backup_path):
    """按 backup.py 的格式打包：notebook.db + avatars/ + MANIFEST.json。"""
    tables = {}
    with closing(sqlite3.connect(db_path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for table in ("users", "problems", "mistakes", "reviews"):
            tables[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    manifest = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database_sha256": sha256_of(db_path),
        "database_bytes": Path(db_path).stat().st_size,
        "schema_version": version,
        "tables": tables,
        "avatar_files": 0,
    }
    with tarfile.open(backup_path, "w:gz") as archive:
        archive.add(db_path, arcname="notebook.db")
        directory = tarfile.TarInfo("avatars")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        content = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        entry = tarfile.TarInfo("MANIFEST.json")
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))


def run_rehearsal(backup_path, workdir, report_path, env_extra=None):
    env = dict(os.environ)
    env.pop("DATABASE_PATH", None)  # 不能把测试的补丁环境漏进子进程
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(backup_path),
         "--workdir", str(workdir), "--report", str(report_path)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_rehearsal_upgrades_old_backup_end_to_end(tmp_path, monkeypatch):
    latest = db.SCHEMA_VERSION  # 在 monkeypatch 之前记下真实最新版本号
    old_db = tmp_path / "old.db"
    build_v16_database(old_db, monkeypatch)

    backup = tmp_path / "backup-test.tar.gz"
    make_fake_backup(old_db, backup)
    sha_before = sha256_of(backup)

    workdir = tmp_path / "work"
    report_path = tmp_path / "rehearsal-report.txt"
    result = run_rehearsal(backup, workdir, report_path)

    assert result.returncode == 0, f"演练脚本退出码非 0：\n{result.stdout}\n{result.stderr}"
    # 原备份一个字节都没变。
    assert sha256_of(backup) == sha_before
    # 报告生成且结论通过。
    assert report_path.is_file()
    text = report_path.read_text(encoding="utf-8")
    assert "结论：通过" in text
    assert "升级前 user_version：16" in text
    assert f"升级后 user_version={latest}" in text
    # 行数一致。
    assert "users：2 → 2 行" in text
    assert "problems：2 → 2 行" in text
    assert "mistakes：2 → 2 行" in text
    assert "reviews：1 → 1 行" in text
    assert "原备份文件字节完全一致，未被修改" in text


def test_rehearsal_rejects_missing_file(tmp_path):
    result = run_rehearsal(tmp_path / "不存在.tar.gz", tmp_path / "w", tmp_path / "r.txt")
    assert result.returncode == 2


def test_rehearsal_rejects_archive_without_database(tmp_path):
    backup = tmp_path / "empty.tar.gz"
    with tarfile.open(backup, "w:gz") as archive:
        content = b"{}"
        entry = tarfile.TarInfo("MANIFEST.json")
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))
    result = run_rehearsal(backup, tmp_path / "w", tmp_path / "r.txt")
    assert result.returncode == 1
    assert "notebook.db" in result.stdout
