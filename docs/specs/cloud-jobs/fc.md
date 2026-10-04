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

# 任务 FC：讨论区主页（分支 `cs/fc`，后端 + 前端由你一人完成）

原本拆成后端（FCB）和前端（FCF）两个并行任务；现在由你一个人做完两部分。两部分的数据契约**逐字一致**，先做后端并写好测试，再做前端。文中提到的“并行任务 / 文件归属”只是提示你别碰其它功能的文件。设计依据：`docs/specs/forum-board/mock/`（高保真效果图 `list.html` 与 `ref-desktop.jpg`、`ref-mobile.jpg`）。数据库迁移编号 **8**。

## 第一部分：后端（FCB）

## 0. 为什么做

讨论区主页现在只有“标题 + 作者 + 时间 + 评论数”，看不出帖子讲什么、有没有解决、谁在等回复，也没有排序 / 筛选 / 分区 / 翻页。前端（并行任务 FCF）要做成：页头读数、搜索 + 筛选标签（全部 / 待回复 / 已解决 / 我的）+ 排序（最新活动 / 最新发布 / 最热门）+ 分区芯片 + 带摘要和状态的帖子行 + 右侧栏。本任务提供它需要的数据，**契约与 FCF 逐字一致**。

## 1. 你负责的文件（并行任务不要碰）

你的：`main.py`（论坛列表 / 发帖 / 改帖 / 帖子详情里的 `zone`）、`db.py`（**只追加迁移 8**）、`sample_world.py`（只改论坛样本数据，见 §6）、新增 `tests/test_forum_board.py`、受影响的旧测试（逐处说明）。**不要碰**：`static/` 下任何文件、README、`thread_summary.py`、兑换码相关代码、`ai.py`。迁移编号必须是 **8**（“复习手感”任务 RV 用 7；你的分支基线的迁移末项是 6，编号有空缺是预期的，合并时我来处理衔接）。

数据库安全：任何实验只用 `DATABASE_PATH` 指向系统临时目录里的新文件；**绝不连接默认的 `data/notebook.db`**（用户的真实数据）。

## 2. 迁移 8（“论坛：分区”）

函数里不 commit、不 rollback、不 `executescript`；不改已发布的 SCHEMA / 基线 / 旧迁移。
- `ALTER TABLE posts ADD COLUMN zone TEXT`（可空；旧帖为 NULL，前端显示“未分区”）；
- `CREATE INDEX idx_post_comments_post_visible ON post_comments(post_id, deleted_at, created_at)`（若已有等价索引则跳过，先 `PRAGMA index_list` 看一眼）。
`tests/test_migrations.py` 把参数化用例延伸到版本 8（升级后旧数据不变、`zone` 为 NULL、可重复启动）。

## 3. 数据契约（必须原样实现）

