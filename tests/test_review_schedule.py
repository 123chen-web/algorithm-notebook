from datetime import date, timedelta

import pytest

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


@pytest.mark.parametrize(
    "overdue_days,quality,expected_interval,expected_ease",
    [
        (10, 5, 40, 2.6),
        (3, 5, 23, 2.6),
        (24, 5, 45, 2.6),
        (24, 4, 30, 2.5),
        (24, 3, 23, 2.36),
    ],
)
def test_overdue_review_credits_and_logs_prior_state(
    client, overdue_days, quality, expected_interval, expected_ease,
):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id, overdue_days=overdue_days)

    response = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": quality, "version": 0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "repetitions": 3,
        "interval_days": expected_interval,
        "ease_factor": expected_ease,
        "due_date": (TODAY + timedelta(days=expected_interval)).isoformat(),
        "version": 1,
    }
    rows = review_rows(mistake_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["quality"] == quality
    assert row["elapsed_days"] == 6 + overdue_days
    assert row["scheduled_days"] == 6
    assert row["ease_before"] == 2.5
    assert row["repetitions_before"] == 2
    assert row["next_due_date"] == response.json()["due_date"]
    assert row["reviewed_at"]


@pytest.mark.parametrize(
    "repetitions,interval_days,quality,expected_interval,expected_ease",
    [
        (0, 0, 4, 1, 2.5),
        (1, 1, 4, 6, 2.5),
        (2, 6, 5, 15, 2.6),
    ],
)
def test_on_time_review_keeps_intervals_and_logs_prior_state(
    client, repetitions, interval_days, quality, expected_interval,
    expected_ease,
):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(
        mistake_id, repetitions=repetitions, interval_days=interval_days,
    )

    response = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": quality, "version": 0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "repetitions": repetitions + 1,
        "interval_days": expected_interval,
        "ease_factor": expected_ease,
        "due_date": (TODAY + timedelta(days=expected_interval)).isoformat(),
        "version": 1,
    }
    rows = review_rows(mistake_id)
    assert len(rows) == 1
    assert rows[0]["elapsed_days"] == interval_days
    assert rows[0]["scheduled_days"] == interval_days
    assert rows[0]["ease_before"] == 2.5
    assert rows[0]["repetitions_before"] == repetitions


@pytest.mark.parametrize("quality", [0, 1, 2])
def test_failed_overdue_review_only_reduces_ease_by_point_two(client, quality):
    register(client)
    mistake_id = new_problem(client)[0]
    set_review_state(mistake_id, overdue_days=10)

    response = client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": quality, "version": 0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "repetitions": 1,  # 答错折半：2 // 2（原为清零）
        "interval_days": 1,
        "ease_factor": 2.3,
        "due_date": "2026-09-20",
        "version": 1,
    }
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
