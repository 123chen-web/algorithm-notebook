"""AI 有用/没用反馈接口测试。"""
import ai
import main
from db import connect
from test_app import client, register, new_problem, mock_generated_practice  # noqa: F401


def _make_variant(client, monkeypatch):
    """注册用户、建题、生成变体题，返回 variant id。"""
    register(client)
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    response = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert response.status_code == 201
    return response.json()["variants"][0]["id"]


def _feedback_rows(user_id):
    with connect() as conn:
        return conn.execute(
            "SELECT kind, ref_id, useful FROM ai_feedback WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()


def test_feedback_vote_and_change_vote(client, monkeypatch):
    variant_id = _make_variant(client, monkeypatch)
    user_id = 1

    response = client.post(
        "/api/ai-feedback",
        json={"kind": "variant", "ref_id": variant_id, "useful": True},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    # 再次点击就是改票：只改 useful，不新增行。
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "variant", "ref_id": variant_id, "useful": False},
    )
    assert response.status_code == 200
    rows = _feedback_rows(user_id)
    assert len(rows) == 1
    assert rows[0]["useful"] == 0
    assert rows[0]["ref_id"] == variant_id


def test_duck_feedback_allows_null_ref_id_and_records_each_answer(client):
    register(client)
    user_id = 1

    response = client.post(
        "/api/ai-feedback", json={"kind": "duck", "ref_id": None, "useful": True}
    )
    assert response.status_code == 200
    # 小黄鸭没有可对应的具体对象：每次回答各记一条，统计的是每次回答的好坏，
    # 不是"每人只留最后一票"。
    response = client.post(
        "/api/ai-feedback", json={"kind": "duck", "ref_id": None, "useful": False}
    )
    assert response.status_code == 200
    rows = _feedback_rows(user_id)
    assert [row["useful"] for row in rows] == [1, 0]
    assert all(row["ref_id"] is None for row in rows)


def test_variant_feedback_for_other_users_variant_is_404(client, monkeypatch):
    variant_id = _make_variant(client, monkeypatch)
    # 换号：bob 看不到 alice 的变体题。
    register(client, "bob")
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "variant", "ref_id": variant_id, "useful": True},
    )
    assert response.status_code == 404


def test_variant_feedback_for_missing_variant_is_404(client):
    register(client)
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "variant", "ref_id": 999999, "useful": True},
    )
    assert response.status_code == 404


def test_invalid_kind_is_422(client):
    register(client)
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "spam", "ref_id": None, "useful": True},
    )
    assert response.status_code == 422


def test_variant_feedback_requires_ref_id(client):
    register(client)
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "variant", "ref_id": None, "useful": True},
    )
    assert response.status_code == 422


def test_duck_feedback_rejects_ref_id(client):
    register(client)
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "duck", "ref_id": 1, "useful": True},
    )
    assert response.status_code == 422


def test_feedback_requires_login(client):
    client.cookies.clear()
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "duck", "ref_id": None, "useful": True},
    )
    assert response.status_code == 401


def test_feedback_rate_limit(client, monkeypatch):
    register(client)
    monkeypatch.setattr("routers.review.AI_FEEDBACK_LIMIT", 2)
    for _ in range(2):
        response = client.post(
            "/api/ai-feedback",
            json={"kind": "duck", "ref_id": None, "useful": True},
        )
        assert response.status_code == 200
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "duck", "ref_id": None, "useful": True},
    )
    assert response.status_code == 429


def test_feedback_cleared_on_account_deletion(client):
    register(client)
    user_id = 1
    response = client.post(
        "/api/ai-feedback",
        json={"kind": "duck", "ref_id": None, "useful": True},
    )
    assert response.status_code == 200
    assert len(_feedback_rows(user_id)) == 1

    response = client.post(
        "/api/me/delete-account",
        json={"password": "a-test-password-123"},
    )
    assert response.status_code == 200
    with connect() as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM ai_feedback").fetchone()[0] == 0
        )


def test_migration_74_is_idempotent(client):
    import db

    # 模拟一台停在 73 版的旧库：删掉 74 号表、把版本号拨回 73。
    with db.connect(write=True) as conn:
        conn.execute("DROP TABLE ai_feedback")
        conn.execute("PRAGMA user_version = 73")
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_feedback'"
            ).fetchone()
            is None
        )

    db.init_db()
    db.init_db()  # 重复启动幂等
    with db.connect() as conn:
        assert db.schema_version(conn) == 83
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_feedback'"
            ).fetchone()
            is not None
        )
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(ai_feedback)")}
        assert cols == {"id", "user_id", "kind", "ref_id", "useful", "created_at"}
