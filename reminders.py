"""F2 每日复习提醒邮件：按用户当地时区算"今天"，只给 due 到期数 > 0 的用户发信。

这是一个独立脚本，设计成每天由外部调度器调用一次（见 send_reminders.py 的
部署注释），也可以被其他定时任务 import 调用 `send_daily_reminders`。

与老脚本 send_reminders.py 的区别：
- 按用户自己的时区算"今天"（老脚本也按时区，但这里是新流程的独立入口）；
- 内容极简：今日待复习 N 道（预计 M 分钟，按每道 2 分钟估）+ 最紧急的 3 道
  直达链接 + 连续打卡 at-risk 时加一句提醒；
- 页脚带一键退订链接（含 reminder_token，免登录），走
  routers/reminder.py 的 GET /api/reminder/unsubscribe；
- 发信渠道抽象成 mailer 参数注入，方便测试 fake；CLI 真实发送时走
  项目现有 mailer.send_email。

与 send_reminders.py 共用 last_reminder_sent，同一用户当地同日成功只发送一次。
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db
import mailer as real_mailer
from learning_stats import current_streak
from legal import PRODUCT_NAME
from scheduler import today_in_timezone

logger = logging.getLogger("algorithm_notebook")

# 每道题估计的复习分钟数：内容里"预计 M 分钟 = N × 2"。
MINUTES_PER_MISTAKE = 2
# 连续打卡 ≥ 这么多天、但今天还没复习时，在邮件里加一句 at-risk 提醒。
STREAK_AT_RISK_DAYS = 3
# 邮件里列出的最紧急的易错点数量。
TOP_DUE_LIMIT = 3

DEFAULT_APP_BASE_URL = "http://localhost:8000"


def app_base_url():
    """邮件里直达链接的前缀；部署时在环境变量 APP_BASE_URL 里配公网地址。"""
    return os.getenv("APP_BASE_URL", "").strip() or DEFAULT_APP_BASE_URL


def _review_dates(conn, user_id):
    """该用户所有复习发生过的（UTC 转到用户时区后的）日期集合。"""
    rows = conn.execute(
        """
        SELECT r.reviewed_at
        FROM reviews r
        JOIN mistakes m ON m.id = r.mistake_id
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    dates = set()
    for row in rows:
        try:
            reviewed_at = datetime.fromisoformat(row["reviewed_at"])
        except (TypeError, ValueError):
            continue
        dates.add(reviewed_at)
    return dates


def streak_at_risk(timezone_name, today, review_datetimes):
    """连续打卡 ≥3 天且今天还没复习 → 返回当前连续天数，否则返回 None。

    current_streak 的语义：今天没打卡但昨天打卡了，算"还没断"。用它就能判断
    今天是否打卡：review_dates 里有没有今天。
    """
    review_dates = {
        today_in_timezone(timezone_name, stamp) for stamp in review_datetimes
    }
    streak = current_streak(review_dates, today)
    if streak >= STREAK_AT_RISK_DAYS and today not in review_dates:
        return streak
    return None


