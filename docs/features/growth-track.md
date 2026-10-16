## 变强轨迹

错题详情里（显示错因之后，和“复习历史”一起出现）多了一条小时间线：每次复习一个点，颜色按那次的评分
（再练 = 红、很吃力 = 黄、记得/很熟 = 绿），点下面写日期和这次定下的“隔 N 天”；最新那次带一圈强调色，
次数多时自动滚到最新那一头。顶上一句总结，比如“复习了 5 次，从「答错了」到「记得」，间隔从 3 天拉长到 30 天。”

- 只用已有的复习记录（`GET /api/mistakes/{id}` 的 `reviews`），不加接口、不加任务、不打分。
- 间隔 = 那次复习的本地日期到 `next_due_date` 的天数。
- 代码：`static/growth-track.js`（`steps` / `summary` / `render`）、`static/growth-track.css`；测试 `tests/growth_track_behaviour.cjs`。
