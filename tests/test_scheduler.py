"""FSRS-6 排期：纯计算部分（不碰数据库）。"""
from datetime import date, datetime, timedelta, timezone

import pytest

import fsrs
from scheduler import MAX_INTERVAL_DAYS, card_state, preview_all, schedule


DAY = date(2026, 1, 1)


def fresh(quality, day=DAY):
    """第一次复习（还没有任何记忆状态）。"""
    return schedule(None, None, 0, quality, day)


def reviewed(stability, difficulty, days_ago, quality, repetitions=3):
    return schedule((stability, difficulty), DAY - timedelta(days=days_ago), repetitions, quality, DAY)


# ---- 第一次复习：官方默认参数 ----

@pytest.mark.parametrize(
    "quality, interval, repetitions",
    [(0, 1, 0), (2, 1, 0), (3, 1, 1), (4, 2, 1), (5, 8, 1)],
)
def test_first_review_intervals_follow_fsrs_defaults(quality, interval, repetitions):
    state = fresh(quality)
    assert state["interval_days"] == interval
    assert state["repetitions"] == repetitions
    assert state["due_date"] == (DAY + timedelta(days=interval)).isoformat()


def test_first_review_stores_initial_stability_and_difficulty():
    good = fresh(4)
    assert good["stability"] == pytest.approx(2.3065, abs=1e-3)
    assert good["difficulty"] == pytest.approx(2.1181, abs=1e-3)
    assert fresh(5)["difficulty"] <= good["difficulty"] < fresh(3)["difficulty"] < fresh(0)["difficulty"]


@pytest.mark.parametrize("quality", [0, 2])
def test_zero_and_two_are_both_again(quality):
    assert fresh(quality) == fresh(0)


# ---- 间隔和稳定度的性质 ----

def test_interval_at_ninety_percent_equals_stability():
    for stability in (1, 3.2, 10, 87.5):
        assert abs(fsrs.next_interval(stability, 0.9, 10_000) - stability) <= 0.5 or fsrs.next_interval(stability, 0.9, 10_000) == 1
        assert fsrs.retrievability(stability, stability) == pytest.approx(0.9, abs=1e-9)


@pytest.mark.parametrize("elapsed", [1, 3, 10, 40])
def test_intervals_grow_with_grade_and_buttons_never_tie(elapsed):
    state = (6.0, 5.0)
    result = preview_all(state, DAY - timedelta(days=elapsed), 3, DAY)
    days = [result[q]["interval_days"] for q in ("0", "3", "4", "5")]
    assert days[0] < days[1] < days[2] < days[3] or days[0] == days[1] < days[2] < days[3]
    assert result["0"] == result["2"]


def test_success_raises_stability_and_failure_lowers_it():
    base = (10.0, 5.0)
    assert reviewed(*base, 10, 4)["stability"] > 10
    assert reviewed(*base, 10, 5)["stability"] > reviewed(*base, 10, 4)["stability"]
    assert reviewed(*base, 10, 3)["stability"] < reviewed(*base, 10, 4)["stability"]
    assert reviewed(*base, 10, 0)["stability"] < 10


def test_waiting_longer_before_a_successful_review_helps_more():
    on_time = reviewed(10.0, 5.0, 10, 4)["stability"]
    late = reviewed(10.0, 5.0, 40, 4)["stability"]
    early = reviewed(10.0, 5.0, 2, 4)["stability"]
    assert early < on_time < late


def test_overdue_review_never_shortens_the_next_interval():
    intervals = [reviewed(6.0, 5.0, days, 4)["interval_days"] for days in (6, 7, 9, 14, 30, 90)]
    assert intervals == sorted(intervals)


def test_difficulty_rises_on_failure_and_falls_on_easy_and_stays_in_range():
    assert reviewed(10.0, 5.0, 10, 0)["difficulty"] > 5
    assert reviewed(10.0, 5.0, 10, 5)["difficulty"] < 5
    difficulty = 5.0
    for _ in range(60):
        difficulty = fsrs.next_difficulty(difficulty, fsrs.AGAIN)
        assert 1.0 <= difficulty <= 10.0
    assert difficulty > 9


def test_failure_halves_repetitions_and_gives_a_one_day_retry():
    state = reviewed(30.0, 5.0, 30, 0, repetitions=5)
    assert state["repetitions"] == 2
    assert state["interval_days"] >= 1
    assert state["stability"] < 30


def test_interval_is_capped():
    state = reviewed(900.0, 3.0, 900, 5)
    assert state["interval_days"] == MAX_INTERVAL_DAYS


def test_ease_factor_field_follows_difficulty_and_has_a_floor():
    assert 1.3 <= fresh(4)["ease_factor"] <= 3.5
    assert reviewed(10.0, 10.0, 10, 0)["ease_factor"] == pytest.approx(1.3, abs=0.02)
    assert fsrs.ease_from_difficulty(fsrs.difficulty_from_ease(2.5)) == 2.5


# ---- 旧记录（FSRS 之前留下的状态）----

def item(**changes):
    base = {
        "stability": None, "difficulty": None, "repetitions": 0, "interval_days": 0,
        "ease_factor": 2.5, "due_date": "2026-01-01", "last_reviewed_at": None,
    }
    return {**base, **changes}


def test_never_reviewed_card_is_new():
    assert card_state(item(), "Asia/Shanghai") == (None, None)


def test_old_card_state_is_converted_from_interval_and_ease():
    last = datetime(2025, 12, 20, 2, 0, tzinfo=timezone.utc).isoformat()
    state, last_day = card_state(
        item(repetitions=3, interval_days=12, ease_factor=2.5, last_reviewed_at=last), "Asia/Shanghai"
    )
    assert state == (12.0, pytest.approx(5.0))
    assert last_day == date(2025, 12, 20)


def test_old_card_without_a_review_timestamp_falls_back_to_due_date_minus_interval():
    _, last_day = card_state(item(repetitions=2, interval_days=6, due_date="2026-01-10"), "UTC")
    assert last_day == date(2026, 1, 4)


def test_last_review_day_uses_the_users_timezone():
    last = datetime(2025, 12, 20, 18, 0, tzinfo=timezone.utc).isoformat()
    assert card_state(item(repetitions=1, last_reviewed_at=last), "Asia/Shanghai")[1] == date(2025, 12, 21)
    assert card_state(item(repetitions=1, last_reviewed_at=last), "UTC")[1] == date(2025, 12, 20)


def test_stored_fsrs_state_wins_over_old_fields():
    state, _ = card_state(
        item(repetitions=3, interval_days=99, ease_factor=1.3, stability=7.5, difficulty=4.0,
             last_reviewed_at="2025-12-25T00:00:00+00:00"),
        "UTC",
    )
    assert state == (7.5, 4.0)


# ---- 预览和真正评分必须一致 ----

@pytest.mark.parametrize("state, last_days", [(None, None), ((3.2, 5.3), 3), ((40.0, 7.0), 55)])
def test_preview_all_matches_each_schedule_result(state, last_days):
    last = None if last_days is None else DAY - timedelta(days=last_days)
    previews = preview_all(state, last, 2, DAY)
    assert set(previews) == {"0", "2", "3", "4", "5"}
    for quality, preview in previews.items():
        assert preview == schedule(state, last, 2, int(quality), DAY)


@pytest.mark.parametrize("quality", [-1, 6, 3.0, True, "4", None])
def test_quality_validation(quality):
    with pytest.raises(ValueError, match="quality 必须是 0 到 5 的整数"):
        schedule(None, None, 0, quality, DAY)
