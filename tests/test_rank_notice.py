"""今日一条：管理员权限、CSRF、校验（文字 / 链接协议 / 日期范围）、有效期选择和迁移。"""
import sqlite3
from contextlib import closing
from datetime import date

import pytest

import db
import rank_board
from rank_helpers import NOW
from test_app import client, register  # noqa: F401

TODAY = "2026-10-04"  # NOW 的北京日期


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(rank_board, "now_utc", lambda: NOW)


def payload(**changes):
    body = {"text": "今天也记得复习一遍错题。", "link": None, "start_date": TODAY,
            "end_date": TODAY, "is_active": True}
    body.update(changes)
    return body


def make_admin(client, username="alice"):
    user = register(client, username)
    with db.connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
    return user


def create(client, **changes):
    response = client.post("/api/admin/daily-notices", json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def shown(client):
    response = client.get("/api/rank/notice")
    assert response.status_code == 200
    return response.json()["notice"]


# ---------------- 权限 ----------------
def test_admin_endpoints_reject_anonymous_and_non_admin(client):
    assert client.get("/api/admin/daily-notices").status_code == 401
    assert client.post("/api/admin/daily-notices", json=payload()).status_code == 401
    assert client.put("/api/admin/daily-notices/1", json=payload()).status_code == 401
    register(client)  # 普通用户
    assert client.get("/api/admin/daily-notices").status_code == 403
    assert client.post("/api/admin/daily-notices", json=payload()).status_code == 403
    assert client.put("/api/admin/daily-notices/1", json=payload()).status_code == 403
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_notices").fetchone()[0] == 0


def test_writes_require_the_csrf_header(client):
    make_admin(client)
    for method, url in (("post", "/api/admin/daily-notices"), ("put", "/api/admin/daily-notices/1")):
        response = getattr(client, method)(url, json=payload(), headers={"X-CSRF-Protection": "0"})
        assert response.status_code == 403


def test_a_demoted_admin_cannot_write_with_a_stale_session(client):
    user = make_admin(client)
    create(client)
    with db.connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (user["id"],))
    assert client.post("/api/admin/daily-notices", json=payload()).status_code == 403


# ---------------- 校验 ----------------
@pytest.mark.parametrize("changes", [
    {"text": ""}, {"text": "   "}, {"text": "字" * 81}, {"text": "两行\n文字"}, {"text": "控制\u0007字符"},
    {"text": "反转‮文字"},
    {"link": "javascript:alert(1)"}, {"link": "ftp://example.com/x"}, {"link": "//example.com/x"},
    {"link": "data:text/html,hi"}, {"link": "https://user:pw@example.com/"},
    {"link": "https://user@example.com/"}, {"link": "https://example.com:bad/"},
    {"link": "https://exa mple.com/"}, {"link": "https://example.com/" + "a" * 500},
    {"link": "https:///nohost"}, {"link": "https://example.com\\evil"},
    {"start_date": "2026-10-05", "end_date": "2026-10-04"},
    {"start_date": "2026-01-01", "end_date": "2027-02-01"},
    {"start_date": "not-a-date"}, {"is_active": "yes"}, {"extra": 1},
], ids=lambda changes: next(iter(changes)) + ":" + str(next(iter(changes.values())))[:12].replace("\n", " "))
def test_invalid_notices_are_rejected(client, changes):
    make_admin(client)
    response = client.post("/api/admin/daily-notices", json=payload(**changes))
    assert response.status_code == 422
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM daily_notices").fetchone()[0] == 0


def test_text_limit_is_80_characters_inclusive_and_links_are_kept_as_typed(client):
    make_admin(client)
    create(client, text="字" * 80, link="https://example.com/a?b=1#c")
    item = client.get("/api/admin/daily-notices").json()["notices"][0]
    assert len(item["text"]) == 80 and item["link"] == "https://example.com/a?b=1#c"
    create(client, link="  http://example.org/  ")
    assert client.get("/api/admin/daily-notices").json()["notices"][0]["link"] == "http://example.org/"
    create(client, link="")
    assert client.get("/api/admin/daily-notices").json()["notices"][0]["link"] is None


# ---------------- 展示、编辑、停用、历史 ----------------
def test_users_see_only_the_notice_valid_today(client):
    make_admin(client)
    assert shown(client) is None
    create(client, text="昨天的", start_date="2026-10-01", end_date="2026-10-03")
    create(client, text="明天的", start_date="2026-10-05", end_date="2026-10-06")
    assert shown(client) is None
    create(client, text="今天的", link="https://example.com/news", start_date="2026-10-04", end_date="2026-10-04")
    assert shown(client) == {"text": "今天的", "link": "https://example.com/news"}
    assert set(shown(client)) == {"text", "link"}


