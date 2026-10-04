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

# 任务 CAP（分支 `cs/cap`）


## 0. 目标与依据
对标笔记：Memos 有**网页剪藏**（把浏览器里的内容直接存成带来源链接的笔记）；我们现在每条错题都要手填分区、题名、思路，**收录门槛偏高**，而数据越多后面所有功能越有用。目标：① 在新增记录页粘贴题目链接，自动识别来源并预填；② 提供一个**书签小工具**，在刷题网站上点一下就带着题名和链接跳到“新增记录”。**不抓取任何外部页面内容**（没有后端请求、没有 SSRF 风险）：只解析链接本身，书签小工具只读取当前页面**已公开显示**的地址和标题，是否使用由用户在自己的浏览器里点击决定。**不改后端、不加迁移。**

## 1. 你负责的文件
`static/index.html` 里“新增记录”页（`#new-page` 或同类容器，先读现有标记）顶部加入链接粘贴区；新增 `static/capture.js` / `static/capture.css`（`window.Capture`）；`static/app.js` 里最小接线（新增记录页进入时初始化、登录后读取待处理的预填、`#/app?new=1…` 的解析）；`static/new-record.js` 如需配合只做小改；测试。**不要碰**其它页面与后端。

## 2. 链接解析（纯函数 `Capture.parse(text) → null | {source, host, id, title, zone, url}`，无网络）
只接受 `http` / `https`；去掉首尾空白与常见中文标点；**拒绝** `javascript:` / `data:` / 过长（>2000 字符）/ 带用户名密码的链接 / 非列表内主机。支持（含 `www.` 与国际站变体）：
- LeetCode：`leetcode.cn` / `leetcode.com` 的 `/problems/<slug>/…` → `title` = `LeetCode · ` + slug 按连字符转标题大小写（`two-sum` → `Two Sum`）；`id` = slug。
- 洛谷：`luogu.com.cn/problem/<P1001|CF1A|…>` → `title` = `洛谷 · P1001`。
- 牛客：`nowcoder.com/practice/<hash>`、`/questionTerminal/<hash>` → `title` = `牛客 · 题目`（没有可读题名，提示用户补题名）。
- Codeforces：`codeforces.com/problemset/problem/<n>/<L>`、`/contest/<n>/problem/<L>`、`/gym/<n>/problem/<L>` → `title` = `Codeforces · <n><L>`。
- AtCoder：`atcoder.jp/contests/<c>/tasks/<t>` → `title` = `AtCoder · <t>`。
`zone` 一律预填“算法”。解析结果里 `url` 去掉跟踪参数（`utm_*`、`spm`、`from` 等，保留路径）。

## 3. 新增记录页的预填
- 页面顶部新增一行：`粘贴题目链接（LeetCode / 洛谷 / 牛客 / Codeforces / AtCoder）` 输入框 + “识别”按钮（粘贴时自动识别；识别失败显示“没认出这个链接，可以直接手填”）。识别成功后**只填空着的字段**：题名（空才填）、分区（默认值才改）、在“思路”最前面加一行 `题目链接：<url>`（该字段为空才加；已有同样的链接不重复加）。**永不自动提交**，也不覆盖用户已写的内容；填完后焦点落在“思路”输入框并播报“已从链接带入题名和链接，请补充你的思路”。
- 保存后的记录里能看到链接即可（它在思路文字里；详情页把以 `题目链接：` 开头的行里的 http(s) 链接渲染成可点击的 `<a rel="noopener noreferrer" target="_blank">`，其它文字照旧 `textContent`——如需改渲染函数，只做这一处最小改动并写测试）。

## 4. 书签小工具（桌面浏览器；手机上隐藏这块并说明“请在电脑上安装”）
- 在链接粘贴区下面一个折叠块“安装书签小工具”：说明三步（显示书签栏 → 把下面的按钮拖到书签栏 → 在刷题网站题目页点它）；一个可拖拽的 `<a class="capture-bookmarklet" href="javascript:…">收录到欧叶OY</a>`，`href` 在运行时用**本站的 `location.origin`** 生成：脚本体只做 `window.open(ORIGIN + '/#/app?new=1&u=' + encodeURIComponent(location.href) + '&t=' + encodeURIComponent(document.title), '_blank', 'noopener')`，不读 cookie、不发请求、不注入页面元素；整段压缩成一行并对引号转义。同一区域再给一个“复制书签代码”按钮（`navigator.clipboard`，失败退回选中文字）。**注意**：我们的页面 CSP 不允许 `javascript:` 链接被导航执行，但“拖到书签栏”不受影响；`<a href="javascript:…">` 在 CSP 下点击无效是预期的——给它加 `onclick` 之外的保护：点击时用 `preventDefault()` 并提示“请把它拖到书签栏，而不是点击”。
- 接收端：应用启动 / 登录后解析 `location.hash`（形如 `#/app?new=1&u=…&t=…`）：校验 `u` 用 `Capture.parse`，`t` 做清洗（去掉站点后缀如 ` - 力扣（LeetCode）`、` - LeetCode`、` | 洛谷`，截到 200 字符，空白折叠），然后切到“新增记录”页并预填（规则同第 3 节；`t` 清洗后的题名优先于链接推出的题名）；顶部提示“已从书签带入，请确认后保存”；**随后用 `history.replaceState` 把参数从地址栏去掉**。用户**未登录**时，把待处理的预填存进 `sessionStorage`（try/catch），登录进入应用后再取出并清除；登出时清除。非法 / 过期 / 重复的参数一律忽略，不报错。
- 预填只在前端内存里完成，不经过后端；不要把链接或标题发给任何接口，除非用户点“保存”提交记录。

## 5. 测试
Node 行为测试：`Capture.parse` 各站点各变体（含大小写、带跟踪参数、末尾斜杠、中文标点、`javascript:` / `data:` / 超长 / 带账号密码 / 未知主机 / 空串）；预填规则（只填空字段、不覆盖、不重复加链接、焦点与播报）；书签脚本生成（`origin` 编码、转义、无外部请求、内容里没有 `cookie` / `fetch` / `XMLHttpRequest`）；哈希接收（合法 / 非法 / 登录前存 `sessionStorage` 再取出 / 登出清除 / `replaceState` 去参数 / 重复参数）；详情页链接渲染只放行 http(s) 并带 `rel`；`localStorage` / `sessionStorage` 抛异常时不崩；静态契约与对比度。变异检查挑 2–3 处（拒绝 `javascript:`、只填空字段、清洗站点后缀）。
