#!/usr/bin/env bash
# SSH 加固：禁用密码登录、只允许密钥登录、限制 root 与重试次数，并安装/配置 fail2ban 防暴力破解。
# 风格与 watchdog-setup.sh 一致：默认只演练（dry-run），加 --apply 才真正改动；可重复运行；
# 改动前先备份被替换的文件；sshd -t 校验不通过就回滚，不留半成品。
#
# 用法（在仓库根目录 /srv/algorithm-notebook 下）：
#   sudo deploy/bin/ssh-harden.sh                         # 演练：只打印将要做的事，不改任何东西
#   sudo deploy/bin/ssh-harden.sh --apply                 # 正式执行
#   sudo deploy/bin/ssh-harden.sh --apply --whitelist 1.2.3.4 --whitelist 5.6.7.8
#   sudo deploy/bin/ssh-harden.sh --revert                # 演练回滚
#   sudo deploy/bin/ssh-harden.sh --revert --apply        # 正式回滚（删除本脚本写入的两个配置）
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

# ---- 固定路径（可用环境变量覆盖，仅便于测试，生产环境用默认值）----
SSHD_MAIN="${OY_SSHD_MAIN:-/etc/ssh/sshd_config}"
SSHD_D="${OY_SSHD_CONFIG_D:-/etc/ssh/sshd_config.d}"
HARDEN_CONF="$SSHD_D/99-oy-hardening.conf"
FAIL2BAN_JAIL_D="${OY_FAIL2BAN_JAIL_D:-/etc/fail2ban/jail.d}"
FAIL2BAN_JAIL="$FAIL2BAN_JAIL_D/oy-sshd.local"
BACKUP_DIR="${OY_HARDEN_BACKUP_DIR:-/var/backups/oy-ssh-hardening}"
# 要检查的 authorized_keys：sudo 执行时默认检查被提权用户的，直接用 root 登录则检查 root 的。
if [ -n "${OY_AUTHORIZED_KEYS:-}" ]; then
  AUTH_KEYS="$OY_AUTHORIZED_KEYS"
elif [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  AUTH_KEYS="$(eval echo "~$SUDO_USER")/.ssh/authorized_keys"
else
  AUTH_KEYS="${HOME:-/root}/.ssh/authorized_keys"
fi

APPLY=0
REVERT=0
WHITELIST=()
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --revert) REVERT=1; shift ;;
    --whitelist)
      [ $# -ge 2 ] || die "--whitelist 后面要跟 IP，例如 --whitelist 1.2.3.4"
      # 同时支持空格分隔和逗号分隔的多个 IP
      IFS=', ' read -ra _parts <<<"$2"
      WHITELIST+=("${_parts[@]}")
      shift 2 ;;
    --whitelist=*)
      IFS=', ' read -ra _parts <<<"${1#--whitelist=}"
      WHITELIST+=("${_parts[@]}")
      shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "不认识的参数：$1（可用参数：--apply / --revert / --whitelist IP）" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo deploy/bin/ssh-harden.sh"
# --revert --apply 是合法组合，表示"正式执行回滚"；APPLY 在回滚分支里同样作为执行开关。

# 演练/正式执行统一的小工具：step 打印"将要做"或"正在做"。
say() {
  if [ "$APPLY" -eq 1 ]; then echo "$*"; else echo "[dry-run] $*"; fi
}
# 只有 --apply 时才真正执行；否则只打印命令。
run() {
  if [ "$APPLY" -eq 1 ]; then
    "$@"
  else
    echo "[dry-run] 将要执行：$*"
  fi
}

# 备份一个即将被替换的已存在文件（内容不同才备份，重复运行不产生重复备份）。
# $1=文件，$2=本次准备写入的新内容临时文件
backup_if_different() {
  local target="$1" newcontent="$2" ts
  [ -f "$target" ] || return 0
  if cmp -s "$target" "$newcontent"; then
    return 0  # 内容已是目标状态，无需备份和替换
  fi
  ts="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP_DIR"
  cp -p "$target" "$BACKUP_DIR/$(basename "$target").$ts"
  msg "已备份原文件：$BACKUP_DIR/$(basename "$target").$ts"
}

# 找最新一份历史备份用于回滚；没有则返回 1。
newest_backup() {
  local name="$1"
  ls -1t "$BACKUP_DIR/$name".* 2>/dev/null | head -1 || true
}

# 校验白名单里的 IPv4（fail2ban 自己也会校验，这里提前拦住笔误）。
valid_ipv4() {
  local ip="$1" a b c d
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  IFS=. read -r a b c d <<<"$ip"
  for n in "$a" "$b" "$c" "$d"; do [ "$n" -le 255 ] || return 1; done
  return 0
}

