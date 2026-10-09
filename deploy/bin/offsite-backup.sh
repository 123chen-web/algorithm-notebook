#!/usr/bin/env bash
# 异地加密备份：把最新的本地备份用 age 加密后，经 rclone 上传到异地对象存储（如腾讯云 COS）。
# 只做五件事：校验备份完整 → age 加密 → rclone 上传 → 异地只保留最近 N 份（默认 14）→ 写日志和状态文件。
# 默认只演练（dry-run），加 --apply 才真正上传；可重复运行；失败写失败状态并清理本地半成品，不删异地旧文件。
#
# 用法（在仓库根目录 /srv/algorithm-notebook 下）：
#   sudo deploy/bin/offsite-backup.sh                # 演练：找到最新备份、本地校验，只读预览异地保留情况
#   sudo deploy/bin/offsite-backup.sh --apply        # 正式加密并上传
# 配置文件 /etc/oy-offsite-backup.env（权限 600）：
#   AGE_RECIPIENT=age1......        # age 公钥（可以放服务器）；对应私钥只能保存在服务器之外
#   RCLONE_REMOTE=oycos:桶名/前缀   # rclone config 里配好的异地路径
#   KEEP=14                         # 异地保留份数，默认 14
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ENV_FILE="${OY_OFFSITE_ENV:-/etc/oy-offsite-backup.env}"
STATE_DIR="${OY_OFFSITE_STATE_DIR:-/var/lib/oy-offsite-backup}"
LOG_FILE="${OY_OFFSITE_LOG:-/var/log/oy-offsite-backup.log}"
STAGING_DIR="$STATE_DIR/staging"
BACKUP_SRC_DIR="${OY_BACKUP_SRC_DIR:-$DATA_DIR/backups}"
KEEP_DEFAULT=14

APPLY=0
CLI_RECIPIENT=""
CLI_REMOTE=""
CLI_KEEP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --recipient) [ $# -ge 2 ] || die "--recipient 后面要跟 age 公钥"; CLI_RECIPIENT="$2"; shift 2 ;;
    --recipient=*) CLI_RECIPIENT="${1#--recipient=}"; shift ;;
    --remote) [ $# -ge 2 ] || die "--remote 后面要跟 rclone 路径，如 oycos:my-bucket/backups"; CLI_REMOTE="$2"; shift 2 ;;
    --remote=*) CLI_REMOTE="${1#--remote=}"; shift ;;
    --keep) [ $# -ge 2 ] || die "--keep 后面要跟份数"; CLI_KEEP="$2"; shift 2 ;;
    --keep=*) CLI_KEEP="${1#--keep=}"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "不认识的参数：$1（可用参数：--apply / --recipient / --remote / --keep）" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo deploy/bin/offsite-backup.sh"
need_cmd age
need_cmd rclone
need_cmd gzip
need_cmd docker

AGE_RECIPIENT="$CLI_RECIPIENT"
RCLONE_REMOTE="$CLI_REMOTE"
KEEP="$CLI_KEEP"
# 配置文件只在没有用命令行参数指定时提供默认值；文件不存在不算错误（也可全用命令行参数）。
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case "$key" in
      AGE_RECIPIENT) [ -z "$AGE_RECIPIENT" ] && AGE_RECIPIENT="$value" ;;
      RCLONE_REMOTE) [ -z "$RCLONE_REMOTE" ] && RCLONE_REMOTE="$value" ;;
      KEEP) [ -z "$KEEP" ] && KEEP="$value" ;;
    esac
  done < <(grep -Ev '^[[:space:]]*(#|$)' "$ENV_FILE" || true)
fi
: "${KEEP:=$KEEP_DEFAULT}"
[ -z "$AGE_RECIPIENT" ] && die "没有配置 AGE_RECIPIENT。请把 age 公钥写入 $ENV_FILE（AGE_RECIPIENT=age1......）或用 --recipient 传入。"
[ -z "$RCLONE_REMOTE" ] && die "没有配置 RCLONE_REMOTE。请写入 $ENV_FILE（RCLONE_REMOTE=oycos:桶名/前缀）或用 --remote 传入。"
[[ "$AGE_RECIPIENT" =~ ^age1[023456789acdefghjklmnpqrstuvwxyz]{58,}$ ]] || die "AGE_RECIPIENT 格式不正确，应以 age1 开头（age 公钥）。"
[[ "$RCLONE_REMOTE" =~ ^[A-Za-z0-9._~-]+:[A-Za-z0-9._~/-]+$ ]] || die "RCLONE_REMOTE 格式不正确，示例：oycos:my-bucket/oy-backups"
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || die "KEEP（异地保留份数）必须是不小于 1 的整数，当前是：$KEEP"

