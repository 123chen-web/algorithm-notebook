"""管理员身份与手动收款：列出/授予/撤销管理员，处理付款登记，批量生成兑换码。"""
import argparse
import hashlib
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import manual_claims
from db import connect, init_db, normalize_username

REDEEM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def find_user(conn, value):
    username = normalize_username(value)
    user = conn.execute(
        "SELECT id, username, is_admin FROM users "
        "WHERE username = ? AND deleted_at IS NULL",
        (username,),
    ).fetchone()
    if user is None:
        user = conn.execute(
            "SELECT id, username, is_admin FROM users "
            "WHERE username = ? AND deleted_at IS NULL",
            (value.lower(),),
        ).fetchone()
    if user is None:
        raise ValueError("找不到这个用户")
    return user


def redeem_hash(raw):
    # 与 main.redeem_code_hash 一致：规范化后取 SHA-256（生成的码本身已是大写无分隔）。
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def pending_claims(limit=50):
    with connect() as conn:
        rows = conn.execute(
            "SELECT c.id, u.username, p.name AS plan_name, c.payer_note, c.created_at "
            "FROM manual_payment_claims c JOIN users u ON u.id = c.user_id "
            "JOIN plans p ON p.id = c.plan_id WHERE c.status = 'pending' "
            "ORDER BY c.id LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        print("没有待处理的付款登记。")
        return 0
    print("编号\t用户\t套餐\t付款备注\t登记时间")
    for row in rows:
        print(f"{row['id']}\t{row['username']}\t{row['plan_name']}\t"
              f"{row['payer_note']}\t{row['created_at']}")
    return 0


def make_codes(plan_id, count, days, note, expires_in_days):
    now = datetime.now(timezone.utc)
    expires = ((now + timedelta(days=expires_in_days)).isoformat(timespec="seconds")
               if expires_in_days else None)
    codes = []
    with connect(write=True) as conn:
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if plan is None:
            raise ValueError("套餐不存在")
        for _ in range(count):
            for attempt in range(10):
                raw = "".join(secrets.choice(REDEEM_ALPHABET) for _ in range(16))
                try:
                    conn.execute(
                        "INSERT INTO redeem_codes(code_hash, code_hint, plan_id, period_days, "
                        "note, created_by, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                        (redeem_hash(raw), raw[-4:], plan_id,
                         days if days is not None else plan["period_days"], note,
                         now.isoformat(timespec="seconds"), expires),
                    )
                    break
                except sqlite3.IntegrityError:
                    if attempt == 9:
                        raise
            codes.append("-".join(raw[index:index + 4] for index in range(0, 16, 4)))
    print(f"已生成 {count} 个『{plan['name']}』兑换码（明文只显示这一次，数据库不保存）：")
    for code in codes:
        print(code)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出当前管理员")
    grant = commands.add_parser("grant", help="授予管理员身份")
    grant.add_argument("username", help="用户名")
    revoke = commands.add_parser("revoke", help="撤销管理员身份")
    revoke.add_argument("username", help="用户名")
    revoke.add_argument("--force", action="store_true", help="允许撤销最后一个管理员")
    commands.add_parser("pending-claims", help="列出待处理的付款登记")
    confirm = commands.add_parser("confirm-claim", help="确认收款并开通套餐")
    confirm.add_argument("claim_id", type=int, help="登记编号")
    reject = commands.add_parser("reject-claim", help="驳回付款登记")
    reject.add_argument("claim_id", type=int, help="登记编号")
    reject.add_argument("--reason", required=True, help="驳回原因（最多 80 字）")
    codes = commands.add_parser("make-codes", help="批量生成兑换码（只打印一次）")
    codes.add_argument("--plan", type=int, required=True, help="套餐编号")
    codes.add_argument("--count", type=int, required=True, help="数量（1–50）")
    codes.add_argument("--days", type=int, help="覆盖套餐默认天数（1–3650）")
    codes.add_argument("--note", default="", help="备注（最多 100 字）")
    codes.add_argument("--expires-in-days", type=int, help="兑换码有效天数（1–365）")
    args = parser.parse_args(argv)

    init_db()
    if args.command in ("pending-claims", "confirm-claim", "reject-claim", "make-codes"):
        try:
            return run_payment_command(args)
        except (ValueError, manual_claims.ClaimError) as error:
            print(f"错误：{getattr(error, 'detail', error)}", file=sys.stderr)
            return 1
    if args.command == "list":
        with connect() as conn:
            users = conn.execute(
                "SELECT id, username FROM users "
                "WHERE is_admin = 1 AND deleted_at IS NULL ORDER BY id"
            ).fetchall()
        if not users:
            print("当前没有管理员账号。")
        else:
            print("管理员账号：")
            for user in users:
                print(f"{user['id']}\t{user['username']}")
        return 0

    try:
        with connect(write=True) as conn:
            user = find_user(conn, args.username)
            if args.command == "revoke" and user["is_admin"] and not args.force:
                count = conn.execute(
                    "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND deleted_at IS NULL"
                ).fetchone()[0]
                if count <= 1:
                    raise ValueError("不能撤销最后一个管理员；如确需撤销，请加 --force")
            is_admin = int(args.command == "grant")
            conn.execute(
                "UPDATE users SET is_admin = ? WHERE id = ?",
                (is_admin, user["id"]),
            )
    except ValueError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1

    action = "授予" if is_admin else "撤销"
    print(f"已{action}『{user['username']}』的管理员身份。")
    return 0


def run_payment_command(args):
    if args.command == "pending-claims":
        return pending_claims()
    if args.command == "make-codes":
        if not 1 <= args.count <= 50:
            raise ValueError("数量必须在 1 到 50 之间")
        if args.days is not None and not 1 <= args.days <= 3650:
            raise ValueError("天数必须在 1 到 3650 之间")
        if args.expires_in_days is not None and not 1 <= args.expires_in_days <= 365:
            raise ValueError("有效天数必须在 1 到 365 之间")
        if len(args.note) > 100:
            raise ValueError("备注最多 100 字")
        return make_codes(args.plan, args.count, args.days, args.note, args.expires_in_days)
    if args.command == "reject-claim":
        reason = args.reason.strip()
        if not 1 <= len(reason) <= 80:
            raise ValueError("驳回原因需要 1 到 80 字")
        with connect(write=True) as conn:
            manual_claims.reject_claim(conn, args.claim_id, None, reason)
        print(f"已驳回登记 #{args.claim_id}。")
        return 0
    with connect(write=True) as conn:
        _, info = manual_claims.confirm_claim(conn, args.claim_id, None)
    print(f"已确认登记 #{args.claim_id}：『{info['username']}』的『{info['plan_name']}』"
          f"已开通，到期 {info['plan_expires_at']}（UTC）。")
    manual_claims.send_confirmation_email(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
