#!/usr/bin/env bash
# 异地备份恢复演练：从异地下载最新一份加密备份 → 用 age 私钥解密 → 在独立临时目录恢复
# → 对恢复出的数据库跑 PRAGMA integrity_check 和核心表行数检查。
# 全程只读线上数据：不停止服务、不替换任何线上文件；backup.py 自身也禁止恢复到线上数据目录。
# 默认只演练（dry-run），加 --apply 才真正下载解密；私钥用 --identity 临时指定，演练完务必从服务器删除。
#
# 用法（在仓库根目录 /srv/algorithm-notebook 下）：
#   sudo deploy/bin/restore-drill.sh --identity /root/oy-age-key.txt          # 演练（只打印步骤）
#   sudo deploy/bin/restore-drill.sh --identity /root/oy-age-key.txt --apply  # 正式演练
#   sudo deploy/bin/restore-drill.sh --identity k.txt --remote oycos:桶/前缀  # 临时覆盖配置
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ENV_FILE="${OY_OFFSITE_ENV:-/etc/oy-offsite-backup.env}"
STATE_DIR="${OY_OFFSITE_STATE_DIR:-/var/lib/oy-offsite-backup}"
DRILL_ROOT="$STATE_DIR/drills"

APPLY=0
IDENTITY=""
CLI_REMOTE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --identity) [ $# -ge 2 ] || die "--identity 后面要跟 age 私钥文件路径"; IDENTITY="$2"; shift 2 ;;
    --identity=*) IDENTITY="${1#--identity=}"; shift ;;
    --remote) [ $# -ge 2 ] || die "--remote 后面要跟 rclone 路径"; CLI_REMOTE="$2"; shift 2 ;;
    --remote=*) CLI_REMOTE="${1#--remote=}"; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "不认识的参数：$1（可用参数：--apply / --identity / --remote）" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo deploy/bin/restore-drill.sh"
need_cmd age
need_cmd rclone
need_cmd gzip
need_cmd python3

RCLONE_REMOTE="$CLI_REMOTE"
if [ -z "$RCLONE_REMOTE" ] && [ -f "$ENV_FILE" ]; then
  RCLONE_REMOTE="$(grep -E '^RCLONE_REMOTE=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
fi
[ -n "$RCLONE_REMOTE" ] || die "没有配置 RCLONE_REMOTE。请在 $ENV_FILE 配置或用 --remote 传入。"
[[ "$RCLONE_REMOTE" =~ ^[A-Za-z0-9._~-]+:[A-Za-z0-9._~/-]+$ ]] || die "RCLONE_REMOTE 格式不正确，示例：oycos:my-bucket/oy-backups"
[ -n "$IDENTITY" ] || die "必须用 --identity 指定 age 私钥文件（私钥平时不放在服务器上，演练时临时拷入，演练后删除）。"

DRILL_DIR=""
cleanup_on_error() {
  rc=$?
  if [ $rc -ne 0 ] && [ -n "$DRILL_DIR" ] && [ -d "$DRILL_DIR" ]; then
    rm -rf "$DRILL_DIR" 2>/dev/null || true
  fi
  exit $rc
}
trap cleanup_on_error EXIT

say() { if [ "$APPLY" -eq 1 ]; then echo "$*"; else echo "[dry-run] $*"; fi; }

msg "== 1/6 检查 age 私钥 =="
if [ ! -f "$IDENTITY" ]; then
  die "找不到私钥文件：$IDENTITY。age-keygen 生成的私钥文件第一行是 # created: ...，第二行以 AGE-SECRET-KEY- 开头。"
fi
if ! grep -q '^AGE-SECRET-KEY-1' "$IDENTITY"; then
  die "$IDENTITY 里没有找到 AGE-SECRET-KEY-1 开头的私钥，请确认拿的是私钥文件而不是 age1 开头的公钥。"
fi
# 私钥权限过松 age 会拒绝读取（也确实危险），顺手收紧。
if [ "$APPLY" -eq 1 ]; then
  chmod 600 "$IDENTITY" 2>/dev/null || true
else
  echo "[dry-run] 将要执行：chmod 600 $IDENTITY"
fi
msg "私钥文件已找到：$IDENTITY（演练结束后请立刻从服务器删除该文件）。"

