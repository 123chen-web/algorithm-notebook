#!/usr/bin/env bash
# 在线备份数据库与头像，并校验最新归档。幂等，可重复运行，建议每天自动跑。
# 用法：在仓库根目录执行 deploy/bin/backup.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need_cmd docker

msg "== 开始备份 =="
if ! compose exec -T app python backup.py --output-dir /app/data/backups --keep "${BACKUP_KEEP:-30}"; then
  die "备份失败：app 容器可能没有运行。请先启动服务（见上线手册第 7 步）再重试。"
fi

msg "== 校验最新备份 =="
LATEST="$(compose exec -T app sh -c 'ls -t /app/data/backups/backup-*.tar.gz 2>/dev/null | head -1' | tr -d '\r' | xargs || true)"
[ -n "$LATEST" ] || die "备份失败：没有生成备份文件。"
compose exec -T app python backup.py verify "$LATEST" || die "备份文件校验没通过，请检查磁盘空间与容器日志。"

msg "备份并校验完成：$LATEST"