**`GET /api/posts`**（登录；查询参数全部可选，非法值 → 422 / 400 与现有风格一致）
- `q`：沿用现有（≤200，LIKE 元字符转义，匹配标题或正文）。
- `sort`：`activity`（默认）｜`new`｜`hot`。
- `filter`：`all`（默认）｜`unanswered`｜`solved`｜`mine`｜`participated`。
- `zone`：`PROBLEM_ZONES` 里的一个名字，或 `none`（只看未分区）。不在其中 → 400“分区不存在”。
- `limit`：1–50，默认 20；`offset`：≥0，默认 0。
- 响应：
```json
{
  "posts": [{
    "id": 42, "title": "…", "created_at": "ISO", "user_id": 7, "username": "周知远",
    "avatar_version": 0, "has_avatar": false,
    "zone": "算法",                    // 或 null
    "excerpt": "最近三道二分题都栽在边界上……",   // ≤140 字，见下
    "comment_count": 7,                // 可见评论数（已有字段，含义不变）
    "last_activity_at": "ISO",         // 帖子创建时间与最新可见评论时间的较大者
    "last_commenter": {"user_id": 9, "username": "苏晚", "avatar_version": 1, "has_avatar": true},   // 无评论时为 null
    "participants": [{"user_id": 7, "username": "周知远", "avatar_version": 0, "has_avatar": false}, …],  // 楼主 + 可见评论作者去重，楼主在前，其余按最近参与时间倒序，最多 4 个
    "participant_count": 4,            // 去重后的总人数（可大于 4）
    "has_code": true,                  // 主帖或任一可见评论含围栏代码块（行首 ``` ）
    "solved": true,                    // FB：accepted_comment_id 指向仍可见的评论
    "helpful_total": 6,                // FB：该帖所有可见评论的“有用”票数之和
    "hot": true,                       // 见 §4
    "is_mine": false                   // 帖子作者是不是当前用户
  }],
  "total": 8,                          // 满足当前 q/filter/zone 的帖子总数（用于“加载更多”）
  "has_more": false,
  "counts": {"all": 8, "unanswered": 3, "solved": 3, "mine": 2},        // 不受 q / filter / zone 影响的全站可见帖子统计
  "zone_counts": {"算法": 3, "前端": 1, "none": 2, …}                   // 同上口径；没有帖子的分区可以省略
}
```
`POST_LIST_LIMIT` 常量改为由 `limit/offset` 取代（删除或改作 `limit` 上限 50）；旧调用（不带参数）仍返回前 20 条，字段是新字段的超集。

**`POST /api/posts`** 与 **`PUT /api/posts/{id}`**：请求体多一个可选字段 `zone`（`PROBLEM_ZONES` 之一或 null / 省略 = 未分区；非法 → 400“分区不存在”）。返回的帖子对象含 `zone`。编辑时只有作者能改（现有规则），`zone` 省略表示不改。
**`GET /api/posts/{id}`**：帖子对象多 `zone`。

## 4. 口径细则

- **摘要 `excerpt`**：取正文 → 统一换行 → 把围栏代码块（``` 到下一个 ```，未闭合则到结尾）整体替换成一个空格 → 折叠所有空白 → 用现有 `truncate_text(…, 140)`（表情安全）→ 被截断则补“…”。不含 HTML。结果为空（正文全是代码）时返回空字符串（前端会显示“（代码）”）。
- **待回复 `unanswered`** = 可见评论数为 0；**已解决 `solved`** 同 FB；**我的 `mine`** = 我发的帖；**参与过 `participated`** = 我发的或我有可见评论的帖。
- **热门分 `hot_score`** = 近 7 天内的可见评论数 + `helpful_total`；`hot` = `hot_score >= 5`；`sort=hot` 按 `hot_score` 降序，同分按 `last_activity_at` 降序。7 天窗口用 UTC 时间计算。
- **排序**：`activity` → `last_activity_at DESC, id DESC`；`new` → `created_at DESC, id DESC`；排序必须稳定（同值按 id）。
- **可见性**：已删除的帖子不出现；已删除的评论不计入任何统计、不出现在参与者里；已注销用户的用户名按现状原样显示（不要泄漏原名）。
- **性能**：不得 N+1。用一条（或少数几条）带分组子查询 / CTE 的 SQL 取全部统计，`participants` 用一次窗口查询（或在 Python 里对已取回的评论行做一次分组）一次取齐；给 100 个帖子 × 各 20 条评论的数据量，查询次数不随帖子数增长（测试里用 SQLite trace / 计数器证明）。

## 5. 安全与边界

`sort / filter / zone` 严格白名单；`limit / offset` 边界；`q` 的 LIKE 转义保持；`filter=mine/participated` 只用当前登录用户 id（不接受参数指定别人）；响应里不得出现邮箱、用户 id 以外的私人字段；体验账号可以看列表（现状如此），`is_mine` 对其恒为 false。

## 6. 样本数据（`sample_world.py`）

让样本站的讨论区一打开就像效果图：给现有 3 个帖子分别设 `zone`（算法 / 算法 / 数据库），并**再补几个**覆盖各种状态的帖子（通过 HTTP 客户端，和样本里其它数据一样走接口）：一个已解决且热门含代码的（评论 ≥ 6 条，其中一条被楼主采纳、有几张“有用”票）、一个 0 评论的待回复帖、一个“我发的”帖（样本同学）、前端 / 概率统计 / 系统设计 / 线性代数各一个。作者、时间要有层次（几小时前到几周前；用接口能做到的方式，必要时直接 `UPDATE created_at`，仅限样本库）。`tests/test_sample_world.py` 继续通过并补断言。

## 7. 测试要求

`tests/test_forum_board.py` 至少覆盖：每个 `filter` × `sort` 的组合、`zone` 与 `none`、`q` 与其它条件叠加、分页（`offset/limit/has_more/total`、边界、稳定排序、翻页不重不漏）、`counts / zone_counts` 口径（不受当前筛选影响）、摘要（代码块被去掉 / 未闭合围栏 / 表情不被劈开 / 全是代码 / 超长 / 空白折叠）、`has_code`、`hot` 阈值边界（4 与 5、7 天边界）、参与者（去重、楼主在前、最多 4 个、已删除评论不计、已注销用户）、`last_commenter`、已删除帖 / 评论不可见、`solved` / `helpful_total`（依赖 FB）、`is_mine`、发帖 / 改帖带 `zone`（合法 / 非法 / 省略 / 非作者不能改）、旧调用兼容、查询次数不随帖子数增长。变异检查：挑 3–4 个关键判断（`unanswered` 的评论计数不含已删除、`mine` 的作者校验、`hot` 阈值、`zone` 白名单）临时改坏确认测试会红，再改回；汇报里写结果。

自检：`python -B -m py_compile` 改过的文件；`git diff --check`；pytest 在你这里可能因系统临时目录权限报 `PermissionError`——**遇到就停止需要临时目录的测试，不要换到项目里的目录**，把原样错误贴在汇报里，我来跑。

## 8. 汇报格式

列出：改了哪些文件（每个一句话）；与契约的任何偏差（应当没有）；迁移 8 内容（以及是否等到迁移 5、6、7）；查询次数的证据；旧测试改动清单（逐处说明）；你实际跑过的命令和结果（失败的原样贴）；建议加进 README 的“讨论区”文字（不要改 README）。**不要提交、不要推送、不要切分支。**


## 第二部分：前端（FCF）

## 0. 设计依据（验收标准）

产品负责人嫌讨论区主页“不美观”，要“高效获取知识”和“一点高科技感”，已确认方向（“信号台”）。**高保真效果图就是验收标准**：
- `docs/specs/forum-board/mock/list.html`、`list.css`、`list.js`（浏览器直接打开 `list.html` 即可看；`?theme=qixi` 是栖间主题，`?state=loading` / `?state=empty` 看加载与空状态；窗口缩到手机宽度看手机版）。`list.css` 已按项目规矩写成（只用令牌、无十六进制、无 `!important`、`#forum-page` 作用域），**可以直接并入 `static/forum.css`**，再按真实 DOM 调整；`list.js` 是演示脚本，逻辑可借鉴，正式实现要拆成可测试的纯函数。已附桌面和手机两张截图。
- 它与评论区详情页效果图（`docs/specs/forum-thread/mock/`）同一套语言，已在 `forum.css` 里的 `--mono / --signal / --signal-glow / --signal-line`、`.thread-seg`、`.thread-status`、`.thread-led`、`.thread-post-bar`、`.thread-tag`、`kbd`、`.thread-link` 等直接复用，不要重复定义（若 FA 改了名字，以代码为准）。
- 效果图里的静态 HTML 只是样例：真实 DOM 必须由数据生成，并**保留现有 id**：`#forum-list #forum-list-title #forum-new-post-btn #forum-search-form #forum-search #forum-list-status #forum-posts #forum-compose #forum-compose-form #forum-compose-cancel`。

