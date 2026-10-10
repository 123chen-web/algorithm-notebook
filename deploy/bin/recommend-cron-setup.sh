#!/usr/bin/env bash
# 欧叶每日推荐候选刷新；默认 dry-run，只有 --apply 修改 root crontab。
set -euo pipefail

die() { printf '出错：%s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo bash deploy/bin/recommend-cron-setup.sh"
apply=0
remove=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --remove) remove=1 ;;
    --help) echo '用法：sudo bash deploy/bin/recommend-cron-setup.sh [--remove] [--apply]'; exit 0 ;;
    *) die "未知参数：$arg" ;;
  esac
done

APP_DIR="${APP_DIR:-/srv/algorithm-notebook}"
LOG_FILE="${RECOMMEND_LOG:-/var/log/oy-recommend.log}"
for path in "$APP_DIR" "$LOG_FILE"; do
  [[ "$path" = /* && "$path" != *$'\n'* && "$path" != *$'\r'* && "$path" != *%* ]] || die "目录与日志必须是绝对路径，不能包含换行或 %"
done
printf -v app_quoted '%q' "$APP_DIR"
printf -v log_quoted '%q' "$LOG_FILE"
marker='# oy-recommend-refresh'
cron="20 3 * * * cd $app_quoted && /usr/bin/timeout 180 /usr/bin/docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T app python cf_problems.py refresh >> $log_quoted 2>&1 $marker"

if [ "$remove" -eq 1 ]; then
  echo "计划：只移除以 $marker 结尾的任务，保留其余 cron。"
else
  echo '计划：每天服务器时间 03:20，在应用容器内刷新候选题（最长 180 秒）；失败保留旧缓存。'
  printf '%s\n' "$cron"
fi
echo '修改前备份 root crontab 至 /var/backups/oy-recommend-crontab.*（权限 600）。'
if [ "$apply" -eq 0 ]; then
  echo 'DRY-RUN：没有读取配置密钥、写文件、运行 Docker 或修改 crontab；加 --apply 才执行。'
  exit 0
fi
command -v crontab >/dev/null || die '缺少 crontab，请安装并启动 cron 服务'
if [ "$remove" -eq 0 ]; then
  [ -x /usr/bin/docker ] && [ -x /usr/bin/timeout ] || die '缺少 /usr/bin/docker 或 /usr/bin/timeout'
  [ -f "$APP_DIR/deploy/docker-compose.prod.yml" ] || die '找不到生产 compose 文件'
fi
umask 077
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
if ! crontab -l > "$work/current" 2> "$work/error"; then
  if ! grep -qi 'no crontab' "$work/error"; then
    cat "$work/error" >&2
    die '无法读取 root crontab，已停止'
  fi
fi
mkdir -p /var/backups
backup="$(mktemp /var/backups/oy-recommend-crontab.XXXXXXXX)"
cp "$work/current" "$backup"
chmod 600 "$backup"
awk '!/# oy-recommend-refresh$/' "$work/current" > "$work/next"
if [ "$remove" -eq 0 ]; then
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
  printf '%s\n' "$cron" >> "$work/next"
fi
if cmp -s "$work/current" "$work/next"; then
  echo '任务已符合配置，无需修改。'
else
  crontab "$work/next"
  echo '已更新自己的任务行，其余任务已保留。'
fi
printf '备份：%s\n' "$backup"