msg "== 2/6 列出异地最新一份加密备份 =="
LATEST_ENC="$(rclone lsf "$RCLONE_REMOTE" 2>/dev/null | grep -E '^backup-[0-9]{8}-[0-9]{6}(-[0-9]+)?\.tar\.gz\.age$' | sort -r | head -1 || true)"
[ -n "$LATEST_ENC" ] || die "异地 $RCLONE_REMOTE 下没有找到 backup-*.tar.gz.age，请先成功执行一次 offsite-backup.sh --apply。"
msg "最新异地备份：$LATEST_ENC"
LATEST_TAR="${LATEST_ENC%.age}"

if [ "$APPLY" -ne 1 ]; then
  cat <<EOF
[dry-run] 将要执行（全部在独立临时目录，不碰线上数据）：
[dry-run]   rclone copyto $RCLONE_REMOTE/$LATEST_ENC <临时目录>/$LATEST_ENC
[dry-run]   age -d -i $IDENTITY -o <临时目录>/$LATEST_TAR <临时目录>/$LATEST_ENC
[dry-run]   gzip -t、python3 backup.py verify、python3 backup.py restore --into <临时目录>/restored
[dry-run]   对恢复出的数据库执行 PRAGMA integrity_check 并统计 users/problems/mistakes/reviews 行数
[dry-run] 演练不会停止服务、不会写入 $DATA_DIR。确认无误后加 --apply 正式演练。
EOF
  exit 0
fi

msg "== 3/6 下载并解密到独立临时目录 =="
DRILL_DIR="$DRILL_ROOT/drill-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$DRILL_DIR"
chmod 700 "$DRILL_DIR"
rclone copyto "$RCLONE_REMOTE/$LATEST_ENC" "$DRILL_DIR/$LATEST_ENC" \
  || die "下载失败：$RCLONE_REMOTE/$LATEST_ENC（检查网络与 rclone 配置）。"
age -d -i "$IDENTITY" -o "$DRILL_DIR/$LATEST_TAR" "$DRILL_DIR/$LATEST_ENC" \
  || die "解密失败：私钥与加密公钥不配对，或文件已损坏。"
gzip -t "$DRILL_DIR/$LATEST_TAR" || die "解密后的文件 gzip 校验没通过。"
msg "下载、解密完成：$DRILL_DIR/$LATEST_TAR"

msg "== 4/6 校验归档并恢复到临时目录（backup.py 禁止恢复到线上数据目录）=="
RESTORED="$DRILL_DIR/restored"
python3 "$REPO_DIR/backup.py" verify "$DRILL_DIR/$LATEST_TAR" \
  || die "归档完整校验没通过：$LATEST_TAR"
python3 "$REPO_DIR/backup.py" restore "$DRILL_DIR/$LATEST_TAR" --into "$RESTORED" \
  || die "恢复到临时目录失败。"
[ -f "$RESTORED/notebook.db" ] || die "恢复结果里没有 notebook.db，归档内容异常。"
msg "已恢复到 $RESTORED（线上数据目录 $DATA_DIR 没有被改动）。"

msg "== 5/6 SQLite 完整性检查与核心表行数 =="
python3 - "$RESTORED/notebook.db" <<'PY'
import sqlite3, sys
from pathlib import Path
uri = Path(sys.argv[1]).resolve().as_uri() + "?mode=ro"
con = sqlite3.connect(uri, uri=True)
result = con.execute("PRAGMA integrity_check").fetchone()[0]
print(f"integrity_check: {result}")
if result != "ok":
    sys.exit(2)
names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
for table in ("users", "problems", "mistakes", "reviews"):
    if table in names:
        print(f"rows {table}: {con.execute(f'SELECT COUNT(*) FROM \"{table}\"').fetchone()[0]}")
    else:
        print(f"rows {table}: 表不存在")
con.close()
PY
msg "完整性检查通过，核心表行数已打印（请与你对网站当前规模的印象核对，明显偏小可能恢复错了版本）。"

msg "== 6/6 演练结束 =="
cat <<EOF
恢复演练成功。演练文件保留在：$DRILL_DIR
  - 抽查无误后应删除演练副本（含用户数据）：rm -rf $DRILL_DIR
  - 【重要】删除临时拷入服务器的 age 私钥：rm -f $IDENTITY
  - 本次演练没有停止服务、没有改动 $DATA_DIR 下的任何线上文件。
建议每季度演练一次；真正的灾难恢复步骤见 docs/operations/backup-and-restore.md。
EOF
