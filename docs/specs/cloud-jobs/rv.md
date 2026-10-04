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

# 任务 RV：复习手感（分支 `cs/rv`，后端 + 前端由你一人完成）

原本拆成后端（RVB）和前端（RVF）两个并行任务；现在由你一个人做完两部分。两部分的契约**逐字一致**，先做后端并写好测试，再做前端。文中提到的“并行任务 / 文件归属”只是提示你别碰其它功能的文件；你负责的范围是这两部分合起来的所有文件。数据库迁移编号 **7**。

## 第一部分：后端（RVB）


## 0. 目标与依据（前端是并行任务 RVF，契约与它逐字一致）
对标笔记结论（Anki 学习页，已核对）：评分按钮上标着“下次多久后再见”；可以撤销；可“埋藏”（今天不看）和“暂停”（一直不看）；复习队列有每日上限。我们的现状：评分按钮没有间隔提示；评分后不能撤销；不能推迟或暂停一条；久不打开后一次堆几百条（`GET /api/mistakes?due_only=true` 全返回，按到期日升序）。本任务提供这些能力的后端。**不改调度算法本身**（`scheduler.schedule` 的规则保持，第二批已加逾期加成与失败只扣 0.2 易度）。

## 1. 你负责的文件
`main.py`（复习相关路由：`review_mistake`、`owned_mistake` 之后的新路由、`list_mistakes` 的到期过滤、新 `GET /api/review/queue`、`/api/me` 里与复习设置相关的字段；改到所有“到期”查询）、`db.py`（**只追加迁移 7**）、`scheduler.py`（只允许加纯函数 `preview_all(...)`，不改 `schedule` 行为）、`send_reminders.py`（到期查询排除已暂停）、`activity.py` / `weekly_recap.py` / `mastery.py` / `learning_stats.py` 中涉及“待复习 / 到期”的统计（逐个核对：**只有“还要复习多少”的统计要排除已暂停；历史复习记录、掌握度、成就、排行榜的口径不变**）、`sample_world.py`（不需要改，除非测试需要）、新增测试。**不要碰** `static/`、README。迁移编号必须是 **7**（`grep -n "^    (6," db.py` 存在才追加，已存在）。

## 2. 迁移 7（“复习手感”）
- `ALTER TABLE mistakes ADD COLUMN suspended_at TEXT`（可空；非空 = 已暂停）；
- `ALTER TABLE reviews ADD COLUMN due_before TEXT`、`ADD COLUMN last_reviewed_before TEXT`、`ADD COLUMN version_after INTEGER`（均可空；旧记录为 NULL，所以**旧记录不能撤销**）；
- `ALTER TABLE users ADD COLUMN daily_review_cap INTEGER`（可空 = 不限；合法取值 5–200）。
`tests/test_migrations.py` 把参数化用例延伸到版本 7（升级后旧数据不变、新列为 NULL、可重复启动）。

## 3. 接口契约（必须原样实现；错误一律 `{"detail": "中文原因"}`；写接口需登录、非体验账号写入除外见各条；调用前 `recheck_account`）

**3.1 间隔预览** `GET /api/mistakes/{id}/preview` → `200 {"version": 7, "previews": {"0": {"interval_days": 1, "due_date": "2026-10-05"}, "2": {…}, "3": {…}, "4": {…}, "5": {…}}}`。用与 `review_mistake` **完全相同**的输入（该易错点当前状态、用户本地今天、实际逾期天数）调用 `schedule` 得到每个评分档（0、2、3、4、5）的结果；不写库；不属于自己 → 404；**未到期的易错点也可预览**（不报 409）。把“批量算 5 档”写成 `scheduler.preview_all(...)` 纯函数并单测。

**3.2 评分**（`POST /api/mistakes/{id}/review`，请求 / 响应原样保留）：在写入 `reviews` 行时多存 `due_before`（评分前的 `due_date`）、`last_reviewed_before`（评分前的 `last_reviewed_at`）、`version_after`（评分后的 `mistakes.version`）。已暂停的易错点评分 → 409“这条易错点已暂停，请先恢复”。

