# 云端编码任务（先读这一段）

你是这个项目的**主要编码者**。我（本地的 Claude 会话）是**审查者**：负责指明方向、审查你的代码、做浏览器验证和合并。你在云端环境工作，**可以运行全部测试**（pytest、Node），请务必跑，别只写代码不验证。

## 项目
FastAPI + SQLite 后端，原生 JS 前端（无构建、无框架、无外部依赖），面向中文用户的“欧叶OY”（原名“算法错题本”）。仓库当前 `main` 的 HEAD 是 `f3b0b08`，是我已完整验证过的基线（全量 pytest 2651 通过）。先读 `AGENTS.md`（其中关于“测试临时目录不要建在项目里”的规定针对的是另一个本地沙箱；你在云端可以用系统临时目录跑 pytest，但仍然**不要在项目目录里新建临时 / 缓存目录**，用 `-p no:cacheprovider`）、`README.md` 里相关章节、你要改的文件及其现有测试、`docs/specs/` 下与你任务相关的设计稿（含可直接打开的高保真效果图）。

## Git 工作方式（必须遵守）
1. `git fetch origin && git checkout -b cs/<任务名> origin/main`（任务名见各任务标题；基线必须是 `origin/main`）。
2. 小步提交，每个提交一个逻辑单元、信息写清楚；提交信息末尾加一行 `Co-Authored-By: Claude <noreply@anthropic.com>`。
3. **只推送你自己的分支**：`git push -u origin cs/<任务名>`。**不要推送 `main`，不要开 PR，不要合并，不要改别的分支。**
4. 你的分支会和其它助手的分支一起合并：**只改你负责的文件和函数**；共享的大文件（`main.py`、`static/app.js`、`static/index.html`）只改你负责的区域，不要“顺手整理”无关代码、不要重排 / 重新格式化整个文件（保持换行风格不变）；新增的全局变量 / 函数名加你任务的前缀以免重名。
5. 数据库迁移：基线 `MIGRATIONS` 末项是 6。**只有任务明确要求时才加迁移，编号按任务指定**（RV = 7，FC = 8），追加在列表末尾；迁移函数里不 commit、不 rollback、不 `executescript`，不改已发布的 SCHEMA / 基线 / 旧迁移；`tests/test_migrations.py` 里“严格递增”的测试允许编号有空缺。

## 验证（必须做，并在汇报里贴结果）
- `pip install -r requirements-dev.txt`；全量 `python -m pytest -q -p no:cacheprovider`（约 7–8 分钟）必须**全部通过**；相关 Node 测试：`node --test tests/*_behaviour.cjs tests/widget_behaviour.cjs`（按存在的文件）必须全部通过。
- 有无法解释的失败时：如实写出，不要靠放宽断言、删测试或跳过来变绿。
- 逻辑密集的部分先写测试再写实现；每个任务至少做 2–3 处**变异检查**（临时改坏关键判断，确认测试会红，再改回；汇报写结果）。
- 提交前自审一遍完整 diff：有没有越界修改、调试残留、重复代码、没处理的失败分支。

## 通用规矩
- **样式**：只用主题令牌（`static/style.css` 里的 `--paper / --ink / --ink-2 / --accent / --line / --soft / --azurite / --success-* / --zone-color` 等），不写十六进制颜色、不用 `!important`；`backdrop-filter` 只能出现在 `(hover: hover) and (pointer: fine)` 块里；不能有内联 `style=""` 或内联脚本（CSP `script-src 'self'; style-src 'self'`；JS 里用 `element.style.setProperty` 等 CSSOM 可以）；用户可控文字一律 `textContent`；ink 与 qixi 两个主题、手机 320 / 360 / 390 宽度都不得横向滚动；触控目标 ≥ 40px（芯片 ≥ 32px）；新的文字 / 底色配对对比度 ≥ 4.5:1，加进 `tests/test_contrast_tokens.py`（两个主题都算）；动效只在 `prefers-reduced-motion: no-preference` 下。改过的静态资源 `?v=` 加一。视觉语言参考 `static/forum.css` 里的 `thread-*` 组件与 `docs/specs/forum-thread/mock/`、`docs/specs/forum-board/mock/`（青色 `--azurite` 信号线、等宽小标签 `--mono`、指示灯、深色代码块），保持一致但克制。
- **迟到响应守卫**：任何新的异步请求，登出再登录别的账号 / 切换页面后才返回的响应必须丢弃——沿用 `static/app.js` 里 `sessionEpoch` 与请求序号的写法。写接口走 `api()`（自带 CSRF 头）。
- **测试写法**：后端 pytest；前端行为用 Node（`tests/js_harness.cjs` 假浏览器，写法见 `tests/widget_behaviour.cjs` + `tests/test_widget_behaviour.py`：`node --test`，兼容终端的 `ℹ fail 0` 与管道的 TAP `# fail 0`，断言通过数下限）；静态契约测试只测“关键 id 存在 / 版本号递增 / 无十六进制色 / 无 `!important` / 无内联样式”这类基础项。
- 不要提交任何密钥 / `.env` / 数据库 / 个人信息；不要依赖或访问真实用户数据。

