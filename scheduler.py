from datetime import date, datetime, timedelta, timezone
from math import ceil
from zoneinfo import ZoneInfo


def today_in_timezone(timezone_name: str, now: datetime | None = None) -> date:
    current = now if now is not None else datetime.now(timezone.utc)
    return current.astimezone(ZoneInfo(timezone_name)).date()


def schedule(
    repetitions: int,
    interval_days: int,
    ease_factor: float,
    quality: int,
    reviewed_on: date,
) -> dict:
    """
    简化 SM-2。

    quality:
      0-2: 回答错误
      3: 很困难，但答对
      4: 记得
      5: 熟练

    前端提供 0、3、4、5 四个按钮。
    下一次日期始终从实际复习当天计算。
    """
    if type(quality) is not int or not 0 <= quality <= 5:
        raise ValueError("quality 必须是 0 到 5 的整数")

    if quality < 3:
        next_repetitions = 0
        next_interval = 1
    else:
        if repetitions == 0:
            next_interval = 1
        elif repetitions == 1:
            next_interval = 6
        else:
            # 使用本次评分前的易度系数计算间隔。
            next_interval = max(1, ceil(interval_days * ease_factor))
        next_repetitions = repetitions + 1

    next_ease = max(
        1.3,
        ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02),
    )

    return {
        "repetitions": next_repetitions,
        "interval_days": next_interval,
        "ease_factor": round(next_ease, 2),
        "due_date": (reviewed_on + timedelta(days=next_interval)).isoformat(),
    }
