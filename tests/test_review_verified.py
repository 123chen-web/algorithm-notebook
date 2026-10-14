"""做题验证复习：成绩由做题结果算出；自评封顶；连续自评压间隔。"""
import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

import review_verify
from db import connect
from test_app import client, new_problem, register
from test_review_schedule import TODAY, review_rows, set_review_state, stored_mistake


def evidence(**fields):
    return json.dumps({"auto": True, **fields}, separators=(",", ":"))


def rate(client, mistake_id, quality, mode, text="", version=0):
    body = {"quality": quality, "version": version, "mode": mode}
    if text:
        body["evidence"] = text
    return client.post(f"/api/mistakes/{mistake_id}/review", json=body)


def fresh_mistake(client):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id)
    return mistake_id


# ---- 成绩规则（纯函数）----

GRADING_CASES = json.loads((Path(__file__).parent / "fixtures" / "verified_grading.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GRADING_CASES, ids=lambda case: f"{case['mode']}-{json.dumps(case['evidence'], sort_keys=True)}")
def test_quality_from_evidence(case):
    """前端 redo-flow.js 用同一份表做对照（tests/redo_flow_behaviour.cjs），两边规则不会悄悄分叉。"""
    assert review_verify.quality_from_evidence(case["mode"], case["evidence"]) == case["quality"]


@pytest.mark.parametrize(
    "mode, fields",
    [("redo", {}), ("redo", {"result": "maybe"}), ("rewrite", {}), ("rewrite", {"similarity": True}), ("recall", {"result": "clean"})],
)
def test_quality_from_evidence_rejects_missing_results(mode, fields):
    with pytest.raises(ValueError):
        review_verify.quality_from_evidence(mode, fields)


def test_only_redo_of_the_original_problem_can_earn_the_top_grade():
    fast = {"result": "clean", "seconds": 10, "limit_seconds": 900}
    assert review_verify.quality_from_evidence("redo", fast) == 5
    assert review_verify.quality_from_evidence("similar", fast) == 4
    assert review_verify.quality_from_evidence("rewrite", {"similarity": 1}) == 4


def test_settle_caps_self_rating_and_remembers_the_original_grade():
    quality, text = review_verify.settle("recall", 5, "")
    assert quality == 4
    assert json.loads(text) == {"suggested": 5, "final": 4}
    assert review_verify.settle("recall", 4, "") == (4, "")
    assert review_verify.settle("recall", 0, "") == (0, "")


def test_settle_lets_the_server_override_the_clients_grade():
    quality, text = review_verify.settle("redo", 5, evidence(result="failed", seconds=5, limit_seconds=900))
    assert quality == 0
    assert json.loads(text)["final"] == 0 and json.loads(text)["suggested"] == 0


def test_settle_rejects_auto_grading_for_self_rating():
    with pytest.raises(HTTPException) as info:
        review_verify.settle("recall", 4, evidence(result="clean"))
    assert info.value.status_code == 422


# ---- 接口 ----

def test_redo_result_decides_the_grade_not_the_clients_number(client):
    mistake_id = fresh_mistake(client)
    response = rate(client, mistake_id, 5, "redo", evidence(result="failed", seconds=400, limit_seconds=900))
    assert response.status_code == 200, response.text
    row, = review_rows(mistake_id)
    assert row["quality"] == 0 and row["mode"] == "redo"
    stored = json.loads(row["evidence"])
    assert stored["result"] == "failed" and stored["final"] == 0 and stored["auto"] is True
    assert response.json()["interval_days"] <= 3


def test_fast_clean_redo_earns_the_longest_interval(client):
    fast = fresh_mistake(client)
    slow = new_problem(client)[0]
    set_review_state(slow)
    a = rate(client, fast, 0, "redo", evidence(result="clean", seconds=120, limit_seconds=900)).json()
    b = rate(client, slow, 0, "redo", evidence(result="hinted", seconds=120, limit_seconds=900)).json()
    assert review_rows(fast)[0]["quality"] == 5 and review_rows(slow)[0]["quality"] == 3
    assert a["interval_days"] > b["interval_days"]


def test_similar_problem_is_a_recorded_review_mode(client):
    mistake_id = fresh_mistake(client)
    response = rate(client, mistake_id, 0, "similar", evidence(result="clean", seconds=500, limit_seconds=900, ref="两数之和"))
    assert response.status_code == 200, response.text
    row, = review_rows(mistake_id)
    assert row["mode"] == "similar" and row["quality"] == 4
    assert json.loads(row["evidence"])["ref"] == "两数之和"


def test_rewrite_is_graded_by_similarity(client):
    mistake_id = fresh_mistake(client)
    assert rate(client, mistake_id, 5, "rewrite", evidence(similarity=0.7)).status_code == 200
    assert review_rows(mistake_id)[0]["quality"] == 3


def test_self_rating_cannot_be_very_familiar(client):
    mistake_id = fresh_mistake(client)
    response = rate(client, mistake_id, 5, "recall")
    assert response.status_code == 200
    row, = review_rows(mistake_id)
    assert row["quality"] == 4
    assert json.loads(row["evidence"]) == {"suggested": 5, "final": 4}


@pytest.mark.parametrize(
    "mode, text",
    [
        ("recall", evidence(result="clean")),
        ("redo", evidence()),
        ("redo", evidence(result="clean", bogus=1)),
        ("redo", json.dumps({"auto": False, "result": "clean"})),
        ("redo", evidence(result="maybe")),
        ("rewrite", evidence()),
    ],
)
def test_bad_auto_evidence_is_rejected_without_touching_the_schedule(client, mode, text):
    mistake_id = fresh_mistake(client)
    before = stored_mistake(mistake_id)
    assert rate(client, mistake_id, 4, mode, text).status_code == 422
    assert stored_mistake(mistake_id) == before and review_rows(mistake_id) == []


def seed_long_interval(mistake_id, previous_mode):
    """稳定度 100 天、100 天前复习过，并留下一条上次评分记录。"""
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET stability = 100, difficulty = 3, repetitions = 5, interval_days = 100, "
            "due_date = ?, last_reviewed_at = ?, version = 4 WHERE id = ?",
            (TODAY.isoformat(), (TODAY - timedelta(days=100)).isoformat() + "T04:00:00+00:00", mistake_id),
        )
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date, mode) VALUES (?, 4, ?, ?, ?)",
            (mistake_id, (TODAY - timedelta(days=100)).isoformat() + "T04:00:00+00:00", TODAY.isoformat(), previous_mode),
        )


