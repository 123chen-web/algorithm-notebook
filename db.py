import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SCHEMA = """
-- 套餐周期或额度变更时新建记录，旧套餐通过 is_active 停用。
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    period_days INTEGER NOT NULL
        CHECK(typeof(period_days) = 'integer' AND period_days > 0),
    ai_daily_limit INTEGER NOT NULL
        CHECK(typeof(ai_daily_limit) = 'integer' AND ai_daily_limit >= 0),
    price_cents INTEGER NOT NULL
        CHECK(typeof(price_cents) = 'integer' AND price_cents > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    timezone TEXT NOT NULL,
    created_at TEXT NOT NULL
    -- email、last_reminder_sent、is_trial 和订阅字段由 init_db() 迁移补上，
    -- 兼容在这些列加入前就已存在的旧数据库文件。
);

CREATE TABLE IF NOT EXISTS orders (
    -- 订单号由业务生成随机值；TEXT 主键需要显式禁止 NULL。
    id TEXT NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
    -- 保存下单时的金额快照，不随套餐价格变化。
    amount_cents INTEGER NOT NULL
        CHECK(typeof(amount_cents) = 'integer' AND amount_cents > 0),
    channel TEXT NOT NULL CHECK(channel IN ('alipay', 'wechat')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'paid', 'failed', 'closed', 'refunded')),
    provider_trade_no TEXT,
    -- 时间沿用 UTC ISO 8601 字符串，由业务写入。
    created_at TEXT NOT NULL,
    paid_at TEXT,
    closed_at TEXT,
    refunded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_user
ON orders(user_id);

CREATE INDEX IF NOT EXISTS idx_orders_plan
ON orders(plan_id);

-- 尚未取得第三方交易号时保留 NULL，同一渠道的非 NULL 交易号不能重复。
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_provider_trade
ON orders(channel, provider_trade_no);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expiry
ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    language TEXT NOT NULL,
    code TEXT NOT NULL,
    thinking TEXT NOT NULL,
    created_at TEXT NOT NULL
    -- zone 由 init_db() 迁移补上，兼容在这个列加入前就已存在的旧数据库文件。
);

CREATE INDEX IF NOT EXISTS idx_problems_user
ON problems(user_id);

CREATE TABLE IF NOT EXISTS mistakes (
    id INTEGER PRIMARY KEY,
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    repetitions INTEGER NOT NULL DEFAULT 0 CHECK(repetitions >= 0),
    interval_days INTEGER NOT NULL DEFAULT 0 CHECK(interval_days >= 0),
    ease_factor REAL NOT NULL DEFAULT 2.5 CHECK(ease_factor >= 1.3),
    due_date TEXT NOT NULL,
    last_reviewed_at TEXT,
    version INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mistakes_problem
ON mistakes(problem_id);

CREATE INDEX IF NOT EXISTS idx_mistakes_due
ON mistakes(due_date);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    mistake_id INTEGER NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
    quality INTEGER NOT NULL CHECK(quality BETWEEN 0 AND 5),
    reviewed_at TEXT NOT NULL,
    next_due_date TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_mistake
ON reviews(mistake_id);

CREATE TABLE IF NOT EXISTS variants (
    id INTEGER PRIMARY KEY,
    mistake_id INTEGER NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT 'unattempted'
        CHECK(result IN ('unattempted', 'solved', 'partial', 'failed')),
    answer_code TEXT NOT NULL DEFAULT '',
    answer TEXT NOT NULL DEFAULT '',
    expected_answer TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    result_updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_variants_mistake
ON variants(mistake_id);

CREATE TABLE IF NOT EXISTS ai_usage (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    attempts INTEGER NOT NULL CHECK(attempts >= 0),
    PRIMARY KEY(user_id, day)
);

CREATE TABLE IF NOT EXISTS password_resets (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_password_resets_user
ON password_resets(user_id);

-- 软删除：deleted_at 非空表示已删除，对所有人（含作者自己）不可见，
-- 但保留在库里，不做物理删除；由管理员或作者手动 UPDATE 这一列。
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_user
ON posts(user_id);

CREATE INDEX IF NOT EXISTS idx_posts_created
ON posts(created_at);

CREATE TABLE IF NOT EXISTS post_comments (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_post_comments_post
ON post_comments(post_id);

CREATE INDEX IF NOT EXISTS idx_post_comments_user
ON post_comments(user_id);

-- 举报指向帖子或评论二选一，CHECK 保证不会两个都填或者都不填。
-- resolved_at 非空表示管理员已经处理过（删除内容或者忽略），
-- 处理过的举报不再出现在管理队列里。
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    reporter_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
    comment_id INTEGER REFERENCES post_comments(id) ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (
        (post_id IS NOT NULL AND comment_id IS NULL)
        OR (post_id IS NULL AND comment_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_reports_resolved
ON reports(resolved_at);

-- 同一用户对同一目标只能有一条待处理举报；处理完之后如果问题复现，
-- 允许再次举报（用部分索引而不是全局唯一索引）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_unique_pending_post
ON reports(reporter_user_id, post_id)
WHERE post_id IS NOT NULL AND resolved_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_unique_pending_comment
ON reports(reporter_user_id, comment_id)
WHERE comment_id IS NOT NULL AND resolved_at IS NULL;

-- 头像举报单独建表，而不是往 reports 加一列：reports 表已有的 CHECK
-- 约束（post_id/comment_id 二选一）没法通过 ALTER TABLE 改掉，SQLite
-- 修改已有 CHECK 约束必须整表重建；新开一张表可以让新库和旧库用同一份
-- schema，不用给这次改动单独写一次"重建表"迁移。
CREATE TABLE IF NOT EXISTS avatar_reports (
    id INTEGER PRIMARY KEY,
    reporter_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    avatar_owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_avatar_reports_resolved
ON avatar_reports(resolved_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_avatar_reports_unique_pending
ON avatar_reports(reporter_user_id, avatar_owner_id)
WHERE resolved_at IS NULL;
"""

