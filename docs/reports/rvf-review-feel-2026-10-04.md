# RVF 复习手感前端交付记录（2026-10-04）

RVF 前端已实现；Node 行为及现有前端静态回归通过。真实数据库联调、完整 pytest 和真实浏览器宽度/截图验收尚未完成，原因见下面的验证限制。

实际基线为 `main` / `a1d1d20`，`61a1a4a` 是其祖先。保留开始时已有的 RVB 后端及其他未提交改动；本任务没有修改后端、迁移、真实数据库，没有提交、推送或切分支。

## 文件清单

| 文件 | 本任务改动 |
| --- | --- |
| `static/app.js` | 普通详情先回忆后揭晓、五档评分、独立提交、菜单、撤销恢复、暂停标签、今日上限和详情/列表/登录代次守卫。 |
| `static/index.html` | 今日上限及状态标记，加载共用模块；app 69→70，focus JS/CSS 2→3，新模块 JS/CSS v1。 |
| `static/focus.js` | 使用上限队列、揭晓评分、间隔预取、撤销、推迟/暂停/恢复、计时及达到上限后继续。 |
| `static/focus.css` | 来源、计时、上限及可播报状态样式，长标题/分区换行。 |
| `static/review-extras.js`（新增） | 两处共用的预览缓存、能力探测、本地偏好、提示条、最近一次撤销和更多菜单。 |
| `static/review-extras.css`（新增） | 共用令牌样式、详情评分布局、可达控件和避让手机底栏的提示条。 |
| `tests/review_feel_behaviour.cjs`（新增） | 38 项真实详情、列表、上限、登出/导航及共用模块集成行为检查。 |
| `tests/review_extras_behaviour.cjs`（新增） | 19 项缓存、降级、撤销、菜单、提示计时、隐藏抽屉和模态焦点检查。 |
| `tests/review_focus_behaviour.cjs`（新增） | 20 项专注行为及真实共用模块联合检查。 |
| `tests/test_review_feel_behaviour.py`（新增） | 统一执行三组 Node 测试，兼容终端/TAP，并断言通过数量下限。 |
| `tests/test_review_feel_static.py`（新增） | id、版本和加载顺序、CSP、令牌、动效门控及提示条定位基础契约。 |
| `tests/test_contrast_tokens.py` | 新增六组复习文字/底色配对，两个主题及现有场景均要求至少 4.5:1。 |
| `tests/test_focus_assets.py` | 把旧的纯本地分区筛选契约更新为队列传递 zone/tag，并继续检查旧接口回退。 |
| `tests/test_app_session_assets.py` | 旧会话测试摘录补入真实 RVF 状态和清理函数，原断言保留。 |
| `tests/test_clusters_assets.py` | 旧 showView 摘录共用上述支持函数，原断言保留。 |
| `tests/test_forum_page_assets.py` | 只同步 app.js 版本 69→70。 |
| `tests/test_intro_film_assets.py` | 两处 app.js 精确版本引用 69→70。 |
| `tests/test_onboarding_assets.py` | 只同步 app.js 版本 69→70。 |
| `tests/test_rank_assets.py` | 只同步 app.js 版本 69→70。 |
| 本报告 | 记录实现、旧断言变动、实际验证及待验项目。 |

`db.py`、`main.py`、`mastery.py`、`scheduler.py`、`send_reminders.py`、`stats_summary.py`、已有 RVB 测试及其迁移测试改动均来自任务开始前，本清单不将它们归为 RVF 成果。

## 行为及实现选择

- 普通详情默认遮住错因、原始思路/代码与评分；回忆输入揭晓后原地保留，完全不写库、不上传。关闭偏好后恢复直接查看流程。今日左栏也遮住描述，避免提前透露错因；记录条数及筛选语义保留。
- 揭晓后焦点到第一个可用评分按钮。数字键仅在揭晓后评分，编辑控件、修饰键、输入法、对话框及菜单开启时不抢键。空格揭晓一次；按钮/链接保留原生激活。
- 五档对应质量 `0/2/3/4/5`。预览按 id+version 缓存，分数提示以评分接口实际返回的间隔为准；同一请求并发装饰也只留下一个间隔标签。
- 评分、推迟、暂停、恢复及设置写请求均走注入的 `api()`，不走全局 `run()`。普通评分只锁定当前评分组。
- 最近一次评分保留八秒撤销，悬停和聚焦分别暂停计时；新通知替换旧通知。409 等中文原因原样显示。专注提示条放在模态内部，撤销/恢复按钮进入 Tab 循环。
- 能力探测只发送 GET：写路由返回 405 视为存在，404 隐藏；网络失败隐藏并允许后续重试，不用写请求探测。
- 今日列表始终显示全部到期条目；专注队列使用服务端排序和上限。`ids` 模式直接取指定记录，不走上限；队列 404 时回到旧到期列表。settings 404 只隐藏上限编辑，队列复习数量仍可展示。
- 撤销/恢复把卡片插回列表或当前队列头；专注分区已改变时回到该卡分区，以确保重新显示它。
- 专注计时从会话开始持续累计，页面不可见期间也计时。这是规格允许的二选一方案。
- 来源显示分区、标题、标签；一键收录的“题目链接”来源行也显示在遮挡区域外。

