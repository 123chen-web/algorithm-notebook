# 欧叶OY · 给 AI 编码助手的项目约定

> 每个新会话先读完这一页再动手。这里只写**必须遵守**的规矩和最容易踩的坑；
> 设计意图看 `docs/specs/`，数据结构看 `docs/dev/data-model.md`，测试看 `docs/dev/testing.md`。
> 和这里冲突的“顺手改进”一律先停下来问站长。

## 0. 产品底线（改功能前先对照）

- 核心闭环只有一条：**做错 → 30 秒内记下 → 到期重做 → 看见自己变强**。
  新功能要说明它服务闭环的哪一步；说不清的，先不做。
- AI **不替用户生成标准答案**：只讲思路、追问、给最小反例。
- 用户数据只属于用户：导出完整、注销真删（见 `delete_account_data`）。

## 1. 安全规矩（违反任何一条都不能合并）

- **严格 CSP**：`script-src 'self'; style-src 'self'`，没有 `unsafe-inline`。
  - 不写行内 `<script>`、行内 `style="..."`、`onclick=` 等；动态样式用 class 或 CSSOM（`el.style.x = ...`）。
  - 用户文本一律 `textContent` / `createElement`。**禁止**给 `innerHTML`/`outerHTML` 赋值、`insertAdjacentHTML`、`eval`、`new Function`。
  - 第三方渲染（KaTeX、Mermaid）只走 `notes-rich.js` 里已有的 DOMParser + importNode 路径，并保持 `trust:false` / `securityLevel:"strict"`。
- **CSRF**：所有写请求（POST/PUT/PATCH/DELETE `/api/...`）必须带 `X-CSRF-Protection: 1`，前端优先走 `static/app/core-api.js` 里的 `api()`（或经 `configure()` 注入的 api）；确需自己 `fetch` 时必须带上这个头。
- **鉴权**：每个接口显式 `Depends(security.current_user)`（`from core import security`）；按 id 取数据一律用 `owned_mistake` / `owned_problem` 这类“属于本人”的查询，**不能只按 id 查**。
- **请求体大小**：全局 10 MiB（`API_MAX_BODY_BYTES`，由 `ApiBodySizeLimit` 按实际字节计数）。单接口更小的上限自己再卡；读上传用 `await file.read(MAX + 1)` 判断超限。
- **密钥**：只从环境变量读；不进代码、不进日志、不进测试快照。新增环境变量要同时写进 `.env.example` 和 `deploy/.env.prod.example`。
- **SQL**：只用 `?` 占位符；列名/表名需要拼接时只能来自代码里的白名单常量。

## 2. 后端结构

- FastAPI + SQLite（WAL）。`main.py` 只做应用装配（约 270 行）；共享的小东西在 `core/`（配置 `config`、时间 `time`、
  限流 `ratelimit`、鉴权与密码 `security`），输入输出模型在 `models/`，路由在 `routers/`，领域逻辑在顶层模块
  （`scheduler.py`、`review_lib.py`、`accounts.py`、`ai.py`、`ai_limits.py`……）。依赖方向单向无环，**不要在 `routers/` 里 `import main`**。
- 引用别的模块的函数时**调用时取属性**：`import db` 然后 `db.connect()`、`from core import time` 然后 `time.today_for(user)`；
  不要 `from db import connect` 这种提前绑定——测试靠 `monkeypatch.setattr(<定义它的模块>, ...)` 替换实现，提前绑定会让替换失效。
- 数据库：读用 `with db.connect() as conn`；要“先读后写且不能被并发插队”的用
  `db.connect(write=True)`（`BEGIN IMMEDIATE`）。**持有写锁期间不要发网络请求**（AI、邮件、支付）。
- 时间：存储一律 UTC ISO 8601；“今天”按用户时区算（`core.time.today_for(user)`）。
- 同步路由跑在线程池里：AI 等慢调用必须经过 `ai_limits.ai_slot()`（并发名额 + 全站熔断），
  并在调用前扣额度、服务端失败时 `refund_on_server_failure` 退还。