def due_count(conn, user_id, day_iso):
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?
        """,
        (user_id, day_iso),
    ).fetchone()
    return row["n"]


def top_due(conn, user_id, day_iso, limit=TOP_DUE_LIMIT):
    """到期最早的若干易错点（带题目和链接用的 mistake id）。"""
    return conn.execute(
        """
        SELECT m.id, p.title, m.description, m.due_date
        FROM mistakes m
        JOIN problems p ON p.id = m.problem_id
        WHERE p.user_id = ? AND m.suspended_at IS NULL AND m.due_date <= ?
        ORDER BY m.due_date ASC, m.id ASC
        LIMIT ?
        """,
        (user_id, day_iso, limit),
    ).fetchall()


def reminder_subject(count):
    return f"{PRODUCT_NAME}：今日有 {count} 道易错点待复习"


def review_url():
    # 前端是单页应用，#/today 是"今日复习"视图；登录态由浏览器 cookie 携带。
    return f"{app_base_url()}/#/today"


def reminder_body(username, count, top_items, streak_days, unsubscribe_url):
    lines = [
        f"你好 {username}，",
        "",
        f"今日待复习 {count} 道（预计 {count * MINUTES_PER_MISTAKE} 分钟，按每道 {MINUTES_PER_MISTAKE} 分钟估算）。",
        "",
        "最紧急的几道（按到期日期从早到晚）：",
    ]
    for index, item in enumerate(top_items, start=1):
        title = " ".join(item["title"].split())
        due = item["due_date"]
        lines.append(f"{index}. {title}（到期：{due}）")
        lines.append(f"   {review_url()}")
    remaining = count - len(top_items)
    if remaining > 0:
        lines.extend(["", f"还有 {remaining} 道待复习，可在网站查看。"])
    if streak_days is not None:
        lines.extend([
            "",
            f"提醒：你已经连续打卡 {streak_days} 天，今天还没复习，"
            "别让连续记录断掉哦。",
        ])
    lines.extend([
        "",
        "————",
        f"不想再收到提醒？点这里一键退订：{unsubscribe_url}",
        "（这是自动提醒邮件，回复不会被处理。）",
    ])
    return "\n".join(lines)


def eligible_users(conn):
    """开启提醒（opt_in=1）、有邮箱、未注销的用户。"""
    return conn.execute(
        """
        SELECT id, username, email, timezone, reminder_token, last_reminder_sent
        FROM users
        WHERE deleted_at IS NULL
          AND reminder_opt_in = 1 AND is_banned = 0 AND is_trial = 0
          AND email IS NOT NULL AND email != ''
        ORDER BY id ASC
        """
    ).fetchall()


def send_daily_reminders(mailer, now=None, base_url=None, *, record_sent=True):
    """给符合条件的用户发每日复习提醒。

    mailer: 发信对象，需有 send_email(to, subject, body) 方法；失败时抛异常
      （真实发送传 mailer 模块，测试传 fake）。
    now: 当前时刻（aware UTC datetime），测试注入用；缺省取真实当前时间。
    base_url: 直达链接前缀，缺省读 APP_BASE_URL 环境变量。
    返回 {"sent": 发出的封数, "skipped": 跳过的人数, "failed": 发信失败数}。
    """
    current = now if now is not None else datetime.now(timezone.utc)
    if base_url is None:
        base_url = app_base_url()

    stats = {"sent": 0, "skipped": 0, "failed": 0}
    db.init_db()
    with db.connect() as conn:
        users = eligible_users(conn)
    for candidate in users:
        with db.connect(write=record_sent) as conn:
            user = conn.execute("SELECT * FROM users WHERE id = ?", (candidate["id"],)).fetchone()
            day = today_in_timezone(user["timezone"], current)
            day_iso = day.isoformat()
            if user["deleted_at"] or user["is_banned"] or not user["reminder_opt_in"] or user["last_reminder_sent"] == day_iso:
                stats["skipped"] += 1
                continue
            count = due_count(conn, user["id"], day_iso)
            if count == 0:
                stats["skipped"] += 1
                continue
            items = top_due(conn, user["id"], day_iso)
            streak_days = streak_at_risk(
                user["timezone"], day, _review_dates(conn, user["id"]),
            )
            unsubscribe_url = (
                f"{base_url.rstrip('/')}/api/reminder/unsubscribe?token={user['reminder_token']}"
            )
            subject = reminder_subject(count)
            body = reminder_body(
                user["username"], count, items, streak_days, unsubscribe_url,
            )
            try:
                mailer.send_email(user["email"], subject, body)
            except Exception as exc:
                # 发信异常不能影响其他用户；调用方记日志即可。
                logger.warning(
                    "复习提醒发信失败 user_id=%s", user["id"],
                )
                stats["failed"] += 1
                continue
            if record_sent:
                conn.execute("UPDATE users SET last_reminder_sent = ? WHERE id = ?", (day_iso, user["id"]))
            stats["sent"] += 1
    return stats


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印会给谁发、发几封，不真的发信",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        class DryRunMailer:
            def send_email(self, to, subject, body):
                print(f"[dry-run] 发给 {to}：{subject}")

        stats = send_daily_reminders(DryRunMailer(), record_sent=False)
    else:
        if not real_mailer.smtp_configured():
            print("SMTP 尚未配置（.env 里缺 SMTP_HOST），无法发送。")
            return 1
        stats = send_daily_reminders(real_mailer)

    print(
        f"完成：发出 {stats['sent']} 封，跳过 {stats['skipped']} 人，"
        f"发信失败 {stats['failed']} 人。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(cli())
