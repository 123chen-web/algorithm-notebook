from datetime import date, datetime, timedelta

import pytest

import fsrs
import scheduler
from db import connect
from test_app import client, new_problem, register


TODAY = date(2026, 9, 19)


def set_review_state(
    mistake_id, *, repetitions=2, interval_days=6, ease_factor=2.5,
    overdue_days=0, version=0,
):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET repetitions = ?, interval_days = ?, "
            "ease_factor = ?, due_date = ?, version = ? WHERE id = ?",
            (
                repetitions, interval_days, ease_factor,
                (TODAY - timedelta(days=overdue_days)).isoformat(),
                version, mistake_id,
            ),
        )


def review_rows(mistake_id):
    with connect() as conn:
        return [
            dict(row) for row in conn.execute(
                "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id",
                (mistake_id,),
            )
        ]


def stored_mistake(mistake_id):
    with connect() as conn:
        return dict(conn.execute(
            "SELECT * FROM mistakes WHERE id = ?", (mistake_id,),
        ).fetchone())


def expected_state(repetitions, interval_days, overdue_days, quality, ease_factor=2.5):
    """旧记录（还没有稳定度/难度）按“稳定度=间隔、难度由易度换算”进入 FSRS。"""
    due = TODAY - timedelta(days=overdue_days)
    return scheduler.schedule(
        (float(max(1, interval_days)), fsrs.difficulty_from_ease(ease_factor)),
        due - timedelta(days=interval_days), repetitions, quality, TODAY,
    )


@pytest.mark.parametrize("overdue_days", [0, 3, 10, 24])
@pytest.mark.parametrize("quality", [3, 4, 5])
def test_review_uses_fsrs_and_logs_prior_state(client, overdue_days, quality):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id, overdue_days=overdue_days)
    effective = min(quality, 4)  # 只回忆（自评）最高算“记得”
    expected = expected_state(2, 6, overdue_days, effective)

    response = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": quality, "version": 0},
    )

    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ("repetitions", "interval_days", "ease_factor", "due_date")} == {
        k: expected[k] for k in ("repetitions", "interval_days", "ease_factor", "due_date")
    }
    assert set(body) == {"repetitions", "interval_days", "ease_factor", "due_date", "version", "revenge"}
    assert body["version"] == 1
    assert body["due_date"] == (TODAY + timedelta(days=body["interval_days"])).isoformat()
    assert body["repetitions"] == 3
    stored = stored_mistake(mistake_id)
    assert (stored["stability"], stored["difficulty"]) == (expected["stability"], expected["difficulty"])
    rows = review_rows(mistake_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["quality"] == effective
    assert row["elapsed_days"] == 6 + overdue_days
    assert row["scheduled_days"] == 6
    assert row["ease_before"] == 2.5
    assert row["repetitions_before"] == 2
    assert row["stability_before"] is None and row["difficulty_before"] is None
    assert row["next_due_date"] == body["due_date"]
    assert row["reviewed_at"]


def test_longer_overdue_gives_a_longer_interval_for_a_good_answer(client):
    register(client)
    intervals = []
    for overdue_days in (0, 20):
        mistake_id = new_problem(client)[0]
        set_review_state(mistake_id, overdue_days=overdue_days)
        intervals.append(client.post(
            f"/api/mistakes/{mistake_id}/review", json={"quality": 4, "version": 0},
        ).json()["interval_days"])
    assert intervals[0] < intervals[1]


@pytest.mark.parametrize(
    "repetitions,interval_days,quality,expected_interval",
    [(0, 0, 4, 2), (0, 0, 5, 2), (0, 0, 3, 1)],
)
def test_first_review_of_a_new_mistake_uses_fsrs_initial_values(
    client, repetitions, interval_days, quality, expected_interval,
):
    register(client)
    mistake_id = new_problem(client)[0]
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET last_reviewed_at = NULL WHERE id = ?", (mistake_id,))

    response = client.post(
        f"/api/mistakes/{mistake_id}/review", json={"quality": quality, "version": 0},
    )

    assert response.status_code == 200
    assert response.json()["interval_days"] == expected_interval
    assert review_rows(mistake_id)[0]["elapsed_days"] == 0


def test_second_review_continues_from_the_stored_fsrs_state_and_undo_restores_it(client):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id)
    first = client.post(f"/api/mistakes/{mistake_id}/review", json={"quality": 4, "version": 0}).json()
    after_first = stored_mistake(mistake_id)
    assert after_first["stability"] is not None and after_first["difficulty"] is not None

    # 第二次：直接把到期日挪到今天，状态必须接着第一次的稳定度/难度算。
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = ? WHERE id = ?", (TODAY.isoformat(), mistake_id))
    last_day = scheduler.today_in_timezone("Asia/Shanghai", datetime.fromisoformat(after_first["last_reviewed_at"]))
    expected = scheduler.schedule(
        (after_first["stability"], after_first["difficulty"]), last_day, first["repetitions"], 4, TODAY,
    )
    second = client.post(
        f"/api/mistakes/{mistake_id}/review", json={"quality": 4, "version": first["version"]},
    ).json()
    assert stored_mistake(mistake_id)["stability"] == expected["stability"]
    assert review_rows(mistake_id)[-1]["stability_before"] == after_first["stability"]

    undone = client.post(
        f"/api/mistakes/{mistake_id}/review/undo", json={"version": second["version"]},
    )
    assert undone.status_code == 200
    restored = stored_mistake(mistake_id)
    assert (restored["stability"], restored["difficulty"]) == (after_first["stability"], after_first["difficulty"])


@pytest.mark.parametrize("quality", [0, 1, 2])
def test_failed_overdue_review_resets_to_a_short_retry(client, quality):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id, overdue_days=10)
    expected = expected_state(2, 6, 10, quality)

    response = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": quality, "version": 0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["interval_days"] == expected["interval_days"]
    assert body["repetitions"] == 1  # 答错折半：2 // 2
    assert body["interval_days"] <= 3
    assert stored_mistake(mistake_id)["stability"] < 6
    assert body["ease_factor"] < 2.5
    rows = review_rows(mistake_id)
    assert len(rows) == 1
    assert rows[0]["quality"] == quality
    assert rows[0]["elapsed_days"] == 16
    assert rows[0]["scheduled_days"] == 6
    assert rows[0]["ease_before"] == 2.5
    assert rows[0]["repetitions_before"] == 2


@pytest.mark.parametrize(
    "overdue_days,stored_version,submitted_version",
    [(10, 2, 0), (-1, 0, 0)],
)
def test_rejected_review_keeps_state_and_does_not_add_log(
    client, overdue_days, stored_version, submitted_version,
):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(
        mistake_id, overdue_days=overdue_days, version=stored_version,
    )
    before = stored_mistake(mistake_id)

    response = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 5, "version": submitted_version},
    )

    assert response.status_code == 409
    assert stored_mistake(mistake_id) == before
    assert review_rows(mistake_id) == []


def test_duplicate_and_early_review_preserve_existing_log_and_state(client):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id, overdue_days=10)
    accepted = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 5, "version": 0},
    )
    assert accepted.status_code == 200
    before = stored_mistake(mistake_id)
    logs_before = review_rows(mistake_id)
    assert len(logs_before) == 1

    for version in (0, 1):
        rejected = client.post(
            f"/api/mistakes/{mistake_id}/review",
            json={"quality": 5, "version": version},
        )
        assert rejected.status_code == 409
        assert stored_mistake(mistake_id) == before
        assert review_rows(mistake_id) == logs_before
