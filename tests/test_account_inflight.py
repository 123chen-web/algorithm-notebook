"""注销与在途 AI 请求交错时，个人数据和成本身份不能重新写回。"""
import pytest
from fastapi import HTTPException

import ai
import clusters
import mailer
import main
from ai_limits import track_call
from db import connect
from test_app import client, new_problem, register


@pytest.fixture(autouse=True)
def block_real_services(monkeypatch, tmp_path):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))

    def unexpected_call(*args, **kwargs):
        pytest.fail("在途账号测试不得调用真实 AI 或邮件服务")

    monkeypatch.setattr(ai, "OpenAI", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)
    monkeypatch.setattr(ai, "cluster_mistakes", unexpected_call)
    monkeypatch.setattr(mailer, "send_email", unexpected_call)


def delete_user(user_id):
    with connect(write=True) as conn:
        main.delete_account_data(conn, user_id, main.utc_now())


def assert_deleted_ai_data(user_id, feature):
    with connect() as conn:
        assert conn.execute("SELECT deleted_at FROM users WHERE id = ?", (user_id,)).fetchone()[0]
        for table in ("problems", "ai_usage", "weakness_insights", "mistake_clusters"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)).fetchone()[0] == 0
        calls = conn.execute("SELECT * FROM ai_calls WHERE feature = ?", (feature,)).fetchall()
        assert len(calls) == 1
        assert calls[0]["user_id"] is None
        assert calls[0]["ok"] == 1


def test_ai_cost_after_midcall_deletion_has_no_user_identity(client):
    user_id = register(client)["id"]
    with track_call(user_id, "account-inflight"):
        delete_user(user_id)
    assert_deleted_ai_data(user_id, "account-inflight")


def test_ai_cost_for_live_account_keeps_user_identity(client):
    user_id = register(client)["id"]
    with track_call(user_id, "account-live"):
        pass
    with connect() as conn:
        row = conn.execute("SELECT * FROM ai_calls WHERE feature = 'account-live'").fetchone()
        assert row["user_id"] == user_id
        assert row["ok"] == 1


def test_clusters_result_is_not_saved_after_ai_deletes_account(client, monkeypatch):
    user_id = register(client)["id"]
    for _ in range(3):
        new_problem(client)

    def generate(reference):
        delete_user(user_id)
        return {
            "summary": "这些错因涉及区间边界，可以结合专题复习。",
            "clusters": [{
                "title": "区间边界", "explanation": "端点含义没有明确。",
                "tip": "先写区间不变量。",
                "mistake_ids": [item["mistake_id"] for item in reference["mistakes"][:2]],
            }],
        }

    monkeypatch.setattr(ai, "cluster_mistakes", generate)
    response = client.post("/api/insights/clusters")
    assert response.status_code == 401
    assert response.json()["detail"] == "登录已过期，请重新登录"
    assert_deleted_ai_data(user_id, "clusters")


def test_weakness_result_is_not_saved_after_ai_deletes_account(client, monkeypatch):
    user_id = register(client)["id"]
    for _ in range(3):
        new_problem(client)

    def analyze(reference):
        delete_user(user_id)
        return {
            "summary": "区间语义需要巩固。",
            "patterns": [{
                "title": "区间边界", "explanation": "端点含义没有明确。",
                "action": "先写区间不变量。", "confidence": "较明确",
                "evidence": [{
                    "mistake_id": reference["mistakes"][0]["mistake_id"],
                    "observation": "遗漏端点相等的情况。",
                }],
            }],
        }

    monkeypatch.setattr(ai, "analyze_weaknesses", analyze)
    response = client.post("/api/insights/weakness-analysis")
    assert response.status_code == 401
    assert response.json()["detail"] == "登录已过期，请重新登录"
    assert_deleted_ai_data(user_id, "weakness")


@pytest.mark.parametrize("feature", ["clusters", "weakness"])
def test_ai_first_write_rejects_account_deleted_after_authentication(client, feature):
    user_id = register(client)["id"]
    user = client.get("/api/me").json()
    delete_user(user_id)
    with pytest.raises(HTTPException) as error:
        if feature == "clusters":
            clusters.create_clusters(user, main.today_for, main.ai_quota)
        else:
            main.create_weakness_analysis(user)
    assert error.value.status_code == 401
    assert error.value.detail == "登录已过期，请重新登录"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_calls").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0] == 0
