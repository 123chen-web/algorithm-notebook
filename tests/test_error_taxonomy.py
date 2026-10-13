"""错因固定词表 + 判题类型 + 最小反例：迁移 80、同义词映射、接口校验、导出/注销/扩展导入。"""

import pytest

import db
import error_taxonomy
from error_taxonomy import CAUSES, SYNONYMS, map_legacy_tag
from test_app import client, new_problem, register


def test_taxonomy_has_at_most_14_causes_and_4_groups():
    assert len(CAUSES) <= 14
    groups = {group for _, _, group, _ in CAUSES}
    assert groups == {"reasoning", "implementation", "performance", "environment"}
    assert len({code for code, _, _, _ in CAUSES}) == len(CAUSES)


def test_overflow_is_implementation_not_performance():
    # 决策：溢出是选错数据类型导致的答案错误（WA），不是变慢。
    assert error_taxonomy.cause_group("overflow") == "implementation"


def test_synonyms_cover_all_builtin_suggestion_writings():
    from tags import REASON_SUGGEST_TAGS, SUGGESTED_TAGS

    for writing in list(SUGGESTED_TAGS) + list(REASON_SUGGEST_TAGS):
        code = map_legacy_tag(writing)
        assert code in {c for c, _, _, _ in CAUSES}, writing


def test_map_legacy_tag_examples():
    assert map_legacy_tag("思路错") == "wrong_idea"
    assert map_legacy_tag("没思路") == "wrong_idea"
    assert map_legacy_tag("漏条件") == "misread"
    assert map_legacy_tag("没读清题") == "misread"
    assert map_legacy_tag("公式记错") == "formula"
    assert map_legacy_tag("语法坑") == "api"
    assert map_legacy_tag("语法或接口") == "api"
    assert map_legacy_tag("测试没覆盖") == "untested"
    assert map_legacy_tag("溢出") == "overflow"
    assert map_legacy_tag("边界没处理") == "boundary"
    # 已经是 code 的原样返回；查不到的归 other。
    assert map_legacy_tag("wrong_idea") == "wrong_idea"
    assert map_legacy_tag("瞎写的标签") == "other"
    assert map_legacy_tag("") == "other"


def test_migration_80_is_idempotent_and_maps_legacy_tags(client):
    register(client)
    mistake_id = new_problem(client)[0]
    with db.connect(write=True) as conn:
        user_id = conn.execute("SELECT id FROM users").fetchone()[0]
        # 先清掉新记录自带的空标签，塞历史自由标签（含重复映射）。
        conn.execute("DELETE FROM mistake_tags WHERE mistake_id = ?", (mistake_id,))
        for tag in ["边界没处理", "思路错", "溢出", "瞎写的标签", "边界", "粗心"]:
            conn.execute(
                "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) VALUES (?,?,?,?)",
                (mistake_id, user_id, tag, "2026-01-01"),
            )
        # 删掉新列，回退版本号，模拟从 77 升级。
        conn.execute("PRAGMA user_version = 77")
    # 列已存在时先删掉列是不可能的（SQLite 不支持 DROP COLUMN 旧版本），
    # 这里直接验证：迁移 80 的列添加是幂等的（已存在则跳过）。
    db.init_db()
    with db.connect() as conn:
        tags = sorted(row[0] for row in conn.execute(
            "SELECT tag FROM mistake_tags WHERE mistake_id = ?", (mistake_id,)))
        # "边界没处理"→boundary、"边界"→boundary 去重；"瞎写的标签"→other。
        assert tags == ["boundary", "careless", "other", "overflow", "wrong_idea"]
        problems_cols = {row[1] for row in conn.execute("PRAGMA table_info(problems)")}
        mistakes_cols = {row[1] for row in conn.execute("PRAGMA table_info(mistakes)")}
        assert "verdict" in problems_cols
        assert "counterexample" in mistakes_cols
        assert db.schema_version(conn) == 81
    db.init_db()  # 再跑一次：幂等
    with db.connect() as conn:
        tags2 = sorted(row[0] for row in conn.execute(
            "SELECT tag FROM mistake_tags WHERE mistake_id = ?", (mistake_id,)))
        assert tags2 == tags
        assert db.schema_version(conn) == 81


def test_put_tags_rejects_non_taxonomy_with_options(client):
    register(client)
    mistake_id = new_problem(client)[0]
    response = client.put(f"/api/mistakes/{mistake_id}/tags", json={"tags": ["瞎写"]})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "固定词表" in detail
    assert "boundary" in detail  # 错误里给出可选项


def test_problem_verdict_accepted_and_validated(client):
    register(client)
    payload = {
        "title": "判题", "zone": "算法", "language": "Python",
        "code": "print(1)", "thinking": "思路",
        "mistakes": ["漏了边界"], "verdict": "WA",
    }
    response = client.post("/api/problems", json=payload)
    assert response.status_code == 201
    problem_id = response.json()["id"]
    mistake_id = response.json()["mistake_ids"][0]
    assert client.get(f"/api/mistakes/{mistake_id}").json()["verdict"] == "WA"
    # 非法判题类型 → 422。
    bad = client.post("/api/problems", json={**payload, "title": "判题2", "verdict": "AC"})
    assert bad.status_code == 422
    # 编辑题目可以改判题类型。
    edit_payload = {key: payload[key] for key in ("title", "zone", "language", "code", "thinking")}
    edit = client.put(f"/api/problems/{problem_id}", json={**edit_payload, "verdict": "TLE"})
    assert edit.status_code == 200
    assert edit.json()["verdict"] == "TLE"