## 1. 你负责的文件（并行任务不要碰）

你的：`static/forum.css`、新增 `static/board.js`（纯函数 + 控制器，挂在 `window.Board`）、`static/index.html`（只改 `#forum-list` 与 `#forum-compose` 两段 + 新增 `<script>` 标签，改过的静态资源 `?v=` 加一）、`static/app.js`（只改论坛列表 / 发帖相关函数）、`tests/test_forum_page_assets.py`、新增 `tests/board_behaviour.cjs` + `tests/test_board_behaviour.py`、`tests/js_harness.cjs`（需要时最小扩展）、`tests/test_contrast_tokens.py`（新配色对）、受影响的旧断言测试（逐处说明）。**不要碰**：所有后端文件、`static/thread.js`（只调用，不改）、帖子详情页（`#forum-detail`）的任何东西、README（汇报里给建议文字）。

## 2. 数据契约（后端任务 FCB 并行实现，字段缺失要优雅降级）

`GET /api/posts?q=&sort=activity|new|hot&filter=all|unanswered|solved|mine|participated&zone=<分区名|none>&limit=1..50&offset=0..` →
```json
{"posts": [{"id", "title", "created_at", "user_id", "username", "avatar_version", "has_avatar",
            "zone": "算法"|null, "excerpt": "…", "comment_count": 7, "last_activity_at": "ISO",
            "last_commenter": {"user_id","username","avatar_version","has_avatar"}|null,
            "participants": [{"user_id","username","avatar_version","has_avatar"}…≤4], "participant_count": 4,
            "has_code": true, "solved": true, "helpful_total": 6, "hot": true, "is_mine": false}],
 "total": 8, "has_more": false,
 "counts": {"all": 8, "unanswered": 3, "solved": 3, "mine": 2},
 "zone_counts": {"算法": 3, "前端": 1, "none": 2}}
```
`POST /api/posts` / `PUT /api/posts/{id}` 请求体多一个可选 `zone`（`PROBLEM_ZONES` 之一，不选就省略）。出错一律 `{"detail": "中文原因"}`，原样显示在状态区。新增字段缺失时：没有 `excerpt` 就不显示摘要行；没有 `counts` 就不显示读数 / 标签上的数字；没有 `participants` 就不显示头像堆叠；没有 `solved` / `hot` 就不显示对应状态——**不能报错、不能留空壳**。

