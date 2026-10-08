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
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mailer
import push_channels
from db import connect, init_db
from legal import PRODUCT_NAME
from scheduler import today_in_timezone

REMINDER_SUBJECT = f"{PRODUCT_NAME}：今天有易错点待复习"
REMINDER_ITEM_LIMIT = 8
# 微信推送连续失败这么多次后自动关闭，等用户检查 Key 后再手动开启。
AUTO_DISABLE_FAIL_COUNT = 5


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def uses_push(user):
    """该用户当天的提醒是否走微信渠道（开关打开且已保存密钥）。"""
    return bool(user["push_channel"]) and bool(user["push_secret"]) and bool(user["push_enabled"])


def record_push_result(conn, user_id, ok, now=None):
    """成功清零失败计数并记录时间；失败累加，达到上限自动关闭开关。"""
    if ok:
        conn.execute(
            "UPDATE user_push SET fail_count = 0, last_ok_at = ? WHERE user_id = ?",
            (now or _utc_now(), user_id),
        )
        return
    conn.execute(
        "UPDATE user_push SET "
        "fail_count = fail_count + 1, "
        "enabled = CASE WHEN fail_count + 1 >= ? THEN 0 ELSE enabled END "
        "WHERE user_id = ?",
        (AUTO_DISABLE_FAIL_COUNT, user_id),
    )


def due_count(conn, user_id, day_iso):
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND m.due_date <= ? AND m.suspended_at IS NULL
        """,
        (user_id, day_iso),
    ).fetchone()
    return row["n"]


def due_mistakes(conn, user_id, day_iso):
    """优先展示最早到期的易错点；同日到期时按 ID 保持顺序稳定。"""
    return conn.execute(
        """
        SELECT p.title, p.zone, m.description, m.due_date
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND m.due_date <= ? AND m.suspended_at IS NULL
        ORDER BY m.due_date ASC, m.id ASC
        LIMIT ?
        """,
        (user_id, day_iso, REMINDER_ITEM_LIMIT),
    ).fetchall()


def reminder_body(username, count, mistakes):
    displayed = mistakes[:REMINDER_ITEM_LIMIT]
    lines = [
        f"你好 {username}，", "",
        f"今天有 {count} 条易错点到期待复习（含逾期），按到期日期从早到晚列出：", "",
    ]
    for index, mistake in enumerate(displayed, start=1):
        title = " ".join(mistake["title"].split())
        zone = " ".join(mistake["zone"].split())
        description = " ".join(mistake["description"].split())
        if len(description) > 80:
            description = description[:79] + "…"
        lines.extend([
            f"{index}. [{zone}] {title}",
            f"   易错点：{description}（到期：{mistake['due_date']}）",
        ])

    remaining = count - len(displayed)
    if remaining > 0:
        lines.extend(["", f"还有 {remaining} 条易错点待复习，可在网站查看。"])
    lines.extend([
        "", f"打开{PRODUCT_NAME}，进入“今日复习”，从最早到期的一条开始吧。", "",
        "（这是自动提醒邮件，回复不会被处理。）",
    ])
    return "\n".join(lines)


def main(argv=None, post=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印会给谁发、发几条，不真的发信也不改数据库",
    )
    args = parser.parse_args(argv)

    # 没配 SMTP 时邮件用户跳过；微信用户仍照常推送。结尾若一条微信都没发出，
    # 再沿用旧约定提示 SMTP 未配置并返回 1。
    email_ready = mailer.smtp_configured()

    init_db()
    sent_total = 0
    sent_push = 0
    skipped_no_email = 0

    with connect(write=not args.dry_run) as conn:
        users = conn.execute(
            "SELECT u.id, u.username, u.email, u.timezone, u.last_reminder_sent, u.reminder_opt_in, "
            "up.channel AS push_channel, up.secret AS push_secret, "
            "up.enabled AS push_enabled, up.fail_count AS push_fail_count "
            "FROM users u LEFT JOIN user_push up ON up.user_id = u.id "
            "WHERE u.deleted_at IS NULL AND u.is_banned = 0"
        ).fetchall()

        for user in users:
            use_push = uses_push(user)
            if not use_push:
                if not user["reminder_opt_in"]:
                    continue
                if not user["email"]:
                    skipped_no_email += 1
                    continue
                if not args.dry_run and not email_ready:
                    # 邮件渠道没配置又没有启用微信：本轮跳过。
                    continue

            day = today_in_timezone(user["timezone"]).isoformat()
            if user["last_reminder_sent"] == day:
                continue

            count = due_count(conn, user["id"], day)
            if count == 0:
                continue

            print(f"{user['username']} <{'微信' if use_push else user['email']}>：{count} 条待复习")
            if args.dry_run:
                continue

            body = reminder_body(
                user["username"], count, due_mistakes(conn, user["id"], day)
            )
            if use_push:
                result = push_channels.send(
                    user["push_channel"], user["push_secret"],
                    REMINDER_SUBJECT, body, post=post,
                )
                if not result["ok"]:
                    # 日志与输出只带渠道与分类，绝不出现密钥原文。
                    record_push_result(conn, user["id"], False)
                    print(f"  微信推送失败（{result['kind']}），跳过（不标记为已发送）。")
                    continue
                record_push_result(conn, user["id"], True)
                sent_push += 1
            else:
                try:
                    mailer.send_email(user["email"], REMINDER_SUBJECT, body)
                except Exception as exc:
                    print(f"  发信失败，跳过（不标记为已发送）：{exc}")
                    continue

            conn.execute(
                "UPDATE users SET last_reminder_sent = ? WHERE id = ?",
                (day, user["id"]),
            )
            sent_total += 1

    if skipped_no_email:
        print(f"{skipped_no_email} 个账号没有邮箱且未启用微信提醒，已跳过。")
    if args.dry_run:
        print("dry-run 完成，未发信。")
        return 0
    if not email_ready and sent_push == 0:
        print("SMTP 尚未配置（.env 里缺 SMTP_HOST），没有发出任何邮件。")
        return 1
    if not email_ready:
        print("SMTP 尚未配置（.env 里缺 SMTP_HOST），邮件用户已跳过，仅发送微信提醒。")
    print(f"完成，共发送 {sent_push} 条微信提醒、{sent_total - sent_push} 封提醒邮件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