**3.3 撤销** `POST /api/mistakes/{id}/review/undo`，请求体 `{"version": 整数}` → `200 {…恢复后的 repetitions / interval_days / ease_factor / due_date / last_reviewed_at…, "version": 新版本}`。规则：取该易错点**最新**一条 `reviews`；必须 `due_before IS NOT NULL`（旧记录不可撤销 → 409“这次评分太早，不能撤销”）、`created`（`reviewed_at`）距今 ≤ 30 分钟（否则 409“超过 30 分钟，不能撤销”）、`mistakes.version == version_after`（说明之后没有别的改动，否则 409“这条记录之后又被修改过，不能撤销”）、且请求体的 `version` 等于当前版本（否则 409 与现有“记录已更新”一致）。执行：同一写事务内，用 `ease_before / repetitions_before / scheduled_days(=评分前间隔) / due_before / last_reviewed_before` 恢复 `mistakes`，`version + 1`，**删除那条 `reviews` 行**（打卡 / 热力图 / 周报 / 成就随之回退，这是预期行为）。幂等性：第二次撤销同一次评分 → 409（因为最新一条 review 已变）。并发下只能成功一次。

**3.4 推迟** `POST /api/mistakes/{id}/snooze`，`{"version": 整数, "days": 1 | 3 | 7}` → `200 {"due_date": …, "version": …}`。把 `due_date` 设为“用户本地今天 + days”；**不改** `repetitions / interval_days / ease_factor`，不写 `reviews`；只对**已到期或今天到期**（`due_date <= 今天`）的有效，否则 409“这条还没到期”；已暂停 → 409。`version + 1`；版本不符 409。

**3.5 暂停 / 恢复** `POST /api/mistakes/{id}/suspend` 与 `POST /api/mistakes/{id}/unsuspend`，`{"version": 整数}` → `200 {"suspended_at": …|null, "version": …}`；幂等（重复暂停 / 恢复返回 200 且不再改版本）。恢复时**不改**调度字段；若恢复后 `due_date` 早于今天，保持原样（它会作为逾期出现）。暂停的易错点：从**所有“待复习 / 到期”口径**里排除——`GET /api/mistakes?due_only=true`、总览的待复习数与列表、侧栏数字、专注模式队列、`/api/stats/*` 中的到期数 / 预测（OV 任务新增的 `/api/stats/summary` 若已存在，一并排除）、`send_reminders.py` 的提醒；**不**排除“全部记录”列表（带 `suspended_at` 字段让前端显示“已暂停”）、导出、打印版、掌握度、标签、错因专题、薄弱点分析。逐个 `grep -n "due_date <=\|due_date <\|due_only"` 找全，并在汇报里列出你改了哪些位置、刻意没改哪些及理由。

**3.6 每日上限设置** `GET /api/me/review-settings` → `{"daily_review_cap": null | 整数}`；`PUT /api/me/review-settings`，`{"daily_review_cap": null | 5..200}` → 同形响应（非法值 422）。体验账号可用（只是个人偏好）。`/api/me` 里**不**需要新增字段。

**3.7 复习队列** `GET /api/review/queue?ignore_cap=false&zone=&tag=` → `200`：
```json
{"today": "2026-10-04", "cap": 20, "done_today": 12, "remaining_today": 8, "total_due": 25, "capped": true,
 "items": [ {…与 GET /api/mistakes 的 items 里一条完全相同的字段（含 tags、suspended_at）…} ]}
```
规则：候选 = 今天到期（含逾期）且未暂停的易错点，可再按 `zone` / `tag` 过滤（语义同 `GET /api/mistakes`）；**排序**按“保持率低的优先”：`overdue_days / max(interval_days, 1)` 降序，平手按 `due_date` 升序、`id` 升序（`overdue_days = max(0, 今天 − due_date)`）；`done_today` = 用户本地今天已有的复习记录条数（`reviews` 按用户本地日）；`cap` 为 null 或 `ignore_cap=true` 时 `items` 返回全部候选且 `capped=false`，`remaining_today = null`；否则 `items` 最多 `max(0, cap − done_today)` 条，`capped = total_due > len(items)`；`total_due` = 候选总数（不受上限影响）。**抽成一个排序 / 截断的纯函数并单测。**

