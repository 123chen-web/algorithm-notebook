from datetime import date, datetime, timezone
import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import ai
import mailer
import main
from db import connect
from scheduler import schedule, today_in_timezone


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    monkeypatch.setattr(
        main, "today_for", lambda user: date(2026, 9, 19)
    )
    main.reset_rate_limits()

    # 所有 AI 测试都会替换 ai.generate，不会发起真实 API 请求。
    with TestClient(
        main.app,
        headers={"X-CSRF-Protection": "1"},
    ) as instance:
        yield instance


def register(client, username="alice", email=None):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": email or f"{username}@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 201
    return response.json()


def new_problem(client, zone="算法"):
    response = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "zone": zone,
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "维护闭区间，但对结束条件理解不清楚。",
            "mistakes": [
                "循环结束条件漏掉 left == right。",
                "没有处理空数组。",
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["mistake_ids"]


def mock_generated_practice(item):
    if item["zone"] in main.CODE_ZONES:
        questions = [
            {
                "question": "在有序数组中查找第一个不小于目标值的位置。",
                "answer": "输入：[1, 3, 3, 7]，目标值 3\n输出：1",
            },
            {
                "question": "日志按时间排序，查找首条不早于指定时刻的记录。",
                "answer": "输入：[(8, 'a'), (10, 'b')]，时刻 9\n输出：'b'",
            },
        ]
    else:
        questions = [
            {"question": "求矩阵 A 的全部特征值，保留重数。", "answer": "1, 1, 4"},
            {"question": "求线性变换 T 的全部特征值，保留重数。", "answer": "-2, 0, 3"},
        ]
    return {
        "mistake_summary": "边界条件或重数处理遗漏。",
        "model": "mock-model",
        "questions": questions,
    }


def test_sm2_success_failure_and_floor():
    day = date(2026, 9, 19)

    first = schedule(0, 0, 2.5, 4, day)
    assert first["interval_days"] == 1
    assert first["due_date"] == "2026-09-20"

    second = schedule(1, 1, first["ease_factor"], 4, day)
    assert second["interval_days"] == 6

    third = schedule(2, 6, second["ease_factor"], 4, day)
    assert third["interval_days"] == 15

    failed = schedule(3, 15, 2.5, 0, day)
    assert failed["repetitions"] == 0
    assert failed["interval_days"] == 1
    assert failed["ease_factor"] == 1.7

    floor = schedule(0, 1, 1.3, 0, day)
    assert floor["ease_factor"] == 1.3

    # 浮点间隔向上取整。
    assert schedule(2, 6, 2.6, 5, day)["interval_days"] == 16

    with pytest.raises(ValueError):
        schedule(0, 0, 2.5, 6, day)


def test_timezone_day_boundary():
    now = datetime(2026, 9, 19, 16, 30, tzinfo=timezone.utc)
    assert today_in_timezone("Asia/Taipei", now) == date(2026, 9, 20)
    assert today_in_timezone("America/Los_Angeles", now) == date(2026, 9, 19)


def test_review_is_per_mistake_and_prevents_duplicate(client):
    register(client)
    first, second = new_problem(client)

    # 模拟逾期，验证下一次日期从实际复习日计算。
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET due_date = ? WHERE id = ?",
            ("2026-09-01", first),
        )

    due = client.get("/api/mistakes").json()
    assert len(due["items"]) == 2

    response = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 0},
    )
    assert response.status_code == 200
    assert response.json()["due_date"] == "2026-09-20"

    # 相同版本的重复提交不能再次延长间隔。
    duplicate = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 0},
    )
    assert duplicate.status_code == 409

    # 即使带最新版本，未到期也不能再次评分。
    early = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 1},
    )
    assert early.status_code == 409

    remaining = client.get("/api/mistakes").json()["items"]
    assert [item["id"] for item in remaining] == [second]
    assert remaining[0]["version"] == 0

    detail = client.get(f"/api/mistakes/{first}").json()
    assert len(detail["reviews"]) == 1

    all_items = client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"]
    assert len(all_items) == 2


def test_ai_persists_results_and_limits_attempts(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]

    def failed_generation(item):
        raise HTTPException(502, "模拟 AI 失败")

    monkeypatch.setattr(ai, "generate", failed_generation)
    failed = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert failed.status_code == 502
    assert client.get(
        f"/api/mistakes/{mistake_id}"
    ).json()["variants"] == []

    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    generated = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert generated.status_code == 201
    variants = generated.json()["variants"]
    assert len(variants) == 2
    variant_id = variants[0]["id"]

    saved = client.put(
        f"/api/variants/{variant_id}/result",
        json={
            "result": "solved",
            "answer_code": "def lower_bound(a, x):\n    pass\n",
        },
    )
    assert saved.status_code == 200

    # 失败的一次也计入今日两次额度。
    limited = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert limited.status_code == 429

    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    stored = {variant["id"]: variant for variant in detail["variants"]}
    assert len(stored) == 2
    assert stored[variant_id]["result"] == "solved"
    assert stored[variant_id]["answer_code"] == "def lower_bound(a, x):\n    pass\n"
    assert stored[variants[1]["id"]]["result"] == "unattempted"
    assert all("notes" not in variant for variant in stored.values())
    assert detail["version"] == 0
    assert detail["due_date"] == "2026-09-19"


