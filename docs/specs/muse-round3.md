# 任务单：第三轮（8 项）——复习默认值、扩展一键录入、复仇成功卡、教练汇总页、论坛开关、手感层第二批、AI 用量分档、更新脚本防呆

给：Muse　　基线：GitHub `origin/main @ 148b355`（**先拉最新**，再开分支）。
**动手前先读完两份文件：`AGENTS.md`（项目宪法，每条都是踩过的坑）和本文“硬规则”。** 这批任务量大、彼此独立，
**一项一个分支、一份报告、一个交付包**，按下面的优先级做；做不完的项在报告里写清楚，不要为了凑数偷工减料。

## 硬规则（全部来自这几天真实发生的事）

1. **代码结构已经拆分（别按旧印象写）**：`main.py` 只装配；`core/`（config / time / ratelimit / security）、`models/`、`routers/`、
   顶层领域模块；前端 `static/app.js` 只是启动文件，业务在 `static/app/<领域>-*.js`（20 个，按 `index.html` 里的顺序加载）。
   路由里**不要** `import main`；引用别的模块写 `import db` / `from core import time` 再调用时取属性（测试靠 monkeypatch 定义处）。
2. **新增顶层目录/包必须同时改 `Dockerfile` 的 `COPY`**。上一轮拆分忘了 `COPY core/ models/`，线上更新后容器直接起不来、网站挂了。
   `tests/test_deploy_files.py` 里已经有一条测试会按 Dockerfile 搭最小镜像并 `import main`，别绕过它。
3. **迁移号**：现在最大是 81，**下一个是 82**（本任务单第 1 项用）。后面需要的依次 83、84…；迁移必须幂等、对旧库可升级、
   在 `docs/dev/data-model.md` 的迁移表补一行，并更新所有写死 `schema_version == N` 的测试（用 `grep -rn "== 81" tests` 找）。
   **服务器已经升到 81，所以绝对不能再改 81 及以前的迁移**。
4. **严格 CSP**：不写行内 `<script>`、`style="..."`、`onclick=`；用户文本一律 `textContent`；不用 `innerHTML` 拼用户内容。
5. **错因标签只能是 `error_taxonomy.py` 里的 14 个固定 code**。前端任何“自动打标签”都会被接口 422（上一轮就因为“举一反三”标签踩过）。
6. **改了静态资源要同步升 `index.html` 里的 `?v=`**；测试里用 `tests/static_helpers.py` 的 `versioned_src()` / `app_source()`，不要再写死数字。
7. **测试**：新测试测行为，不测“源码里有某段字符串”。本地跑法：`PYTHONUTF8=1 python -m pytest tests -q -p no:cacheprovider --ignore=tests/e2e`（全量约 16 分钟）+
   `node --test tests/*_behaviour.cjs`。AI、邮件、支付一律 mock，不联网、不计费。**不要在项目目录里建临时目录**（见 `AGENTS.md` 第 7 节）。
8. **每项交付**：`git format-patch` 出来的 `patches/*.patch` + `files/`（改动后的完整文件，保持仓库里的换行风格 CRLF）+ `APPLY.md`（基线提交、应用方式）+ `REPORT.md`
   （改了什么、为什么、怎么验证、没做什么、需要站长拍板的点）。有界面的项附桌面 + 手机宽度截图，**雾白 / 水墨红笔 / 栖间三个主题各一张**。
   **提交信息里不要加 `Co-Authored-By`**。
9. 不确定的产品决定（文案、默认值、阈值）不要自己拍板，列在报告里。

## 优先级与交付顺序

①（默认值）→ ⑧（更新脚本防呆）→ ⑤（论坛开关）→ ⑦（AI 用量分档）→ ②（扩展一键录入）→ ③（复仇成功卡）→ ④（教练汇总页）→ ⑥（手感层第二批）。

---

## ① 复习方式偏好：默认改成“只回忆”（迁移 82）★ 先做，很小

### 背景
上一轮把默认设成“重做优先”，外部评审认为对新用户太重（要默写代码或限时重做），建议默认“只回忆”，**检测到自评虚高时再默认引导重做**。
“检测自评虚高”已经做好了：`GET /api/mistakes/{id}/redo-hint`（`review_lib.redo_overconfident_hint`），前端 `static/app/review-detail.js` 的 `rvfPickDefaultMode`
和 `static/focus.js` 的 `focusPickDefaultMode` 在 `hint` 为真时会优先选重做，**这部分逻辑不用改**。