## 汇报格式（最终消息）
分支名与最后一个提交的哈希；改了哪些文件（每个一句话）；与规格的偏差及原因；新增的 id / 类名 / 对外函数 / 接口；旧测试改动清单（逐处说明）；全量 pytest 与 Node 测试的**实际结果行**；变异检查结果；你认为**风险最高的 3 处**供我重点验证；建议加进 README 的文字（不要改 README）。

---

# 任务 OV（分支 `cs/ov`）


## 0. 目标与依据
对标笔记结论：
- **Anki 统计页**：“未来到期”预测每天要复习多少；**“真实保持率”**（官方文档点明它最适合用来检查调度是否有效）。
- **Plausible**：顶部一排指标，每个带“比上一周期涨跌”，选中的指标决定下面那张主图，右上角统一时间范围。
现状：总览（`#home`，`static/overview.js`）有“今日待批”大卡、连续打卡、半年热力图、待复习列表、薄弱点、小组、热帖；**没有**未来负载预测、真实保持率，也看不出和上一周期相比是好是坏。目标：在**不删除现有卡片**的前提下，加一个“趋势”区：指标条 + 一张随指标切换的主图。

## 1. 你负责的文件
`main.py` 新增 `GET /api/stats/summary`（可放进新模块 `stats_summary.py`，`main.py` 只挂路由）、`static/overview.js` / `static/overview.css`（只加新区域与新函数；现有 `render*` 函数的行为不变）、`static/app.js` 里 `loadHome()` 的最小接线（一两行）、相关测试。**不要碰**：复习相关路由（RVB 在改，它会把“已暂停”的易错点从所有到期统计里排除——你的新查询写成集中的一个 `due_condition()` / 同类辅助函数，方便 RVB 之后只改一处；并在汇报里列出你写的所有“到期”查询位置）、迁移（本任务**不需要**新表新列）。

## 2. 接口 `GET /api/stats/summary?days=30`
登录用户；`days` ∈ {7, 30, 90}（默认 30，非法 → 422）。全部按**用户本地日**（`today_for(user)` / 用户时区）划分，与现有“打卡 / 热力图 / 周报”口径一致。响应：
```json
{
  "timezone": "Asia/Shanghai", "today": "2026-10-04", "days": 30,
  "due": {"today": 25, "overdue": 22},                        // 今日到期总数（含逾期）与其中逾期数
  "streak_days": 21,                                           // 沿用现有连续打卡口径（learning_stats.current_streak）
  "reviews": {"current": 92, "previous": 80},                  // 本周期 / 上一等长周期的复习次数
  "retention": {                                               // 真实保持率
    "current": {"reviews": 70, "passed": 61, "rate": 0.871},   // rate 可为 null（没有符合条件的复习）
    "previous": {"reviews": 60, "passed": 53, "rate": 0.883},
    "weekly": [{"week_start": "2026-08-17", "reviews": 9, "passed": 8, "rate": 0.889}, …]   // 最近 8 个自然周（周一为起点，按用户本地日），没有数据的周 rate 为 null
  },
  "forecast": [{"date": "2026-10-04", "due": 25}, {"date": "2026-10-05", "due": 3}, …],   // 今天起 14 天；今天这一格包含全部逾期
  "daily_reviews": [{"date": "2026-09-05", "count": 3}, …]     // 本周期每天的复习次数（含 0 的天），长度 = days
}
```
口径：
- **真实保持率** = 在周期内的复习记录里，**非首次复习**（该易错点在这条记录之前至少还有一条复习记录；用 SQL `EXISTS` 判断，旧数据缺新日志列也适用）中，评分 ≥ 3 的占比。周期边界按用户本地日。分母为 0 时 `rate = null`。
- **预测**：按 `mistakes.due_date` 分组，未来 14 天每天的到期数；今天的数 = `due_date <= today` 的全部（含逾期）；`due.overdue` = `due_date < today` 的数量。
- 需要时对大账号友好：用 SQL 分组，不要把全部复习逐条取回 Python 再算（现状里的反面例子见对标笔记）；给 6 万条复习记录的账号，响应应在百毫秒量级（写一个粗略的性能断言或在汇报里给出实测）。
- 不含邮箱等私人字段；体验账号可用（返回的是自己的数据）。