for ip in "${WHITELIST[@]:-}"; do
  [ -z "$ip" ] && continue
  valid_ipv4 "$ip" || die "白名单 IP 格式不正确：$ip（示例：--whitelist 1.2.3.4）"
done

# ---- 回滚模式：删除本脚本写入的两个配置，恢复到加固前 ----
if [ "$REVERT" -eq 1 ]; then
  msg "== 回滚 SSH 加固 =="
  if [ -f "$HARDEN_CONF" ]; then
    run rm -f "$HARDEN_CONF"
    [ "$APPLY" -eq 1 ] && msg "已删除 $HARDEN_CONF"
  else
    msg "$HARDEN_CONF 不存在，无需处理。"
  fi
  # 如果这个文件在加固前就存在（极少见），用最新备份还原。
  if [ "$APPLY" -eq 1 ]; then
    old="$(newest_backup "$(basename "$HARDEN_CONF")")"
    if [ -n "$old" ] && [ ! -f "$HARDEN_CONF" ]; then
      cp -p "$old" "$HARDEN_CONF"
      msg "已从备份还原：$old"
    fi
  fi
  if [ "$APPLY" -eq 1 ]; then
    sshd -t || die "删除加固配置后 sshd -t 反而没通过，请人工检查 $SSHD_MAIN，未执行 reload。"
    if systemctl cat ssh.service >/dev/null 2>&1; then systemctl reload ssh; else systemctl reload sshd; fi
    msg "sshd 已重新加载，密码登录策略恢复为系统原配置。"
  else
    echo "[dry-run] 将要执行：sshd -t，通过后 systemctl reload ssh（或 sshd）"
  fi

  if [ -f "$FAIL2BAN_JAIL" ]; then
    run rm -f "$FAIL2BAN_JAIL"
    [ "$APPLY" -eq 1 ] && msg "已删除 $FAIL2BAN_JAIL"
    if [ "$APPLY" -eq 1 ]; then
      if command -v fail2ban-client >/dev/null 2>&1; then
        fail2ban-client -t
        systemctl is-active --quiet fail2ban && fail2ban-client reload || true
        msg "fail2ban 已重新加载（本脚本添加的 sshd 监狱规则已移除，fail2ban 软件本身未卸载）。"
      fi
    else
      echo "[dry-run] 将要执行：fail2ban-client -t && fail2ban-client reload"
    fi
  else
    msg "$FAIL2BAN_JAIL 不存在，无需处理。"
  fi
  msg "回滚完成。如需彻底卸载 fail2ban：sudo apt-get remove fail2ban（一般不建议）。"
  exit 0
fi

# ---- 正式加固前的只读检查（演练模式也会执行，确保演练能暴露问题）----
msg "== 1/5 检查登录公钥（防止把自己锁在外面）=="
if [ ! -f "$AUTH_KEYS" ]; then
  cat >&2 <<EOF
出错：没找到公钥文件 $AUTH_KEYS，拒绝继续。
请先在你自己的电脑（不是服务器）上准备好密钥，并确认能用密钥登录服务器，再运行本脚本：

  1) 电脑上没有密钥就生成一把（Mac/Linux/Windows PowerShell 均可）：
       ssh-keygen -t ed25519 -C "你的备注"
     一路回车即可，默认会在 ~/.ssh/ 下生成 id_ed25519（私钥，绝不能外传）和 id_ed25519.pub（公钥）。
  2) 把公钥放到服务器（在你自己的电脑上执行，把 ubuntu 换成你登录服务器用的用户名）：
       ssh-copy-id ubuntu@你的服务器IP
     没有 ssh-copy-id 时，手动执行：
       type %USERPROFILE%\\.ssh\\id_ed25519.pub | ssh ubuntu@你的服务器IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
  3) 另开一个新终端执行 ssh ubuntu@你的服务器IP，确认不再要求输入密码就能登录。
  4) 保持那个终端不要关，回到服务器重新运行本脚本。
EOF
  exit 1
fi

