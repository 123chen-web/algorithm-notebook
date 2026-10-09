"""压测数据预置：直接写库创建 N 个用户，每人 50 条易错点（10 题 x 5），
并为每人预置一个 session token（绕开登录接口的 15 分钟 10 次/IP 限流，
生产环境用户来自不同 IP，该限流在压测里不具代表性；登录接口单独低并发实测）。

用法：
    DATABASE_PATH=/tmp/loadtest/data/notebook.db LOADTEST_USERS=100 \
        /home/hatch/workspace/ouye/venv/bin/python tools/loadtest/seed.py
"""
import os
import sqlite3
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import main

DB = os.environ.get("DATABASE_PATH", "/tmp/loadtest/data/notebook.db")
N_USERS = int(os.environ.get("LOADTEST_USERS", "100"))
PROBLEMS_PER_USER = 10
MISTAKES_PER_PROBLEM = 5
PASSWORD = "Loadtest123!@#"


def seed():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    now = main.utc_now()
    today = date.today().isoformat()
    created = 0
    for i in range(N_USERS):
        username = f"loaduser{i:03d}"
        email = f"loaduser{i:03d}@loadtest.local"
        try:
            cur = conn.execute(
                """INSERT INTO users(username, password_hash, email, timezone,
                                     created_at, is_admin, terms_accepted_at, terms_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (username, main.password_hash(PASSWORD), email, "Asia/Shanghai",
                 now, 0, now, main.TERMS_VERSION),
            )
        except sqlite3.IntegrityError:
            continue  # 已预置过，跳过
        uid = cur.lastrowid
        created += 1
        token = f"loadtest-session-token-{i:03d}-static"
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (main.token_hash(token), uid, int(time.time()) + main.SESSION_SECONDS),
        )
        for p in range(PROBLEMS_PER_USER):
            cur = conn.execute(
                """INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uid, f"压测题目-{i:03d}-{p}", "算法", "python", "x = 1", "思考过程", now),
            )
            pid = cur.lastrowid
            for m in range(MISTAKES_PER_PROBLEM):
                conn.execute(
                    "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
                    (pid, f"易错点 {m}：边界条件未考虑", today),
                )
    conn.commit()
    users = conn.execute("SELECT COUNT(*) FROM users WHERE username LIKE 'loaduser%'").fetchone()[0]
    mistakes = conn.execute(
        "SELECT COUNT(*) FROM mistakes WHERE problem_id IN "
        "(SELECT id FROM problems WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'loaduser%'))"
    ).fetchone()[0]
    conn.close()
    print(f"new users this run: {created}, total loadtest users: {users}, their mistakes: {mistakes}")


if __name__ == "__main__":
    seed()