## 3. 界面规格（以效果图为准，下面是要点与效果图表达不了的行为）

**3.1 页头面板**：`BOARD FORUM` 小标签 + 右侧状态胶囊“N 个帖子”（`total`）；标题“讨论区”；一行说明；“发帖 N”按钮（`N` 键位提示，桌面显示；体验账号隐藏，仍保留现有 `#forum-new-post-btn` 的显示规则）；四格读数：全部帖子 / 等你回复 / 已解决 / 我发的（用 `counts`；效果图里的“今日新帖”没有数据来源，**换成“我发的”**；读数里“等你回复”可点击 = 切到“待回复”标签）。

**3.2 指令栏**：搜索框（左放大镜图标、右 `/` 键位提示；`input` 事件防抖 250ms 后重新请求，回车立即请求；`Esc` 清空并失焦；保留 `#forum-search-form` 的 `submit` 与 `role="search"`）；分段标签 `全部 | 待回复 [n] | 已解决 [n] | 我的`（“我的”对体验账号隐藏；`aria-pressed`）；排序下拉 `最新活动 | 最新发布 | 最热门`；分区芯片（固定顺序：算法 前端 后端 数据库 系统设计 高等数学 线性代数 概率统计，圆点颜色用 `shell.css` 里已有的 `[data-zone]` → `--zone-color`；只显示 `zone_counts` 里 >0 的分区和当前选中的分区；`zone_counts.none > 0` 时多一个“未分区”芯片；再点一次取消）。任何条件变化都从 `offset=0` 重新请求，并用 `#forum-list-status` 播报“当前显示 N 个帖子 / 没有符合条件的帖子”。

**3.3 帖子行**：`ol#forum-posts > li.board-item[data-post-id] > article.board-row`。左：回复数小读数（三种状态：已解决 = 绿色实心 + 勾；`comment_count === 0` = 虚线框写“待回复”；`hot` = 左上红色指示灯；`aria-label` 写成“7 条回复，已解决”）。中：标签行（分区芯片、`✓ 已解决` / `热门` / `含代码` / `等你来回`〔待回复帖〕/ `我发的` / 发帖不足 6 小时的 `刚刚` 胶囊）、标题（`button.board-open`，用 `::after` 撑满整行成为整行可点；点击 = 现有的 `run(() => openForumPost(id))`）、两行摘要（空摘要显示灰色“（代码）”）、元信息行（22px 头像 + 作者名 + 等宽 `#0042` + “2 天前发布” + “↩ 苏晚 · 16 小时前”〔最后回复〕）。右：参与者头像堆叠（最多 4 个，`+N` = `participant_count - participants.length`，`aria-label="N 人参与"`）。**头像必须和文字垂直居中对齐**（现状头像压在标题与作者行之间，是个视觉缺陷）。时间用相对时间：`Board.relativeTime(iso, nowMs)` → `刚刚`（<1 分钟）/`N 分钟前`/`N 小时前`/`N 天前`（<30 天）/ 否则 `YYYY-MM-DD`（用户时区）；元素带 `title` = 现有 `timestamp()` 的绝对时间；`<time datetime>`。`restoreForumListPosition` 改为聚焦该帖的 `.board-open`（找不到仍落在 `#forum-list-title`）。

