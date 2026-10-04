from datetime import date, datetime, timezone

import pytest

import scheduler


DAY = date(2026, 10, 4)


@pytest.mark.parametrize(
    "repetitions,interval_days,ease_factor,overdue_days",
    [
        (0, 0, 2.5, 0),
        (1, 1, 2.5, 20),
        (2, 6, 2.5, 0),
        (2, 6, 2.5, 3),
        (2, 6, 2.5, 365),
        (5, 40, 1.3, 365),
    ],
    ids=["new", "second", "on-time", "overdue", "credit-cap", "ease-floor"],
)
def test_preview_all_matches_each_schedule_result(
    repetitions, interval_days, ease_factor, overdue_days,
):
    state = {
        "repetitions": repetitions,
        "interval_days": interval_days,
        "ease_factor": ease_factor,
        "reviewed_on": DAY,
        "overdue_days": overdue_days,
    }
    before = state.copy()
    previews = scheduler.preview_all(**state)
    assert list(previews) == ["0", "2", "3", "4", "5"]
    assert previews == {
        str(quality): scheduler.schedule(quality=quality, **state)
        for quality in (0, 2, 3, 4, 5)
    }
    assert state == before
    assert scheduler.preview_all(**state) == previews


def test_preview_all_retains_overdue_credit_and_failure_point_two_penalty():
    previews = scheduler.preview_all(2, 6, 2.5, DAY, overdue_days=24)
    assert {
        quality: result["interval_days"] for quality, result in previews.items()
    } == {"0": 1, "2": 1, "3": 23, "4": 30, "5": 45}
    for quality in ("0", "2"):
        assert previews[quality] == {
            "repetitions": 0,
            "interval_days": 1,
            "ease_factor": 2.3,
            "due_date": "2026-10-05",
        }
    assert previews["3"]["due_date"] == "2026-10-27"
    assert previews["4"]["due_date"] == "2026-11-03"
    assert previews["5"]["due_date"] == "2026-11-18"


@pytest.mark.parametrize(
    "timezone_name,expected_due",
    [("Asia/Taipei", "2026-10-06"), ("America/Los_Angeles", "2026-10-05")],
    ids=["taipei-next-day", "los-angeles-same-day"],
)
def test_preview_all_uses_the_callers_local_review_date(timezone_name, expected_due):
    now = datetime(2026, 10, 4, 17, tzinfo=timezone.utc)
    local_day = scheduler.today_in_timezone(timezone_name, now)
    previews = scheduler.preview_all(0, 0, 2.5, local_day)
    assert {result["due_date"] for result in previews.values()} == {expected_due}


def test_preview_all_overdue_days_is_keyword_only():
    with pytest.raises(TypeError):
        scheduler.preview_all(2, 6, 2.5, DAY, 3)