@pytest.mark.parametrize("zone", main.PROBLEM_ZONES)
def test_generation_stores_two_questions_with_zone_specific_answers(
    client, monkeypatch, zone
):
    user_id = register(client)["id"]
    mistake_id = new_problem(client, zone=zone)[0]
    original = client.get(f"/api/mistakes/{mistake_id}").json()
    expected = mock_generated_practice(original)
    calls = []

    def generate(item):
        calls.append(item)
        return expected

    monkeypatch.setattr(ai, "generate", generate)
    response = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"variants", "mistake_description"}
    assert body["mistake_description"] == original["description"]
    assert len(calls) == 1
    assert calls[0]["zone"] == zone
    assert len(body["variants"]) == 2
    assert len({variant["id"] for variant in body["variants"]}) == 2
    for variant, question in zip(body["variants"], expected["questions"]):
        assert variant["mistake_id"] == mistake_id
        assert variant["model"] == expected["model"]
        assert variant["created_at"]
        assert variant["result"] == "unattempted"
        assert variant["answer"] == ""
        assert "notes" not in variant
        if zone in main.CODE_ZONES:
            assert variant["description"] == (
                f"{question['question']}\n\n【样例】\n{question['answer']}"
            )
            assert variant["expected_answer"] == ""
        else:
            assert variant["description"] == question["question"]
            assert question["answer"] not in variant["description"]
            # 生成阶段不下发真实标准答案，只给一个非空占位，等提交练习结果
            # 之后才通过 save_variant_result 的响应看到真实文字（见下面
            # test_math_zone_auto_judges_result 等用例）。
            assert variant["expected_answer"] == "***"
            with connect() as conn:
                stored = conn.execute(
                    "SELECT expected_answer FROM variants WHERE id = ?",
                    (variant["id"],),
                ).fetchone()["expected_answer"]
            assert stored == question["answer"]

    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    assert detail["variants"] == list(reversed(body["variants"]))
    with connect() as conn:
        assert conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ? AND day = ?",
            (user_id, "2026-09-19"),
        ).fetchone()["attempts"] == 1


@pytest.mark.parametrize(
    "initial_description,concurrent_description,expected_description",
    [
        ("", None, "边界条件或重数处理遗漏。"),
        ("原来的错因", None, "原来的错因"),
        ("", "生成时用户补充的错因", "生成时用户补充的错因"),
        ("原来的错因", "", "边界条件或重数处理遗漏。"),
    ],
)
def test_generation_backfills_current_description_once(
    client, monkeypatch, initial_description, concurrent_description,
    expected_description,
):
    register(client)
    mistake_id = new_problem(client)[0]
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET description = ? WHERE id = ?",
            (initial_description, mistake_id),
        )

    def generate(item):
        assert item["description"] == initial_description
        if concurrent_description is not None:
            updated = client.put(
                f"/api/mistakes/{mistake_id}",
                json={"description": concurrent_description, "version": 0},
            )
            assert updated.status_code == 200
        return mock_generated_practice(item)

    monkeypatch.setattr(ai, "generate", generate)
    response = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert response.status_code == 201
    assert len(response.json()["variants"]) == 2
    assert response.json()["mistake_description"] == expected_description
    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    assert detail["description"] == expected_description
    assert detail["version"] == int(concurrent_description is not None)


