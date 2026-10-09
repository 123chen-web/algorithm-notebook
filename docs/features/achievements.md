
## 成就徽章

学习大厅的「成就徽章」入口展示当前账号的全部徽章。进入页面或刷新时，
`GET /api/achievements` 实时返回 `{metrics, achievements}`；需要登录，
只读现有数据，不调用 AI，不扣额度，不新增数据库表或字段。

`learning_stats.py` 的 `learning_metrics()` 汇总基础指标，
`achievements.py` 的 `evaluate_achievements()` 只负责阈值和展示文案。
基础指标不依赖徽章档位，后续学习概览可以复用；它们是当前数据的总量/状态，
不是某周的增量，周报应另按时间范围统计，不能把总量当作当周成绩。

| metrics 字段 | 口径 | 徽章档位 |
| --- | --- | --- |
| `current_streak_days` | 全部评分的复习记录，按用户时区去重日期；复用原 `current_streak()`，今天未复习但昨天复习仍延续 | 3 / 7 / 30 / 100 天 |
| `mistake_count` | 当前账号题目下仍存在的易错点数量 | 1 / 10 / 50 / 100 条 |
| `recorded_zone_count` | 当前账号 `problems.zone` 去重数量，同分区多道题只计一次 | 3 / 5 个分区 |
| `generated_practice_count` | 当前账号已成功保存的 `variants` 题目数量，与练习结果无关 | 2 / 20 / 100 道 |
| `has_weakness_analysis` | 是否存在该账号的 `weakness_insights` 记录（布尔值），不检查分析质量 | 首次分析 |

`ai_usage.attempts` 是按[AI 额度与计费](../../static/ai-billing.html)结算后的次数，包含识图和薄弱点分析，无法表示成功生成练习的
次数，因此徽章明确按成功落库的题目「道数」计数。当前一次成功生成 2 道题，
20 道对应 10 次；不按时间戳猜测批次，同一秒内生成的题目也逐道计入。

每枚徽章包含稳定英文 `key`、`category`、`name`、`description`、
`unlocked` 及 `progress`。`progress` 包含 `metric`（对应基础指标名）、
`current`（真实当前值，不截断到目标；布尔指标转为 0/1）、`target`、
`remaining`（最小为 0）、`unit`、`message`（未达成时的具体行动提示）。
同类别的每档均独立比较 `current >= target`，因此达成高档会同时达成低档。
例如打卡 4 天时，`streak_7` 的 `remaining` 为 3，提示继续复习 3 天。

徽章反映当前数据，不永久保留解锁状态。断签或删除相关记录后，下次读取会
重新计算；不保存解锁时间，也没有预留历史解锁字段。

### 分享卡片

在「成就徽章」页点击「生成分享卡片」，即可预览 1080×1350 的 PNG 图片，
长按或右键图片即可保存、分享。`GET /api/achievements/share-card` 需要登录，
所有套餐及体验账号均可使用；只读现有数据，不调用 AI，不扣额度。

`share_card.py` 使用 Pillow 渲染，直接复用 `learning_metrics()` 和
`evaluate_achievements()` 的结果。图片展示用户名、连续打卡天数、徽章与学习
数据、下一个目标及用户时区的生成日期，不包含邮箱、用户 ID 等非公开信息。
字体优先使用仓库自带的 Noto Sans CJK SC Regular 子集（`assets/fonts/`，约 1.6 MB，覆盖 ASCII、常用标点和 GB2312 全部汉字），Linux / 容器里不用另装系统字体；找不到自带字体时依次备选 Windows 的微软雅黑、黑体、宋体。所有字体都不可用时接口返回 HTTP 503（而不是 500）。字体采用 SIL Open Font License 1.1，许可证见 `assets/fonts/OFL.txt`，来源与重新生成方法见 `assets/fonts/README.md`；分发本项目时请保留许可证。GB2312 之外的生僻字会显示为方框。

