"""复习=重做：迁移 81、mode/evidence 入库、redo-hint、复习方式偏好、总览统计。"""

import json
from datetime import datetime, timedelta, timezone

import pytest

import db
import main
from core import time as core_time
from db import connect
from test_app import client, register


def seed(owner, *, zone="算法", due="2026-09-19"):
    with connect(write=True) as conn:
        problem = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, correct_code, source_url, thinking, created_at) "
            "VALUES (?, '复习测试', ?, 'Python', '', 'def f(): pass', 'https://example.com/1', '', ?)",
            (owner, zone, datetime(2026, 9, 19, 4, tzinfo=timezone.utc).isoformat()),
        ).lastrowid
        mistake = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date, interval_days, "
            "repetitions, ease_factor, version, last_reviewed_at) VALUES (?, '边界遗漏', ?, 6, 2, 2.5, 0, ?)",
            (problem, due, datetime(2026, 9, 10, 4, tzinfo=timezone.utc).isoformat()),
        ).lastrowid
    return mistake


def logs(mistake):
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id", (mistake,))]


def insert_review(mistake, *, mode, quality, reviewed_at):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (mistake, quality, reviewed_at, "2026-09-20", mode),
        )


def test_migration_81_adds_columns_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "redo.db"))
    db.init_db()
    db.init_db()  # 重复启动不报错
    with connect() as conn:
        assert db.schema_version(conn) == 84 == db.SCHEMA_VERSION
        review_cols = {row[1] for row in conn.execute("PRAGMA table_info(reviews)")}
        assert {"mode", "evidence"} <= review_cols
        user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert "redo_preference" in user_cols
        assert versions_sorted_unique()


def versions_sorted_unique():
    versions = [version for version, _name, _apply in db.MIGRATIONS]
    return versions == sorted(set(versions)) and db.SCHEMA_VERSION == versions[-1]


def test_upgrade_from_80_keeps_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "redo-upgrade.db"))
    with monkeypatch.context() as old:
        old.setattr(db, "MIGRATIONS", [entry for entry in db.MIGRATIONS if entry[0] <= 80])
        db.init_db()
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, timezone, created_at) "
            "VALUES ('old-user', 'x', 'Asia/Shanghai', '2026-09-01T00:00:00+00:00')"
        )
        user_id = conn.execute("SELECT id FROM users WHERE username='old-user'").fetchone()[0]
    db.init_db()
    with connect() as conn:
        assert db.schema_version(conn) == 84
        row = conn.execute("SELECT redo_preference FROM users WHERE id = ?", (user_id,)).fetchone()
        assert row["redo_preference"] == "recall_only"


