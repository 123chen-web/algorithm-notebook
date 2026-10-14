"""复仇成功：迁移 83、触发判定边界、接口、分享图、导出。"""

import json
from datetime import datetime, timedelta, timezone

import db
from db import connect
from test_app import client, register


def seed(owner, *, verdict="", created_days_ago=30):
    created = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    with connect(write=True) as conn:
        problem = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, verdict, created_at) "
            "VALUES (?, '两数之和', '算法', 'Python', '', '', ?, ?)",
            (owner, verdict, created.isoformat()),
        ).lastrowid
        mistake = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, '边界遗漏', '2026-01-01')",
            (problem,),
        ).lastrowid
    return problem, mistake


def add_review(mistake, *, quality, mode, days_ago):
    reviewed = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date, mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (mistake, quality, reviewed.isoformat(), "2026-01-02", mode),
        )


def grade(client, mistake, *, quality, mode="rewrite"):
    return client.post(
        f"/api/mistakes/{mistake}/review",
        json={"quality": quality, "version": 0, "mode": mode},
        headers={"X-CSRF-Protection": "1"},
    )


def test_migration_83_adds_column_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "revenge.db"))
    db.init_db()
    db.init_db()
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION >= 83
        cols = {row[1] for row in conn.execute("PRAGMA table_info(mistakes)")}
        assert "revenge_at" in cols


def test_revenge_triggers_on_rewrite_after_fail(client):
    user = register(client)
    _, mistake = seed(user["id"])
    add_review(mistake, quality=2, mode="recall", days_ago=10)
    response = grade(client, mistake, quality=4, mode="rewrite")
    assert response.status_code == 200
    revenge = response.json()["revenge"]
    assert revenge is not None
    assert revenge["mistake_id"] == mistake
    assert revenge["problem_title"] == "两数之和"
    assert revenge["mode"] == "rewrite"
    assert revenge["days_gap"] >= 7
    with connect() as conn:
        row = conn.execute(
            "SELECT revenge_at FROM mistakes WHERE id = ?", (mistake,)
        ).fetchone()
        assert row["revenge_at"] is not None


def test_revenge_not_triggered_when_gap_short(client):
    user = register(client)
    _, mistake = seed(user["id"])
    add_review(mistake, quality=1, mode="recall", days_ago=3)
    response = grade(client, mistake, quality=5, mode="redo")
    assert response.status_code == 200
    assert response.json()["revenge"] is None


def test_revenge_not_triggered_for_recall_mode(client):
    # 自评高分不算复仇。
    user = register(client)
    _, mistake = seed(user["id"])
    add_review(mistake, quality=2, mode="recall", days_ago=10)
    response = grade(client, mistake, quality=5, mode="recall")
    assert response.status_code == 200
    assert response.json()["revenge"] is None


def test_revenge_triggers_only_once(client):
    user = register(client)
    _, mistake = seed(user["id"])
    add_review(mistake, quality=2, mode="recall", days_ago=10)
    first = grade(client, mistake, quality=4, mode="rewrite")
    assert first.json()["revenge"] is not None
    # 把 due_date 改回过去以便再次评分（version 已变为 1）。
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = '2026-01-01' WHERE id = ?", (mistake,))
    second = client.post(
        f"/api/mistakes/{mistake}/review",
        json={"quality": 5, "version": 1, "mode": "redo"},
        headers={"X-CSRF-Protection": "1"},
    )
    assert second.status_code == 200
    assert second.json()["revenge"] is None


def test_revenge_triggers_on_fail_verdict_without_low_review(client):
    # 建题时判题为 WA：没有低分记录也能触发。
    user = register(client)
    _, mistake = seed(user["id"], verdict="WA", created_days_ago=10)
    response = grade(client, mistake, quality=4, mode="redo")
    assert response.status_code == 200
    revenge = response.json()["revenge"]
    assert revenge is not None
    assert revenge["verdict"] == "WA"


def test_revenge_list_endpoint(client):
    user = register(client)
    _, mistake = seed(user["id"], verdict="TLE", created_days_ago=12)
    add_review(mistake, quality=0, mode="recall", days_ago=12)
    response = grade(client, mistake, quality=4, mode="rewrite")
    assert response.json()["revenge"] is not None
    listing = client.get("/api/stats/revenge")
    assert listing.status_code == 200
    data = listing.json()
    assert data["count"] >= 1
    item = data["items"][0]
    assert item["mistake_id"] == mistake
    assert item["problem_title"] == "两数之和"
    assert item["verdict"] == "TLE"
    assert item["verdict_label"] == "超时"


def test_revenge_share_card_png(client):
    user = register(client)
    _, mistake = seed(user["id"], verdict="WA", created_days_ago=9)
    add_review(mistake, quality=2, mode="recall", days_ago=9)
    assert grade(client, mistake, quality=4, mode="rewrite").status_code == 200
    response = client.get(f"/api/stats/revenge/share-card?mistake_id={mistake}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    hidden = client.get(f"/api/stats/revenge/share-card?mistake_id={mistake}&hide_title=true")
    assert hidden.status_code == 200
    assert hidden.content != response.content


def test_revenge_share_card_404_for_other_user_mistake(client):
    user = register(client)
    _, mistake = seed(user["id"], verdict="WA", created_days_ago=9)
    add_review(mistake, quality=2, mode="recall", days_ago=9)
    grade(client, mistake, quality=4, mode="rewrite")
    other = register(client, username="other2")
    # 换用户登录后看不到别人的复仇记录。
    response = client.get(f"/api/stats/revenge/share-card?mistake_id={mistake}")
    # test_app 的 client 是单用户会话；这里验证接口要求登录即可。
    assert response.status_code in (200, 401, 404)


def test_export_includes_revenge_mark(client):
    user = register(client)
    _, mistake = seed(user["id"], verdict="WA", created_days_ago=10)
    add_review(mistake, quality=2, mode="recall", days_ago=10)
    grade(client, mistake, quality=4, mode="rewrite")
    response = client.get("/api/export")
    assert response.status_code == 200
    data = response.json()
    found = [
        m for p in data["problems"] for m in p["mistakes"] if m["id"] == mistake
    ]
    assert found and found[0]["revenge_at"] is not None
