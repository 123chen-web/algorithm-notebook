#!/usr/bin/env bash
# 更新流程：先自动备份 -> 拉新代码 -> 重建容器 -> 健康检查 -> 失败自动回滚。
# 用法：在仓库根目录执行 deploy/bin/update.sh
# 如需更新其他分支：UPDATE_BRANCH=xxx deploy/bin/update.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need_cmd docker
need_cmd git
need_cmd curl
preflight

UPDATE_BRANCH="${UPDATE_BRANCH:-main}"

msg "== 1/5 自动备份当前数据 =="
if ! "$SCRIPT_DIR/backup.sh"; then
  die "自动备份失败，为保护数据已中止更新。请先手动排查备份问题（看 /var/log/notebook-backup.log）。"
fi

OLD_REV="$(git rev-parse HEAD)"
OLD_SHORT="$(git rev-parse --short HEAD)"
msg "当前版本：$OLD_SHORT"

msg "== 2/5 拉取新代码（分支 $UPDATE_BRANCH）=="
git fetch origin "$UPDATE_BRANCH" || die "拉取代码失败：检查服务器能否访问 GitHub（网络或代理）。"
git reset --hard "origin/$UPDATE_BRANCH"
NEW_SHORT="$(git rev-parse --short HEAD)"
msg "新版本：$NEW_SHORT"

msg "== 3/5 重建并启动容器 =="
compose up -d --build

msg "== 4/5 健康检查（最多等待 3 分钟）=="
if wait_healthy 180; then
  msg "更新成功：$OLD_SHORT -> $NEW_SHORT，服务健康。"
  msg "可以用 deploy/bin/status.sh 再看一眼整体状态。"
  exit 0
fi

msg "新版本健康检查失败，开始自动回滚到 $OLD_SHORT ..." >&2
git reset --hard "$OLD_REV"
compose up -d --build
if wait_healthy 180; then
  msg "已回滚到 $OLD_SHORT 并恢复健康。本次更新没有生效，数据没有变化。" >&2
  msg "排查方向：执行 docker compose -f $COMPOSE_FILE logs app 看新版的启动报错。" >&2
  exit 1
fi

msg "严重：回滚后服务仍然不健康！" >&2
msg "可能原因：数据库结构已经被新版迁移升级，旧代码拒绝启动（这是程序的安全保护）。" >&2
LATEST_BACKUP="$(ls -t "$DATA_DIR/backups"/backup-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$LATEST_BACKUP" ]; then
  msg "请用更新前自动生成的备份恢复数据：" >&2
  msg "  deploy/bin/restore.sh $LATEST_BACKUP" >&2
else
  msg "备份目录里找不到备份文件，请先确认数据目录是否完好，再联系技术支持。" >&2
fi
msg "并把这条命令的输出发给技术支持：docker compose -f $COMPOSE_FILE logs app" >&2
exit 1
