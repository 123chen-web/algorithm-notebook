#!/usr/bin/env bash
# 更新流程：先自动备份 -> 拉新代码 -> 重建容器 -> 健康检查 -> 失败自动回滚。
# 用法：在仓库根目录执行 deploy/bin/update.sh
# 如需更新其他分支：UPDATE_BRANCH=xxx deploy/bin/update.sh
# 只看会做什么、不动任何东西：deploy/bin/update.sh --dry-run
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need_cmd docker
need_cmd git
need_cmd curl
preflight

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) die "未知参数：$arg（目前只支持 --dry-run）" ;;
  esac
done

UPDATE_BRANCH="${UPDATE_BRANCH:-main}"

# 找出更新前的最新备份（含冷备份目录），用于回滚失败时告诉站长去哪恢复
latest_backup_hint() {
  ls -dt "$DATA_DIR/backups"/backup-*.tar.gz "$DATA_DIR/backups"/cold-*/ 2>/dev/null | head -1 || true
}

if [ "$DRY_RUN" = "1" ]; then
  msg "== 演练模式（--dry-run）：只打印计划，不做任何改动 =="
  if app_health; then
    msg "备份方式：在线备份（app 容器运行且健康）"
  else
    msg "备份方式：冷备份（应用当前没在运行）"
    msg "冷备份目标目录：${COLD_BACKUP_DIR:-$DATA_DIR/backups}/cold-<时间>/"
  fi
  msg "更新分支：$UPDATE_BRANCH"
  msg "当前版本：$(git rev-parse --short HEAD 2>/dev/null || echo 未知)"
  msg "目标版本：$(git rev-parse --short "origin/$UPDATE_BRANCH" 2>/dev/null || echo '未知（本地还没有 fetch 过该分支）')"
  msg "演练结束：没有执行备份、拉代码、重建容器中的任何一步，数据和代码都没有动。"
  exit 0
fi

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
  msg "已回滚到 $OLD_SHORT 并恢复健康。" >&2
  msg "数据没动：回滚只换回了旧代码，数据库文件没有被修改过。" >&2
  BACKUP_HINT="$(latest_backup_hint)"
  if [ -n "$BACKUP_HINT" ]; then
    msg "更新前的备份在：$BACKUP_HINT" >&2
    case "$BACKUP_HINT" in
      */cold-*/)
        msg "这是冷备份（整个数据目录的拷贝）。如需恢复：先停容器，把里面除 backups/ 外的文件拷回 $DATA_DIR（建议先把现在的 data/ 改名留底）." >&2
        ;;
      *)
        msg "如果之后发现数据有问题，用这条命令恢复：deploy/bin/restore.sh \"$BACKUP_HINT\"" >&2
        ;;
    esac
  fi
  msg "排查方向：执行 docker compose -f $COMPOSE_FILE logs app 看新版的启动报错。" >&2
  exit 1
fi

msg "严重：回滚后服务仍然不健康！" >&2
msg "可能原因：数据库结构已经被新版迁移升级，旧代码拒绝启动（这是程序的安全保护）。" >&2
LATEST_BACKUP="$(latest_backup_hint)"
if [ -n "$LATEST_BACKUP" ]; then
  case "$LATEST_BACKUP" in
    */cold-*/)
      msg "更新前自动生成的冷备份在：$LATEST_BACKUP" >&2
      msg "恢复办法：先停掉容器（docker compose -f $COMPOSE_FILE stop），把冷备份里除 backups/ 外的文件拷回 $DATA_DIR（先把现在的 data/ 改名留底），再启动。" >&2
      ;;
    *)
      msg "请用更新前自动生成的备份恢复数据：" >&2
      msg "  deploy/bin/restore.sh $LATEST_BACKUP" >&2
      ;;
  esac
else
  msg "备份目录里找不到备份文件，请先确认数据目录是否完好，再联系技术支持。" >&2
fi
msg "并把这条命令的输出发给技术支持：docker compose -f $COMPOSE_FILE logs app" >&2
exit 1
