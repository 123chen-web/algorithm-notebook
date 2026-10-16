# 学习路线（套路库搬进主站）

把原来独立的只读页面 `static/patterns.html` 做成主站里的「学习路线」：登录后在左侧栏
「学习」分组进入，可以给每个算法套路标记学习状态，看分类和总体进度，并和自己记过的错题联动。
独立页保留（给没登录的人看、也方便分享），**两边共用同一份数据和同一份渲染代码**。

## 数据模型（迁移 74）

三张表，都在 `db._apply_roadmap`（幂等：`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`）：

| 表 | 主键 | 说明 |
| --- | --- | --- |
| `pattern_progress` | `(user_id, pattern_id)` | `status ∈ {learning, mastered}`；**没有记录 = 未学** |
| `pattern_example_done` | `(user_id, pattern_id, example_key)` | `example_key = "<source>:<id>"`（如 `luogu:P2249`） |
| `problem_patterns` | `(problem_id, pattern_id)` | 一道错题最多挂 3 个套路；随题目级联删除 |

`pattern_id` / `example_key` 不做数据库层约束：套路清单会随内容扩充变化，写死 CHECK 会让
新套路没法勾选。合法性由服务端按启动期读进来的 `static/data/patterns.json` 白名单在接口层校验，
不在白名单就 422。

## 接口（`routers/roadmap.py`）

都要登录，写操作还要 `X-CSRF-Protection: 1` 头（沿用全局中间件），并按用户限流
（`ROADMAP_LIMIT = 120` / 分钟）。

- `GET /api/roadmap` → `{progress, examples_done, mistake_counts}`，只含本人数据；
  已经从清单里下架的套路不会下发（前端就不会画出空白卡片）。
- `PUT /api/roadmap/{pattern_id}` `{status: "none" | "learning" | "mastered"}`，`none` = 删记录回到未学。
- `PUT /api/roadmap/{pattern_id}/examples/{key}` `{done: bool}`，key 必须是该套路 `examples` 里真实存在的。

题目侧：`POST /api/problems` 和 `PUT /api/problems/{id}` 接受可选 `pattern_ids: string[]`
（≤3、去重、必须在白名单，否则 422）。**编辑时不传 = 保持不变，传 `[]` = 清空**。
错题列表 / 详情会带上 `pattern_ids`，供编辑表单作为初始值。

注销（`main.delete_account_data`）会清掉 `pattern_progress`、`pattern_example_done` 和本人题目下的
`problem_patterns`；JSON 导出新增 `roadmap`（进度 + 勾过的例题）和每道题的 `pattern_ids`；
隐私政策补了「学习路线进度」一条，`TERMS_VERSION` 同步到当天。

## 前端

- `static/pattern-render.js`：卡片渲染 + 筛选纯函数，**独立页和主站共用**，不复制两份。
- `static/roadmap.js`：视图。总体进度条 + 每个分类一条进度条；筛选（分类 / 难度 / 状态 / 搜索）；
  薄弱提示（最多 3 条"《X》你错过 N 题但还没学"）；例题全勾完时提示"要不要改成已掌握"（只提示，不自动改）。
- `static/pattern-picker.js`：新增记录页与编辑题目弹窗里的「属于哪个套路」选择器（带搜索，最多 3 个）。
- 卡片内：三态按钮是 `aria-pressed` 的原生 button 组（键盘可用）；模板有 Python / C++ 标签页 + 复制；
  样例输入输出（B 线补的 `sample_input` / `sample_output`）也展示出来。
- 晚到响应按登录代次 + 账号 + 请求序号丢弃（同 `recommend.js` 的 `ticket()` / `current()`）；
  请求失败或离线时按钮**静默回滚**，不弹错误打断阅读。
- `prefers-reduced-motion: reduce` 下不做动效；520px 以下是单列。

## A1 / A2 / A3 完成情况

### A1 数据与接口 —— 全部完成

- [x] 三张表（`pattern_progress` / `pattern_example_done` / `problem_patterns`），迁移号 **74**
      （`grep MIGRATIONS.append db.py` 看到最新是 73，接在其后），幂等。
- [x] `GET /api/roadmap`：`progress` / `examples_done` / `mistake_counts`，只含本人数据。
- [x] `PUT /api/roadmap/{pattern_id}`：`none` / `learning` / `mastered`，`none` 删记录。
- [x] `PUT /api/roadmap/{pattern_id}/examples/{key}`：key 必须在该套路的 `examples` 里。
- [x] `POST /api/problems`、`PUT /api/problems/{id}` 接受 `pattern_ids`（≤3、去重、白名单）；
      编辑时**不传 = 保持不变，传 `[]` = 清空**。
- [x] `pattern_id` 白名单：服务端启动时读一次 `static/data/patterns.json`，不在就 422。
      文件缺失/损坏时白名单为空（只记日志，不打断启动），此时所有写入都会被拒。
- [x] 注销一并删除三张表的数据；JSON 导出加 `roadmap` + 每题 `pattern_ids`。
- [x] 隐私政策补「学习路线进度」一条，`TERMS_VERSION` 改成 2026-10-11（测试 pin 同步）。

