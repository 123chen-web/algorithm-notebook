"""目标日期倒排复习计划：计算每天建议复习多少条（纯函数，不读写数据库）。"""

from datetime import date
from math import ceil


def plan(today: date, goal_date, pending_now: int, due_by_day: list, done_today: int) -> dict:
    """根据目标日期倒排，计算今天建议复习的条数。

    today: 今天（用户本地日期）。goal_date: 目标日期，None 表示未设置目标。
    pending_now: 现在已经到期、待复习的条数（不能为负）。
    due_by_day: 长度等于 days_left；第 i 项是今天之后第 i+1 天按现有规则自然到期的条数（不能为负）。
    done_today: 今天已经复习的条数（不能为负）。
    返回 days_left / status / total_workload / daily_target / remaining_today / on_track。
    """
    if pending_now < 0:
        raise ValueError("pending_now 不能为负：当前已到期条数最小为 0")
    if done_today < 0:
        raise ValueError("done_today 不能为负：今天已复习条数最小为 0")
    if any(count < 0 for count in due_by_day):
        raise ValueError("due_by_day 的每一项都不能为负")

    if goal_date is None:
        days_left, status = 0, "no_goal"      # 没有目标：没有时间轴，days_left 约定为 0
    elif goal_date < today:
        days_left, status = 0, "passed"       # 目标日已过：days_left 钳为 0
    elif goal_date == today:
        days_left, status = 0, "today"        # 今天就是目标日：全部压到今天
    else:
        days_left, status = (goal_date - today).days, None  # 稍后按工作量分为 empty / active

    # 无时间轴的几种状态下 days_left 为 0，due_by_day 必须是空列表
    if len(due_by_day) != days_left:
        raise ValueError(f"due_by_day 长度 {len(due_by_day)} 与 days_left {days_left} 不一致")

    total_workload = pending_now + sum(due_by_day)
    if status is None:
        status = "empty" if total_workload == 0 else "active"

    if status == "active":
        # 均摊到剩余天数并向上取整；至少 1，且不超过总量
        daily_target = min(max(ceil(total_workload / days_left), 1), total_workload)
    elif status == "today":
        daily_target = total_workload         # 今天是截止日：剩余工作全做；没有工作则为 0
    else:
        daily_target = 0                      # no_goal / passed / empty

    return {
        "days_left": days_left,
        "status": status,
        "total_workload": total_workload,
        "daily_target": daily_target,
        "remaining_today": max(daily_target - done_today, 0),
        "on_track": done_today >= daily_target,
    }
