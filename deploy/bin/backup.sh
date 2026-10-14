#!/usr/bin/env bash
# 在线备份数据库与头像，并校验最新归档。幂等，可重复运行，建议每天自动跑。
# 如果 app 容器没在运行或不健康，自动改走冷备份（停容器后直接拷贝数据目录），
# 保证“应用崩了也照样有备份”，更新脚本不会卡在第一步。
# 用法：在仓库根目录执行 deploy/bin/backup.sh
# 冷备份目录可用环境变量 COLD_BACKUP_DIR 覆盖（默认 $DATA_DIR/backups）。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need_cmd docker
need_cmd python3

COLD_BACKUP_ROOT="${COLD_BACKUP_DIR:-$DATA_DIR/backups}"

# 冷备份：docker compose stop app -> cp -a 数据目录（不含 backups/ 自身）->
# quick_check 校验。空间不足时先报错，且不动服务。
cold_backup() {
  local ts dest
  ts="$(date +%Y%m%d-%H%M%S)"
  dest="$COLD_BACKUP_ROOT/cold-$ts"
  msg "应用当前没在运行，已改用冷备份：路径 $dest"

  [ -d "$DATA_DIR" ] || die "数据目录 $DATA_DIR 不存在，无法冷备份。"
  mkdir -p "$COLD_BACKUP_ROOT"

  # 1. 空间检查：可用空间至少是数据目录的 1.2 倍，不够就先报错（此时服务还没被停）
  local data_kb avail_kb need_kb
  data_kb="$(du -sk "$DATA_DIR" | cut -f1)"
  avail_kb="$(df -k "$COLD_BACKUP_ROOT" | awk 'NR==2 {print $4}')"
  need_kb=$((data_kb * 12 / 10))
  if [ "$avail_kb" -lt "$need_kb" ]; then
    die "磁盘空间不足：冷备份约需 $((need_kb / 1024)) MB，可用 $((avail_kb / 1024)) MB。已中止，服务没有被停止，请先清理磁盘再重试。"
  fi

  # 2. 停掉 app（已经停了也没关系），保证拷贝时 SQLite 文件一致
  msg "正在停止 app 容器以保证拷贝一致性 ..."
  compose stop app >/dev/null 2>&1 || true

  # 3. 拷贝数据目录；排除 backups/ 自身，避免把备份拷进备份里
  mkdir -p "$dest"
  local item base
  for item in "$DATA_DIR"/* "$DATA_DIR"/.[!.]*; do
    [ -e "$item" ] || continue
    base="$(basename "$item")"
    [ "$base" = "backups" ] && continue
    cp -a "$item" "$dest/"
  done

  # 4. 校验：数据库文件存在且 quick_check 通过（含 -wal/-shm 一起拷走，恢复时 SQLite 会自动处理）
  [ -f "$dest/notebook.db" ] || die "冷备份校验失败：$dest/notebook.db 不存在。"
  if ! python3 - "$dest/notebook.db" <<'PYEOF' >/dev/null 2>&1; then
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
row = con.execute("PRAGMA quick_check").fetchone()
sys.exit(0 if row and row[0] == "ok" else 1)
PYEOF
    die "冷备份校验失败：$dest/notebook.db 完整性检查没通过。"
  fi

  msg "冷备份完成：$dest"
  msg "说明：冷备份目录以 cold- 开头，不会被每日备份的“保留最近 30 份”清理（只清理 backup-*.tar.gz）。"
}

msg "== 开始备份 =="
if app_health; then
  if ! compose exec -T app python backup.py --output-dir /app/data/backups --keep "${BACKUP_KEEP:-30}"; then
    die "备份失败：app 容器可能没有运行。请先启动服务（见上线手册第 7 步）再重试。"
  fi

  msg "== 校验最新备份 =="
  LATEST="$(compose exec -T app sh -c 'ls -t /app/data/backups/backup-*.tar.gz 2>/dev/null | head -1' | tr -d '\r' | xargs || true)"
  [ -n "$LATEST" ] || die "备份失败：没有生成备份文件。"
  compose exec -T app python backup.py verify "$LATEST" || die "备份文件校验没通过，请检查磁盘空间与容器日志。"

  msg "备份并校验完成：$LATEST"
else
  cold_backup
fi