### A2 界面 —— 全部完成

- [x] 左侧栏「学习」分组 + 手机「更多」加入口，图标是内联 SVG（沿用现有风格）。
- [x] 总体进度（已掌握 X / 总数、学习中 Y）+ 每个分类一条进度条。
- [x] 筛选：分类、难度、状态、搜索（标题 + 触发信号等全文）。
- [x] 卡片：标题、分类、难度、三态切换（`aria-pressed` 按钮组）、错题数徽标、
      展开后触发信号 / 核心思路 / 模板（Python 与 C++ 标签页 + 复制）/ 复杂度 / 常见坑 /
      易混淆 / 相关套路（可点击跳转）/ 经典例题（勾选框 + 洛谷链接）/ 样例输入输出。
- [x] 例题全勾完时提示"要不要改成已掌握"（只提示，不自动改）。
- [x] 新增记录页 + 编辑题目弹窗的「属于哪个套路」选择器（带搜索，最多 3 个）。
      错题列表/详情带上 `pattern_ids` 作为初始值。
- [x] 薄弱提示：学习路线页顶部，最多 3 条。
- [x] 渲染逻辑抽到 `static/pattern-render.js`，独立页 `patterns.js` 与新视图共用，没有两份。
- [x] 失败/离线静默回滚；晚到响应按登录代次与账号校验丢弃（`ticket()` / `current()`）。
- [x] 手机宽度可用；`prefers-reduced-motion` 下不做动效；严格 CSP（新增脚本无 innerHTML /
      无行内样式 / 无 eval，样式只用主题令牌）。

### A3 测试 —— 全部完成

- [x] `tests/test_roadmap.py`（29 条）：登录与越权、白名单拒绝、三态与清空、例题勾选与取消、
      题目的 pattern_ids（≤3 / 去重 / 非法 / 编辑语义 / 删题级联）、限流、CSRF 头缺失 403、
      注销清理、导出含新字段、迁移幂等、目录读取与 `normalize_pattern_ids` 纯函数。
- [x] `tests/roadmap_behaviour.cjs` + `tests/test_roadmap_behaviour.py`（18 条，假浏览器）：
      渲染与进度、三态切换发 PUT、失败回滚、reset / 换号后迟到响应丢弃、例题勾选与回滚、
      全勾完提示、状态与搜索筛选、请求失败的重试、模板标签页、相关套路跳转。
- [x] `tests/test_roadmap_assets.py`（14 条）：入口、页面骨架、脚本顺序、资源存在、
      CSP 兼容、样式令牌、减少动画、只访问自己的接口与静态清单。
- [x] 静态资源改了就同步版本号（`app.js` 96 → 97，`patterns.js`/`patterns.css` → 2）并同步测试 pin。

## 没做的 / 第二批

- 「你记过 N 道错题」徽标目前只是**展示**，点了不会跳到「全部记录」并按套路筛选——
  那需要列表页支持按套路过滤（服务端过滤 + 列表筛选状态），工作量较大，按任务书放到第二批。
- 例题链接只给洛谷（`https://www.luogu.com.cn/problem/<题号>`）。力扣的题目地址用 slug、
  题号拼不出确定地址，宁可不放链接也不放一个跳不到题目上的链接；力扣题只显示"力扣 704 题名"。

## AI 辅助归类（待分类箱里“让 AI 猜一下”）

- 入口：待分类箱里把卡片丢进桶、弹出的气泡里有“让 AI 帮我猜一下（用 1 次 AI 额度）”，**点一下才调用**。
- 接口：`POST /api/roadmap/ai-suggest {problem_id}`。只猜**自己的算法分区**的题；只把题名、来源链接、前 600 字思路和 150 个套路的“id | 大类 | 名称”清单发给 AI。
- AI 只能从清单里选，最多 3 个：服务端校验每个 id 必须真实存在、去重、理由截断到 60 字；格式不对返回 502，不会写入任何数据。
- 额度：和生成练习题、拍照识别共用同一套规则（写事务里原子扣减，服务端 502/503/504 时退还，体验账号用自己更小的额度）。
- 结果只是**建议**：点哪个才归哪个（走原来的 `PUT /api/problems/{id}/patterns`）；没有 AI 密钥返回 503 且不扣额度。

### 大类、默认入门、模板收起（10/16）

- 套路数据里的 28 个细分类在界面上收成 13 个大类（数组与双指针、二分、链表、栈队列与堆、哈希与字符串、树、图、搜索与回溯、动态规划、贪心与分治、数学与位运算、数据结构与设计、易错陷阱）；
  只是显示和筛选，`patterns.json` 的 category 不变。对应表在 `static/pattern-render.js` 的 `GROUPS`，卡片上显示“大类 · 细分类”。新加的细分类没登记时原样当一个大类。
- 学习路线默认只显示“入门”难度，计数旁提示怎么看进阶和竞赛；从别处跳到某个套路时照常清掉筛选。
- 每个套路的模板代码默认收起，摘要写“先想想触发信号，再展开看模板代码”，避免一上来就背模板。