## 3. 前端（`overview.js`）
在现有大卡**下方**新增“趋势”区（`section#home-trend`，标题“趋势”，右上角时间范围分段 `7 天 | 30 天 | 90 天`，选择存 `localStorage` 键 `home-trend-days`，try/catch）：
- **指标条**：4 个可点击的指标块（`role="tablist"`，选中项 `aria-selected`；←/→ 键切换）：`待复习 25（其中逾期 22）`、`连续打卡 21 天`、`近 30 天复习 92 次 ↗ +12%`（与上一周期比，涨绿 / 跌红 / 持平灰，用令牌；上一周期为 0 时不显示百分比而显示“新”）、`真实保持率 87% ↘ −1.2 个点`（`rate` 为 null 显示“—”并在说明里解释“需要至少一次非首次复习”）。每块下面一行小字解释口径（点开“怎么算”折叠说明）。
- **主图**（一个 SVG 图，随选中指标切换，数据缺失时显示说明而不是空图）：
  - 待复习 → **未来 14 天柱状图**（今天的柱标“含逾期”并用强调色，其余用 `--azurite`；柱顶标数字；横轴 `今天 / 明 / 后 / 10-07 …`；0 的天显示细线）。
  - 连续打卡 → 复用现有热力图数据，近 `days` 天的小热力方块条（不要重复实现整张半年热力图，也不要删除现有那张）。
  - 近 N 天复习 → **面积折线**（每天次数；轻网格；终点强调；鼠标 / 触摸悬停显示当天数值）。
  - 真实保持率 → **8 周折线**（`rate` 为 null 的周断线；参考线 85%，写明“目标线”）。
  图要按比例画：同一个比例尺放刻度、标签和数据点，标签只写图里真实到达的值；文字颜色用令牌；SVG 留足边距；提供“查看数据表”折叠（可访问的 `<table>`）。
- 请求用 `api()`，带迟到响应守卫；加载中显示骨架，失败显示一行错误 + “重试”；`prefers-reduced-motion` 下无动画。手机上指标条横向滚动（`scroll-snap`），图自适应宽度。
- 对外：`window.Overview` 增加 `renderTrend(summary)`（可测试的纯渲染）与 `trendModel(summary)`（纯函数：指标块文案、涨跌方向与百分比、图数据与刻度）。

## 4. 测试
后端（新增 `tests/test_stats_summary.py`）：口径逐项——用户时区跨午夜、`days` 三个取值与非法值、上一周期边界（含空数据）、真实保持率（首次复习不计、评分 3 的边界、旧数据缺日志列、分母为 0）、周分组边界、预测的今天含逾期、`overdue` 计数、`daily_reviews` 含 0 的天、已注销 / 他人数据不混入、体验账号、查询次数不随复习数增长。前端：Node 测试覆盖 `trendModel` 的涨跌与“新”、null 保持率、刻度；渲染函数的各状态（加载 / 错误 / 空数据 / 切换指标 / 键盘）；迟到响应；静态契约与对比度。变异检查：挑 3 个关键判断（首次复习排除、今天含逾期、用户时区日界）临时改坏确认测试会红。
