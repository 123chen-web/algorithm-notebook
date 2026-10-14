"""FSRS-6 记忆模型（纯计算，不碰数据库）。

三个量：稳定度 S（记忆衰减到 90% 所需的天数）、难度 D（1–10）、可提取性 R（此刻还记得的概率）。
公式和默认参数核对自 open-spaced-repetition 的 py-fsrs（参考实现）与 awesome-fsrs 的算法说明；
这里使用官方默认参数，没有按用户的复习记录训练，参数训练留到以后有足够数据时再做。
和参考实现不同的地方只有一处：不做“间隔随机打散”，这样同样的记录永远排出同样的日期，方便测试和排查。

评分用 FSRS 的四档：1 重来、2 困难、3 良好、4 简单。
"""
from math import exp

W = (
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796,
    1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542,
)
DECAY = -W[20]
FACTOR = 0.9 ** (1 / DECAY) - 1
S_MIN = 0.001
D_MIN, D_MAX = 1.0, 10.0

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4


def _clamp_d(value: float) -> float:
    return min(D_MAX, max(D_MIN, value))


def retrievability(elapsed_days: float, stability: float) -> float:
    return (1 + FACTOR * max(0.0, elapsed_days) / stability) ** DECAY


def next_interval(stability: float, desired_retention: float, max_days: int) -> int:
    """记忆概率降到 desired_retention 时的天数，取整后至少 1 天。"""
    raw = stability / FACTOR * (desired_retention ** (1 / DECAY) - 1)
    return max(1, min(max_days, round(raw)))


def initial_stability(grade: int) -> float:
    return max(S_MIN, W[grade - 1])


def _raw_initial_difficulty(grade: int) -> float:
    return W[4] - exp(W[5] * (grade - 1)) + 1


def initial_difficulty(grade: int) -> float:
    return _clamp_d(_raw_initial_difficulty(grade))


def next_difficulty(difficulty: float, grade: int) -> float:
    delta = -W[6] * (grade - 3)
    damped = difficulty + delta * (D_MAX - difficulty) / 9
    # 向“简单”档的初始难度（不截断）回归，避免难度一路被压到 10。
    return _clamp_d(W[7] * _raw_initial_difficulty(EASY) + (1 - W[7]) * damped)


def next_stability(difficulty: float, stability: float, retrievability_: float, grade: int) -> float:
    if grade == AGAIN:
        lapsed = (
            W[11] * difficulty ** -W[12] * ((stability + 1) ** W[13] - 1)
            * exp(W[14] * (1 - retrievability_))
        )
        # 忘记后的稳定度不会比“当天重来”的上限更高，也不会比忘记前更高。
        return max(S_MIN, min(lapsed, stability / exp(W[17] * W[18])))
    hard_penalty = W[15] if grade == HARD else 1.0
    easy_bonus = W[16] if grade == EASY else 1.0
    gain = (
        exp(W[8]) * (11 - difficulty) * stability ** -W[9]
        * (exp(W[10] * (1 - retrievability_)) - 1) * hard_penalty * easy_bonus
    )
    return max(S_MIN, stability * (1 + gain))


def same_day_stability(stability: float, grade: int) -> float:
    increase = exp(W[17] * (grade - 3 + W[18])) * stability ** -W[19]
    if grade >= HARD:
        increase = max(1.0, increase)
    return max(S_MIN, stability * increase)


def review(state: tuple[float, float] | None, grade: int, elapsed_days: int) -> tuple[float, float]:
    """返回评分后的 (稳定度, 难度)。state 为 None 表示第一次复习。"""
    if state is None:
        return initial_stability(grade), initial_difficulty(grade)
    stability, difficulty = state
    if elapsed_days < 1:
        return same_day_stability(stability, grade), next_difficulty(difficulty, grade)
    r = retrievability(elapsed_days, stability)
    return next_stability(difficulty, stability, r, grade), next_difficulty(difficulty, grade)


def difficulty_from_ease(ease_factor: float) -> float:
    """旧算法的易度（1.3–3.x）换算成难度：2.5 对应中间值 5，1.3 对应最难 10。"""
    return _clamp_d(D_MAX - (ease_factor - 1.3) * (5 / 1.2))


def ease_from_difficulty(difficulty: float) -> float:
    """反向换算，只用来让旧的“易度”字段继续有意义；下限 1.3。"""
    return round(max(1.3, 1.3 + (D_MAX - difficulty) * (1.2 / 5)), 2)


__all__ = [
    "AGAIN", "HARD", "GOOD", "EASY", "retrievability", "next_interval", "review",
    "difficulty_from_ease", "ease_from_difficulty",
]