### 要做的
1. **迁移 82**（`db.py`，函数名 `_apply_redo_default_recall`）：SQLite 不能直接改列默认值，所以：
   `ALTER TABLE users ADD COLUMN redo_pref_tmp TEXT NOT NULL DEFAULT 'recall_only'` → `ALTER TABLE users DROP COLUMN redo_preference` →
   `ALTER TABLE users RENAME COLUMN redo_pref_tmp TO redo_preference`。已有用户统一变成 `recall_only`（这个选项从未发布给用户，没有人主动选过）。
   **幂等**：先 `PRAGMA table_info(users)` 看 `redo_preference` 的 `dflt_value` 已含 `recall_only` 就直接返回，不能把用户之后主动选的 `redo_first` 重置掉；
   如果上次迁移中途留下了 `redo_pref_tmp` 列，要先清掉再来。
2. 前端兜底值全部改成 `recall_only`：`static/app/core-ui.js`（`rvfFetchRedoPreference`、偏好下拉的保存/回滚）和 `static/focus.js`（`focusRedoPreference`）
   里“读失败/非法值”时原来回落到 `redo_first`，现在回落到 `recall_only`。
3. `static/index.html` 账号页的“复习方式偏好”：选项顺序改为 `recall_only`（文案“只回忆（默认）”）、`redo_first`（“重做优先”）；
   说明文字改成：“默认只回忆（自己打分）。系统发现你对某条的自评可能偏高时，会提示并默认改用重做；选‘重做优先’则算法分区每次都先引导默写或限时重做。”
4. `models/review.py` 里的注释同步。
5. **测试**：更新所有期望 `redo_first` 默认值的测试（`tests/test_db.py`、`test_profile.py`、`test_review_feel.py`、`test_review_redo.py`、`test_review_redo_static.py` 的选项顺序）；
   把所有 `schema_version == 81` 的断言改成 82；新增 `tests/test_redo_default_migration.py`：①从 81 形状（默认 `redo_first`）升级后所有人变成 `recall_only` 且列默认值变了；
   ②迁移幂等，且不会重置升级后用户主动选的 `redo_first`；③新注册账号默认 `recall_only`。
6. 升 `core-ui.js`、`focus.js` 的 `?v=`。

---

## ⑧ 更新脚本防呆：应用崩溃循环时，备份失败不该让站长束手无策

### 背景
上一轮更新后应用容器一直崩溃重启（缺文件），`deploy/bin/update.sh` 第一步“自动备份”要进容器执行，于是备份失败、脚本中止——**保护数据是对的**，但站长（非技术背景）只能靠我们现场教他手动冷备份再手动更新。

### 要做的
1. `deploy/bin/backup.sh` / `update.sh`：自动备份前判断 `app` 容器是否处于“运行且健康”。如果不是（崩溃重启、已停止），**自动改走冷备份**：
   `docker compose stop app` → 把 `data/`（含 SQLite 的 `-wal`/`-shm`）`cp -a` 到 `data/backups/cold-<时间>/`（或站长指定目录；**空间不足要先报错**）→ 继续更新；
   输出用中文说明“应用当前没在运行，已改用冷备份：路径…”。冷备份目录不会被每日备份的“保留最近 30 份”清理误删（或在文档里明确）。
2. 新增 `deploy/bin/update.sh --dry-run`：只打印会做什么（是否冷备份、备份到哪、拉哪个提交），不动任何东西。
3. 更新失败自动回滚后，输出要明确告诉站长“数据没动，备份在哪”。
4. 测试：`tests/test_ops_scripts.py` 里已有脚本测试的写法（用假 `docker`/`git` 命令放进 PATH），照着加：应用不健康 → 走冷备份；空间不足 → 中止且不停服务；`--dry-run` 零副作用。
5. 文档：`docs/operations/deploy.md` 加一节“更新卡在自动备份怎么办”。

---

## ⑤ 论坛开关（先冻结，不删）

### 背景
评审建议：用户很少时论坛看起来冷清，还带来审核义务，建议先冻结。**站长还没决定是否真的关**，所以要做的是一个**开关**，默认行为完全不变。