## 4. 测试
新增 `tests/test_review_feel.py`（可拆多个文件），至少覆盖：预览与 `review_mistake` 的结果逐档一致（含逾期加成、失败只扣 0.2、未到期可预览、跨时区）、预览不写库；评分后日志列被写入；撤销的全部分支（成功恢复全部字段并删除 review 行、旧记录不可撤销、超 30 分钟、之后被修改、版本不符、二次撤销、并发只成功一次、打卡 / 热力图回退、体验账号照常可用）；推迟（只对到期有效、不改调度字段、版本、时区日界、已暂停）；暂停 / 恢复（幂等、从全部“待复习”口径中消失但仍在“全部记录”里、恢复后逾期出现、评分 409）；上限设置（合法 / 非法 / null）；队列（排序规则逐条验证、上限截断、`ignore_cap`、`done_today` 的本地日界、`zone/tag` 过滤、`capped/total_due/remaining_today` 的值、空队列）；提醒脚本排除暂停；迁移 7；旧测试不回归（`test_app.py`、`test_review_schedule.py`、`test_mastery.py`、`test_overview_activity.py`、`test_send_reminders.py` 等）。变异检查：挑 4 个关键判断（撤销的 `version_after` 校验、30 分钟窗口、暂停排除、队列排序方向）临时改坏确认测试会红，再改回。


## 第二部分：前端（RVF）


## 0. 目标与依据（后端是并行任务 RVB，契约与它逐字一致）
对标笔记结论：
- **Anki 学习页**（已核对）：先只看问题，**按空格显示答案**；四个评分按钮上各标着**“下次多久后再见”**；数字键评分；可**撤销**；可“埋藏 / 暂停”；开始前先看到 新 / 学习中 / 待复习 的数量。
- **Obsidian 间隔重复插件**（已核对）：每张卡显示来源路径。
我们的现状：专注模式（`static/focus.js`）已经有“空格显示思路 / 1–5 打分 / S 稍后再看”；但**普通详情页**（今日复习的右侧详情，`app.js` 的 `openMistake` 及其评分区，`“这次，你掌握得怎么样？”`）一打开就直接显示“这次需要记住的错因”，没有“先回忆”这一步；两处评分按钮都没有间隔提示，评分后不能撤销，不能推迟 / 暂停一条，没有每日上限。目标：把这些做进去，两处手感一致。

## 1. 你负责的文件
`static/app.js` 里 `openMistake` 及其评分 / 详情渲染相关函数、`static/focus.js` / `static/focus.css`、新增 `static/review-extras.js` / `static/review-extras.css`（放共用的间隔预览缓存、撤销提示条、更多菜单等，挂在 `window.ReviewExtras`）、`static/index.html` 里“今日复习”页头与详情区的必要标记、测试。**不要碰**：论坛、套餐、总览、管理后台相关代码；后端。

## 2. 接口契约（RVB 并行实现；缺失时要优雅降级，不能报错）
- `GET /api/mistakes/{id}/preview` → `{"version", "previews": {"0": {"interval_days", "due_date"}, "2": …, "3": …, "4": …, "5": …}}`（不写库）。
- `POST /api/mistakes/{id}/review` 原样（`{quality, version}`）。
- `POST /api/mistakes/{id}/review/undo` `{"version"}` → 恢复后的字段 + 新 `version`；409 带 `detail`（“超过 30 分钟，不能撤销”等）。
- `POST /api/mistakes/{id}/snooze` `{"version", "days": 1|3|7}` → `{"due_date", "version"}`。
- `POST /api/mistakes/{id}/suspend` / `unsuspend` `{"version"}` → `{"suspended_at", "version"}`。
- `GET /api/me/review-settings` / `PUT …` `{"daily_review_cap": null|5..200}`。
- `GET /api/review/queue?ignore_cap=&zone=&tag=` → `{"today", "cap", "done_today", "remaining_today", "total_due", "capped", "items": [同 /api/mistakes 的 items]}`，按“保持率低的优先”排好序、已按上限截断。
- 所有错误 `{"detail": "中文原因"}`，原样显示在状态区。`/api/mistakes` 的条目新增 `suspended_at`（非空 = 已暂停）。
任一新接口 404 / 缺失时：对应功能不显示（预览不显示间隔、撤销按钮隐藏等），旧流程照常可用。

