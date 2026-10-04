"""列出、授予或撤销数据库中的管理员身份。"""
import argparse
import sys

from db import connect, init_db, normalize_username


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出当前管理员")
    grant = commands.add_parser("grant", help="授予管理员身份")
    grant.add_argument("username", help="用户名")
    revoke = commands.add_parser("revoke", help="撤销管理员身份")
    revoke.add_argument("username", help="用户名")
    revoke.add_argument("--force", action="store_true", help="允许撤销最后一个管理员")
    args = parser.parse_args(argv)

    init_db()
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


if __name__ == "__main__":
    raise SystemExit(main())