def test_review_rejects_bad_mode(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    response = client.post(f"/api/mistakes/{mistake}/review", json={"quality": 4, "version": 0, "mode": "guess"})
    assert response.status_code == 422


@pytest.mark.parametrize("evidence", [
    "not-json",
    "[1,2]",
    json.dumps({"hacker": 1}),
    json.dumps({"suggested": 9}),
    json.dumps({"similarity": 1.5}),
    json.dumps({"seconds": -1}),
    "x" * 501,
])
def test_review_rejects_bad_evidence(client, evidence):
    owner = register(client)["id"]
    mistake = seed(owner)
    response = client.post(
        f"/api/mistakes/{mistake}/review",
        json={"quality": 4, "version": 0, "mode": "rewrite", "evidence": evidence},
    )
    assert response.status_code == 422, evidence[:40]


def test_review_stores_mode_and_evidence(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    evidence = json.dumps({"suggested": 3, "final": 4, "similarity": 0.82})
    response = client.post(
        f"/api/mistakes/{mistake}/review",
        json={"quality": 4, "version": 0, "mode": "rewrite", "evidence": evidence},
    )
    assert response.status_code == 200, response.text
    (row,) = logs(mistake)
    assert row["mode"] == "rewrite"
    assert row["evidence"] == evidence
    assert row["quality"] == 4


def test_review_without_mode_defaults_to_recall(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    response = client.post(f"/api/mistakes/{mistake}/review", json={"quality": 3, "version": 0})
    assert response.status_code == 200, response.text
    (row,) = logs(mistake)
    assert row["mode"] == "recall"
    assert row["evidence"] == ""


def test_redo_hint_detects_overconfident_recall(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    base = datetime(2026, 10, 5, 4, tzinfo=timezone.utc)
    # recall 打 4 紧接着评 2，出现两次 → 触发提示
    for day, quality in [(5, 4), (6, 2), (7, 4), (8, 2)]:
        insert_review(mistake, mode="recall", quality=quality,
                      reviewed_at=(base + timedelta(days=day - 5)).isoformat())
    response = client.get(f"/api/mistakes/{mistake}/redo-hint")
    assert response.status_code == 200, response.text
    assert response.json()["hint"] is True


def test_redo_hint_quiet_without_pattern(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    base = datetime(2026, 10, 5, 4, tzinfo=timezone.utc)
    for day, quality in [(5, 4), (6, 4), (7, 3)]:
        insert_review(mistake, mode="recall", quality=quality,
                      reviewed_at=(base + timedelta(days=day - 5)).isoformat())
    response = client.get(f"/api/mistakes/{mistake}/redo-hint")
    assert response.json()["hint"] is False


def test_redo_hint_ignores_old_reviews(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    base = datetime(2026, 9, 1, 4, tzinfo=timezone.utc)  # 14 天前之外
    for day, quality in [(1, 4), (2, 2), (3, 4), (4, 2)]:
        insert_review(mistake, mode="recall", quality=quality,
                      reviewed_at=(base + timedelta(days=day - 1)).isoformat())
    response = client.get(f"/api/mistakes/{mistake}/redo-hint")
    assert response.json()["hint"] is False


def test_redo_hint_404_for_other_user(client):
    register(client)  # alice
    alice_cookie = client.cookies.get("session")
    other = register(client, username="other-user")["id"]
    other_mistake = seed(other)
    # 切回 alice 的会话再访问别人的错题
    client.cookies.set("session", alice_cookie)
    response = client.get(f"/api/mistakes/{other_mistake}/redo-hint")
    assert response.status_code == 404


def test_review_settings_preference_roundtrip(client):
    register(client)
    response = client.get("/api/me/review-settings")
    assert response.json()["redo_preference"] == "recall_only"
    response = client.put("/api/me/review-settings", json={"redo_preference": "recall_only"})
    assert response.status_code == 200, response.text
    assert response.json()["redo_preference"] == "recall_only"
    assert client.get("/api/me/review-settings").json()["redo_preference"] == "recall_only"


def test_review_settings_rejects_bad_preference(client):
    register(client)
    response = client.put("/api/me/review-settings", json={"redo_preference": "nope"})
    assert response.status_code == 422


def test_review_settings_cap_update_does_not_clobber_preference(client):
    register(client)
    client.put("/api/me/review-settings", json={"redo_preference": "recall_only"})
    response = client.put("/api/me/review-settings", json={"daily_review_cap": 30})
    assert response.status_code == 200, response.text
    settings = client.get("/api/me/review-settings").json()
    assert settings["redo_preference"] == "recall_only"
    assert settings["daily_review_cap"] == 30


def test_overview_redo_week_present_with_redo_data(client, monkeypatch):
    owner = register(client)["id"]
    mistake = seed(owner)
    now = datetime(2026, 10, 12, 4, tzinfo=timezone.utc)
    monkeypatch.setattr(core_time, "utc_now", lambda: now.isoformat())
    for quality, mode in [(4, "rewrite"), (2, "redo"), (5, "recall"), (3, "recall")]:
        insert_review(mistake, mode=mode, quality=quality, reviewed_at=now.isoformat())
    overview = client.get("/api/overview").json()
    week = overview["redo_week"]
    assert week["redo_count"] == 2
    assert week["pass_rate"] == 0.5
    # 自评平均 (5+3)/2=4，重做平均 (4+2)/2=3 → 高 1 档
    assert week["recall_minus_redo"] == 1.0


def test_overview_redo_week_absent_without_redo_data(client):
    register(client)
    overview = client.get("/api/overview").json()
    assert overview["redo_week"] is None
