#!/usr/bin/env python3
"""数据库升级演练脚本（只读备份 → 临时副本升级 → 中文报告）。

用途：上线前，拿一份**真实备份**（backup.py 产生的 tar.gz）在本机演练
数据库升级，验证迁移链完整、数据不丢失，并估计上线停机时间。

用法：
    python tools/migration_rehearsal.py <backup.tar.gz> [--workdir DIR] [--report PATH]

安全保证（硬性）：
- 备份文件只以只读方式打开；演练前后各取一次 sha256 并在报告里对比，
  证明原备份**一个字节都没被改动**。
- 只在临时目录里操作解出的数据库副本；DATABASE_PATH 始终指向副本，
  **绝不连接线上数据库**。
- 不调用 AI / 邮件 / 支付；只做读 + 迁移写（写只发生在副本上）。

退出码：0 = 全部检查通过；1 = 有检查失败；2 = 用法/输入错误。
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

# 允许以脚本方式从仓库根目录直接运行：python tools/migration_rehearsal.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

# 行数对比的核心表（只统计存在的表）。
CORE_TABLES = (
    "users", "problems", "mistakes", "reviews", "mistake_tags",
    "posts", "post_comments", "study_groups", "study_group_members",
)

# 代表核心功能的抽样查询（读操作）。注意：这是演练用的代表性查询，
# 不是线上代码的逐字复制；目的是验证关键表/列升级后仍可正常读取。
SAMPLE_QUERIES = [
    ("总览统计：每用户题目数",
     "SELECT user_id, COUNT(*) FROM problems GROUP BY user_id LIMIT 5"),
    ("总览统计：每用户易错点数",
     "SELECT p.user_id, COUNT(*) FROM mistakes m "
     "JOIN problems p ON p.id = m.problem_id GROUP BY p.user_id LIMIT 5"),
    ("复习队列：已到期易错点",
     "SELECT COUNT(*) FROM mistakes WHERE due_date <= date('now')"),
    ("复习记录：最近复习",
     "SELECT mistake_id, quality FROM reviews ORDER BY reviewed_at DESC LIMIT 5"),
    ("掌握度：标签统计",
     "SELECT tag, COUNT(*) FROM mistake_tags GROUP BY tag LIMIT 10"),
]

ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_RESET = "\033[0m"


class RehearsalError(Exception):
    """演练失败（会写入报告并返回退出码 1）。"""


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _use_color():
    return sys.stdout.isatty()


def _mark(text, ok):
    """控制台输出：通过绿色 / 失败红色（非 tty 则不带颜色码）。"""
    if not _use_color():
        return text
    color = ANSI_GREEN if ok else ANSI_RED
    return f"{color}{text}{ANSI_RESET}"


def safe_extract_db(archive_path, dest_dir):
    """从备份 tar.gz 里安全解出 notebook.db 副本。

    只提取 notebook.db 一个成员；拒绝绝对路径、".." 和异常成员名，
    防止路径穿越。返回解出后的 Path。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "notebook.db"
    found = False
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            name = member.name.replace("\\", "/").lstrip("/")
            if name in ("notebook.db", "./notebook.db"):
                if not member.isfile():
                    raise RehearsalError("备份中的 notebook.db 不是普通文件")
                with archive.extractfile(member) as source, open(target, "wb") as out:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        out.write(chunk)
                found = True
                break
            # 顺带做一轮全成员名检查：备份里不应出现可疑路径。
            if name.startswith("/") or ".." in name.split("/"):
                raise RehearsalError(f"备份包含可疑成员路径，已拒绝解压：{member.name!r}")
    if not found:
        raise RehearsalError("备份里没有找到 notebook.db（不是 backup.py 产生的备份？）")
    return target


def read_manifest(archive_path):
    """读取 MANIFEST.json（仅作展示与交叉核对，不信任它的版本号做决策）。"""
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                name = member.name.replace("\\", "/").lstrip("/")
                if name in ("MANIFEST.json", "./MANIFEST.json"):
                    with archive.extractfile(member) as source:
                        return json.load(source)
    except (tarfile.TarError, json.JSONDecodeError, KeyError):
        pass
    return None


def snapshot_counts(conn):
    """记录 user_version 与核心表行数。"""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    counts = {}
    for table in CORE_TABLES:
        if table in tables:
            counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return version, counts