### 要做的
1. 环境变量 `FORUM_ENABLED`（默认 `1`；`0`/`false` 关闭），写进 `.env.example` 和 `deploy/.env.prod.example`，放到 `core/config.py`。
2. 关闭时：侧栏/底栏/手机“更多”/命令面板（`palette.js`）里不出现“讨论区”；总览里与讨论区相关的卡片、小组页里“相关讨论”入口隐藏；
   `/api/forum/*`、`/api/posts*` 等论坛接口统一返回 404（中文 detail“讨论区暂未开放”）；**已有数据一行都不删**，重新打开就原样回来。
   `GET /api/me`（或一个轻量接口）返回 `features: {forum: bool}`，前端据此隐藏，不要靠 404 猜。
3. 榜单、小组、成就等**不依赖论坛**的功能不受影响；依赖论坛数据的统计（例如“本周战报”里的发帖数）关闭时显示 0 或隐藏该行，别报错。
4. 测试：开/关两种配置各一组（接口 404、`/api/me` features、导出/注销在关闭时仍然完整）；前端行为测试（假浏览器）验证入口隐藏。
5. 文档：`docs/features/forum.md` 加“冻结开关”一节。

---

## ⑦ AI 用量分档（max_tokens 按任务）

### 背景
评审指出：多数 AI 入口 `max_tokens` 都给得很大（最大 30000），而线上用的是推理型模型（思考也计入上限），
大多数任务实际只需要几千。额度虽有每人每天上限，但单次上限过大会放大成本和等待时间（最长 120 秒）。

### 要做的
1. 在 `core/config.py` 集中定义每类任务的 `max_tokens`（常量表 + 注释说明依据）：生成变体题、拍照识别、橡皮鸭、薄弱点分析、错因归并、AI 讨论要点、今日一读摘要（`digest.py`）、
   套路归类（`ai.suggest_patterns`，推理型模型要留余量，现在 8000）。**不要凭感觉**：先看管理后台“AI 明细”/`ai_calls` 表里各 `feature` 的 `completion_tokens`，
   按“P95 的 3 倍，且不低于 2000”取值；没有线上数据的就保持现值并在报告里写明“待观察”。
2. `ai.py` 里各函数改读常量，**行为和提示词不变**。
3. 如果输出因 `finish_reason == "length"` 被截断，现在各入口已有统一的“格式不合格 → 502 → 退还额度”处理，确认这一点对每个入口都成立并补测试（用桩模拟截断）。
4. 报告里给一张表：入口、旧值、新值、依据。
5. （可选，做不完不影响交付）AI 回答改成流式（SSE）的**调研结论**写进报告：现有同步路由 + 线程池如何改、前端怎么渲染、额度和退还怎么保持原子；**只写方案，不实现**。

---

## ② 浏览器扩展：在“提交结果页”识别失败，一键录入

### 背景
错题本类产品死于录入麻烦。现在扩展只能在**题目页**点“收录”；更自然的时刻是**刚提交完、看到 WA/TLE 的那一刻**。
目标：在各 OJ 的提交结果页检测到失败判定，弹一个不打扰的“记一笔？”，题名、来源链接、判题类型、**你提交的代码**都预先填好，点一下就写入欧叶OY（走现有 `/api/plugin/...` 接口，需要的话沿用/扩展 `verdict` 字段，A 项已支持）。

### 前置：把扩展源码放进仓库
现在仓库里只有构建好的 `static/downloads/ouye-companion-extension.zip`（20 个文件，目录 `ouye-extension/`），**源码没有进 Git**。第一步：
1. 解压成仓库里的 `extension/ouye-extension/`（保持原结构），加 `extension/build.py`（标准库 zip，确定性输出：固定时间戳、排序），
   生成 `static/downloads/ouye-companion-extension.zip`；加测试：构建出的 zip 与仓库里提交的 zip 逐字节一致（防止源码和下载包漂移）。
2. 扩展的版本号放在 `manifest.json`，每次改动递增，`static/extension.html` 的安装说明里同步写版本。

