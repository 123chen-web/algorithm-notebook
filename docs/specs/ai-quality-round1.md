# 任务单：AI 讲题质量第一轮 + 套路库例题链接

给：Muse　　基线：GitHub main 最新提交（`a87ee75` 或更新），请先拉最新再动手。
交付：只含改动文件的 zip（保持仓库内路径）+ `REPORT.md`（改了什么、测试结果、没做的和原因）。

## 背景（30 秒）

欧叶OY 是算法错题本。每条错题有“当时的代码 `code`”和（刚上线的）可选“正确代码 `correct_code`，用户自己补，网站不生成答案”。AI 有三块：**变体题**（`ai.generate`，出一段【错因】【讲解】…【答案二】七段）、**橡皮鸭**（`duck_prompt.py`，只提问不给答案）、**薄弱点分析**（`ai.analyze_weaknesses`）。别人反馈 AI 讲题质量一般，这一轮先做能立刻改善、且不增加成本的几件事。

## 硬规则（违反就退回）

1. **测试里绝不真调用 AI / 发邮件**。AI 相关测试一律 mock（参照现有 `tests/test_ai*.py` 的写法）。
2. **不要在仓库目录里建临时目录**（见仓库根 `AGENTS.md`）；测试用系统临时目录，不可写就停下来报告。
3. 不改变变体题输出的**七个标题**和 `_SECTION_PATTERN`（前端与解析都依赖），只改标题里面的内容规则。
4. 小黄鸭（`duck_prompt.py`）**这一轮不要碰**：它的铁律是绝不给答案，不要把正确代码喂给它。
5. 用户文字一律 `textContent`，不用 innerHTML；不加行内 style（严格 CSP）。
6. 改了静态资源就把 `index.html` 里对应的 `?v=` 加一，并同步所有测试里写死的版本号（`grep -rn "app.js?v=" tests` 等，漏改会让测试红）。
7. 数据库迁移号从 **74** 接着加（`db.py` 的 `MIGRATIONS.append((74, …))`，必须幂等：先查表/列是否存在）；同步更新写死“最新版本 73”的测试（`grep -rn "== 73\|\[23, 24, 28, 70, 71, 72, 73\]" tests`）和 `README.md` 的“迁移当前到 73”、`/healthz` 相关断言。
8. 跑测试：`PYTHONUTF8=1 python -m pytest tests -q -p no:cacheprovider --ignore=tests/e2e`。本机 `.claude` 目录会让 `test_billing_docs_consistency` 误报，可忽略这一项；其余必须全绿。

## 任务 1：变体题讲得更准（`ai.py`、`routers/review.py`）

1. `ai.generate` 的 `reference` 里，如果错题所属的题有 `correct_code`（非空），加一个键 `correct_code`（截到 3000 字符，和 `original_work` 处理一致）。`routers/review.py` 里 `item` 来自 `main.MISTAKE_SELECT`，已经带了 `p.correct_code`，确认能走到 `generate()`。没有正确代码时，**提示词和请求体必须和现在逐字一致**（现有测试要继续绿）。
2. 在 `INSTRUCTIONS` 的【讲解】里加入明确结构（仍然是纯文本、简体中文、不用 Markdown）：
   - 第一句：错在哪，**要定位到代码或步骤里的具体位置**（例如“第 3 行的循环条件”），不要只说“边界没处理好”。
   - 第二段：一个**能让错代码出错的最小例子**，写出输入、错代码的结果、正确结果。
   - 第三段：一句话记住的规则（不超过 30 字）。
   - 有 `correct_code` 时：以它为依据，指出它和 `original_work` 的关键差异；没有时按原来的方式从代码和思路反推。
   - 仍然不得直接泄露【练习题一/二】的解法。
3. 测试：mock 模型返回，断言 ①有正确代码时请求体里出现 `correct_code`，没有时请求体与旧版一致；②七段解析仍然通过；③`correct_code` 超长被截断。

## 任务 2：“有用 / 没用”反馈（迁移 74 + 接口 + 界面）

目的：先收集真实反馈，再决定怎么调提示词。**不收集自由文本**，只收布尔值，隐私简单。

