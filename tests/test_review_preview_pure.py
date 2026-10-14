from datetime import date, datetime, timedelta, timezone

import pytest

import scheduler


DAY = date(2026, 10, 4)


@pytest.mark.parametrize(
    "state,last_days",
    [(None, None), ((3.2, 5.3), 3), ((12.0, 5.0), 12), ((12.0, 5.0), 400), ((40.0, 9.0), 40)],
    ids=["new", "second", "on-time", "very-overdue", "hard-card"],
)
def test_preview_all_matches_each_schedule_result(state, last_days):
    last = None if last_days is None else DAY - timedelta(days=last_days)
    previews = scheduler.preview_all(state, last, 2, DAY)
    assert list(previews) == ["0", "2", "3", "4", "5"]
    assert previews == {
        str(quality): scheduler.schedule(state, last, 2, quality, DAY)
        for quality in (0, 2, 3, 4, 5)
    }
    assert scheduler.preview_all(state, last, 2, DAY) == previews


def test_preview_all_is_pure():
    state = (12.0, 5.0)
    scheduler.preview_all(state, DAY - timedelta(days=12), 2, DAY)
    assert state == (12.0, 5.0)


@pytest.mark.parametrize(
    "timezone_name,expected_due",
    [("Asia/Taipei", "2026-10-06"), ("America/Los_Angeles", "2026-10-05")],
    ids=["taipei-next-day", "los-angeles-same-day"],
)
def test_preview_all_uses_the_callers_local_review_date(timezone_name, expected_due):
    now = datetime(2026, 10, 4, 17, tzinfo=timezone.utc)
    local_day = scheduler.today_in_timezone(timezone_name, now)
    previews = scheduler.preview_all(None, None, 0, local_day)
    # 第一次复习：重来和困难都是 1 天。
    assert {previews[q]["due_date"] for q in ("0", "2", "3")} == {expected_due}