### 要做的
1. **站点适配器**（`content/sites/*.js` 里各加“结果页”适配）：
   - AtCoder：`/contests/*/submissions/*`（状态：WA / TLE / RE / MLE / CE；页面里有提交代码）。
   - Codeforces：`/contest/*/submission/*` 与 `/problemset/submission/*`（verdict 文本：Wrong answer on test N / Time limit exceeded / Runtime error / Memory limit exceeded / Compilation error）。
   - 力扣中国站：`/problems/*/submissions/*`（解答错误 / 超出时间限制 / 运行出错 / 超出内存限制 / 编译出错）；力扣是单页应用，用 `MutationObserver` 等结果出现再弹。
   - 洛谷：`/record/*`（Wrong Answer / Time Limit Exceeded / Runtime Error / Memory Limit Exceeded / Compile Error）。
   - 牛客：`ac.nowcoder.com/acm/...submission`（尽力而为，拿不准的写进报告，不要硬做）。
2. **判题映射**到我们的 `WA/TLE/MLE/RE/CE`（`error_taxonomy.VERDICTS`）；只对失败判定弹，通过（AC）**不弹**。映射表单独成文件（`lib/verdict.js`），每个站点的原文→code 写成表驱动，方便补。
3. **体验**：右下角一个小条“这次是 TLE，记一笔？[记下] [今天不再提醒这道题]”；点“记下”才发请求；**绝不自动上传**；同一次提交只弹一次（`lib/dedup.js` 的去重沿用，键加上提交号）；
   用户可在扩展设置里关掉“结果页提示”（默认开）。代码字段只取页面上用户自己的提交代码，长度超过接口上限要截断并提示。
4. **隐私**：结果页识别只在本地页面里读 DOM，不向任何第三方发请求；只有用户点“记下”才向欧叶OY 发一次；权限（`host_permissions`/`matches`）只增加上述结果页路径，不要申请更宽的。
5. **测试**：每个站点放一份**真实页面结构的最小 HTML 夹具**（`tests/fixtures/extension/`，你从真实页面简化而来，保留判定和代码所在的节点），
   用假 DOM 跑适配器：识别出 verdict、题名、代码；AC 不弹；SPA 延迟出现的情况；去重。扩展发出的请求体用假 fetch 断言（字段、`verdict` code、截断）。
   **你没有真实账号的站点，夹具来源和“未在真机验证”必须写进报告**，由站长手动验证。
6. 后端：如 `POST /api/plugin/...` 还不接受 `verdict`/`counterexample` 以外需要的字段，在 `docs/features/plugin-api.md` 写清并补测试；**不要放宽鉴权/CSRF/限流**。
7. 文档：`static/extension.html` 的使用说明加“提交后一键记错题”一节（配截图位）；`docs/features/plugin-api.md` 同步。

---

## ③ “复仇成功”卡片

### 背景
评审：情绪价值应该来自**真实的进步**。B 项让我们有了重做数据：`reviews.mode/evidence`、`problems.verdict`。
要生成这样的时刻：“**三周前你在《xxx》上 WA 了，今天一次做对。**”

### 要做的
1. 定义“复仇成功”（写进 `docs/features/` 并在报告里请站长确认阈值）：同一条易错点，**此前至少有一次评分 ≤2（或该题记录时的判题类型是失败类）**，
   现在这一次**评分 ≥4 且模式是 `rewrite` 或 `redo`（实测，不是自评）**，且两次相隔 ≥7 天；每条易错点只触发一次（存一个标记，需要的话用迁移 83，幂等）。
2. 后端：`GET /api/stats/revenge`（最近 30 天的复仇成功列表，含题名、首次失败日期、判题类型、这次的模式与用时、间隔天数）；评分接口成功返回里附一个 `revenge: {…}|null`，
   让前端在评分后立刻盖章庆祝。
3. 前端：①评分成功后，若 `revenge` 非空，在现有“盖章反馈”（`static/review-extras.js` 的印章流程，**沿用，不要写第二套**）上加一句文案和一枚特别的印章
   （“雪耻”之类，文案请给 3 个备选放进报告让站长选）；②总览页加一张小卡“本月复仇成功 N 次”，点开列出；③可生成**分享图**（复用现有 `share_card.py` 的思路，纯服务端绘制，
   不含用户代码，只含题名、天数、判题类型和欧叶OY 的名字）。
4. 隐私：分享图只有用户点“分享”才生成；题名可能含个人信息，生成前显示预览并允许隐藏题名。
5. 测试：定义的各种边界（间隔不足 7 天、自评高分不算、同一条只触发一次、导出/注销包含标记）、接口返回、前端行为（假浏览器）。

---

