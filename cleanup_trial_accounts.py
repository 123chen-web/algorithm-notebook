"""删除过期的免邀请码体验账号（users.is_trial = 1）。

体验账号（见 main.py 的 POST /api/auth/trial）任何人都能创建，会持续
产生数据库行。V1 没有后台任务队列（见 README「部署说明」），所以这是
一个独立脚本，设计成定期由外部调度器调用一次：
- Windows 本机：用"任务计划程序"每天运行一次
  `.venv\\Scripts\\python.exe cleanup_trial_accounts.py`
- 部署到 Linux 服务器：用 cron，例如每天凌晨 3 点
  `0 3 * * * /path/to/.venv/bin/python /path/to/cleanup_trial_accounts.py`

删除 users 表里的一行会级联删除这个账号名下的全部
problems/mistakes/reviews/variants/sessions/ai_usage/password_resets
（db.py 里这些表对 users 都是 ON DELETE CASCADE 外键），不用手动挨个表删。
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import connect, init_db

DEFAULT_MAX_AGE_DAYS = 7


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印会删除哪些体验账号，不真的删除",
    )
    parser.add_argument(
        "--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
        help=f"体验账号创建超过多少天后视为过期（默认 {DEFAULT_MAX_AGE_DAYS} 天）",
    )
    args = parser.parse_args(argv)

    if args.max_age_days < 0:
        parser.error("--max-age-days 不能是负数")

    init_db()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=args.max_age_days)
    ).isoformat(timespec="seconds")

    with connect(write=not args.dry_run) as conn:
        expired = conn.execute(
            """
            SELECT id, username, created_at FROM users
            WHERE is_trial = 1 AND created_at < ?
            ORDER BY created_at
            """,
            (cutoff,),
        ).fetchall()

        for row in expired:
            print(f"{row['username']}（创建于 {row['created_at']}）")

        if not args.dry_run and expired:
            conn.executemany(
                "DELETE FROM users WHERE id = ?",
                [(row["id"],) for row in expired],
            )

    if args.dry_run:
        print(f"dry-run 完成，共 {len(expired)} 个过期体验账号，未删除。")
    else:
        print(f"完成，共删除 {len(expired)} 个过期体验账号。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
