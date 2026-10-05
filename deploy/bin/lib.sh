#!/usr/bin/env bash
# 部署脚本公共函数库：被 deploy/bin/ 下其他脚本 source，不直接执行。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_DIR"

# 服务器上的固定目录（与上线手册一致）；如需改动可设环境变量覆盖。
APP_DIR="${APP_DIR:-/srv/algorithm-notebook}"
DATA_DIR="${DATA_DIR:-$APP_DIR/data}"
COMPOSE_FILE="$REPO_DIR/deploy/docker-compose.prod.yml"

msg() { echo "$*"; }
die() { echo "出错：$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令 $1，请先运行 deploy/bin/install.sh 安装环境。"
}

# 统一的 compose 调用
compose() {
  link_env
  docker compose -f "$COMPOSE_FILE" "$@"
}

# 等待 app 容器健康，最多 $1 秒（默认 180）
wait_healthy() {
  local timeout="${1:-180}" elapsed=0
  while [ "$elapsed" -lt "$timeout" ]; do
    if compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).close()" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
    elapsed=$((elapsed + 5))
  done
  return 1
}

# docker compose 只在 compose 文件所在目录（deploy/）找 .env 来替换 ${SITE_DOMAIN}，
# 所以把仓库根目录的 .env 链接过去（.gitignore 已排除 .env，链接不会进 Git）。
link_env() {
  [ -f "$REPO_DIR/.env" ] && ln -sf ../.env "$REPO_DIR/deploy/.env"
  return 0
}

# 预检：.env 存在且 SITE_DOMAIN 已填
preflight() {
  [ -f "$REPO_DIR/.env" ] || die "找不到 $REPO_DIR/.env，请先按《零基础上线手册》第 6 步创建并填写。"
  link_env
  grep -Eq '^SITE_DOMAIN=[^[:space:]]+' "$REPO_DIR/.env" || die ".env 里的 SITE_DOMAIN 还是空的，请先填你的域名。"
}