**3.4 桌面右侧栏（≥1100px）**：`PULSE · 论坛脉搏`：大数字“N 个帖子还没人回复 → 去帮忙”（点击 = 切到“待回复”）+ 三行读数（已解决 / 全部帖子 / 我发的，用 `counts`）；`ZONES · 分区` 列表（同分区芯片，可点击筛选）；`ASK · 好问题模板`（三条：我想做什么 / 我已经试过什么 / 具体卡在哪一步）+ “发帖时一键套用”（打开发帖页并插入模板，体验账号隐藏）；键位提示行。<1100px 隐藏右侧栏。

**3.5 手机（≤600px）**：页头压缩（隐藏说明文字，“发帖”与标题同一行，读数一行四格），指令栏：搜索独占一行，标签与排序同一行，分区芯片横向滚动；**手机首屏就要看到第一条帖子**；帖子行为两列（52px 读数 + 内容），隐藏参与者头像堆叠；320 / 360 / 390 宽度不得出现横向滚动。

**3.6 状态**：加载中显示 4 条骨架行（`aria-hidden`，`#forum-list-status` 播报“正在加载帖子列表…”，闪烁动画只在 `prefers-reduced-motion: no-preference`）；出错显示错误文字 + “重试”按钮；一个帖子都没有（无筛选）= 印章空状态“还没有帖子，来发第一条吧” + 发帖按钮（体验账号只显示文字）；筛选后为空 =“没有符合条件的帖子，换个筛选，或者清除筛选”+ 按钮（重置标签 / 分区 / 搜索）。列表末尾：`has_more` 时显示“加载更多（还有 N 个）”按钮（追加，不重置；焦点留在按钮上，完成后播报“已加载 N 个”），否则显示“已显示全部 N 个帖子”。

**3.7 记忆与守卫**：标签 / 排序 / 分区三个选择存 `localStorage`（键 `forum-board:v1`，读写都包 try/catch，取不到就用默认值；**搜索词不存**）。所有请求用代际计数器丢弃迟到响应（沿用 `forumListGeneration` 写法），并受 `sessionEpoch` 守卫（登出再登录别的账号后迟到的响应一律丢弃）；从帖子详情返回列表时保留当前选择和已加载的内容（不要强制重置），仍然按现有逻辑刷新一次数据。

**3.8 键盘**（仅当论坛列表可见、焦点不在 `input / textarea / select / [contenteditable]`、没有 Ctrl/⌘/Alt、没有打开的对话框或命令面板时生效）：`/` 聚焦搜索；`J` / `K` 在可见帖子间移动（聚焦该行的 `.board-open`，加 `is-selected`，居中滚动）；`Enter` 打开（按钮原生行为）；`N` 聚焦“发帖”按钮（非体验账号）。先 `grep` 现有全部 `keydown` 监听，不能与 Ctrl K 命令面板、专注模式键位、FA 在帖子详情页注册的 J/K/R 冲突（详情页可见时列表的键位不生效，反之亦然）。

