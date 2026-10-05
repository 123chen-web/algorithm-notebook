import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def sec_username_key(value):
    """历史用户名的比较键；不改变存量账号，也不限制其登录。"""
    return unicodedata.normalize("NFKC", value).strip().lower()


def sec_has_invisible_username(value):
    return any(unicodedata.category(char) in ("Cc", "Cf") for char in value)


def normalize_username(value):
    # strip 前检查，避免首尾控制字符被悄悄移除。
    if sec_has_invisible_username(value):
        raise ValueError("用户名不能包含不可见字符")
    value = sec_username_key(value)
    if not value:
        raise ValueError("用户名不能为空")
    if len(value) > 32:
        raise ValueError("用户名不能超过 32 个字符")
    return value


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

CREATE TABLE IF NOT EXISTS study_groups (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_group_members (
    group_id INTEGER NOT NULL REFERENCES study_groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_study_group_members_user
ON study_group_members(user_id);

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

-- 错因标签：属于某个用户的某条易错点；user_id 冗余存一份，列标签/计数不用联表。
-- tag 不区分大小写（英文标签），删除易错点或账号时级联删除。
CREATE TABLE IF NOT EXISTS mistake_tags (
    mistake_id INTEGER NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (mistake_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_mistake_tags_user_tag
ON mistake_tags(user_id, tag);

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

-- 新表随 SCHEMA 的幂等执行同时迁移旧库；每个用户只保留最近一次分析。
CREATE TABLE IF NOT EXISTS weakness_insights (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mistake_clusters (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    deleted_at TEXT,
    reply_to_id INTEGER REFERENCES post_comments(id) ON DELETE SET NULL
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


PLAN_COLUMN_MIGRATIONS = (
    # is_active 控制"展示 + 能买"两件事一起开关；purchasable 单独控制"能不能
    # 下单"，用来支持"先只展示新套餐、暂不接真实支付"这种上线节奏，默认 1
    # 让所有已有套餐的购买行为保持不变。
    (
        "purchasable",
        "ALTER TABLE plans ADD COLUMN purchasable INTEGER NOT NULL DEFAULT 1",
    ),
)


POST_COMMENT_COLUMN_MIGRATIONS = (
    (
        "reply_to_id",
        "ALTER TABLE post_comments ADD COLUMN reply_to_id INTEGER "
        "REFERENCES post_comments(id) ON DELETE SET NULL",
    ),
)


@contextmanager
def connect(write=False, *, create=True):
    path = Path(os.getenv("DATABASE_PATH", "data/notebook.db")).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=10)
    else:
        # 健康检查只打开已有数据库，不能把缺失文件误建成空库。
        conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
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


def _apply_baseline(conn):
    # executescript 会隐式提交；逐条执行同一份 SQL，让基线也能完整回滚。
    statement = ""
    for line in SCHEMA.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""

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

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(plans)")}
    for column, statement in PLAN_COLUMN_MIGRATIONS:
        if column not in existing:
            conn.execute(statement)

    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(post_comments)")
    }
    for column, statement in POST_COMMENT_COLUMN_MIGRATIONS:
        if column not in existing:
            conn.execute(statement)

    # 旧表先补 reply_to_id 列，再创建索引。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_comments_reply_to "
        "ON post_comments(reply_to_id)"
    )

    # 多个账号都没填邮箱时 email 是 NULL，SQLite 的唯一索引允许
    # 多个 NULL 并存，所以旧账号不会因为这条索引互相冲突。
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)"
    )
    # plan_id 由上面的迁移添加，旧库必须先补列再建索引。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_plan ON users(plan_id)"
    )