除上述今日描述同步遮挡、遮住可能透露答案的历史/AI区外，没有扩展其他产品范围。接口实现及迁移 10 不在 RVF 范围内。本次没有真实浏览器、数据库验收证据，不能把 Node 假浏览器测试当作真实设备或后端联调证明。

## 新标记、类名和对外 API

固定标记：`review-cap-controls`、`review-daily-cap`、`review-done-today`、`review-cap-note`、`review-queue-status`。

详情动态标记：`review-hide-reason`、`review-recall`、`review-reveal`、`review-detail-status`、`review-reason`、`review-original`、`review-grade-row`；共用提示条 `review-toast-status`；菜单 `review-more-menu-N`；专注 `focus-status`。

新共用类：`review-toolbar`、`review-source`、`review-source-link`、`review-preference`、`review-recall`、`review-reveal`、`review-detail-status`、`review-reason`、`review-grade`、`review-interval`、`review-suspended`、`review-cap-controls`、`review-cap-note`、`review-more`、`review-more-button`、`review-more-menu`、`review-more-action`、`review-restore`、`review-toast`、`review-toast-text`、`review-toast-action`。专注新增 `focus-source`、`focus-status`、`focus-cap-note`、`focus-continue`。

`window.ReviewExtras`：`GRADES`、`configure`、`capture`、`preview`、`decorateGrades`、`intervalText`、`hideReason`、`setHideReason`、`editable`、`blockedKey`、`menu`、`notify`、`rememberReview`、`clearNotice`、`reset`。菜单节点提供 `ready/open/close/destroy`，操作按钮使用 `data-review-action` 和 `data-days`。

`window.FocusReview` 保留 `start/close/isOpen`，新增 `configure({api,getEpoch,getUser,getView})`。

普通详情新增内部 `rvf*` 函数负责页面守卫、清理、列表卡片、队列页头、每日上限、移除/恢复、揭晓和键盘；没有新增后端接口。

## 旧测试改动原因

1. forum/onboarding/rank 的各一处 app.js 版本，以及 intro-film 的两处引用：实际资源修改后必须从 69 加到 70；未改变其他资源版本断言。
2. focus 的分区契约：旧断言禁止请求带 zone，与新队列接口筛选要求冲突；改为明确检查 queue、zone、tag 和旧 due_only 回退。五档映射、CSRF、焦点和旧行为断言保留。
3. 会话 harness 与 clusters 页面 harness：生产 `signedOut/showView` 新增 RVF 清理依赖，原摘录漏掉这些定义；补入真实声明及真实 `rvfClearDetail()`，没有用桩代替，也没有放宽原来的迟到 401 和导航断言。
4. 对比度增加新配对，不降低旧阈值；现有 widget focus 测试没有改动。

## 实际验证

最终 Node 命令：

```powershell
node --test tests/review_feel_behaviour.cjs tests/review_extras_behaviour.cjs tests/review_focus_behaviour.cjs
```

结果：`tests 77 / pass 77 / fail 0`（详情 38、共用 19、专注 20）。Python 包装器也成功运行了这三组测试。

旧专注回归：

```powershell
node --test --test-name-pattern='focus:' tests/widget_behaviour.cjs
```

结果：`tests 3 / pass 3 / fail 0`；完整 widget 包装器在下述 pytest 前端组中通过。

最终较广的纯前端检查：

```powershell
$env:PYTHONIOENCODING='utf-8'
$rvfAssetTests = @(rg --files tests | Where-Object { $_ -match 'test_.*_assets.py$' })
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider @rvfAssetTests tests/test_contrast_tokens.py tests/test_review_feel_static.py tests/test_review_feel_behaviour.py tests/test_widget_behaviour.py -k 'not test_the_four_sample_records_are_accepted_by_the_real_api_and_removable'
```

最终结果原文：`1046 passed, 1 deselected, 2 warnings in 10.55s`。排除项是已被系统临时目录权限阻塞的那一项 onboarding 数据库测试；没有更换其临时路径。两个 warning 来自现有 Starlette/httpx 弃用提示。

另外实际运行并通过：

```powershell
node --check static/app.js
node --check static/focus.js
node --check static/review-extras.js
git diff --check
```

### 失败记录及处理

首次含 onboarding 数据库测试的命令：

