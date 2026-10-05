#!/usr/bin/env bash
# 从备份归档恢复数据。
# 流程：先再备份一份当前数据（安全网）-> 校验归档 -> 停服务 -> 恢复到独立目录
#      -> 替换数据库与头像 -> 重启 -> 健康检查。
# 用法：deploy/bin/restore.sh [备份文件名或路径]（不填则用最新的）
# 注意：只支持恢复 $DATA_DIR/backups 下的备份文件。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need_cmd docker

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ]; then
  ARCHIVE="$(ls -t "$DATA_DIR/backups"/backup-*.tar.gz 2>/dev/null | head -1 || true)"
fi
[ -n "$ARCHIVE" ] && [ -f "$ARCHIVE" ] || die "找不到备份文件。用法：deploy/bin/restore.sh $DATA_DIR/backups/<备份文件名>.tar.gz"

ARCHIVE_NAME="$(basename "$ARCHIVE")"
if [ "$ARCHIVE" != "$DATA_DIR/backups/$ARCHIVE_NAME" ]; then
  die "只支持恢复 $DATA_DIR/backups 下的备份，请先把文件放进去再重试。"
fi
CONTAINER_ARCHIVE="/app/data/backups/$ARCHIVE_NAME"

msg "准备从备份恢复：$ARCHIVE"
msg "== 1/6 先备份当前数据（恢复前的安全网，万一恢复错了还能回去） =="
"$SCRIPT_DIR/backup.sh"

msg "== 2/6 校验要恢复的归档 =="
compose run --rm app python backup.py verify "$CONTAINER_ARCHIVE" \
  || die "归档校验失败，已中止，没有动任何数据。"

msg "== 3/6 停止应用服务 =="
compose stop app

TS="$(date +%Y%m%d-%H%M%S)"
RESTORE_TMP="/app/data/restore-$TS"  # 放在挂载的数据卷里：每次 compose run 都是新容器，/tmp 不共享
msg "== 4/6 把归档恢复到独立目录（先演练，不直接覆盖） =="
if ! compose run --rm app python backup.py restore "$CONTAINER_ARCHIVE" --into "$RESTORE_TMP"; then
  msg "恢复失败，正在重新启动原来的服务 ..." >&2
  compose up -d app
  die "恢复失败，原服务已重启，数据没有变化。请检查归档文件是否完好。"
fi

msg "== 5/6 替换数据库与头像（原数据已在第 1 步备份，不用担心） =="
compose run --rm app sh -c "
  set -e
  rm -f /app/data/notebook.db-wal /app/data/notebook.db-shm /app/data/notebook.db-journal
  cp '$RESTORE_TMP/notebook.db' /app/data/notebook.db
  chmod 600 /app/data/notebook.db
  rm -rf /app/data/avatars
  cp -r '$RESTORE_TMP/avatars' /app/data/avatars
  rm -rf '$RESTORE_TMP'
  echo 替换完成
" || {
  msg "文件替换失败，正在重启服务，请检查数据目录 ..." >&2
  compose up -d app
  exit 1
}

msg "== 6/6 重启并健康检查 =="
compose up -d app
if wait_healthy 120; then
  msg "恢复成功，服务健康。请登录网站抽查几条错题、头像和订单，确认数据是你想要的那一份。"
else
  die "恢复后服务不健康，请执行 docker compose -f $COMPOSE_FILE logs app 查看原因。"
fi
