#!/usr/bin/env bash
# 安装"守夜人"：建目录、从管理员的微信提醒配置里取出通知密钥（不打印）、
# 放好公开状态页、加 cron 任务、重启 Caddy 让 /status/ 生效。幂等，可重复运行。
# 用法：sudo deploy/bin/watchdog-setup.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo deploy/bin/watchdog-setup.sh"
need_cmd docker
need_cmd python3
preflight

STATUS_DIR="${STATUS_DIR:-$APP_DIR/status}"
ENV_FILE="/etc/oy-watchdog.env"

msg "== 1/4 取出通知密钥（不会显示在屏幕上）=="
# 从第一个已开启微信提醒的管理员那里取渠道和密钥，直接写进只有 root 能读的文件。
compose exec -T app python - > "$ENV_FILE.tmp" <<'PY' || { rm -f "$ENV_FILE.tmp"; die "没找到已开启微信提醒的管理员账号：请先在网站账号菜单里配好微信提醒并发一条测试消息。"; }
import sqlite3, sys
conn = sqlite3.connect("/app/data/notebook.db")
row = conn.execute(
    "SELECT p.channel, p.secret FROM user_push p JOIN users u ON u.id = p.user_id "
    "WHERE u.is_admin = 1 AND u.deleted_at IS NULL AND p.enabled = 1 ORDER BY p.user_id LIMIT 1"
).fetchone()
if row is None:
    sys.exit(1)
print(f"CHANNEL={row[0]}")
print(f"KEY={row[1]}")
PY
chmod 600 "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"
msg "已保存到 $ENV_FILE（权限 600）。"

msg "== 2/4 放好公开状态页 =="
mkdir -p "$STATUS_DIR" /var/lib/oy-watchdog
cp "$REPO_DIR"/deploy/status/index.html "$REPO_DIR"/deploy/status/status.css "$REPO_DIR"/deploy/status/status.js "$STATUS_DIR"/
chmod -R a+rX "$STATUS_DIR"

msg "== 3/4 加定时任务 =="
CHECK="* * * * * cd $APP_DIR && /usr/bin/python3 deploy/bin/watchdog.py check >> /var/log/oy-watchdog.log 2>&1"
SELF="0 9 * * 0 cd $APP_DIR && /usr/bin/python3 deploy/bin/watchdog.py selfcheck >> /var/log/oy-watchdog.log 2>&1"
CURRENT="$(crontab -l 2>/dev/null || true)"
add_cron() {  # $1 = 用来判断是否已存在的关键字，$2 = 完整的 cron 行
  grep -qF -- "$1" <<<"$CURRENT" || CURRENT="$CURRENT"$'\n'"$2"
}
add_cron "watchdog.py check" "$CHECK"
add_cron "watchdog.py selfcheck" "$SELF"
printf '%s\n' "$CURRENT" | sed '/^$/d' | crontab -
touch /var/log/oy-watchdog.log && chmod 600 /var/log/oy-watchdog.log

msg "== 4/4 重启 Caddy 让 /status/ 生效 =="
# 先在一次性容器里校验新的 Caddyfile，写错了就停在这里，不碰正在运行的网站。
SITE_DOMAIN="$(grep -E '^SITE_DOMAIN=' "$REPO_DIR/.env" | head -1 | cut -d= -f2-)"
docker run --rm -e "SITE_DOMAIN=$SITE_DOMAIN" \
  -v "$REPO_DIR/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine \
  caddy validate --config /etc/caddy/Caddyfile \
  || die "Caddyfile 校验没通过，已中止，正在运行的网站没有被改动。"
compose up -d --force-recreate caddy
sleep 5
python3 "$REPO_DIR/deploy/bin/watchdog.py" check
SITE="$(grep -E '^SITE_DOMAIN=' "$REPO_DIR/.env" | head -1 | cut -d= -f2-)"
msg "完成。状态页：https://$SITE/status/ （若是第一次，等 1 分钟让 cron 写出第一份数据）"