```powershell
.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider tests/test_review_feel_static.py tests/test_contrast_tokens.py tests/test_focus_assets.py tests/test_forum_page_assets.py tests/test_intro_film_assets.py tests/test_onboarding_assets.py tests/test_rank_assets.py
```

失败原文：

```text
ERROR tests/test_onboarding_assets.py::test_the_four_sample_records_are_accepted_by_the_real_api_and_removable
PermissionError: [WinError 5] 拒绝访问。: 'C:\Users\chenjin\AppData\Local\Temp\pytest-of-chenjin'
678 passed, 2 warnings, 1 error in 2.76s
```

已停数据库测试并向用户报告/询问处理方式；没有设置 basetemp，也没有建立项目内替代目录。此前一次独立 `tempfile.TemporaryFile()` 探针成功，只能证明 Temp 根目录可创建新文件，不能证明 pytest 既有子目录可访问。

修复期间的 Node 联合测试曾有以下真实失败，均已补测试和修复后重新通过：

```text
tests 58 / pass 57 / fail 1
focus integration: real menu survives card removal through a suspend toast and restores the card
TypeError: Cannot read properties of null (reading 'textContent')
```

原因是菜单成功后销毁与恢复能力探测的集成时序；后续用真实模块联合覆盖。

```text
tests 67 / pass 66 / fail 1
detail menu: P suspends, restore uses the new version and returns the item to list head
The input did not match the regular expression /已暂停.*恢复/. Input:
'已暂停这条复习'
1 failed, 679 passed, 1 deselected, 2 warnings in 4.72s
```

测试补齐独立 unsuspend GET 能力响应后通过，保留“恢复按钮存在”的原断言。

首次扩展到全部 assets 测试：

```text
ReferenceError: rvfPageGeneration is not defined
test_real_page_rendering_and_async_guards[focus-closed]
'all' !== 'clusters'
7 failed, 1039 passed, 1 deselected, 2 warnings in 10.13s
```

修复两套旧源码摘录 harness 的依赖后，focused 结果 `43 passed`；最终全前端组结果如上。

RED 阶段还分别证实缺少揭晓按钮、并发重复间隔标签、分区切换后撤销未显示原卡和隐藏抽屉误屏蔽快捷键；这些测试在最终 77 项中均已转绿。Windows 原生命令转义曾导致测试命令语法错误，已改用 here-string 经 stdin 执行，不改变产品逻辑。

### 变异检查

选取三处关键判断临时改坏后，指定行为测试均确实变红，随后恢复原源码并回归通过：

| 变异 | 结果 |
| --- | --- |
| 预览缓存键去掉 version | 版本缓存测试 `fail 1`。 |
| 撤销提交旧 item.version | 撤销版本测试 `fail 1`。 |
| 共用 capture 去掉 epoch 比较 | 迟到撤销测试 `fail 1`。 |

另以纯内存源码变异检查详情默认揭晓、详情请求序号、详情 epoch、专注未揭晓评分门控，都被测试捕获；没有为变异建立临时/缓存目录。

### 浏览器限制

浏览器工具库存无可用浏览器，创建验证标签页返回原文：

```text
Browser is not available: iab
```

没有启动替代数据库或模拟服务，没有截图；未声称 ink/qixi 或 320/360/390 的真实浏览器宽度检查通过。CSS 使用换行、min-width、主题配对和手机底栏变量防护，仍需要实机/真实浏览器确认。

## 风险最高的三处，建议重点验收

1. **手机布局与焦点**：在 ink/qixi、320/360/390 宽度检查详情五档按钮、长标题/来源、更多菜单及提示条；确认无横向滚动、不遮底栏，专注模式 Tab 能到撤销/恢复，Esc 先关闭菜单。
2. **恢复与迟到响应**：真实后端评分→撤销，暂停→恢复，切换卡片/分区/页面或登出再登录期间让响应延迟；核对 version、卡片回到队列头、只允许最近一次撤销及 409 中文原因。
3. **每日上限与筛选**：设置 10 条，done_today 接近/达到上限，配合 zone/tag；左栏仍全量、专注只取服务端队列，总结“还有 N 条”与 ignore_cap=true 继续一致，ids 不受上限。

## 建议加入 README 的文字（本任务未改 README）

> 复习时默认先遮住错因，空格揭晓后用 1–5 评分；可先写下回忆作对照，这段文字只留在当前页面，不保存、不上传。评分按钮显示预计下次复习间隔，最近一次评分可在提示条中或用 Ctrl/⌘+Z 撤销。更多菜单支持推迟 1/3/7 天、暂停与恢复；专注模式的 S 只在本轮排到末尾，T 推迟一天，P 暂停。今日上限只限制专注队列，左栏仍显示全部到期记录；达到上限可选择“再多练一点”。专注计时包括页面不可见期间。后端尚未部署相应接口时，新功能自动隐藏或回退，原复习流程仍可使用。