## 3. 界面规格
**3.1 “先回忆，再揭晓”（普通详情页）**：默认遮住“这次需要记住的错因”和“当时的思路和代码”，显示分区 / 标题 / 标签 / 来源（顶部一行 `分区 · 题目 · 标签`，学 Obsidian 的来源路径）和一个大按钮“显示错因（空格）”；可选的“先写下我的回忆”一行输入（点“显示”后保留在原处供对照，**不保存、不上传**）；揭晓后显示错因与评分区。偏好“复习时先遮住错因”（默认开）放在详情顶部一个小开关，存 `localStorage`（键 `review-hide-reason`，try/catch）；关闭时行为与现在一致。揭晓后焦点移到评分区第一个按钮；空格 = 揭晓（焦点在输入框 / 按钮 / 链接上时不拦截；已揭晓后空格不做事）。
**3.2 评分按钮（详情页与专注模式一致）**：5 档沿用 `focus.js` 的 `GRADES`（1 完全忘了→0、2 答错了→2、3 很吃力→3、4 记得→4、5 很熟→5），每个按钮下面一行小字“约 6 天后”（`interval_days` 为 1 写“明天”，否则“N 天后”；取自预览接口，**与实际评分结果一致**）；预览按需请求并缓存（键 = `id + version`），专注模式提前预取下一张；请求失败则不显示小字。键盘：数字键 1–5。评分请求不经过全局 `run()`（保持现状，避免“禁用所有按钮”）。
**3.3 撤销**：评分成功后出现底部提示条（`role="status"`，8 秒后消失；悬停 / 聚焦时暂停计时）：`已评分「记得」 · 下次 6 天后 · [撤销]`；点“撤销”或按 **Ctrl/⌘+Z**（焦点不在输入框时）调用撤销接口，成功后：该条回到队列 / 列表顶部（专注模式把它插回当前位置并重新显示这张卡；详情页重新打开它），提示“已撤销”；失败把 `detail` 显示在提示条里。同一时刻只保留最近一次评分的撤销；再评下一张后上一张的撤销入口消失。**迟到响应**（撤销期间切走 / 登出再登录）一律丢弃。
**3.4 推迟 / 暂停**：详情页和专注模式各有一个“更多”菜单（按钮 + 小弹层，Esc 关闭，焦点管理完整）：`推迟到明天 (T)`、`推迟 3 天`、`推迟 7 天`、`暂停这条（不再出现在待复习里）(P)`；专注模式里现有的“稍后再看 (S)”（只在本次会话里排到末尾）保持不变。成功后该条从待复习列表 / 队列移除并出现提示条（推迟可撤回 = 无；暂停提示条带“恢复”按钮）。“全部记录”列表里 `suspended_at` 非空的条目显示“已暂停”小标签；其详情里提供“恢复复习”按钮（调用 unsuspend）。
**3.5 进度与上限**：专注模式顶部在现有进度条旁显示 `第 3 / 25 条 · 已用 06:12`（计时从会话开始，暂停页面不可见时不累计也可以，二选一并在汇报里说明）；“今日复习”页头加一个“今日上限”下拉（不限 / 10 / 20 / 30 / 50 / 100）+ `今天已复习 12 / 20`（取队列接口的 `done_today / cap`），选择调用 `PUT /api/me/review-settings`。专注模式与“开始复习”改用 `GET /api/review/queue` 取队列（保留 `zone` / `tag` / `ids` 参数的现有语义；`ids` 模式仍直接用传入的 id，不走上限）。队列 `capped` 为真且全部评完时，总结页显示“今天已达上限，还有 N 条明天再复习”+“再多练一点”按钮（用 `ignore_cap=true` 再取队列继续）。列表页（今日复习的左侧列表）仍显示全部到期条目，页头文案写明“今日队列上限 20 条，其余明天再说”（仅当 `capped`）。
**3.6 样式 / 无障碍**：沿用现有令牌；评分按钮的小字用 `--ink-2` 并保证对比度；提示条不遮挡手机底栏（用 `env(safe-area-inset-bottom)` 与底栏高度变量定位）；所有新控件键盘可达，状态用 `#…-status` 的 `aria-live` 播报。

## 4. 测试
Node 行为测试（`tests/review_feel_behaviour.cjs` + `tests/test_review_feel_behaviour.py`）覆盖：揭晓状态机（偏好开 / 关、空格、焦点保护）、预览缓存与降级（接口缺失 / 失败 / 与评分结果一致）、撤销（成功回到队列、409 文案、二次、Ctrl+Z 门控、迟到响应、只保留最近一次）、推迟 / 暂停 / 恢复的列表与队列更新、上限下拉与 `done_today` 展示、`capped` 总结页与“再多练一点”、键盘门控（输入框 / 修饰键 / 对话框 / 菜单打开时）；回归：专注模式现有测试（`tests/widget_behaviour.cjs` 里 focus 部分）不得变红；静态契约与对比度；变异检查挑 3 处。