ARCHIVE=""; ARCHIVE_BYTES=0; ENC_BYTES=0; STAGE=""; STAGE_NAME=""
log_line() {
  mkdir -p "$(dirname "$LOG_FILE")"
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG_FILE" 2>/dev/null || true
}
write_status() {  # $1=last-success/last-failure，$2=JSON 内容
  mkdir -p "$STATE_DIR"
  local tmp="$STATE_DIR/.status.$$.$RANDOM"
  printf '%s\n' "$2" >"$tmp"
  mv "$tmp" "$STATE_DIR/$1.json"
  chmod 600 "$STATE_DIR/$1.json" 2>/dev/null || true
}
# 失败：记录阶段、写 last-failure.json、记日志，然后退出（EXIT trap 负责清理临时文件）。
fail() {  # $1=阶段 $2=说明
  local stage="$1" msg_text="$2"
  if [ "$APPLY" -eq 1 ]; then
    write_status last-failure "$(printf '{"status":"failed","finished_at":"%s","stage":"%s","archive":"%s","error":"%s"}' \
      "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$stage" "$ARCHIVE" "$msg_text")"
    log_line "失败 阶段=$stage 归档=$ARCHIVE 说明=$msg_text"
  fi
  echo "出错：$msg_text" >&2
  exit 1
}
# 任何退出都清掉本地加密临时文件（私钥不在服务器上，也不留多余密文）。
trap 'rc=$?; rm -rf "$STAGING_DIR"/.upload-* "$STAGING_DIR"/*.age 2>/dev/null || true; [ $rc -ne 0 ] && [ "$APPLY" -eq 1 ] && log_line "异常退出 rc=$rc 归档=$ARCHIVE"; exit $rc' EXIT

say() { if [ "$APPLY" -eq 1 ]; then echo "$*"; else echo "[dry-run] $*"; fi; }

msg "== 1/5 找到并校验最新本地备份 =="
[ -d "$BACKUP_SRC_DIR" ] || fail "find" "找不到本地备份目录 $BACKUP_SRC_DIR，请确认每日备份（deploy/bin/backup.sh）已经在跑。"
ARCHIVE_PATH="$(ls -t "$BACKUP_SRC_DIR"/backup-*.tar.gz 2>/dev/null | head -1 || true)"
[ -n "$ARCHIVE_PATH" ] && [ -f "$ARCHIVE_PATH" ] || fail "find" "$BACKUP_SRC_DIR 下没有 backup-*.tar.gz，请先手动跑一次 deploy/bin/backup.sh。"
ARCHIVE="$(basename "$ARCHIVE_PATH")"
[[ "$ARCHIVE" =~ ^backup-[0-9]{8}-[0-9]{6}(-[0-9]+)?\.tar\.gz$ ]] || fail "find" "最新备份文件名不符合 backup-YYYYMMDD-HHMMSS.tar.gz：$ARCHIVE"
ARCHIVE_BYTES="$(stat -c '%s' "$ARCHIVE_PATH" 2>/dev/null || stat -f '%z' "$ARCHIVE_PATH")"
[ "$ARCHIVE_BYTES" -gt 0 ] || fail "verify" "备份文件大小为 0：$ARCHIVE"
msg "最新备份：$ARCHIVE（$ARCHIVE_BYTES 字节）"
gzip -t "$ARCHIVE_PATH" || fail "verify" "gzip 完整性测试没通过，备份可能已损坏：$ARCHIVE"
if [ "$APPLY" -eq 1 ]; then
  # 容器内做完整校验（SHA-256、schema、核心表行数），与 deploy/bin/backup.sh 的校验口径一致。
  compose exec -T app python backup.py verify "/app/data/backups/$ARCHIVE" \
    || fail "verify" "backup.py 完整校验没通过：$ARCHIVE（详见容器日志），本次不上传。"
  msg "完整性校验通过（gzip + backup.py verify）。"
else
  echo "[dry-run] gzip -t 已通过；正式执行时还会在 app 容器内运行 backup.py verify 做完整校验。"
fi

msg "== 2/5 用 age 加密（公钥加密；服务器上没有私钥就无法解密）=="
ENC_NAME="$ARCHIVE.age"
if [ "$APPLY" -eq 1 ]; then
  mkdir -p "$STAGING_DIR"
  chmod 700 "$STAGING_DIR"
  STAGE_NAME=".upload-$RANDOM-$$"
  STAGE="$STAGING_DIR/$STAGE_NAME"
  mkdir -p "$STAGE"
  age -r "$AGE_RECIPIENT" -o "$STAGE/$ENC_NAME" "$ARCHIVE_PATH" \
    || fail "encrypt" "age 加密失败，请确认 AGE_RECIPIENT 是有效的 age 公钥。"
  ENC_BYTES="$(stat -c '%s' "$STAGE/$ENC_NAME" 2>/dev/null || stat -f '%z' "$STAGE/$ENC_NAME")"
  [ "$ENC_BYTES" -gt 0 ] || fail "encrypt" "加密产物为空：$ENC_NAME"
  msg "加密完成：$ENC_NAME（$ENC_BYTES 字节），临时密文上传后立即删除。"