# CREATE TABLE IF NOT EXISTS 不会给旧表补列，需要按需 ALTER TABLE ADD COLUMN；
# SQLite 不能通过 ADD COLUMN 添加 UNIQUE 约束，改用唯一索引代替。
USER_COLUMN_MIGRATIONS = (
    ("email", "ALTER TABLE users ADD COLUMN email TEXT"),
    ("last_reminder_sent", "ALTER TABLE users ADD COLUMN last_reminder_sent TEXT"),
    (
        "is_trial",
        "ALTER TABLE users ADD COLUMN is_trial INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "plan_id",
        "ALTER TABLE users ADD COLUMN plan_id INTEGER REFERENCES plans(id) "
        "ON DELETE RESTRICT",
    ),
    ("plan_expires_at", "ALTER TABLE users ADD COLUMN plan_expires_at TEXT"),
    (
        "is_banned",
        "ALTER TABLE users ADD COLUMN is_banned INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "avatar_version",
        "ALTER TABLE users ADD COLUMN avatar_version INTEGER NOT NULL DEFAULT 0",
    ),
)


ORDER_COLUMN_MIGRATIONS = (
    ("refunded_at", "ALTER TABLE orders ADD COLUMN refunded_at TEXT"),
)


PROBLEM_COLUMN_MIGRATIONS = (
    (
        "zone",
        "ALTER TABLE problems ADD COLUMN zone TEXT NOT NULL DEFAULT '算法'",
    ),
)


VARIANT_COLUMN_MIGRATIONS = (
    (
        "expected_answer",
        "ALTER TABLE variants ADD COLUMN expected_answer TEXT NOT NULL DEFAULT ''",
    ),
    (
        "answer",
        "ALTER TABLE variants ADD COLUMN answer TEXT NOT NULL DEFAULT ''",
    ),
)


@contextmanager
def connect(write=False):
    path = Path(os.getenv("DATABASE_PATH", "data/notebook.db")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        if write:
            # 在读取调度状态前取得写锁，避免并发评分。
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for column, statement in USER_COLUMN_MIGRATIONS:
            if column not in existing:
                conn.execute(statement)

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(orders)")}
        for column, statement in ORDER_COLUMN_MIGRATIONS:
            if column not in existing:
                conn.execute(statement)

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(problems)")}
        for column, statement in PROBLEM_COLUMN_MIGRATIONS:
            if column not in existing:
                conn.execute(statement)

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(variants)")}
        for column, statement in VARIANT_COLUMN_MIGRATIONS:
            if column not in existing:
                conn.execute(statement)

        # 多个账号都没填邮箱时 email 是 NULL，SQLite 的唯一索引允许
        # 多个 NULL 并存，所以旧账号不会因为这条索引互相冲突。
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)"
        )
        # plan_id 由上面的迁移添加，旧库必须先补列再建索引。
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_plan ON users(plan_id)"
        )