1. 表 `ai_feedback(id, user_id → users ON DELETE CASCADE, kind TEXT, ref_id INTEGER NULL, useful INTEGER 0/1, created_at)`，对 `(user_id, kind, ref_id)` 唯一（再次点击就是改票）。`kind` 只允许 `variant`、`duck`、`weakness`；`variant` 的 `ref_id` 必须是**当前用户自己**的变体题 id（别人的一律 404，沿用现有 `owned_*` 的写法）。
2. 接口 `POST /api/ai-feedback`，请求 `{kind, ref_id|null, useful}`，返回 `{ok:true}`。限流：每用户每小时 120 次（用现有 `rate_limited`）。要登录，要有 CSRF 头（和现有写接口一致）。
3. 界面：
   - 变体题卡片（`static/app.js` 里 `renderVariant`）底部加一行“这道题对你有帮助吗？”+「有用」「没用」两个按钮；点后按钮变为“已记录，谢谢”，不可重复弹提示。
   - 橡皮鸭面板（`static/duck-panel.js`）在总结出现后加同样一行（`kind=duck`，`ref_id` 传 null）。
   - 离线或失败时静默，不打断使用；晚到的响应要按现有的登录代次/账号校验丢弃（参照 `recommend.js` 里的 `ticket()/current()` 写法）。
4. 管理后台：`admin_metrics.py` 的 AI 指标里加三行汇总（各 kind 的有用数/没用数，按近 7 天和累计），前端展示沿用现有指标卡的写法，不要新建页面。
5. 账号注销（`main.py` 的 `delete_account_data`）必须一并删除 `ai_feedback`；数据导出不含此表（它不是用户内容），在 `docs/features/` 下写一小段说明。
6. `legal.py` 隐私政策列表里补一条“AI 回答的有用/没用反馈（不含文字）”，并把 `TERMS_VERSION` 改成今天的日期；同步 pin 它的测试。
7. 测试：重复点击改票、越权 404、非法 kind 422、限流 429、注销后清空、迁移幂等（回退版本号再跑一次不报错）。

## 任务 3：评估小工具（不进网站，只是开发用）

`tools/ai_eval/run_eval.py` + `tools/ai_eval/cases.sample.json`（5 条**虚构**样例，覆盖：有/无 `correct_code`、算法区/数学区、`mistake` 为空）。

- 用法：`python tools/ai_eval/run_eval.py --cases 文件 [--dry-run]`。`--dry-run` 只打印将要发送的提示词，**不联网**；不带时才读环境变量里的 key 真调用，把每条的七段输出打印出来方便人眼对比。
- 单元测试只测 `--dry-run` 和参数解析，**不得**联网。
- 写 `tools/ai_eval/README.md`：怎么把自己的真实错题导成 cases 文件（字段说明），提醒“真实数据不要提交进仓库”（在 `.gitignore` 加 `tools/ai_eval/cases.local.json`）。

## 任务 4：套路库的例题可点击（`static/patterns.js`、`static/patterns.css`）

现状：每个套路已有 3–5 道例题（`static/data/patterns.json` 的 `examples`），但只显示成文字。改成链接：

- `source=luogu` → `https://www.luogu.com.cn/problem/<id>`（id 如 `P2249`）。
- `source=leetcode-cn` → 现有数据只有题号没有 slug，**不要猜 slug**：链到 `https://leetcode.cn/problemset/?search=<题号>`。
- 其它 source 先不显示链接，保持文字。
- 链接 `target="_blank" rel="noopener noreferrer"`；文案保持“经典例题（只列题号和题名，请自行到对应网站查看）”。
- 同时抽查 `patterns.json` 里 41 个套路每个至少 3 道例题，缺的补上（只补洛谷题号 + 准确题名，不确定的宁缺毋滥，在 REPORT 里列出来让我人工复核）。
- 测试：生成的链接域名只能是 `www.luogu.com.cn` / `leetcode.cn`（防止数据里混入别的域名）；`patterns.js?v=` 加一并同步测试。

## 验收清单（REPORT.md 里逐条打勾）

- [ ] 任务 1：有/无正确代码两种请求体、七段解析、截断，测试齐全且全绿
- [ ] 任务 2：迁移 74 幂等；接口、界面、后台指标、注销清理、隐私政策、版本号同步
- [ ] 任务 3：`--dry-run` 不联网；`.gitignore` 已更新
- [ ] 任务 4：链接域名白名单；例题数量抽查结果
- [ ] 全量 pytest（排除上面说的那一项）全绿，贴出最后一行汇总
- [ ] 没有留下任何临时目录/本地配置文件
