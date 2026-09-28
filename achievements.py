"""固定里程碑定义与纯函数求值，不保存徽章状态或解锁时间。"""

from dataclasses import dataclass

from learning_stats import LearningMetrics


@dataclass(frozen=True)
class MilestoneGroup:
    category: str
    metric: str
    unit: str
    description: str
    remaining_hint: str
    tiers: tuple[tuple[int, str], ...]


MILESTONE_GROUPS = (
    MilestoneGroup(
        category="streak",
        metric="current_streak_days",
        unit="天",
        description="连续复习打卡 {target} 天，稳步养成学习习惯。",
        remaining_hint="再连续复习 {remaining} 天，达到 {target} 天打卡目标",
        tiers=((3, "三日启程"), (7, "一周坚持"), (30, "月度自律"), (100, "百日不辍")),
    ),
    MilestoneGroup(
        category="mistakes",
        metric="mistake_count",
        unit="条",
        description="记录 {target} 条易错点，把教训变成可复习的积累。",
        remaining_hint="再记录 {remaining} 条易错点",
        tiers=((1, "第一份收获"), (10, "点滴积累"), (50, "错因收藏家"), (100, "百条心得")),
    ),
    MilestoneGroup(
        category="zones",
        metric="recorded_zone_count",
        unit="个分区",
        description="在 {target} 个不同分区记录题目，拓宽学习方向。",
        remaining_hint="再到 {remaining} 个不同分区记录题目",
        tiers=((3, "跨区探索"), (5, "多面学习者")),
    ),
    MilestoneGroup(
        category="practice",
        metric="generated_practice_count",
        unit="道",
        description="成功生成 {target} 道 AI 练习题，围绕薄弱点举一反三。",
        remaining_hint="再成功生成 {remaining} 道练习题",
        tiers=((2, "举一反三"), (20, "练习进阶"), (100, "百题磨炼")),
    ),
    MilestoneGroup(
        category="analysis",
        metric="has_weakness_analysis",
        unit="次",
        description="完成首次薄弱点分析，了解值得集中练习的方向。",
        remaining_hint="前往薄弱点分析，完成首次分析",
        tiers=((1, "知己知弱"),),
    ),
)


def evaluate_achievements(metrics: LearningMetrics) -> list[dict]:
    achievements = []
    for group in MILESTONE_GROUPS:
        current = int(metrics[group.metric])
        for target, name in group.tiers:
            remaining = max(0, target - current)
            unlocked = current >= target
            achievements.append({
                "key": f"{group.category}_{target}",
                "category": group.category,
                "name": name,
                "description": group.description.format(target=target),
                "unlocked": unlocked,
                "progress": {
                    "metric": group.metric,
                    "current": current,
                    "target": target,
                    "remaining": remaining,
                    "unit": group.unit,
                    "message": "已达成" if unlocked else group.remaining_hint.format(
                        remaining=remaining, target=target,
                    ),
                },
            })
    return achievements