def test_two_self_ratings_in_a_row_cap_the_interval_at_two_weeks(client):
    register(client)
    mistake_id = new_problem(client)[0]
    seed_long_interval(mistake_id, "recall")
    response = rate(client, mistake_id, 4, "recall", version=4)
    assert response.status_code == 200
    body = response.json()
    assert body["interval_days"] == 14 and body["unverified_cap"] is True
    assert body["due_date"] == (TODAY + timedelta(days=14)).isoformat()
    # 稳定度仍按 FSRS 记下来，之后做一次验证就能把间隔拉回去。
    assert stored_mistake(mistake_id)["stability"] > 100


@pytest.mark.parametrize("previous_mode", ["redo", "rewrite", "similar"])
def test_a_verified_review_in_between_lifts_the_cap(client, previous_mode):
    register(client)
    mistake_id = new_problem(client)[0]
    seed_long_interval(mistake_id, previous_mode)
    body = rate(client, mistake_id, 4, "recall", version=4).json()
    assert body["interval_days"] > 14 and "unverified_cap" not in body


def test_verified_review_is_never_capped(client):
    register(client)
    mistake_id = new_problem(client)[0]
    seed_long_interval(mistake_id, "recall")
    body = rate(client, mistake_id, 0, "redo", evidence(result="clean", seconds=60, limit_seconds=900), version=4).json()
    assert body["interval_days"] > 14 and "unverified_cap" not in body


def test_first_self_rating_is_not_capped(client):
    register(client)
    mistake_id = new_problem(client)[0]
    seed_long_interval(mistake_id, "recall")
    with connect(write=True) as conn:
        conn.execute("DELETE FROM reviews WHERE mistake_id = ?", (mistake_id,))
    body = rate(client, mistake_id, 4, "recall", version=4).json()
    assert body["interval_days"] > 14


def test_preview_matches_the_rules_for_each_mode(client):
    register(client)
    mistake_id = new_problem(client)[0]
    seed_long_interval(mistake_id, "recall")
    recall = client.get(f"/api/mistakes/{mistake_id}/preview").json()["previews"]
    assert recall["5"] == recall["4"]
    assert recall["4"]["interval_days"] == 14
    redo = client.get(f"/api/mistakes/{mistake_id}/preview?mode=redo").json()["previews"]
    assert redo["5"]["interval_days"] > redo["4"]["interval_days"] > 14
    assert client.get(f"/api/mistakes/{mistake_id}/preview?mode=bogus").status_code == 422


def test_non_algorithm_zones_keep_plain_self_rating(client):
    """数学等分区没有做题验证的办法：自评可以给“很熟”，连续自评也不压间隔。"""
    register(client)
    mistake_id = new_problem(client, zone="高等数学")[0]
    seed_long_interval(mistake_id, "recall")
    body = rate(client, mistake_id, 5, "recall", version=4).json()
    assert review_rows(mistake_id)[-1]["quality"] == 5
    assert body["interval_days"] > 14 and "unverified_cap" not in body
    preview = client.get(f"/api/mistakes/{mistake_id}/preview").json()["previews"]
    assert preview["4"]["interval_days"] > 14 and preview["5"]["interval_days"] > 14