def test_counterexample_create_edit_and_length_limit(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "反例", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "思路",
            "mistakes": [{"description": "边界错了", "counterexample": "n=1 时输出 0，应为 1"}],
        },
    )
    assert response.status_code == 201
    mistake_id = response.json()["mistake_ids"][0]
    assert client.get(f"/api/mistakes/{mistake_id}").json()["counterexample"] == "n=1 时输出 0，应为 1"
    # 旧格式（纯字符串）仍然兼容。
    response2 = client.post(
        "/api/problems",
        json={
            "title": "反例2", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "思路", "mistakes": ["纯字符串"],
        },
    )
    assert response2.status_code == 201
    # 编辑易错点可以改最小反例；超长 → 422。
    version = client.get(f"/api/mistakes/{mistake_id}").json()["version"]
    edit = client.put(
        f"/api/mistakes/{mistake_id}",
        json={"description": "边界错了", "counterexample": "n=2", "version": version},
    )
    assert edit.status_code == 200
    assert edit.json()["counterexample"] == "n=2"
    too_long = client.put(
        f"/api/mistakes/{mistake_id}",
        json={"description": "边界错了", "counterexample": "x" * 1001, "version": version + 1},
    )
    assert too_long.status_code == 422


def test_export_includes_verdict_and_counterexample(client):
    register(client)
    client.post(
        "/api/problems",
        json={
            "title": "导出", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "思路", "verdict": "RE",
            "mistakes": [{"description": "越界", "counterexample": "n=0"}],
        },
    )
    data = client.get("/api/export").json()
    problem = next(item for item in data["problems"] if item["title"] == "导出")
    assert problem["verdict"] == "RE"
    assert problem["mistakes"][0]["counterexample"] == "n=0"


def test_plugin_import_accepts_verdict_and_defaults_empty(client):
    register(client)
    token = client.post("/api/users/api-token").json()["token"]
    headers = {"Authorization": f"Bearer {token}", "X-CSRF-Protection": "1"}
    response = client.post(
        "/api/problems/import-from-extension",
        json={"title": "插件题", "source": "leetcode", "source_url": "https://leetcode.com/problems/two-sum/", "content": "题面", "verdict": "WA"},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["verdict"] == "WA"
    response2 = client.post(
        "/api/problems/import-from-extension",
        json={"title": "插件题2", "source": "leetcode", "source_url": "https://leetcode.com/problems/two-sum/", "content": "题面"},
        headers=headers,
    )
    assert response2.status_code == 201
    assert response2.json()["verdict"] == ""
    bad = client.post(
        "/api/problems/import-from-extension",
        json={"title": "插件题3", "source": "leetcode", "source_url": "https://leetcode.com/problems/two-sum/", "content": "题面", "verdict": "AC"},
        headers=headers,
    )
    assert bad.status_code == 422


def test_error_profile_stats(client):
    register(client)
    first, second = new_problem(client)
    client.put(f"/api/mistakes/{first}/tags", json={"tags": ["wrong_idea", "boundary"]})
    client.put(f"/api/mistakes/{second}/tags", json={"tags": ["complexity"]})
    data = client.get("/api/stats/error-profile").json()
    groups = {group["code"]: group for group in data["groups"]}
    assert groups["reasoning"]["count"] == 1
    assert groups["implementation"]["count"] == 1
    assert groups["performance"]["count"] == 1
    assert data["top_causes"][0]["code"] in {"wrong_idea", "boundary", "complexity"}
    assert len(data["top_causes"]) == 3
    # 判题类型分布：new_problem 没填 verdict，所以为空。
    assert data["verdicts"] == []
    assert data["total_tagged"] == 3


def test_import_wizard_preserves_verdict_and_counterexample(client):
    import json
    register(client)
    payload = {
        "problems": [{
            "title": "导入题", "language": "Python", "code": "pass\n", "thinking": "思路",
            "verdict": "WA",
            "mistakes": [{"description": "越界", "counterexample": "n=0 时崩", "pending_reason": False}],
        }]
    }
    preview = client.post(
        "/api/import/preview", data={"zone": "算法"},
        files={"file": ("export.json", json.dumps(payload), "application/json")},
    )
    assert preview.status_code == 200
    token = preview.json()["preview_id"]
    confirm = client.post("/api/import/confirm", json={"preview_id": token, "indices": [0]})
    assert confirm.status_code == 200
    assert confirm.json()["imported"] == 1
    items = client.get("/api/mistakes", params={"due_only": "false"}).json()["items"]
    assert items[0]["verdict"] == "WA"
    assert items[0]["counterexample"] == "n=0 时崩"


def test_account_deletion_covers_new_fields(client):
    from db import connect
    register(client)
    client.post(
        "/api/problems",
        json={
            "title": "注销", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "思路", "verdict": "WA",
            "mistakes": [{"description": "错了", "counterexample": "n=1"}],
        },
    )
    response = client.post("/api/me/delete-account", json={"password": "a-test-password-123"})
    assert response.status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM problems").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM mistake_tags").fetchone()[0] == 0