def run_upgrade(db_path):
    """用仓库自己的初始化流程升级副本。返回 (耗时秒, 应用的迁移列表, 前版本, 后版本)。

    失败时尝试定位是哪个迁移出错：看 user_version 停在了哪，
    下一个未应用的迁移就是嫌疑对象。
    """
    os.environ["DATABASE_PATH"] = str(db_path)
    with closing(sqlite3.connect(db_path)) as probe:
        before = probe.execute("PRAGMA user_version").fetchone()[0]
    started = time.monotonic()
    try:
        db.init_db()
    except Exception as exc:
        with closing(sqlite3.connect(db_path)) as probe:
            stuck_at = probe.execute("PRAGMA user_version").fetchone()[0]
        suspect = next(
            (f"{v}（{name}）" for v, name, _ in db.MIGRATIONS if v > stuck_at),
            "未知",
        )
        raise RehearsalError(
            f"升级失败：user_version 停在 {stuck_at}（升级前 {before}），"
            f"疑似出错的迁移是 {suspect}，原始错误：{exc}"
        ) from exc
    elapsed = time.monotonic() - started
    with closing(sqlite3.connect(db_path)) as probe:
        after = probe.execute("PRAGMA user_version").fetchone()[0]
    applied = [f"{v}（{name}）" for v, name, _ in db.MIGRATIONS if before < v <= after]
    return elapsed, applied, before, after


def check_integrity(db_path):
    """integrity_check 与 foreign_key_check。返回 (ok, 说明)。"""
    with closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        if rows != [("ok",)]:
            return False, f"integrity_check 未通过：{rows[:3]}"
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            return False, f"foreign_key_check 发现 {len(fk)} 条外键违规"
    return True, "integrity_check=ok，foreign_key_check 无违规"


def run_sample_queries(db_path):
    """抽样执行核心查询。返回 [(名称, 是否通过, 说明)]。"""
    results = []
    with closing(sqlite3.connect(db_path)) as conn:
        for name, sql in SAMPLE_QUERIES:
            try:
                rows = conn.execute(sql).fetchall()
                results.append((name, True, f"执行成功，返回 {len(rows)} 行"))
            except sqlite3.Error as exc:
                results.append((name, False, f"执行失败：{exc}"))
    return results


class Report:
    def __init__(self):
        self.lines = []
        self.failures = []

    def add(self, text=""):
        self.lines.append(text)

    def check(self, ok, text):
        """一条检查结论：ok 为 False 时记为失败。"""
        mark = "✓" if ok else "✗【失败】"
        self.lines.append(f"[{mark}] {text}")
        if not ok:
            self.failures.append(text)

    def text(self):
        return "\n".join(self.lines) + "\n"


