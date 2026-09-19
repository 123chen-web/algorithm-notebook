import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    timezone TEXT NOT NULL,
    created_at TEXT NOT NULL
);

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
"""


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