def _apply_ai_calls(conn):
    conn.execute(
        """
        CREATE TABLE ai_calls (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            feature TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            ok INTEGER NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX idx_ai_calls_created_at ON ai_calls(created_at)")
    conn.execute(
        "CREATE INDEX idx_ai_calls_user_created_at ON ai_calls(user_id, created_at)"
    )


def _apply_accounts_and_privacy(conn):
    conn.execute("ALTER TABLE users ADD COLUMN deleted_at TEXT")
    conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE users ADD COLUMN terms_accepted_at TEXT")
    conn.execute("ALTER TABLE users ADD COLUMN terms_version TEXT")


def _apply_review_log_columns(conn):
    conn.execute("ALTER TABLE reviews ADD COLUMN elapsed_days INTEGER")
    conn.execute("ALTER TABLE reviews ADD COLUMN scheduled_days INTEGER")
    conn.execute("ALTER TABLE reviews ADD COLUMN ease_before REAL")
    conn.execute("ALTER TABLE reviews ADD COLUMN repetitions_before INTEGER")


def _apply_forum_accept_votes_summaries(conn):
    conn.execute("ALTER TABLE posts ADD COLUMN accepted_comment_id INTEGER")
    conn.execute(
        """
        CREATE TABLE comment_votes (
            comment_id INTEGER NOT NULL REFERENCES post_comments(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY(comment_id, user_id)
        )
        """
    )
    conn.execute("CREATE INDEX idx_comment_votes_user ON comment_votes(user_id)")
    conn.execute(
        """
        CREATE TABLE post_summaries (
            post_id INTEGER PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
            signature TEXT NOT NULL,
            content TEXT NOT NULL,
            comment_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _apply_manual_payment_and_redeem_codes(conn):
    conn.execute(
        """
        CREATE TABLE redeem_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_hash TEXT NOT NULL UNIQUE,
            code_hint TEXT NOT NULL,
            plan_id INTEGER NOT NULL REFERENCES plans(id),
            period_days INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            expires_at TEXT,
            redeemed_by INTEGER REFERENCES users(id),
            redeemed_at TEXT,
            revoked_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_redeem_codes_redeemed_by ON redeem_codes(redeemed_by)"
    )
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO app_settings(key, value) VALUES "
        "('manual_payment_enabled', '0'), ('manual_payment_contact', '')"
    )


def _apply_forum_zones(conn):
    # 旧帖子的 zone 为 NULL（前端显示“未分区”）。
    conn.execute("ALTER TABLE posts ADD COLUMN zone TEXT")
    # 讨论区列表按帖子分组统计未删除评论数、最新评论时间和近 7 天评论数。
    # 现有 idx_post_comments_post 只含 post_id，不等价，所以新建覆盖索引。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_post_comments_post_visible "
        "ON post_comments(post_id, deleted_at, created_at)"
    )


def _apply_rank_board(conn):
    # 默认参与公开榜单；用户在账号菜单里关掉后为 1。
    conn.execute(
        "ALTER TABLE users ADD COLUMN public_rank_opt_out INTEGER NOT NULL DEFAULT 0"
    )
    # 昨日榜单按 reviewed_at 的时间范围聚合，没有这条索引就是全表扫描。
    conn.execute("CREATE INDEX idx_reviews_reviewed_at ON reviews(reviewed_at)")
    # 管理员手写的“今日一条”。日期是北京时间自然日（YYYY-MM-DD），含首尾；
    # is_active = 0 表示停用，记录保留作历史。
    conn.execute(
        """
        CREATE TABLE daily_notices (
            id INTEGER PRIMARY KEY,
            text TEXT NOT NULL,
            link TEXT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_daily_notices_range ON daily_notices(start_date, end_date)"
    )


def _apply_review_feel(conn):
    conn.execute("ALTER TABLE mistakes ADD COLUMN suspended_at TEXT")
    conn.execute("ALTER TABLE reviews ADD COLUMN due_before TEXT")
    conn.execute("ALTER TABLE reviews ADD COLUMN last_reviewed_before TEXT")
    conn.execute("ALTER TABLE reviews ADD COLUMN version_after INTEGER")
    conn.execute("ALTER TABLE users ADD COLUMN daily_review_cap INTEGER")


def _apply_manual_payment_claims(conn):
    # 用户付款后登记“我已付款”，站长确认后开通套餐；注销账号时随用户删除。
    conn.execute(
        """
        CREATE TABLE manual_payment_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_id INTEGER NOT NULL REFERENCES plans(id),
            payer_note TEXT NOT NULL,
            contact TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending', 'confirmed', 'rejected')),
            reject_reason TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            decided_by INTEGER REFERENCES users(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX idx_manual_claims_user ON manual_payment_claims(user_id, status)"
    )
    conn.execute(
        "CREATE INDEX idx_manual_claims_status ON manual_payment_claims(status, id)"
    )


def _apply_goals(conn):
    # 目标卡：每个用户最多一个目标（user_id 唯一）；ended_at 非空表示已结束，
    # 行保留作历史，注销账号时随用户删除。
    conn.execute(
        """
        CREATE TABLE goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            goal_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            ended_at TEXT
        )
        """
    )


# 新迁移写成 apply(conn) 函数，追加递增且不重复的版本号；不要修改已发布的
# SCHEMA、基线或旧迁移，也不要在迁移函数里 commit、rollback 或 executescript。
MIGRATIONS = [
    (1, "历史数据库基线", _apply_baseline),
    (2, "AI 调用记账", _apply_ai_calls),
    (3, "复习日志补充列", _apply_review_log_columns),
    (4, "账号安全与隐私", _apply_accounts_and_privacy),
    (5, "论坛：采纳、有用、AI 要点", _apply_forum_accept_votes_summaries),
    (6, "手动收款与兑换码", _apply_manual_payment_and_redeem_codes),
    # 历史版本未发布 7；复习手感追加为 10，保留已发布迁移编号。
    (8, "论坛：分区", _apply_forum_zones),
    (9, "榜单：公开参与设置、今日一条", _apply_rank_board),
    (10, "复习手感：暂停、撤销日志、每日上限", _apply_review_feel),
    (11, "手动收款登记", _apply_manual_payment_claims),
    (12, "目标卡", _apply_goals),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _check_schema_version(version):
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {version} 比当前程序支持的 {SCHEMA_VERSION} 更新，"
            "请先升级程序再启动（不要用旧版程序打开新版数据库）"
        )


def _baseline_needs_repair(conn):
    # 保留旧 init_db 对缺表、缺列和缺索引的自修复，完整库不会重跑基线。
    expected = {
        "idx_post_comments_reply_to", "idx_users_email", "idx_users_plan"
    }
    for line in SCHEMA.splitlines():
        for prefix in (
            "CREATE TABLE IF NOT EXISTS ",
            "CREATE INDEX IF NOT EXISTS ",
            "CREATE UNIQUE INDEX IF NOT EXISTS ",
        ):
            if line.startswith(prefix):
                expected.add(line[len(prefix):].split()[0])
    existing = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
        )
    }
    if not expected <= existing:
        return True

    for table, migrations in (
        ("users", USER_COLUMN_MIGRATIONS),
        ("orders", ORDER_COLUMN_MIGRATIONS),
        ("problems", PROBLEM_COLUMN_MIGRATIONS),
        ("variants", VARIANT_COLUMN_MIGRATIONS),
        ("plans", PLAN_COLUMN_MIGRATIONS),
        ("post_comments", POST_COMMENT_COLUMN_MIGRATIONS),
    ):
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if any(column not in columns for column, _statement in migrations):
            return True
    return False


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        current = schema_version(conn)
        _check_schema_version(current)

        if current >= 1 and _baseline_needs_repair(conn):
            conn.execute("BEGIN IMMEDIATE")
            try:
                _check_schema_version(schema_version(conn))
                _apply_baseline(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

        for version, _name, apply in MIGRATIONS:
            if version <= current:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 多个进程同时启动时，取得写锁后重新确认迁移是否已完成。
                current = schema_version(conn)
                _check_schema_version(current)
                if version > current:
                    apply(conn)
                    conn.execute(f"PRAGMA user_version = {version}")
                    current = version
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