def rehearse(archive_path, workdir=None, report_path=None):
    """执行完整演练。返回 (report_text, exit_code)。"""
    archive_path = Path(archive_path)
    report = Report()
    report.add("=" * 60)
    report.add("数据库升级演练报告")
    report.add("=" * 60)

    if not archive_path.is_file():
        report.add(f"【失败】备份文件不存在：{archive_path}")
        return report.text(), 2

    tmp = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="migration-rehearsal-"))
    tmp.mkdir(parents=True, exist_ok=True)

    sha_before = sha256_of(archive_path)
    size_before = archive_path.stat().st_size
    report.add(f"备份文件：{archive_path}")
    report.add(f"备份大小：{size_before} 字节")
    report.add(f"备份 sha256（演练前）：{sha_before}")
    manifest = read_manifest(archive_path)
    if manifest:
        report.add(
            "MANIFEST 信息：schema_version="
            f"{manifest.get('schema_version')}，tables={manifest.get('tables')}"
        )
    else:
        report.add("MANIFEST.json：未找到或无法解析（仅作参考，不影响演练）")
    report.add("")

    # 1. 解出副本
    report.add("── 步骤 1：解出数据库副本（只读备份，副本放在临时目录）──")
    try:
        db_copy = safe_extract_db(archive_path, tmp)
    except RehearsalError as exc:
        report.add(f"【失败】{exc}")
        return report.text(), 1
    report.add(f"副本路径：{db_copy}")
    report.check(True, "备份解压成功，原备份未被写入（演练结束会再次校验 sha256）")
    report.add("")

    # 2. 升级前快照
    report.add("── 步骤 2：升级前快照 ──")
    with closing(sqlite3.connect(db_copy)) as conn:
        ver_before, counts_before = snapshot_counts(conn)
    report.add(f"升级前 user_version：{ver_before}")
    for table, count in counts_before.items():
        report.add(f"  {table}：{count} 行")
    report.add("")

    # 3. 升级
    report.add("── 步骤 3：执行升级（仓库 init_db 流程）──")
    try:
        elapsed, applied, _, ver_after = run_upgrade(db_copy)
    except RehearsalError as exc:
        report.add(f"【失败】{exc}")
        _finalize(report, tmp, report_path)
        return report.text(), 1
    latest = max(v for v, _, _ in db.MIGRATIONS)
    report.add(f"应用的迁移（{len(applied)} 个）：")
    for item in applied:
        report.add(f"  - {item}")
    if not applied:
        report.add("  （无：副本已经是最新版本）")
    report.add(f"升级耗时：{elapsed:.2f} 秒")
    report.check(ver_after == latest, f"升级后 user_version={ver_after}，期望 {latest}")
    report.add("")

    # 4. 行数对比
    report.add("── 步骤 4：数据行数对比（升级前后不应减少）──")
    with closing(sqlite3.connect(db_copy)) as conn:
        _, counts_after = snapshot_counts(conn)
    for table in counts_before:
        b, a = counts_before[table], counts_after.get(table, 0)
        if a < b:
            report.check(False, f"{table}：{b} → {a} 行，数据减少！")
        else:
            extra = f"（+{a - b}，迁移新增）" if a > b else ""
            report.check(True, f"{table}：{b} → {a} 行{extra}")
    report.add("")

    # 5. 完整性
    report.add("── 步骤 5：完整性检查 ──")
    ok, detail = check_integrity(db_copy)
    report.check(ok, detail)
    report.add("")

    # 6. 核心查询抽样
    report.add("── 步骤 6：核心查询抽样 ──")
    for name, ok, detail in run_sample_queries(db_copy):
        report.check(ok, f"{name}：{detail}")
    report.add("")

    # 7. 幂等：重复启动
    report.add("── 步骤 7：幂等检查（重复启动一次）──")
    os.environ["DATABASE_PATH"] = str(db_copy)
    try:
        db.init_db()
        with closing(sqlite3.connect(db_copy)) as conn:
            ver_repeat, counts_repeat = snapshot_counts(conn)
        unchanged = ver_repeat == ver_after and counts_repeat == counts_after
        report.check(unchanged, "重复启动后版本号与行数无任何变化")
    except Exception as exc:  # noqa: BLE001
        report.check(False, f"重复启动抛异常：{exc}")
    report.add("")

    # 8. 空库冷启动
    report.add("── 步骤 8：空库冷启动（从 1 跑到最新）──")
    cold_path = tmp / "cold-start.db"
    if cold_path.exists():
        cold_path.unlink()
    os.environ["DATABASE_PATH"] = str(cold_path)
    try:
        db.init_db()
        with closing(sqlite3.connect(cold_path)) as conn:
            ver_cold = conn.execute("PRAGMA user_version").fetchone()[0]
        report.check(ver_cold == latest, f"空库冷启动后 user_version={ver_cold}，期望 {latest}")
    except Exception as exc:  # noqa: BLE001
        report.check(False, f"空库冷启动抛异常：{exc}")
    report.add("")

    # 9. 原备份未被修改
    report.add("── 步骤 9：确认原备份未被修改 ──")
    sha_after = sha256_of(archive_path)
    report.add(f"备份 sha256（演练后）：{sha_after}")
    report.check(sha_after == sha_before, "原备份文件字节完全一致，未被修改")
    report.add("")

    # 结论
    report.add("=" * 60)
    if report.failures:
        report.add(f"结论：【失败】共 {len(report.failures)} 项检查未通过，上线前必须处理：")
        for item in report.failures:
            report.add(f"  - {item}")
        code = 1
    else:
        report.add("结论：通过。备份可以在上线时按此流程升级。")
        report.add(f"停机时间估计：升级本身约 {elapsed:.2f} 秒（另加备份/恢复操作时间）。")
        code = 0
    report.add("=" * 60)

    _finalize(report, tmp, report_path)
    return report.text(), code


def _finalize(report, tmp, report_path):
    """写报告文件（如果指定了路径）。不删除临时目录，留给出问题时排查。"""
    if report_path:
        Path(report_path).write_text(report.text(), encoding="utf-8")
        report.add(f"（报告已写入 {report_path}；临时工作目录保留在 {tmp}，排查完可手动删除）")


def main(argv=None):
    parser = argparse.ArgumentParser(description="数据库升级演练脚本（只读备份，升级副本）")
    parser.add_argument("backup", help="backup.py 产生的备份 tar.gz 路径")
    parser.add_argument("--workdir", default=None, help="临时工作目录（默认系统临时目录）")
    parser.add_argument("--report", default=None, help="报告 txt 写到哪里（默认只打印控制台）")
    args = parser.parse_args(argv)
    text, code = rehearse(args.backup, workdir=args.workdir, report_path=args.report)
    # 控制台输出：失败行标红（tty 时）。
    for line in text.splitlines():
        if "【失败】" in line:
            print(_mark(line, False))
        elif line.startswith("[✓]") or line.startswith("结论：通过"):
            print(_mark(line, True))
        else:
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
