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

# 任务 AD（分支 `cs/ad`）


## 0. 目标与依据
对标笔记：**Plausible**（已核对）——顶部一排带“比上一周期涨跌”的指标，加一张主趋势图，细节放标签页卡片里。我们的管理后台现在只有运营总量和举报队列（外加刚做好的“手动收款与兑换码”卡片）；看不出：这周新增了多少人、多少人真的在用、AI 调了多少次花了多少 token、新用户有没有走完第一轮。目标：给站长一眼能看懂的运营指标。**只读统计，不改业务数据；加一个只读接口，不加迁移。**

## 1. 你负责的文件
`main.py` 新增 `GET /api/admin/metrics`（建议放进新模块 `admin_metrics.py`，`main.py` 只挂路由，管理员鉴权沿用现有管理员路由的写法与错误）、`static/index.html` 里管理后台页面**最上方**新增指标区的标记（不要动兑换码卡片）、`static/app.js` 里管理后台加载函数的最小接线、新增 `static/admin-metrics.js` / `static/admin-metrics.css`（`window.AdminMetrics`）、测试。**不要碰**兑换码卡片（`redeem.js`）、其它页面、迁移。

## 2. 接口 `GET /api/admin/metrics?days=7`（仅管理员；`days` ∈ {7, 30}，默认 7，非法 → 422）
周期按**服务器 UTC 日**切分即可（后台是站长视角，不按个人时区），“本周期”= 最近 `days` 天（含今天），“上一周期”= 紧邻的前 `days` 天。排除已注销账号与体验账号（除非特别注明），不含邮箱等私人字段。响应：
```json
{
  "days": 7, "generated_at": "ISO",
  "new_users": {"current": 12, "previous": 9},
  "active_users": {"current": 31, "previous": 28},             // 周期内有过 复习 / 新增记录 / 发帖 / 评论 任一行为的去重用户数
  "records": {"current": 140, "previous": 98},                 // 新增错题记录数（problems）
  "reviews": {"current": 620, "previous": 540},                // 复习次数
  "posts": {"current": 4, "previous": 3}, "comments": {"current": 21, "previous": 17},
  "ai": {"calls": {"current": 55, "previous": 40}, "failed": {"current": 3, "previous": 2},
         "tokens": {"prompt": 120000, "completion": 31000},    // 本周期，来自 ai_calls 表里服务商返回的用量（可能为 0 / 缺失）
         "by_feature": [{"feature": "variants", "calls": 30, "failed": 1, "tokens": 90000}, …]},
  "pending_reports": 2,                                         // 现有举报队列里待处理的数量
  "redeem": {"created": 10, "redeemed": 6, "unused": 4},       // 本周期生成 / 兑换的兑换码数；unused = 现存未使用且未过期未撤销
  "funnel": {                                                   // 在本周期内注册的正式账号的转化漏斗
    "registered": 12, "first_record": 9, "first_review": 6, "returned_next_day": 3
  },
  "daily": [{"date": "2026-09-28", "new_users": 2, "active_users": 6, "reviews": 80}, …]   // 本周期每天（含 0），长度 = days
}
```
口径写清楚并有测试：`first_record` = 注册后创建过至少一条错题记录；`first_review` = 注册后有过至少一次复习；`returned_next_day` = 注册日之后的某个**更晚的日期**还有过任一行为（复习 / 新增 / 发帖 / 评论）。所有统计用 SQL 分组，别把整表取回 Python 逐条算；`ai_calls` 里没有 token 的记录按 0 计且不报错。

## 3. 前端
- 管理后台页面最上方新增“运营概览”：右上角时间范围分段 `7 天 | 30 天`（存 `localStorage` 键 `admin-metrics-days`，try/catch）。
- **指标条**（横排可换行，手机横向滚动并 `scroll-snap`）：`新增用户`、`活跃用户`、`新增记录`、`复习次数`、`AI 调用`（副行 `失败 3 · 令牌 15.1 万`）、`待处理举报`（> 0 时用警示色并带“去处理”链接到现有举报队列区域）；每块带与上一周期的涨跌（涨绿 / 跌红 / 持平灰；上一周期为 0 显示“新”）。口径说明用折叠的“怎么算”。
- **主图**：一张按天的折线 / 面积图（`daily`），图上方分段选择看 `新增用户 | 活跃用户 | 复习次数`；图按比例画、颜色用令牌、提供“查看数据表”。
- **漏斗**：四根水平条（注册 → 记第一条 → 完成第一次复习 → 次日回访），每根显示人数与相对上一根的转化率；`registered` 为 0 时显示说明而不是空条。
- **AI 明细**：小表格按 `by_feature` 列出各功能的调用 / 失败 / 令牌，数字用 `tabular-nums`；提示“令牌来自服务商返回的用量，仅供估算成本；单价请按你所用服务商的价目表自己换算”。**不要内置任何金额单价。**
- 请求用 `api()`，带迟到响应守卫、骨架、失败重试；非管理员不显示、也不请求；`prefers-reduced-motion` 下无动画。

## 4. 测试
后端（`tests/test_admin_metrics.py`）：管理员鉴权（非管理员 / 未登录 / 体验账号）、`days` 取值、周期边界（UTC 日界、上一周期、空数据）、各计数口径（排除已注销与体验账号；活跃用户去重；漏斗三个口径逐条构造数据验证，含“当天注册又回来”不算次日回访）、`ai_calls` 的失败与令牌汇总（含缺失令牌）、举报与兑换码计数、`daily` 长度与 0 填充、查询次数不随数据量增长、不泄露邮箱 / 用户名以外的字段。前端 Node 测试：指标条涨跌与“新”、漏斗转化率与 0 分母、图数据与刻度、各状态渲染、非管理员不请求、迟到响应、对比度与静态契约。变异检查挑 3 处（排除体验账号、次日回访口径、上一周期边界）。
