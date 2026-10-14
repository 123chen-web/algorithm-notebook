from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import fsrs

# 想在到期那天还记得的概率，和 FSRS 默认值一致；间隔最长一年，避免一条错题几年不出现。
DESIRED_RETENTION = 0.9
MAX_INTERVAL_DAYS = 365

# 页面上的五个评分按钮（0 完全忘了、2 答错了、3 很吃力、4 记得、5 很熟）合并成 FSRS 的四档。
GRADE_OF_QUALITY = {0: fsrs.AGAIN, 1: fsrs.AGAIN, 2: fsrs.AGAIN, 3: fsrs.HARD, 4: fsrs.GOOD, 5: fsrs.EASY}


def today_in_timezone(timezone_name: str, now: datetime | None = None) -> date:
    current = now if now is not None else datetime.now(timezone.utc)
    return current.astimezone(ZoneInfo(timezone_name)).date()


def card_state(item, timezone_name: str):
    """取出一条易错点当前的记忆状态：((稳定度, 难度) 或 None, 上次复习的本地日期或 None)。

    FSRS 上线前复习过的旧记录没有稳定度和难度：稳定度取当时的间隔，难度由旧的“易度”换算，
    下一次评分时就会写入真实的 FSRS 状态。从没复习过的记录返回 None，按“第一次复习”算。
    """
    stability, difficulty = item["stability"], item["difficulty"]
    last_at = item["last_reviewed_at"]
    reviewed = last_at is not None or item["repetitions"] > 0 or stability is not None
    if not reviewed:
        return None, None
    if last_at is not None:
        last_day = today_in_timezone(timezone_name, datetime.fromisoformat(last_at))
    else:
        last_day = date.fromisoformat(item["due_date"]) - timedelta(days=item["interval_days"])
    if stability is None or difficulty is None:
        stability = float(max(1, item["interval_days"]))
        difficulty = fsrs.difficulty_from_ease(item["ease_factor"])
    return (stability, difficulty), last_day


def _grade_outcomes(state, last_review_on, reviewed_on):
    """四档评分各自的 (稳定度, 难度, 间隔天数)；间隔保证 困难 ≤ 良好 < 简单，按钮上的天数才不会重复。"""
    elapsed = 0 if last_review_on is None else max(0, (reviewed_on - last_review_on).days)
    raw = {}
    for grade in (fsrs.AGAIN, fsrs.HARD, fsrs.GOOD, fsrs.EASY):
        stability, difficulty = fsrs.review(state, grade, elapsed)
        raw[grade] = (stability, difficulty, fsrs.next_interval(stability, DESIRED_RETENTION, MAX_INTERVAL_DAYS))
    hard = min(raw[fsrs.HARD][2], raw[fsrs.GOOD][2])
    good = max(raw[fsrs.GOOD][2], hard + 1)
    easy = max(raw[fsrs.EASY][2], good + 1)
    days = {
        fsrs.AGAIN: raw[fsrs.AGAIN][2],
        fsrs.HARD: min(hard, MAX_INTERVAL_DAYS),
        fsrs.GOOD: min(good, MAX_INTERVAL_DAYS),
        fsrs.EASY: min(easy, MAX_INTERVAL_DAYS),
    }
    return {grade: (raw[grade][0], raw[grade][1], days[grade]) for grade in raw}


def schedule(state, last_review_on, repetitions: int, quality: int, reviewed_on: date) -> dict:
    """
    FSRS-6 排期。

    state / last_review_on 来自 card_state()；repetitions 是页面上“连续成功次数”，
    答错折半（不清零），答对加一，和调度本身无关，只用来显示。
    下一次日期始终从实际复习当天计算。连续失败 8 次的 leech 暂停由复习接口处理。
    """
    if type(quality) is not int or not 0 <= quality <= 5:
        raise ValueError("quality 必须是 0 到 5 的整数")
    grade = GRADE_OF_QUALITY[quality]
    stability, difficulty, interval = _grade_outcomes(state, last_review_on, reviewed_on)[grade]
    return {
        "repetitions": repetitions // 2 if grade == fsrs.AGAIN else repetitions + 1,
        "interval_days": interval,
        "ease_factor": fsrs.ease_from_difficulty(difficulty),
        "stability": round(stability, 4),
        "difficulty": round(difficulty, 4),
        "due_date": (reviewed_on + timedelta(days=interval)).isoformat(),
    }


def preview_all(state, last_review_on, repetitions: int, reviewed_on: date) -> dict:
    """按同一份评分前状态，纯计算五个评分档的下一次调度。"""
    return {
        str(quality): schedule(state, last_review_on, repetitions, quality, reviewed_on)
        for quality in (0, 2, 3, 4, 5)
    }