VALID_KEYS=0
while IFS= read -r line || [ -n "$line" ]; do
  # 跳过空行和注释行；只看以受支持公钥类型开头的行。
  { [ -z "$line" ] || [[ "$line" =~ ^[[:space:]]*# ]]; } && continue
  if [[ "$line" =~ ^[[:space:]]*(ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ecdsa-sha2-nistp521|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)[[:space:]]+ ]]; then
    if command -v ssh-keygen >/dev/null 2>&1; then
      if printf '%s\n' "$line" | ssh-keygen -l -f - >/dev/null 2>&1; then
        VALID_KEYS=$((VALID_KEYS + 1))
      fi
    else
      VALID_KEYS=$((VALID_KEYS + 1))
    fi
  fi
done <"$AUTH_KEYS"
[ "$VALID_KEYS" -ge 1 ] || {
  cat >&2 <<EOF
出错：$AUTH_KEYS 里没有一行是有效的公钥，拒绝继续。
公钥是一整行，以 ssh-ed25519、ssh-rsa 或 ecdsa-sha2- 开头，例如：
  ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI...... 你的备注
请按脚本提示用 ssh-keygen 生成、用 ssh-copy-id 放置后，先验证能免密登录再重试。
EOF
  exit 1
}
msg "在 $AUTH_KEYS 中找到 $VALID_KEYS 个有效公钥。"
# ~/.ssh 与 authorized_keys 权限过松会被 sshd 拒绝使用，顺手修正（只改权限，不动内容）。
SSH_DIR="$(dirname "$AUTH_KEYS")"
if [ "$APPLY" -eq 1 ]; then
  chmod 700 "$SSH_DIR" 2>/dev/null || true
  chmod 600 "$AUTH_KEYS" 2>/dev/null || true
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    chown -R "$SUDO_USER:$(id -gn "$SUDO_USER")" "$SSH_DIR" 2>/dev/null || true
  fi
else
  echo "[dry-run] 将要执行：chmod 700 $SSH_DIR；chmod 600 $AUTH_KEYS（修正过松的权限，不动内容）"
fi

msg "== 2/5 检查 sshd 主配置是否包含 drop-in 目录 =="
if [ ! -f "$SSHD_MAIN" ]; then
  die "找不到 $SSHD_MAIN，这台机器的 SSH 配置位置异常，请人工确认。"
fi
if ! grep -Eq '^[[:space:]]*Include[[:space:]]+[^#]*sshd_config\.d/\*\.conf' "$SSHD_MAIN"; then
  cat >&2 <<EOF
出错：$SSHD_MAIN 里没有 "Include /etc/ssh/sshd_config.d/*.conf"，本脚本的独立配置文件不会生效。
Ubuntu 20.04 及更新版本默认包含这一行。请人工检查 sshd 配置后再运行本脚本；
本脚本不会直接改写 sshd 主配置文件。
EOF
  exit 1
fi
# Include 之前若已有生效的密码登录策略，drop-in 会被主配置覆盖，必须提示。
INCLUDE_LINE="$(grep -n -E '^[[:space:]]*Include[[:space:]]+[^#]*sshd_config\.d/\*\.conf' "$SSHD_MAIN" | head -1 | cut -d: -f1)"
BEFORE="$(head -n "$((INCLUDE_LINE - 1))" "$SSHD_MAIN" || true)"
if grep -Eq '^[[:space:]]*(PasswordAuthentication|PermitRootLogin)[[:space:]]+' <<<"$BEFORE"; then
  die "$SSHD_MAIN 在 Include 之前已手写 PasswordAuthentication 或 PermitRootLogin，会覆盖独立配置。请先人工注释掉这些行再运行本脚本。"
fi
msg "主配置已包含 sshd_config.d，独立配置文件可以生效。"

msg "== 3/5 准备 SSH 加固配置 $HARDEN_CONF =="
IGNORE_LINE="ignoreip = 127.0.0.1/8 ::1"
for ip in "${WHITELIST[@]:-}"; do [ -n "$ip" ] && IGNORE_LINE="$IGNORE_LINE $ip"; done
TMP_CONF="$(mktemp -p "$SSHD_D" .99-oy-hardening.XXXXXX 2>/dev/null || mktemp)"
cat >"$TMP_CONF" <<EOF
# 欧叶OY SSH 加固配置，由 deploy/bin/ssh-harden.sh 写入，请勿手工编辑。
# 回滚方法：sudo deploy/bin/ssh-harden.sh --revert --apply
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF
if [ "$APPLY" -eq 1 ]; then
  mkdir -p "$SSHD_D"
  if [ -f "$HARDEN_CONF" ] && cmp -s "$HARDEN_CONF" "$TMP_CONF"; then
    msg "加固配置内容已是目标状态，跳过写入。"
    rm -f "$TMP_CONF"
  else
    backup_if_different "$HARDEN_CONF" "$TMP_CONF"
    install -m 644 "$TMP_CONF" "$HARDEN_CONF"
    rm -f "$TMP_CONF"
    msg "已写入 $HARDEN_CONF。"
    # 关键关卡：配置语法不通过就立刻回滚，绝不 reload 坏配置。
    if ! sshd -t; then
      msg "sshd -t 校验没通过，正在回滚 ..." >&2
      rm -f "$HARDEN_CONF"
      old="$(newest_backup "$(basename "$HARDEN_CONF")")"
      [ -n "$old" ] && cp -p "$old" "$HARDEN_CONF"
      rm -f "$TMP_CONF"
      die "sshd -t 校验没通过，已回滚，正在运行的 SSH 没有被改动。请把上面的报错发给技术支持。"
    fi
    if systemctl cat ssh.service >/dev/null 2>&1; then
      systemctl reload ssh
    else
      systemctl reload sshd
    fi
    msg "sshd 配置校验通过并已重新加载。"
  fi
else
  echo "[dry-run] 将要写入 $HARDEN_CONF，内容如下："
  sed 's/^/[dry-run]   /' "$TMP_CONF"
  rm -f "$TMP_CONF"
  echo "[dry-run] 将要执行：sshd -t，通过后 systemctl reload ssh（或 sshd）"
fi

msg "== 4/5 安装并配置 fail2ban（sshd 监狱：失败 5 次封 1 小时）=="
if command -v fail2ban-client >/dev/null 2>&1; then
  say "fail2ban 已安装，跳过安装。"
else
  if [ "$APPLY" -eq 1 ]; then
    msg "正在通过 apt 安装 fail2ban（可能需要一两分钟）..."
    apt-get update -y
    DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban
  else
    echo "[dry-run] 将要执行：apt-get update && apt-get install -y fail2ban"
  fi
fi
TMP_JAIL="$(mktemp -p "$FAIL2BAN_JAIL_D" .oy-sshd.XXXXXX 2>/dev/null || mktemp)"
cat >"$TMP_JAIL" <<EOF
# 欧叶OY fail2ban 规则，由 deploy/bin/ssh-harden.sh 写入，请勿手工编辑。
# 回滚方法：sudo deploy/bin/ssh-harden.sh --revert --apply
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
$IGNORE_LINE
EOF
if [ "$APPLY" -eq 1 ]; then
  mkdir -p "$FAIL2BAN_JAIL_D"
  if [ -f "$FAIL2BAN_JAIL" ] && cmp -s "$FAIL2BAN_JAIL" "$TMP_JAIL"; then
    msg "fail2ban 配置内容已是目标状态，跳过写入。"
    rm -f "$TMP_JAIL"
  else
    backup_if_different "$FAIL2BAN_JAIL" "$TMP_JAIL"
    install -m 644 "$TMP_JAIL" "$FAIL2BAN_JAIL"
    rm -f "$TMP_JAIL"
    msg "已写入 $FAIL2BAN_JAIL。"
  fi
  fail2ban-client -t || die "fail2ban 配置校验没通过，已保留原 fail2ban 运行状态；SSH 加固部分仍然有效。"
  systemctl enable fail2ban >/dev/null 2>&1 || true
  if systemctl is-active --quiet fail2ban; then
    fail2ban-client reload
  else
    systemctl restart fail2ban
  fi
  sleep 2
  fail2ban-client status sshd || msg "提示：fail2ban 刚启动，sshd 监狱状态可稍后用 fail2ban-client status sshd 查看。"
else
  echo "[dry-run] 将要写入 $FAIL2BAN_JAIL，内容如下："
  sed 's/^/[dry-run]   /' "$TMP_JAIL"
  rm -f "$TMP_JAIL"
  echo "[dry-run] 将要执行：fail2ban-client -t，通过后启用并 reload/restart fail2ban"
fi

msg "== 5/5 完成后的必做验证 =="
cat <<EOF
$([ "$APPLY" -eq 1 ] && echo "加固已完成。" || echo "以上是演练将要执行的全部内容，确认无误后加 --apply 正式执行。")

【最重要】请先保持当前终端不要关，另开一个新终端执行：
    ssh $( [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] && echo "$SUDO_USER@" || true )<你的服务器IP>
  1) 新终端能用密钥正常登录 → 加固成功，再关掉旧终端；
  2) 新终端连不上 → 不要慌，旧终端还在，立刻回滚：
       sudo deploy/bin/ssh-harden.sh --revert --apply
     然后检查公钥配置后重来。

补充说明：
- 腾讯云网页终端（OrcaTerm）走的是腾讯云平台通道，不经过 sshd，任何时候都能用它进服务器救援；
  万一彻底锁死，也可以在腾讯云轻量应用服务器控制台"重置密码"后用 OrcaTerm / VNC 登录排查。
- fail2ban 会把 10 分钟内 SSH 失败 5 次的 IP 封禁 1 小时；
  查看：sudo fail2ban-client status sshd；手动解封：sudo fail2ban-client set sshd unbanip <IP>。
- 以后要加白名单 IP（例如你公司的固定 IP），重新运行并带上参数即可，配置会幂等更新：
    sudo deploy/bin/ssh-harden.sh --apply --whitelist 1.2.3.4
EOF
