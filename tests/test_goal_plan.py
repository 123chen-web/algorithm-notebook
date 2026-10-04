"""goal_plan.plan() 的测试：覆盖所有边界情形，预期值的算法写在注释里。"""

from datetime import date, timedelta

import pytest

from goal_plan import plan

TODAY = date(2026, 10, 4)


def after(days):
    return TODAY + timedelta(days=days)


def test_no_goal():
    # 没有目标：days_left=0，total=5，target=0，0>=0 所以 on_track
    assert plan(TODAY, None, 5, [], 0) == {
        "days_left": 0, "status": "no_goal", "total_workload": 5,
        "daily_target": 0, "remaining_today": 0, "on_track": True,
    }


def test_no_goal_with_done_today_is_still_on_track():
    result = plan(TODAY, None, 5, [], 3)
    assert result["on_track"] is True and result["remaining_today"] == 0


def test_no_goal_with_a_due_list_is_rejected():
    with pytest.raises(ValueError):
        plan(TODAY, None, 5, [2], 0)


def test_passed_goal():
    result = plan(TODAY, after(-3), 7, [], 0)
    assert (result["status"], result["days_left"], result["total_workload"], result["daily_target"]) == ("passed", 0, 7, 0)
    assert result["on_track"] is True


def test_passed_goal_with_a_due_list_is_rejected():
    with pytest.raises(ValueError):
        plan(TODAY, after(-1), 7, [1, 2], 0)


def test_goal_is_today_with_workload():
    # 总量 8 全部压到今天；做了 3 条 → 还差 5；3 < 8 不达标
    result = plan(TODAY, TODAY, 8, [], 3)
    assert (result["status"], result["daily_target"], result["remaining_today"], result["on_track"]) == ("today", 8, 5, False)


def test_goal_is_today_without_workload_is_today_not_empty():
    result = plan(TODAY, TODAY, 0, [], 0)
    assert (result["status"], result["daily_target"], result["on_track"]) == ("today", 0, True)


def test_goal_is_today_and_done_covers_the_target():
    result = plan(TODAY, TODAY, 4, [], 5)
    assert (result["daily_target"], result["remaining_today"], result["on_track"]) == (4, 0, True)


def test_goal_tomorrow_means_everything_today():
    # days_left=1：总量 5+3=8，ceil(8/1)=8
    result = plan(TODAY, after(1), 5, [3], 0)
    assert (result["days_left"], result["status"], result["total_workload"]) == (1, "active", 8)
    assert (result["daily_target"], result["remaining_today"], result["on_track"]) == (8, 8, False)


def test_far_goal_365_days():
    # 总量 365，365 天 → ceil(365/365)=1
    result = plan(TODAY, after(365), 365, [0] * 365, 0)
    assert (result["days_left"], result["status"], result["daily_target"]) == (365, "active", 1)


def test_far_goal_exact_division_does_not_round_up():
    # 730/365 = 2 整除，不应变成 3
    assert plan(TODAY, after(365), 730, [0] * 365, 0)["daily_target"] == 2


def test_nothing_to_review_is_empty():
    result = plan(TODAY, after(10), 0, [0] * 10, 0)
    assert (result["status"], result["total_workload"], result["daily_target"], result["on_track"]) == ("empty", 0, 0, True)


def test_no_pending_but_future_due_is_active():
    # 总量 0+10=10，ceil(10/3)=4
    result = plan(TODAY, after(3), 0, [3, 3, 4], 0)
    assert (result["status"], result["total_workload"], result["daily_target"]) == ("active", 10, 4)


def test_rounds_up():
    # 总量 2+3+3+2=10，10/3=3.33 → 4
    assert plan(TODAY, after(3), 2, [3, 3, 2], 0)["daily_target"] == 4


def test_exact_division():
    # 总量 4+6=10，10/2=5
    assert plan(TODAY, after(2), 4, [3, 3], 0)["daily_target"] == 5


def test_at_least_one_when_active():
    # 总量 3，5 天 → ceil(0.6)=1
    assert plan(TODAY, after(5), 3, [0] * 5, 0)["daily_target"] == 1


def test_target_never_exceeds_total():
    result = plan(TODAY, after(5), 1, [1, 0, 1, 0, 0], 0)
    assert result["total_workload"] == 3 and result["daily_target"] == 1 <= result["total_workload"]


def test_done_today_beyond_the_target():
    # 总量 10，3 天 → 4；做了 6 条 → remaining 钳为 0
    result = plan(TODAY, after(3), 10, [0, 0, 0], 6)
    assert (result["daily_target"], result["remaining_today"], result["on_track"]) == (4, 0, True)


def test_done_today_exactly_meets_the_target():
    result = plan(TODAY, after(3), 10, [0, 0, 0], 4)
    assert (result["remaining_today"], result["on_track"]) == (0, True)


def test_done_today_partial():
    result = plan(TODAY, after(3), 10, [0, 0, 0], 1)
    assert (result["remaining_today"], result["on_track"]) == (3, False)


@pytest.mark.parametrize("pending,done,due", [(-1, 0, [0, 0, 0]), (5, -2, [0, 0, 0]), (5, 0, [0, -1, 0])])
def test_negative_inputs_are_rejected(pending, done, due):
    with pytest.raises(ValueError):
        plan(TODAY, after(3), pending, due, done)


def test_negative_pending_is_rejected_even_without_a_goal():
    with pytest.raises(ValueError):
        plan(TODAY, None, -1, [], 0)


@pytest.mark.parametrize("due", [[1, 1, 1, 1], [1, 1], []])
def test_due_list_of_the_wrong_length_is_rejected(due):
    with pytest.raises(ValueError):
        plan(TODAY, after(3), 5, due, 0)


def test_typical_scenario():
    # 7 天后；积压 20，未来 2*7=14，总量 34；ceil(34/7)=5；做了 2 → 还差 3
    assert plan(TODAY, after(7), 20, [2] * 7, 2) == {
        "days_left": 7, "status": "active", "total_workload": 34,
        "daily_target": 5, "remaining_today": 3, "on_track": False,
    }
