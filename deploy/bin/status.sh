#!/usr/bin/env bash
# 一屏显示服务状态：容器是否运行、健康检查、磁盘、最近备份、证书有效期。
# 只读，不改任何东西，可随时运行。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

need_cmd docker
need_cmd openssl
need_cmd curl

msg "===== 容器状态 ====="
compose ps 2>/dev/null || msg "（读不到容器状态，Docker 可能没运行）"

msg ""
msg "===== 健康检查 ====="
SITE_DOMAIN="$(grep -E '^SITE_DOMAIN=' "$REPO_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' | xargs || true)"
if [ -n "${SITE_DOMAIN:-}" ]; then
  if curl -fsS --max-time 10 "https://$SITE_DOMAIN/healthz" 2>/dev/null; then
    echo ""
    msg "健康检查通过。"
  else
    msg "健康检查失败：https://$SITE_DOMAIN/healthz 访问不通（服务可能没启动，或域名还没解析到本机）。"
  fi
else
  msg "没在 .env 里找到 SITE_DOMAIN，跳过 HTTPS 健康检查。"
fi

msg ""
msg "===== 磁盘 ====="
df -h "$DATA_DIR" 2>/dev/null || df -h "$REPO_DIR"

msg ""
msg "===== 最近 5 份备份 ====="
ls -lt "$DATA_DIR/backups"/backup-*.tar.gz 2>/dev/null | head -5 || msg "还没有备份文件，请先运行 deploy/bin/backup.sh。"

msg ""
msg "===== HTTPS 证书有效期 ====="
if [ -n "${SITE_DOMAIN:-}" ]; then
  ENDDATE="$(echo | openssl s_client -connect "$SITE_DOMAIN:443" -servername "$SITE_DOMAIN" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null || true)"
  if [ -n "$ENDDATE" ]; then
    msg "$ENDDATE"
    msg "（Caddy 会在到期前自动续期；如果看到快到期了还没续，检查 80 端口能否从公网访问）"
  else
    msg "读不到证书：443 端口可能不通，或证书还没申请下来。"
  fi
fi
