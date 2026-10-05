"""列出、授予或撤销数据库中的管理员身份。"""
import argparse
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from db import ROOT, connect, init_db, normalize_username, sec_username_key, sec_has_invisible_username


def find_user(conn, value=None, *, user_id=None):
    if user_id is not None:
        user = conn.execute(
            "SELECT id, username, is_admin FROM users "
            "WHERE id = ? AND deleted_at IS NULL", (user_id,),
        ).fetchone()
        if user is None:
            raise ValueError("找不到这个用户")
        return user
    username = normalize_username(value)
    candidates = [user for user in conn.execute(
        "SELECT id, username, is_admin FROM users WHERE deleted_at IS NULL ORDER BY id"
    ) if sec_username_key(user["username"]) == username]
    if not candidates:
        raise ValueError("找不到这个用户")
    if len(candidates) > 1:
        names = "；".join(f"#{user['id']} {user['username']!r}" for user in candidates)
        raise ValueError(f"用户名有歧义，请使用 --id 明确指定用户：{names}")
    if candidates[0]["username"] != value:
        raise ValueError("找不到精确用户名，请使用库中的原始用户名或 --id")
    return candidates[0]


def check_usernames():
    """仅以 SQLite mode=ro 打开已有库，绝不初始化、迁移或修改数据库。"""
    path = Path(os.getenv("DATABASE_PATH", "data/notebook.db")).expanduser()
    path = (path if path.is_absolute() else ROOT / path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        users = conn.execute(
            "SELECT id, username FROM users WHERE deleted_at IS NULL ORDER BY id"
        ).fetchall()
    groups = {}
    for user_id, username in users:
        groups.setdefault(sec_username_key(username), []).append((user_id, username))
    issues = 0
    for key, candidates in groups.items():
        if len(candidates) > 1:
            names = "；".join(f"#{uid} {name!r}" for uid, name in candidates)
            print(f"规范化冲突 {key!r}：{names}")
            issues += 1
    for user_id, username in users:
        if sec_has_invisible_username(username):
            print(f"不可见字符：#{user_id} {ascii(username)}")
            issues += 1
        if sec_username_key(username).startswith("已注销用户"):
            print(f"保留名前缀：#{user_id} {username!r}")
            issues += 1
    if not issues:
        print("未发现用户名冲突、不可见字符或保留名前缀占用。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出当前管理员")
    commands.add_parser("check-usernames", help="只读检查存量用户名冲突与保留名前缀")
    grant = commands.add_parser("grant", help="授予管理员身份")
    revoke = commands.add_parser("revoke", help="撤销管理员身份")
    for command in (grant, revoke):
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument("username", nargs="?", help="用户名（有歧义时拒绝）")
        target.add_argument("--id", dest="user_id", type=int, help="明确的用户 ID")
    revoke.add_argument("--force", action="store_true", help="允许撤销最后一个管理员")
    args = parser.parse_args(argv)

    if args.command == "check-usernames":
        try:
            check_usernames()
        except (OSError, sqlite3.Error) as error:
            print(f"错误：{error}", file=sys.stderr)
            return 1
        return 0
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
            user = find_user(conn, args.username, user_id=args.user_id)
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
