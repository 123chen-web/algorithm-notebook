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

# 任务 PL（分支 `cs/pl`）


## 0. 目标与依据
对标笔记结论：Cal.com 的定价页**先摆“免费，永久”并列出免费版包含什么**，再往下是付费档，推荐档用深色卡突出，主按钮文案直白。我们现在的套餐页：一张“当前订阅”文字卡 + “可购买套餐”列表 + 订单；没有免费版对照、没有用量条、退款说明藏在订单卡里；手动收款 / 兑换码（刚做好的 `static/redeem.js`）是追加在页面里的两块，没有融入整体。目标：让人一眼看懂“我现在是什么、升级能得到什么、怎么开通、不满意怎么退”。**不改任何后端、不改支付逻辑，不改官方支付下单 / 退款的行为。**

## 1. 你负责的文件
`static/index.html` 里 `#plan-page` 一整段；`static/app.js` 里套餐相关函数（`planFacts`、`refreshPlanSubscription`、`loadPlanOrders`、`refundPlanOrder`、`renderPlans`、`renderPlanOrder`、`loadPlanPage` 及其订单轮询）；`static/redeem.css` 里套餐页（非管理后台）的布局；新增 `static/plan.js`（纯函数 + 渲染，挂在 `window.PlanView`）与 `static/plan.css`；相关测试。**不要碰**管理后台那张兑换码卡片的功能（`redeem.js` 里管理员部分）、其它页面。保持 RC 的现有 id 与行为（兑换流程、手动付款块的显示条件、`GET /api/manual-payment`、`POST /api/redeem`），只允许调整它们所在的容器和排版；改到的 RC 测试逐处说明。

## 2. 页面结构（从上到下）
1. **当前状态区**（替换 `#plan-subscription` 的内容）：
   - 无有效套餐：标题“免费版”，下面一条**用量条**：`今日 AI 3 / 10 次 · 午夜重置`（数据来自 `/api/me` 的 `ai_daily_used / ai_daily_limit / ai_daily_remaining`；条的填充 = 已用 / 上限；用完变成警示色并写“今天的次数用完了，明天零点恢复”；“午夜”按用户时区）。
   - 有有效套餐：套餐名胶囊 + `到期 2026-11-02 · 还剩 29 天`（用 `plan_expires_at` 与当前时间算，向上取整；≤3 天用警示色并出现“续费”引导）+ 每日额度与用量条 + 一条“提前续费会顺延，不浪费剩余天数”的说明。
2. **套餐卡片行**（替换 `#plan-list`，标题改成“选择套餐”）：左起第一张是**免费版**（固定内容：`¥0 · 永久`；清单：`每天 {AI_DAILY_LIMIT} 次 AI 生成`〔取当前用户的免费额度，没有就写“每天 10 次”〕、`全部复习、统计、小组、讨论区功能`）；然后是 `/api/plans` 返回的每个套餐，**价格最低的付费套餐标“推荐”并用深色 / 强调边框突出**（只有一个付费套餐时不标）。付费卡片内容：套餐名、`¥9.90 / 30 天`（金额取整数分换算，显示两位小数）、清单（均为**真实**权益，别编造功能）：`每天 {N} 次 AI 生成（是免费版的 {N/免费额度} 倍）`〔倍数不是整数时保留一位小数〕、`其余功能与免费版相同`、`{period_days} 天有效，到期前续费会顺延`、`退款：官方支付的订单可在“我的订单”自助全额退款（当天已用的 AI 次数不退）；手动付款请联系站长`。当前正在使用的套餐卡加“当前套餐”标记。**主按钮**：套餐 `purchasable` 且官方渠道可下单 → 保持现有的下单按钮与行为（含渠道选择、二维码 / 支付链接展示、轮询）；否则按钮写“开通方式”并平滑滚动到第 3 块（`scrollIntoView`，`prefers-reduced-motion` 下不用平滑）。体验账号：沿用现有“体验账号不支持购买套餐”提示，卡片只读。
3. **开通方式**（新的分组标题，把 RC 的“有兑换码？”输入块和“手动付款（内测期）”块放进来）：顶部三步说明（`① 扫码付款，备注写用户名 → ② 站长确认后发给你兑换码 → ③ 在下面输入兑换码，立刻生效`；只有手动付款启用时显示这三步，否则只显示兑换码输入）。两块的显示条件、请求、状态播报、迟到响应守卫保持原样。
4. **我的订单**：桌面用紧凑表格（时间 · 套餐 · 金额 · 渠道 · 状态胶囊 · 操作），手机上改为堆叠卡片；状态胶囊用 `--success-*`（已支付）/ `--soft`（待支付）/ 警示（已退款 / 已关闭）令牌；退款按钮与二次确认行为不变；表头上方一行退款规则小字。没有订单显示印章空状态。
5. 所有数字（金额、天数、次数）用 `font-variant-numeric: tabular-nums`；小标签可用 `--mono`（定义在页面容器上，别污染全局）。

## 3. 实现要点
- 新增 `static/plan.js`：纯函数 `PlanView.model(plans, me, now)` → 返回 `{ freeLimit, status: {active, daysLeft, expiresAt, urgent}, usage: {used, limit, remaining, ratio, exhausted}, cards: [{id, name, priceText, perDayText, multiplierText, periodText, recommended, current, purchasable}] }`；`PlanView.formatPrice(cents)`、`PlanView.daysLeft(expiresIso, nowMs)`；渲染函数用 DOM API + `textContent`。`app.js` 里只保留数据获取与调用。
- 价格、天数计算要有边界：0 元 / 非整数倍 / 过期（`plan_expires_at` ≤ now）/ 无套餐 / `ai_daily_limit` 为 0 / 无 `plans`。
- 保持官方支付现有行为的回归：下单、二维码文本、订单轮询、退款确认。

## 4. 测试
Node 行为测试覆盖 `PlanView.model` 的各边界、渲染出的卡片与按钮分支（purchasable / 非 purchasable / 体验账号 / 当前套餐 / 推荐规则）、滚动按钮、迟到响应；静态契约测试（版本号、无十六进制 / `!important` / 内联样式、关键 id 仍存在）；对比度配对。受影响的旧测试（`tests/test_redeem_assets.py`、`tests/test_layout_assets.py`、套餐相关资源测试）逐处说明。
