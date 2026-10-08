from datetime import date, timedelta

import pytest

from scheduler import schedule


DAY = date(2026, 1, 1)


@pytest.mark.parametrize("quality, next_ease", [(3, 2.36), (4, 2.5), (5, 2.6)])
@pytest.mark.parametrize(
    "repetitions, interval_days, next_repetitions, next_interval, due_date",
    [
        (0, 0, 1, 1, "2026-01-02"),
        (1, 1, 2, 6, "2026-01-07"),
        (2, 6, 3, 15, "2026-01-16"),
        (5, 20, 6, 50, "2026-02-20"),
    ],
)
def test_on_time_success_matches_previous_schedule(
    quality, next_ease, repetitions, interval_days, next_repetitions, next_interval, due_date
):
    expected = {
        "repetitions": next_repetitions,
        "interval_days": next_interval,
        "ease_factor": next_ease,
        "due_date": due_date,
    }
    state = schedule(repetitions, interval_days, 2.5, quality, DAY)
    assert state == expected
    assert schedule(
        repetitions=repetitions,
        interval_days=interval_days,
        ease_factor=2.5,
        quality=quality,
        reviewed_on=DAY,
        overdue_days=0,
    ) == expected


@pytest.mark.parametrize(
    "overdue_days, quality, next_interval",
    [(24, 5, 45), (24, 4, 30), (24, 3, 23), (3, 5, 23)],
)
def test_overdue_credit_uses_previous_ease_and_rounds_up(overdue_days, quality, next_interval):
    state = schedule(2, 6, 2.5, quality, DAY, overdue_days=overdue_days)
    assert state["repetitions"] == 3
    assert state["interval_days"] == next_interval
    assert state["due_date"] == (DAY + timedelta(days=next_interval)).isoformat()


@pytest.mark.parametrize("quality, next_interval", [(3, 23), (4, 30), (5, 45)])
@pytest.mark.parametrize("overdue_days", [12, 24, 365])
def test_overdue_credit_is_capped_at_twice_the_scheduled_interval(
    quality, next_interval, overdue_days
):
    assert schedule(2, 6, 2.5, quality, DAY, overdue_days=overdue_days)[
        "interval_days"
    ] == next_interval


@pytest.mark.parametrize("quality", [3, 4, 5])
@pytest.mark.parametrize("repetitions, interval_days, next_interval", [(0, 0, 1), (1, 1, 6)])
def test_overdue_does_not_change_first_two_successful_intervals(
    quality, repetitions, interval_days, next_interval
):
    state = schedule(repetitions, interval_days, 2.5, quality, DAY, overdue_days=365)
    assert state == schedule(repetitions, interval_days, 2.5, quality, DAY)
    assert state["interval_days"] == next_interval


@pytest.mark.parametrize("quality", [0, 1, 2])
@pytest.mark.parametrize("ease_before, next_ease", [(2.5, 2.3), (1.4, 1.3), (1.3, 1.3)])
def test_failure_halves_repetitions_and_only_reduces_ease_by_point_two(quality, ease_before, next_ease):
    # 答错不再清零：repetitions 折半（5→2），interval 回 1 天。
    expected = {
        "repetitions": 2,
        "interval_days": 1,
        "ease_factor": next_ease,
        "due_date": "2026-01-02",
    }
    assert schedule(5, 40, ease_before, quality, DAY) == expected
    assert schedule(5, 40, ease_before, quality, DAY, overdue_days=365) == expected


@pytest.mark.parametrize("repetitions, expected", [(5, 2), (4, 2), (3, 1), (2, 1), (1, 0), (0, 0)])
def test_failure_halving_floors_at_zero(repetitions, expected):
    state = schedule(repetitions, 10, 2.5, 0, DAY)
    assert state["repetitions"] == expected
    assert state["interval_days"] == 1


@pytest.mark.parametrize("quality", [3, 4, 5])
@pytest.mark.parametrize("ease_before", [1.3, 1.4, 2.5, 2.613])
def test_success_ease_formula_and_rounding_are_unchanged(quality, ease_before):
    expected_ease = round(
        max(1.3, ease_before + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)),
        2,
    )
    state = schedule(2, 6, ease_before, quality, DAY, overdue_days=10)
    assert state["ease_factor"] == expected_ease


@pytest.mark.parametrize("quality", [3, 4, 5])
def test_more_overdue_days_cannot_shorten_the_interval(quality):
    intervals = [
        schedule(2, 6, 2.5, quality, DAY, overdue_days=overdue_days)["interval_days"]
        for overdue_days in [0, 1, 3, 6, 12, 24, 365]
    ]
    assert intervals == sorted(intervals)
    assert intervals[-1] == intervals[-2]


@pytest.mark.parametrize("quality", [-1, 6, 3.0, True, "4", None])
def test_quality_validation_is_unchanged(quality):
    with pytest.raises(ValueError, match="quality 必须是 0 到 5 的整数"):
        schedule(2, 6, 2.5, quality, DAY, overdue_days=10)


def test_overdue_days_is_keyword_only():
    with pytest.raises(TypeError):
        schedule(2, 6, 2.5, 5, DAY, 3)
