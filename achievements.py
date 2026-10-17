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
        description="连续复习打卡 {target} 天（中间歇一天不算断），稳步养成学习习惯。",
        remaining_hint="再复习 {remaining} 天（中间可以歇一天），达到 {target} 天打卡目标",
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
    # 徽章奖励“质量”而不是次数（10/14 评估建议）：用做题验证通过、复仇成功代替“生成了多少道 AI 题”。
    MilestoneGroup(
        category="verified",
        metric="verified_pass_count",
        unit="次",
        description="用做题验证复习（重做原题 / 做类似题 / 默写）拿到“记得”以上 {target} 次——真会了，不只是眼熟。",
        remaining_hint="再做题验证通过 {remaining} 次",
        tiers=((1, "真的会了"), (10, "实测十连"), (50, "实测五十")),
    ),
    MilestoneGroup(
        category="revenge",
        metric="revenge_count",
        unit="条",
        description="复仇成功 {target} 条：以前错过的题，隔了一段时间亲手做对。",
        remaining_hint="再复仇成功 {remaining} 条",
        tiers=((1, "一雪前耻"), (10, "十战十捷")),
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