**3.9 发帖页（`#forum-compose`）**：沿用评论区详情页写回复的“控制台”风格：顶栏 `> 发布新帖子`；标题输入（`maxlength=200`，右侧字数）；**分区选择**（8 个分区芯片，单选，可不选，选中态与分区圆点同色系）；正文 `textarea`（`maxlength=8000`）；工具栏：表情（沿用 `EmojiPicker.attach`）、“代码块”（在光标处插入 "```\n\n```" 并把光标放在中间；有选中文字则包住它）、“套用好问题模板”（正文为空直接填入；不为空则追加在末尾并先确认一次）、`编辑 | 预览` 标签（预览用 `Thread.renderBody` 渲染，缺失时退回纯文本换行）、字数条 `n / 8000`；“发布 Ctrl ⏎”按钮（`Ctrl/⌘ + Enter` 提交）+ “取消”。模板文本（原样）：`【我想做什么 / 题目是什么】\n\n【我已经试过什么】\n\n【具体卡在哪一步（可贴代码或报错）】\n```\n\n```\n`。**草稿自动保存**：`input` 后防抖 800ms 写 `localStorage`（键 `forum-draft:v1:<用户id>`，含标题 / 正文 / 分区；try/catch）；再次打开发帖页时若有草稿，在表单上方显示一行“已恢复上次未发布的草稿 · [清除]”；发布成功或点“清除”后删除；登出时不保留别人的草稿（键里带用户 id，且登出时不需要清除，但读取必须校验用户 id）。提交：禁用按钮防重复提交，请求体带 `zone`（未选则省略），出错把 `detail` 显示在表单下方状态区（`role="status"`），成功后行为与现在一致（保持现有的跳转 / 刷新逻辑）。

**3.10 样式规矩（项目有测试守着）**：只用主题令牌，不写十六进制颜色、不用 `!important`；`backdrop-filter` 只能出现在 `(hover: hover) and (pointer: fine)` 的块里；不能有内联 `style=""` 属性或内联脚本（CSP `script-src 'self'; style-src 'self'`；用 `element.style.setProperty` 之类 CSSOM 是允许的）；用户可控文字一律 `textContent`；ink 与 qixi 两个主题都要读得清；触控目标 ≥ 40px（芯片 ≥ 32px）；所有新文字 / 底色配对对比度 ≥ 4.5:1（大字 3:1），把新配对加进 `tests/test_contrast_tokens.py`（两个主题都算）；动效只在 `prefers-reduced-motion: no-preference` 下启用；`static/style.css` 里原有的 `.record-button`（错题记录用）不要动，帖子不再使用它。

## 4. 测试要求

1. `tests/board_behaviour.cjs` + `tests/test_board_behaviour.py`（照 `test_widget_behaviour.py` 的写法：`node --test`，兼容 `ℹ fail 0` 与 TAP `# fail 0`，并断言通过数下限）。覆盖至少：`relativeTime` 各边界（59 秒 / 60 秒 / 59 分 / 23 小时 59 分 / 29 天 / 30 天 / 未来时间 / 非法输入）；状态 → 查询串（只带非默认值、`zone=none`、编码）；响应 → 视图模型（已解决 / 待回复 / 热门 / 我发的 / 刚刚 的判定、字段缺失的降级、`+N` 计算）；防抖与迟到响应丢弃（快速连续改条件只采用最后一次；登出再登录后旧响应不写入）；分页追加与 `has_more`；键盘门控（输入框里不触发、带修饰键不触发、对话框打开不触发、列表不可见不触发、详情页可见不触发）；`localStorage` 读写失败（抛异常）时不崩；草稿保存 / 恢复 / 清除 / 用户 id 不匹配时忽略；模板插入与代码块插入（光标位置、选中包裹、空正文与非空正文）；预览渲染的退路；重复点击发布只发一次请求。能用变异检查验证“这条测试真的会红”的，请自己试一两处并在汇报里写结果。
2. 更新受影响的旧字符串断言测试（`test_forum_page_assets.py` 等），**逐处说明为什么改**；不要为了让测试变绿而放宽断言。
3. 自检：`node --check` 所有改过的 JS；`git diff --check`；pytest 在你这里可能因为系统临时目录权限报 `PermissionError`——**遇到就停止需要临时目录的测试，不要换到项目里的目录**，把原样错误贴在汇报里，我来跑；Node 测试不需要临时目录，请全部跑通。数据库安全：任何实验只用临时库，**绝不连接默认的 `data/notebook.db`**。

## 5. 汇报格式

列出：改了哪些文件（每个一句话）；与效果图的偏差及原因；新增的 id / 类名 / 对外函数（`window.Board` 的 API）；旧测试改动清单；你实际跑过的命令和结果（失败的原样贴）；建议加进 README 的“讨论区”文字（不要改 README）。**不要提交、不要推送、不要切分支。**