else
  echo "[dry-run] 将要执行：age -r <AGE_RECIPIENT> -o <临时目录>/$ENC_NAME $ARCHIVE_PATH"
fi

msg "== 3/5 经 rclone 上传到 $RCLONE_REMOTE =="
if [ "$APPLY" -eq 1 ]; then
  rclone copyto "$STAGE/$ENC_NAME" "$RCLONE_REMOTE/$ENC_NAME" \
    || fail "upload" "rclone 上传失败，请用 rclone lsf $RCLONE_REMOTE 和 rclone config 检查配置与网络。"
  # 上传后立刻比对远端大小，确认对象真的在、且传完整了。
  REMOTE_SIZE="$(rclone lsl "$RCLONE_REMOTE/$ENC_NAME" | awk 'NR==1{print $1}')"
  [ "$REMOTE_SIZE" = "$ENC_BYTES" ] || fail "upload" "远端文件大小不一致（本地 $ENC_BYTES / 远端 ${REMOTE_SIZE:-无}），请检查对象存储。"
  msg "上传完成并已核对远端大小。"
else
  echo "[dry-run] 将要执行：rclone copyto <临时目录>/$ENC_NAME $RCLONE_REMOTE/$ENC_NAME"
  echo "[dry-run] 上传后将用 rclone lsl 比对远端文件大小，不一致会判失败且不做清理。"
fi

msg "== 4/5 异地保留最近 $KEEP 份（只删异地的旧加密备份）=="
# 文件名带 UTC 时间戳，按文件名倒序即为从新到旧；只处理本脚本命名的 *.tar.gz.age。
# lsf 是只读操作，演练时也执行，方便预览会删掉哪些文件。
mapfile -t REMOTE_FILES < <(rclone lsf "$RCLONE_REMOTE" 2>/dev/null | grep -E '^backup-[0-9]{8}-[0-9]{6}(-[0-9]+)?\.tar\.gz\.age$' | sort -r || true)
TOTAL="${#REMOTE_FILES[@]}"
DELETED=()
if [ "$TOTAL" -gt "$KEEP" ]; then
  for old in "${REMOTE_FILES[@]:$KEEP}"; do
    DELETED+=("$old")
    if [ "$APPLY" -eq 1 ]; then
      rclone deletefile "$RCLONE_REMOTE/$old" || fail "prune" "删除旧备份失败：$old（已停止继续删除，请人工检查）"
      log_line "已删除异地旧备份 $old"
    fi
  done
fi
KEPT="$(( TOTAL > KEEP ? KEEP : TOTAL ))"
if [ "$APPLY" -eq 1 ]; then
  msg "异地现有 $TOTAL 份，保留最近 $KEPT 份，删除 ${#DELETED[@]} 份。"
else
  echo "[dry-run] 当前异地有 $TOTAL 份加密备份；正式执行后将保留 $KEEP 份。"
  if [ "${#DELETED[@]}" -gt 0 ]; then
    printf '[dry-run] 将要删除：%s\n' "${DELETED[@]}"
  else
    echo "[dry-run] 没有需要删除的旧备份。"
  fi
fi

msg "== 5/5 写日志与状态文件 =="
if [ "$APPLY" -eq 1 ]; then
  DELETED_JSON="["
  for d in "${DELETED[@]:-}"; do [ -n "$d" ] && DELETED_JSON+="\"$d\","; done
  DELETED_JSON="${DELETED_JSON%,}]"
  write_status last-success "$(printf '{"status":"ok","finished_at":"%s","archive":"%s","archive_bytes":%s,"encrypted_bytes":%s,"remote":"%s","kept_remote":%s,"deleted_remote":%s}' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ARCHIVE" "$ARCHIVE_BYTES" "$ENC_BYTES" "$RCLONE_REMOTE" "$KEPT" "$DELETED_JSON")"
  rm -f "$STATE_DIR/last-failure.json"
  log_line "成功 归档=$ARCHIVE 本地字节=$ARCHIVE_BYTES 加密字节=$ENC_BYTES 异地保留=$KEPT 删除=${#DELETED[@]}"
  msg "完成。状态：$STATE_DIR/last-success.json；日志：$LOG_FILE"
else
  echo "[dry-run] 演练结束：没有加密、没有上传、没有删除、没有写状态文件。确认无误后加 --apply 正式执行。"
fi