def test_range_is_inclusive_of_both_ends(client, monkeypatch):
    make_admin(client)
    create(client, text="三天", start_date="2026-10-03", end_date="2026-10-05")
    for day in (3, 4, 5):
        monkeypatch.setattr(rank_board, "now_utc", lambda day=day: NOW.replace(day=day))
        assert shown(client)["text"] == "三天"
    for day in (2, 6):
        monkeypatch.setattr(rank_board, "now_utc", lambda day=day: NOW.replace(day=day))
        assert shown(client) is None


def test_day_boundary_is_beijing_time_not_utc(client, monkeypatch):
    make_admin(client)
    create(client, text="十月四号", start_date="2026-10-04", end_date="2026-10-04")
    from datetime import datetime, timezone
    # UTC 10-03 16:00 = 北京 10-04 00:00
    monkeypatch.setattr(rank_board, "now_utc", lambda: datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc))
    assert shown(client)["text"] == "十月四号"
    monkeypatch.setattr(rank_board, "now_utc", lambda: datetime(2026, 10, 3, 15, 59, tzinfo=timezone.utc))
    assert shown(client) is None


def test_edit_disable_enable_and_history(client):
    make_admin(client)
    first = create(client, text="第一条")
    second = create(client, text="第二条")
    assert shown(client)["text"] == "第二条", "重叠时取最新建的"
    assert client.put(f"/api/admin/daily-notices/{second}", json=payload(text="第二条（停用）", is_active=False)).status_code == 200
    assert shown(client)["text"] == "第一条"
    history = client.get("/api/admin/daily-notices").json()["notices"]
    assert [(item["id"], item["status"]) for item in history] == [(second, "disabled"), (first, "active")]
    assert history[0]["text"] == "第二条（停用）"
    client.put(f"/api/admin/daily-notices/{second}", json=payload(text="第二条", is_active=True))
    assert shown(client)["text"] == "第二条"
    assert client.put("/api/admin/daily-notices/999", json=payload()).status_code == 404
    old = create(client, text="过期", start_date="2026-09-01", end_date="2026-09-02")
    later = create(client, text="未来", start_date="2026-11-01", end_date="2026-11-02")
    status = {item["id"]: item["status"] for item in client.get("/api/admin/daily-notices").json()["notices"]}
    assert (status[old], status[later]) == ("expired", "scheduled")


def test_notice_endpoint_requires_login(client):
    assert client.get("/api/rank/notice").status_code == 401


# ---------------- 迁移 ----------------
def test_migration_9_adds_the_column_index_and_table_to_an_old_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    original = list(db.MIGRATIONS)
    # 先只迁移到 8，造一个“升级前”的库，再用真实迁移列表升级到最新。
    monkeypatch.setattr(db, "MIGRATIONS", [item for item in original if item[0] <= 8])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 8)
    db.init_db()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES ('旧用户', 'x', 'Asia/Shanghai', '2026-01-01T00:00:00+00:00')"
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    monkeypatch.setattr(db, "MIGRATIONS", original)
    monkeypatch.setattr(db, "SCHEMA_VERSION", original[-1][0])
    db.init_db()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == original[-1][0]
        assert conn.execute("SELECT public_rank_opt_out FROM users").fetchone()[0] == 0, "旧用户默认参与"
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(reviews)")}
        assert "idx_reviews_reviewed_at" in indexes
        columns = [row[1] for row in conn.execute("PRAGMA table_info(daily_notices)")]
        assert columns == ["id", "text", "link", "start_date", "end_date", "is_active",
                           "created_by", "created_at", "updated_at"]
    db.init_db()  # 再次启动不会重复迁移


def test_migration_9_rolls_back_completely_when_a_later_step_fails(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    original = list(db.MIGRATIONS)
    monkeypatch.setattr(db, "MIGRATIONS", [item for item in original if item[0] <= 8])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 8)
    db.init_db()
    real = next(apply for version, _name, apply in original if version == 9)

    def broken(conn):
        real(conn)
        raise ValueError("第九号迁移最后一步失败")

    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, (9, "坏迁移", broken)])
    monkeypatch.setattr(db, "SCHEMA_VERSION", 9)
    with pytest.raises(ValueError, match="最后一步失败"):
        db.init_db()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        assert "public_rank_opt_out" not in {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'daily_notices'").fetchone() is None