## ④ 教练 / 小组长“成员薄弱点汇总”页

### 背景
评审：学校 ACM 集训队、算法课是最自然的推广渠道；教练想一眼看到“我的队员整体栽在哪”。我们已有小组（`routers/groups.py`、`groups_lib.py`）和 A 项的错因画像（`GET /api/stats/error-profile`）。

### 要做的（隐私优先，这一项请多留设计时间）
1. **只汇总，不展示个人明细**：小组页新增“薄弱点汇总”（仅组长可见），展示：①全组错因分布（4 大类 + 14 个错因）；②全组判题类型分布；③套路维度：错得最多的 5 个套路和“学习路线”进度；
   ④本周活跃人数、复习完成率。**少于 3 个活跃成员时不显示任何汇总**（防止从汇总反推个人），页面上说明原因。
2. **成员知情**：加入小组时（以及已有成员第一次打开小组页时）要明确提示“组长可以看到全组的汇总统计，看不到你的具体题目和笔记”；
   成员可以在自己的小组设置里**退出汇总**（新增布尔设置，迁移 84 或并入 83，幂等，默认“参与”，在报告里请站长确认默认值）。退出者不计入汇总，也不计入“少于 3 人”的判断。
3. 接口：`GET /api/groups/{id}/weakness-summary`，鉴权必须是**该组组长**（别人 403/404，且不泄露组是否存在）；返回只含聚合数字。
4. 前端：小组页组长视图加一个标签页；图表用现有的横向条（`weakness.css` 的 `.weakness-bar-*` 样式可复用，注意作用域别串到别的页面）；空状态、人数不足、全员退出都要有诚实的文案。
5. 测试：权限（成员/非成员/组长）、人数阈值、退出汇总生效、聚合口径（单测用手算的小数据集）、接口不含任何个人字段（用“扫描响应里不应出现用户名/题名”的断言）、注销账号后汇总不再计入。
6. 文档：`docs/features/groups.md` 加一节，并在“账号与隐私”相关文档里写清数据口径。

---

## ⑥ 手感层第二批

`docs/specs/fog-theme-and-feel.md` 的“第二期”里，Claude 已经做完第一版（`static/feel.css/js`），下面这些**还没做**，请接着做（沿用那份文档的所有规则，尤其：只动 `transform/opacity`、`prefers-reduced-motion` 和触屏要有关闭版本、不引入动画库）：

1. **03 主角卡倾斜 + 反光**：只用在“今日一签”“成就徽章”“本周战报头图”这三处；目标值由鼠标决定，实际值每帧向目标靠拢；最大 ±7°；静止后 rAF 停止。
2. **05 数字滚动**扩展到：薄弱点页的统计数字、掌握度页的总览数字、榜单“我的名次”；柱状/条形图首次进入视口时生长一次（`IntersectionObserver`，每页只播一次）。
3. **06 `Feel.flip()` 接入**：今日复习队列评分一条后其余卡片平滑上移；笔记置顶/取消置顶；（待分类箱 Muse 自己已有动画，不动）。
4. **10 开关 / 勾选 / 输入框**：设置页所有开关改成“水珠滑动”；复选框的勾“画”出来；输入框聚焦下划线从中间展开；
   **只改样式，保持原生 `<input>` 语义和提交行为**；浮动标签只对“新增记录”表单做。
5. **12 其余“正在加载…”文字改骨架屏**（薄弱点页、掌握度页、榜单、小组、今日一读/一签的加载态）和一个进度环（目标卡）。
6. **11 提示条接入**：现有全站提示信息（`#notice` 等）统一走 `Feel.toast`；**盖章完成时的提示条文案前面带 emoji**（如“✅ 复习完成，下次在 6 天后”），普通提示不加 emoji。
7. 测试：`tests/feel_behaviour.cjs` 扩充（倾斜的目标/实际值、数字滚动最终值、FLIP、reduced-motion 全部关闭、静止后无空转 rAF）。

---

## 其他说明
- 以上每项都**不要顺手改别的**；发现别的问题写进报告的“顺便发现”。
- 本轮结束后请再给一份 **汇总 `ROUND3_SUMMARY.md`**：每项的状态（做完 / 部分 / 没做）、迁移号占用情况（82 / 83 / 84）、需要站长在服务器上做的事（例如新增环境变量）、需要站长在真机上验证的清单。