- 限流桶在进程内存里：**只能单进程部署**，不要给 uvicorn 加 `--workers`。

## 3. 数据库迁移

- `db.py` 的 `MIGRATIONS` 追加 `(版本号, 说明, apply 函数)`；版本号由任务单指定，**允许空号，不要为了连续而重排**。
- 不改已发布的 `SCHEMA`、基线和旧迁移；迁移函数里不 `commit` / `rollback` / `executescript`。
- 迁移必须幂等、对旧库可升级；同时在 `docs/dev/data-model.md` 的迁移表里补一行。
- 会丢信息的数据转换（比如把自由文本压成固定 code）要先把原值留在一列里。

## 4. 前端结构

- 原生 JavaScript，**没有构建步骤、没有框架、没有 ES Modules**：经典 `<script defer>`，按功能一个文件，
  对外暴露 `window.X`，依赖通过 `configure()` 注入。主应用在 `static/app/<领域>-*.js`（按 `index.html` 里的顺序加载，
  `static/app.js` 只是启动文件）；跨领域共享只放进很小的 `window.OY`。测试里要读主应用源码用 `tests/static_helpers.py` 的 `app_source()`。
- 改了静态资源要同步提升 `index.html` 里对应的 `?v=NN`（Service Worker 按完整 URL 缓存）；测试里用 `versioned_src("x.js")` 取当前版本，别再写死数字。
- 错因标签只能是 `error_taxonomy.py` 里的固定 code，前端不要自动给记录打别的标签。
- 动效遵守 `prefers-reduced-motion`；颜色只用主题令牌（对比度 ≥ 4.5:1 有测试守着）。
- 单个文件超过 ~600 行、单个函数超过 ~80 行就该拆。

## 5. 测试

- 跑法：`PYTHONUTF8=1 python -m pytest -p no:cacheprovider -q -n auto`；前端行为：`node --test tests/*_behaviour.cjs`。
- **测行为，不测源码长什么样**：新测试通过接口、数据库状态或“假浏览器”（`tests/js_harness.cjs`）断言结果；
  不要再新增“读源码文件、断言里面有某段字符串”的测试——它们让每次重构都要改几十个测试。
- 修 bug 先写一个在修复前会失败的回归测试。
- `parametrize` 的参数不要直接来自 `set` 的遍历顺序（并行收集要求顺序固定）。
- 测试里 PBKDF2 默认降到 1000 次；需要真实强度时加 `@pytest.mark.real_password_hash`。
- 外部服务（AI、邮件、支付、代码运行）在 `tests/conftest.py` 里已被拦截，测试中必须 mock。

## 6. 交付

- 一个任务单一个分支、一份报告：改了什么、为什么、怎么验证、还有什么没做。
- 不确定的产品决定（文案、默认值、阈值）列在报告里交给站长，不要自己拍板。

## 7. 测试临时目录：不要建在项目工作区里

跑 pytest（或任何测试）如果遇到默认系统临时目录不可写/权限受限，**不要**改用
`--basetemp` 或其他方式在项目工作区内（仓库根目录或其任何子目录）创建替代的
临时/缓存目录。

这已经在这个仓库里反复发生：workspace-write 沙盒创建的这类目录会带上沙盒
专属的访问控制列表，沙盒进程结束后，连仓库所有者的正常账号都无法列出、
修改或删除它们（`Access is denied`，甚至 `cmd /c dir` 会报异常的
`File Not Found`），必须由人在提升权限的管理员终端里用
`takeown /F ... /R /D Y` 强制接管所有权才能清掉。这已经发生过不止一次
（`.tmp-alipay-*`、`.tmp-phase4-*`、`.tmp-payment-baseline-*`、
`.pytest-refund-service-tests`、`.ai-boundary-test-*` 等），每次都需要人工
用管理员权限手动清理，不能指望自动清理能成功。

遇到默认临时目录不可用时：**先停下来，在回复里报告具体报的什么错，问清楚
再决定怎么绕过**，不要自己在项目目录内找替代路径重试。
