#!/usr/bin/env bash
# 一键初始化服务器：装 Docker、开防火墙（只放行 22/80/443）、建数据目录、配每日自动备份。
# 幂等：重复运行不会重复安装，只会补齐缺的东西。
# 必须用 root 运行：sudo bash deploy/bin/install.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo bash deploy/bin/install.sh"

msg "== 1/4 检查 Docker =="
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  msg "Docker 已安装，跳过安装。"
else
  msg "正在安装 Docker（官方脚本，可能需要几分钟，请耐心等待）..."
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
  systemctl enable --now docker 2>/dev/null || true
  msg "Docker 安装完成。"
fi
docker compose version || die "Docker Compose 插件不可用，请检查 Docker 安装。"

# 把执行 sudo 的普通用户加入 docker 组（重新登录后不用每次 sudo）
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  usermod -aG docker "$SUDO_USER" || true
  msg "已把 $SUDO_USER 加入 docker 组（重新登录服务器后生效）。"
fi

msg "== 2/4 配置防火墙（只放行 22/80/443）=="
command -v ufw >/dev/null 2>&1 || apt-get install -y ufw
ufw allow 22/tcp comment 'SSH' >/dev/null
ufw allow 80/tcp comment 'HTTP' >/dev/null
ufw allow 443/tcp comment 'HTTPS' >/dev/null
if ufw status | grep -q "Status: active"; then
  msg "防火墙已是开启状态，规则已更新为只放行 22/80/443。"
else
  msg "注意：如果你的 SSH 端口不是 22，请先执行 ufw allow <你的端口>/tcp，否则开防火墙后会被锁在外面！"
  ufw --force enable
  msg "防火墙已开启。"
fi

msg "== 3/4 创建数据目录并授权 =="
mkdir -p "$DATA_DIR/backups" "$DATA_DIR/avatars" "$DATA_DIR/pay-qr"
# 容器内以 UID/GID 10001（notebook 用户）运行，宿主机目录必须让它可写
chown -R 10001:10001 "$DATA_DIR"
chmod 700 "$DATA_DIR"
msg "数据目录：$DATA_DIR（容器重建、升级都不会丢里面的数据）"

msg "== 4/4 配置每日自动备份（cron，服务器本地时间每天 02:30）=="
CRON_LINE="30 2 * * * cd $APP_DIR && deploy/bin/backup.sh >> /var/log/notebook-backup.log 2>&1"
if crontab -l 2>/dev/null | grep -q "deploy/bin/backup.sh"; then
  msg "自动备份任务已存在，跳过。"
else
  (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
  touch /var/log/notebook-backup.log
  chmod 600 /var/log/notebook-backup.log
  msg "已添加 cron 任务：每天 02:30 自动备份并校验。"
fi

msg ""
msg "初始化完成！接下来按《零基础上线手册》继续："
msg "  1) 把代码放到 $APP_DIR（手册第 5 步）"
msg "  2) 创建并填写 .env（手册第 6 步）"
msg "  3) 启动服务（手册第 7 步）"