def test_generation_rolls_back_both_questions_if_second_insert_fails(client, monkeypatch):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET description = '' WHERE id = ?", (mistake_id,))
        conn.execute(
            """
            CREATE TRIGGER reject_second_variant BEFORE INSERT ON variants
            WHEN (SELECT COUNT(*) FROM variants WHERE mistake_id = NEW.mistake_id) = 1
            BEGIN
                SELECT RAISE(ABORT, 'second variant rejected');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="second variant rejected"):
        client.post(f"/api/mistakes/{mistake_id}/variants")

    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    assert detail["variants"] == []
    assert detail["description"] == ""
    with connect() as conn:
        # 生成确实执行过，失败仍消耗一次额度；两道题和错因回填整体回滚。
        assert conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()["attempts"] == 1


@pytest.mark.parametrize(
    "expected_answer,answer,expected_result",
    [
        ("1, 1, 4", "4, 1, 1", "solved"),
        ("1, 1, 4", " ４，１、１ ", "solved"),
        ("1, 1, 4", "１；４;１", "solved"),
        ("1, 1, 4", "1.0, 4e0, 1", "solved"),
        ("1, 1, 4", "1, 4", "failed"),
        ("1, 1, 4", "1, 1, 5", "failed"),
        ("0, 2.0", "2, -0", "solved"),
        (" X + Y ", "x+\ny", "solved"),
        ("x, y", "y, x", "failed"),
        ("1/2", "１／２", "solved"),
        ("1/2", "0.5", "failed"),
    ],
)
def test_math_variant_grades_normalized_answer_and_ignores_client_result(
    client, monkeypatch, expected_answer, answer, expected_result
):
    register(client)
    mistake_id = new_problem(client, zone="线性代数")[0]

    def generate(item):
        generated = mock_generated_practice(item)
        generated["questions"][0]["answer"] = expected_answer
        return generated

    monkeypatch.setattr(ai, "generate", generate)
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["variants"][0]["id"]
    requested_result = "failed" if expected_result == "solved" else "solved"
    response = client.put(
        f"/api/variants/{variant_id}/result",
        json={"result": requested_result, "answer": answer},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == expected_result
    assert body["expected_answer"] == expected_answer
    assert body["answer"] == answer
    assert body["answer_code"] == ""
    assert body["result_updated_at"]
    assert "notes" not in body
    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    stored = next(variant for variant in detail["variants"] if variant["id"] == variant_id)
    assert stored == body
    assert detail["version"] == 0
    assert detail["due_date"] == "2026-09-19"


@pytest.mark.parametrize("zone", ["算法", "线性代数"], ids=["code", "legacy-math"])
@pytest.mark.parametrize("result", ["unattempted", "solved", "partial", "failed"])
def test_variants_without_expected_answer_keep_manual_results(
    client, monkeypatch, zone, result
):
    register(client)
    mistake_id = new_problem(client, zone=zone)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["variants"][0]["id"]
    with connect(write=True) as conn:
        # 旧题迁移后的 expected_answer 为空；保留当年已经存下来的 notes。
        conn.execute(
            "UPDATE variants SET expected_answer = '', notes = ? WHERE id = ?",
            ("旧练习备注", variant_id),
        )
    response = client.put(
        f"/api/variants/{variant_id}/result",
        json={"result": result, "answer_code": "  pass\n", "answer": "4, 1, 1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == result
    assert body["answer_code"] == "  pass\n"
    assert body["answer"] == "4, 1, 1"
    assert body["expected_answer"] == ""
    assert "notes" not in body
    with connect() as conn:
        stored = conn.execute("SELECT * FROM variants WHERE id = ?", (variant_id,)).fetchone()
        assert stored["notes"] == "旧练习备注"
        assert stored["result"] == result
        assert stored["answer_code"] == "  pass\n"
        assert stored["answer"] == "4, 1, 1"


@pytest.mark.parametrize("answer", [None, "", " \u3000\n"])
def test_math_variant_with_no_answer_keeps_requested_result(client, monkeypatch, answer):
    register(client)
    mistake_id = new_problem(client, zone="高等数学")[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["variants"][0]["id"]
    payload = {"result": "partial"}
    if answer is not None:
        payload["answer"] = answer
    response = client.put(f"/api/variants/{variant_id}/result", json=payload)
    assert response.status_code == 200
    assert response.json()["result"] == "partial"
    assert response.json()["answer"] == (answer or "")
    assert response.json()["expected_answer"] == "1, 1, 4"


@pytest.mark.parametrize(
    "invalid_fields", [{"notes": ""}, {"notes": "新备注"}, {"answer": "1" * 501}]
)
def test_variant_result_rejects_notes_and_overlong_answer(
    client, monkeypatch, invalid_fields
):
    register(client)
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["variants"][0]["id"]
    response = client.put(
        f"/api/variants/{variant_id}/result", json={"result": "solved", **invalid_fields}
    )
    assert response.status_code == 422
    with connect() as conn:
        stored = conn.execute("SELECT * FROM variants WHERE id = ?", (variant_id,)).fetchone()
        assert stored["result"] == "unattempted"
        assert stored["result_updated_at"] is None


@pytest.mark.parametrize("zone", ["算法", "线性代数"])
def test_user_isolation(client, monkeypatch, zone):
    register(client, "alice")
    mistake_id = new_problem(client, zone=zone)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["variants"][0]["id"]

    client.post("/api/auth/logout")
    register(client, "bob")

    assert client.get("/api/mistakes").json()["items"] == []
    assert client.get(f"/api/mistakes/{mistake_id}").status_code == 404

    assert client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 5, "version": 0},
    ).status_code == 404

    assert client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).status_code == 404

    assert client.put(
        f"/api/variants/{variant_id}/result",
        json={"result": "failed", "answer_code": "", "answer": "1, 1, 4"},
    ).status_code == 404


def test_auth_and_write_request_protection(client):
    assert client.get("/api/me").status_code == 401

    register(client)
    response = client.post(
        "/api/problems",
        headers={"X-CSRF-Protection": ""},
        json={},
    )
    assert response.status_code == 403

    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_edit_and_delete_mistake(client):
    register(client)
    first, second = new_problem(client)

    edited = client.put(
        f"/api/mistakes/{first}",
        json={"description": "更准确的易错点描述。", "version": 0},
    )
    assert edited.status_code == 200
    assert edited.json()["description"] == "更准确的易错点描述。"
    assert edited.json()["version"] == 1

    # 版本过期的修改会被拒绝，不会覆盖别处已经生效的更新。
    stale = client.put(
        f"/api/mistakes/{first}",
        json={"description": "过期的修改。", "version": 0},
    )
    assert stale.status_code == 409

    deleted = client.delete(f"/api/mistakes/{second}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert client.delete(f"/api/mistakes/{second}").status_code == 404

    remaining = client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"]
    assert [item["id"] for item in remaining] == [first]


def test_edit_and_delete_problem_cascades(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(ai, "generate", mock_generated_practice)
    client.post(f"/api/mistakes/{mistake_id}/variants")
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]

    edited = client.put(
        f"/api/problems/{problem_id}",
        json={
            "title": "二分查找（修订）",
            "zone": "算法",
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "补充了对空数组的处理。",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "二分查找（修订）"

    refreshed = client.get(f"/api/mistakes/{mistake_id}").json()
    assert refreshed["title"] == "二分查找（修订）"
    assert refreshed["thinking"] == "补充了对空数组的处理。"

    deleted = client.delete(f"/api/problems/{problem_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}

    # 级联删除：这道题下的易错点、复习记录和变体题都跟着消失。
    assert client.get(f"/api/mistakes/{mistake_id}").status_code == 404
    assert client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"] == []
    assert client.delete(f"/api/problems/{problem_id}").status_code == 404


def test_edit_and_delete_respect_ownership(client):
    register(client, "alice")
    mistake_id = new_problem(client)[0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]

    client.post("/api/auth/logout")
    register(client, "bob")

    assert client.put(
        f"/api/mistakes/{mistake_id}",
        json={"description": "越权修改", "version": 0},
    ).status_code == 404
    assert client.delete(f"/api/mistakes/{mistake_id}").status_code == 404
    assert client.put(
        f"/api/problems/{problem_id}",
        json={
            "title": "越权修改",
            "zone": "算法",
            "language": "Python",
            "code": "pass",
            "thinking": "x",
        },
    ).status_code == 404
    assert client.delete(f"/api/problems/{problem_id}").status_code == 404


def test_zones_endpoint_lists_fixed_zones(client):
    register(client)
    response = client.get("/api/zones")
    assert response.status_code == 200
    assert response.json() == {
        "zones": list(main.PROBLEM_ZONES),
        "code_zones": list(main.CODE_ZONES),
    }


def test_problem_creation_rejects_unknown_zone(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "zone": "生物",
            "language": "Python",
            "code": "pass",
            "thinking": "x",
            "mistakes": ["边界条件"],
        },
    )
    assert response.status_code == 422


def test_problem_creation_requires_zone(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "language": "Python",
            "code": "pass",
            "thinking": "x",
            "mistakes": ["边界条件"],
        },
    )
    assert response.status_code == 422


def test_mistakes_can_be_filtered_by_zone(client):
    register(client)
    # new_problem() 的题目自带两条易错点，两条都属于同一个"算法"题目。
    algo_mistake_ids = set(new_problem(client))
    frontend_response = client.post(
        "/api/problems",
        json={
            "title": "flex 布局",
            "zone": "前端",
            "language": "CSS",
            "code": ".box { display: flex; }",
            "thinking": "以为 justify-content 能控制交叉轴对齐。",
            "mistakes": ["主轴和交叉轴的对齐属性搞混了"],
        },
    )
    assert frontend_response.status_code == 201
    frontend_mistake_id = frontend_response.json()["mistake_ids"][0]

    all_items = client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"]
    assert {item["id"] for item in all_items} == algo_mistake_ids | {frontend_mistake_id}

    algo_only = client.get(
        "/api/mistakes", params={"due_only": "false", "zone": "算法"}
    ).json()["items"]
    assert {item["id"] for item in algo_only} == algo_mistake_ids

    frontend_only = client.get(
        "/api/mistakes", params={"due_only": "false", "zone": "前端"}
    ).json()["items"]
    assert [item["id"] for item in frontend_only] == [frontend_mistake_id]

    assert client.get(
        "/api/mistakes", params={"due_only": "false", "zone": "不存在的分区"}
    ).status_code == 400


def test_problem_zone_can_be_edited(client):
    register(client)
    mistake_id = new_problem(client)[0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]

    edited = client.put(
        f"/api/problems/{problem_id}",
        json={
            "title": "二分查找",
            "zone": "后端",
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "维护闭区间，但对结束条件理解不清楚。",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["zone"] == "后端"
    assert client.get(f"/api/mistakes/{mistake_id}").json()["zone"] == "后端"


def test_math_zone_does_not_require_language(client):
    register(client)
    response = client.post(
        "/api/problems",
        json={
            "title": "特征值计算",
            "zone": "线性代数",
            "language": "",
            "code": "把特征多项式的符号算错了，det(A - λI) 展开时漏了一项。",
            "thinking": "先求特征多项式，再解特征值。",
            "mistakes": ["行列式展开漏项"],
        },
    )
    assert response.status_code == 201
    mistake_id = response.json()["mistake_ids"][0]
    assert client.get(f"/api/mistakes/{mistake_id}").json()["language"] == ""


def test_code_zone_requires_language(client):
    register(client)
    missing_language = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "zone": "算法",
            "language": "",
            "code": "pass",
            "thinking": "x",
            "mistakes": ["边界条件"],
        },
    )
    assert missing_language.status_code == 422


def test_zones_endpoint_marks_math_zones_as_non_code(client):
    register(client)
    data = client.get("/api/zones").json()
    for zone in ("高等数学", "线性代数", "概率统计"):
        assert zone in data["zones"]
        assert zone not in data["code_zones"]


def test_register_is_rate_limited(client):
    for index in range(main.REGISTER_LIMIT):
        client.post(
            "/api/auth/register",
            json={
                "username": f"flooduser{index}",
                "password": "a-test-password-123",
                "invite_code": "wrong-code",
                "email": f"flooduser{index}@example.com",
                "timezone": "Asia/Shanghai",
            },
        )

    # 即使邀请码这次是对的，超过次数限制也会被挡在验证之前。
    blocked = client.post(
        "/api/auth/register",
        json={
            "username": "onemore",
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": "onemore@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert blocked.status_code == 429


def test_login_is_rate_limited(client):
    register(client)
    client.post("/api/auth/logout")

    for _ in range(main.LOGIN_LIMIT):
        client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )

    blocked = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    )
    assert blocked.status_code == 429


def test_forgot_and_reset_password_flow(client, monkeypatch):
    register(client, "alice")
    client.post("/api/auth/logout")

    sent = []
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )

    response = client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "alice@example.com"
    assert "重置" in subject
    token = body.split("reset_token=")[1].split()[0]

    # 密码强度校验和普通注册/登录一样生效。
    weak = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "short"}
    )
    assert weak.status_code == 422

    reset = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "a-new-password-456"},
    )
    assert reset.status_code == 200

    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-new-password-456"},
    ).status_code == 200

    # 用过的重置链接不能再用第二次。
    reuse = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "another-password-789"},
    )
    assert reuse.status_code == 400


def test_forgot_password_does_not_leak_account_existence(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: sent.append(to)
    )

    # 其他请求持有写锁时，未知邮箱仍应能直接返回，不争抢写锁。
    with connect(write=True):
        response = client.post(
            "/api/auth/forgot-password", json={"email": "nobody@example.com"}
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == []


@pytest.mark.parametrize("smtp_fails", [False, True])
def test_reset_email_runs_after_token_commit(client, monkeypatch, smtp_fails):
    register(client, "alice")
    observed = []

    def send_email(to, subject, body):
        token = body.split("reset_token=")[1].split()[0]
        # SMTP 执行期间，另一个连接应能取得写锁并看到已提交的 token。
        with connect(write=True) as conn:
            row = conn.execute(
                "SELECT token_hash FROM password_resets WHERE token_hash = ?",
                (main.token_hash(token),),
            ).fetchone()
            observed.append(row["token_hash"] if row else None)
        if smtp_fails:
            raise RuntimeError("模拟 SMTP 发送失败")

    monkeypatch.setattr(mailer, "send_email", send_email)
    response = client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(observed) == 1
    assert observed[0] is not None
    with connect() as conn:
        rows = conn.execute("SELECT token_hash FROM password_resets").fetchall()
        assert [row["token_hash"] for row in rows] == observed


def test_forgot_password_rechecks_email_before_issuing_token(client, monkeypatch):
    register(client, "alice")
    original_token = main.secrets.token_urlsafe
    sent = []

    def change_email(size):
        # 模拟查询邮箱与写入 token 之间，账号已更换邮箱。
        with connect(write=True) as conn:
            conn.execute(
                "UPDATE users SET email = ? WHERE username = 'alice'",
                ("new@example.com",),
            )
        return original_token(size)

    monkeypatch.setattr(main.secrets, "token_urlsafe", change_email)
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))
    response = client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == []
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM password_resets").fetchone()[0] == 0


@pytest.mark.parametrize("expired", [False, True])
def test_reset_password_rejects_bad_token(client, expired):
    token = "not-a-real-token"
    if expired:
        register(client, "alice")
        with connect(write=True) as conn:
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = 'alice'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO password_resets(token_hash, user_id, expires_at) "
                "VALUES (?, ?, ?)",
                (main.token_hash(token), user_id, int(main.time.time()) - 1),
            )
    with connect(write=True):
        response = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "a-new-password-456"},
        )
    assert response.status_code == 400


@pytest.mark.parametrize("change", ["consumed", "expired"])
def test_reset_rechecks_token_after_hashing(client, monkeypatch, change):
    register(client, "alice")
    token = "test-reset-token"
    with connect(write=True) as conn:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = 'alice'"
        ).fetchone()
        conn.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at) "
            "VALUES (?, ?, ?)",
            (main.token_hash(token), user["id"], int(main.time.time()) + 300),
        )
    original_hash = main.password_hash

    def hash_with_concurrent_change(password):
        # 哈希期间不持有写锁；另一个请求可使刚校验过的 token 失效。
        with connect(write=True) as conn:
            if change == "consumed":
                conn.execute("DELETE FROM password_resets WHERE user_id = ?",
                             (user["id"],))
            else:
                conn.execute(
                    "UPDATE password_resets SET expires_at = ? WHERE user_id = ?",
                    (int(main.time.time()) - 1, user["id"]),
                )
        return original_hash(password)

    monkeypatch.setattr(main, "password_hash", hash_with_concurrent_change)
    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "a-new-password-456"},
    )
    assert response.status_code == 400
    with connect() as conn:
        actual = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        assert actual == user["password_hash"]
    assert client.get("/api/me").status_code == 200


def test_reset_invalidates_existing_sessions(client, monkeypatch):
    register(client, "alice")  # 注册后处于登录状态。

    sent = []
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: sent.append(body)
    )
    client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})
    token = sent[0].split("reset_token=")[1].split()[0]

    assert client.get("/api/me").status_code == 200

    client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "a-new-password-456"},
    )

    # 之前的会话已经失效，得用新密码重新登录。
    assert client.get("/api/me").status_code == 401


def test_register_validates_and_dedupes_email(client):
    bad = client.post(
        "/api/auth/register",
        json={
            "username": "carol",
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": "not-an-email",
            "timezone": "Asia/Shanghai",
        },
    )
    assert bad.status_code == 422

    register(client, "carol", email="shared@example.com")
    client.post("/api/auth/logout")

    duplicate = client.post(
        "/api/auth/register",
        json={
            "username": "dave",
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": "shared@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert duplicate.status_code == 409
    assert "邮箱" in duplicate.json()["detail"]


def test_update_email_for_existing_account(client):
    register(client, "alice", email="old@example.com")

    updated = client.put("/api/me/email", json={"email": "NEW@Example.com"})
    assert updated.status_code == 200
    # 存进去之前会统一转小写，和注册时的处理保持一致。
    assert updated.json()["email"] == "new@example.com"
    assert client.get("/api/me").json()["email"] == "new@example.com"

    invalid = client.put("/api/me/email", json={"email": "not-an-email"})
    assert invalid.status_code == 422


def test_update_email_rejects_duplicate(client):
    register(client, "alice", email="alice@example.com")
    client.post("/api/auth/logout")
    register(client, "bob", email="bob@example.com")

    conflict = client.put("/api/me/email", json={"email": "alice@example.com"})
    assert conflict.status_code == 409


def test_update_username(client):
    register(client, "alice")

    updated = client.put("/api/me/username", json={"username": "alice_renamed"})
    assert updated.status_code == 200
    assert updated.json()["username"] == "alice_renamed"
    assert client.get("/api/me").json()["username"] == "alice_renamed"

    # 字符集不再限制（中文、单字符都允许），只保留非空和长度上限。
    short = client.put("/api/me/username", json={"username": "a"})
    assert short.status_code == 200
    assert short.json()["username"] == "a"

    chinese = client.put("/api/me/username", json={"username": "小明"})
    assert chinese.status_code == 200
    assert chinese.json()["username"] == "小明"

    empty = client.put("/api/me/username", json={"username": "   "})
    assert empty.status_code == 422

    too_long = client.put("/api/me/username", json={"username": "x" * 33})
    assert too_long.status_code == 422


def test_update_username_rejects_duplicate(client):
    register(client, "alice")
    client.post("/api/auth/logout")
    register(client, "bob")

    conflict = client.put("/api/me/username", json={"username": "alice"})
    assert conflict.status_code == 409
    # 冲突被拒绝之后用户名不应该被改动。
    assert client.get("/api/me").json()["username"] == "bob"


def test_renamed_author_shows_new_name_on_existing_forum_content(client):
    from test_forum import create_post

    register(client, "alice")
    post = create_post(client, "帖子标题")
    client.put("/api/me/username", json={"username": "alice_v2"})

    assert client.get("/api/posts").json()["posts"][0]["username"] == "alice_v2"
    assert client.get(f"/api/posts/{post['id']}").json()["username"] == "alice_v2"


def test_trial_account_cannot_change_username(client):
    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    response = client.put("/api/me/username", json={"username": "renamed_trial"})
    assert response.status_code == 403


def test_renaming_away_from_admin_username_loses_admin_immediately(client, monkeypatch):
    # is_admin 是按当前用户名跟 ADMIN_USERNAME 实时比较算出来的，不是存在
    # 数据库里的角色列；改名后不再匹配就应该立刻失去管理员权限。
    monkeypatch.setenv("ADMIN_USERNAME", "alice")
    register(client, "alice")
    assert client.get("/api/me").json()["is_admin"] is True

    client.put("/api/me/username", json={"username": "alice_renamed"})
    assert client.get("/api/me").json()["is_admin"] is False


def test_forgot_password_is_rate_limited(client):
    for _ in range(main.FORGOT_PASSWORD_LIMIT):
        client.post(
            "/api/auth/forgot-password", json={"email": "nobody@example.com"}
        )
    blocked = client.post(
        "/api/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert blocked.status_code == 429


def test_reset_password_is_rate_limited(client):
    for _ in range(main.RESET_PASSWORD_LIMIT):
        client.post(
            "/api/auth/reset-password",
            json={"token": "whatever", "password": "a-test-password-123"},
        )
    blocked = client.post(
        "/api/auth/reset-password",
        json={"token": "whatever", "password": "a-test-password-123"},
    )
    assert blocked.status_code == 429


def test_trial_account_signs_in_without_invite_code(client):
    response = client.post(
        "/api/auth/trial", json={"timezone": "Asia/Shanghai"}
    )
    assert response.status_code == 201
    username = response.json()["username"]
    assert username.startswith("trial_")

    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == username
    assert body["is_trial"] is True
    assert body["email"] is None


def test_trial_account_is_rate_limited(client):
    for _ in range(main.TRIAL_LIMIT):
        client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})

    blocked = client.post(
        "/api/auth/trial", json={"timezone": "Asia/Shanghai"}
    )
    assert blocked.status_code == 429


def test_trial_account_uses_its_own_lower_ai_limit(client, monkeypatch):
    # 正式账号的额度（client fixture 里设成 2）比体验账号（这里设成 1）
    # 更宽松，确认体验账号用的是自己的更低上限，不是全局的 AI_DAILY_LIMIT。
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "1")
    monkeypatch.setattr(ai, "generate", mock_generated_practice)

    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    mistake_id = new_problem(client)[0]

    first = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert first.status_code == 201

    second = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert second.status_code == 429


AI_QUOTA_NOW = datetime(2026, 9, 22, 10, 0, 0, 500000, tzinfo=timezone.utc)


@pytest.fixture
def quota_environment(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return AI_QUOTA_NOW.replace(tzinfo=None)
            return AI_QUOTA_NOW.astimezone(tz)

    monkeypatch.setattr(main, "datetime", FrozenDatetime)
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "1")
    calls = []

    def generate(item):
        calls.append(item["id"])
        return mock_generated_practice(item)

    monkeypatch.setattr(ai, "generate", generate)
    return calls


def set_ai_subscription(user_id, limit, expires_at, *, is_active=1):
    with connect(write=True) as conn:
        cursor = conn.execute(
            "INSERT INTO plans(name,period_days,ai_daily_limit,price_cents,"
            "is_active,created_at) VALUES ('配额测试套餐',30,?,990,?,?)",
            (limit, is_active, AI_QUOTA_NOW.isoformat()),
        )
        conn.execute(
            "UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
            (cursor.lastrowid, expires_at, user_id),
        )


@pytest.mark.parametrize(
    "is_trial,plan_limit,expires_at,is_active,expected_limit",
    [
        pytest.param(False, None, None, 1, 2, id="no-plan"),
        pytest.param(
            False, 4, "2026-09-23T10:00:00+00:00", 1, 4, id="valid-plan"
        ),
        pytest.param(
            False, 1, "2026-09-23T10:00:00+00:00", 1, 1,
            id="plan-below-default",
        ),
        pytest.param(
            False, 4, "2026-09-21T10:00:00+00:00", 1, 2, id="expired-plan"
        ),
        pytest.param(False, 4, None, 1, 2, id="missing-expiry"),
        pytest.param(
            False, 4, "2026-09-22T10:00:00.500000+00:00", 1, 2,
            id="expires-exactly-now",
        ),
        pytest.param(
            False, 4, "2026-09-22T10:00:00.500001+00:00", 1, 4,
            id="expires-one-microsecond-later",
        ),
        pytest.param(
            False, 4, "2026-09-22T10:00:00.499999+00:00", 1, 2,
            id="expired-one-microsecond-ago",
        ),
        pytest.param(
            False, 4, "2026-09-22T18:00:00.499999+08:00", 1, 2,
            id="expiry-offset-compared-in-utc",
        ),
        pytest.param(
            False, 4, "2026-09-23T10:00:00+00:00", 0, 4,
            id="inactive-plan-still-valid",
        ),
        pytest.param(True, None, None, 1, 1, id="trial-without-plan"),
        pytest.param(
            True, 4, "2026-09-23T10:00:00+00:00", 1, 1,
            id="trial-ignores-paid-plan",
        ),
    ],
)
def test_ai_quota_uses_current_subscription(
    client, quota_environment,
    is_trial, plan_limit, expires_at, is_active, expected_limit,
):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    if is_trial:
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET is_trial = 1 WHERE id = ?", (user_id,))
    if plan_limit is not None:
        set_ai_subscription(user_id, plan_limit, expires_at, is_active=is_active)

    assert client.get("/api/me").json()["ai_daily_limit"] == expected_limit
    for _ in range(expected_limit):
        assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 201
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 429
    assert len(quota_environment) == expected_limit

    with connect() as conn:
        row = conn.execute(
            "SELECT day,attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()
        # 额度使用请求时刻的 UTC；计数日期仍使用 client fixture 的用户当地日。
        assert dict(row) == {"day": "2026-09-19", "attempts": expected_limit}
        assert conn.execute(
            "SELECT COUNT(*) FROM variants WHERE mistake_id = ?", (mistake_id,)
        ).fetchone()[0] == 2 * expected_limit
        stored_expiry = conn.execute(
            "SELECT plan_expires_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()["plan_expires_at"]
        assert stored_expiry == expires_at


@pytest.mark.parametrize("is_trial", [False, True], ids=["plan", "trial"])
@pytest.mark.parametrize("existing_usage", [False, True], ids=["first-use", "usage-exists"])
def test_zero_ai_quota_never_allows_generation(
    client, monkeypatch, quota_environment, is_trial, existing_usage,
):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    # 体验额度为 0 时，有付费套餐也不能绕过；正式账号使用套餐的 0 额度。
    set_ai_subscription(
        user_id, 4 if is_trial else 0, "2026-09-23T10:00:00+00:00"
    )
    if is_trial:
        monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "0")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET is_trial = ? WHERE id = ?", (int(is_trial), user_id)
        )
        if existing_usage:
            conn.execute(
                "INSERT INTO ai_usage(user_id,day,attempts) VALUES (?, ?, 0)",
                (user_id, "2026-09-19"),
            )

    assert client.get("/api/me").json()["ai_daily_limit"] == 0
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 429
    assert quota_environment == []
    with connect() as conn:
        row = conn.execute(
            "SELECT attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchone()
        assert (row["attempts"] if row is not None else None) == (
            0 if existing_usage else None
        )


@pytest.mark.parametrize(
    "change,initial_used,expected_limit,additional_allowed",
    [
        pytest.param("upgrade", 2, 4, 2, id="upgrade-keeps-used-attempts"),
        pytest.param("downgrade", 3, 1, 0, id="lower-plan-below-used-attempts"),
        pytest.param("expire", 3, 2, 0, id="expiry-below-used-attempts"),
    ],
)
def test_ai_quota_changes_do_not_reset_same_day_usage(
    client, quota_environment, change, initial_used, expected_limit, additional_allowed,
):
    user_id = register(client)["id"]
    mistake_id = new_problem(client)[0]
    if change != "upgrade":
        set_ai_subscription(user_id, 4, "2026-09-23T10:00:00+00:00")
    for _ in range(initial_used):
        assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 201

    if change == "expire":
        with connect(write=True) as conn:
            conn.execute(
                "UPDATE users SET plan_expires_at = ? WHERE id = ?",
                (AI_QUOTA_NOW.isoformat(), user_id),
            )
    else:
        set_ai_subscription(user_id, expected_limit, "2026-09-23T10:00:00+00:00")

    assert client.get("/api/me").json()["ai_daily_limit"] == expected_limit
    for _ in range(additional_allowed):
        assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 201
    assert client.post(f"/api/mistakes/{mistake_id}/variants").status_code == 429
    expected_used = initial_used + additional_allowed
    assert len(quota_environment) == expected_used
    with connect() as conn:
        rows = conn.execute(
            "SELECT day,attempts FROM ai_usage WHERE user_id = ?", (user_id,)
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {"day": "2026-09-19", "attempts": expected_used}
        ]
