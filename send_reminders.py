"""按用户当地日期，给有到期易错点的用户发一封提醒邮件。

V1 没有后台任务队列（见 README「部署说明」），所以这是一个独立脚本，
设计成每天由外部调度器调用一次：
- Windows 本机：用"任务计划程序"每天运行一次
  `.venv\\Scripts\\python.exe send_reminders.py`
- 部署到 Linux 服务器：用 cron，例如每天早上 8 点
  `0 8 * * * /path/to/.venv/bin/python /path/to/send_reminders.py`

同一天内多运行几次也是安全的：发过之后会把
users.last_reminder_sent 设成当天日期，同一天不会重复发送。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mailer
from db import connect, init_db
from scheduler import today_in_timezone

REMINDER_SUBJECT = "算法错题本：今天有易错点待复习"


def due_count(conn, user_id, day_iso):
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND m.due_date <= ?
        """,
        (user_id, day_iso),
    ).fetchone()
    return row["n"]


def reminder_body(username, count):
    return (
        f"你好 {username}，\n\n"
        f"今天有 {count} 条易错点到期待复习，打开算法错题本看看吧。\n\n"
        "（这是自动提醒邮件，回复不会被处理。）"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印会给谁发、发几条，不真的发信也不改数据库",
    )
    args = parser.parse_args(argv)

    if not args.dry_run and not mailer.smtp_configured():
        print("SMTP 尚未配置（.env 里缺 SMTP_HOST），没有发出任何邮件。")
        return 1

    init_db()
    sent = 0
    skipped_no_email = 0

    with connect(write=not args.dry_run) as conn:
        users = conn.execute(
            "SELECT id, username, email, timezone, last_reminder_sent FROM users"
        ).fetchall()

        for user in users:
            if not user["email"]:
                skipped_no_email += 1
                continue

            day = today_in_timezone(user["timezone"]).isoformat()
            if user["last_reminder_sent"] == day:
                continue

            count = due_count(conn, user["id"], day)
            if count == 0:
                continue

            print(f"{user['username']} <{user['email']}>：{count} 条待复习")
            if args.dry_run:
                continue

            try:
                mailer.send_email(
                    user["email"], REMINDER_SUBJECT,
                    reminder_body(user["username"], count),
                )
            except Exception as exc:
                print(f"  发信失败，跳过（不标记为已发送）：{exc}")
                continue

            conn.execute(
                "UPDATE users SET last_reminder_sent = ? WHERE id = ?",
                (day, user["id"]),
            )
            sent += 1

    if skipped_no_email:
        print(f"{skipped_no_email} 个账号没有邮箱，已跳过。")
    print("dry-run 完成，未发信。" if args.dry_run else f"完成，共发送 {sent} 封提醒邮件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
