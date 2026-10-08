# PWA 客户端接入说明

新增文件（保持相同相对路径放进仓库即可）：

```
static/manifest.webmanifest
static/sw.js
static/pwa-register.js
static/offline-sync.js
static/pwa.css
static/icons/icon-192.png
static/icons/icon-512.png
static/icons/icon-maskable-192.png
static/icons/icon-maskable-512.png
assets/make_pwa_icons.py
tests/pwa_behaviour.cjs
tests/test_pwa_behaviour.py
tests/test_pwa_assets.py
```

图标如需重新生成：`python assets/make_pwa_icons.py`（依赖 Pillow，读取仓库自带的
`assets/fonts/NotoSansSC-Regular-subset.otf`）。

## 1. static/index.html 需要加的标签

`<head>` 里、`<link rel="icon" …>` 之后加：

```html
  <link rel="manifest" href="/static/manifest.webmanifest">
  <meta name="theme-color" content="#c23a2b">
```

样式表区，`<link rel="stylesheet" href="/static/plan.css?v=1">` 之后、print.css 之前加：

```html
  <link rel="stylesheet" href="/static/pwa.css?v=1">
```

脚本区，`<script defer src="/static/plan.js?v=3"></script>` 之后、**app.js 之前**加
（顺序固定：offline-queue.js 必须在 offline-sync.js 前面）：

```html
  <script defer src="/static/offline-queue.js?v=1"></script>
  <script defer src="/static/pwa-register.js?v=1"></script>
  <script defer src="/static/offline-sync.js?v=1"></script>
```

注意 offline-queue.js 此前只在 Node 测试里用、index.html 还没引过，这次一并加上。

## 2. static/app.js 的最少改动（3 处，可直接粘贴）

(1) 启动时配置与注册——在 `window.Onboarding?.configure({ api });`（约 3334 行）后面加：

```js
window.OfflineSync?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
window.PwaRegister?.configure({ version: "1" }); // 发布新外壳（改 CSS/JS 版本号）时 +1
window.PwaRegister?.register(); // 不支持 serviceWorker 时内部静默
```

(2) 登录完成、进入应用时预取今日复习——在 `window.Onboarding?.reset(user);`
（约 839 行）后面加：

```js
window.OfflineSync?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
window.OfflineSync?.prefetch(); // 失败内部静默，离线时 readTodayQueue() 读这份
```

(3) 登出时清理——在 `signedOut()` 里的 `window.Onboarding?.reset();`（约 457 行）后面加：

```js
window.OfflineSync?.reset(); // 内存队列/预取缓存清掉；IndexedDB 里该用户的队列仍保留
```

app.js 改过之后，index.html 里它的版本号 `?v=72` 要 +1。

离线复习界面（后续任务）从 `await OfflineSync.readTodayQueue()` 拿预取队列，
离线评分调 `await OfflineSync.enqueueGrade(mistakeId, grade)`；
联网补交由「pwa-register 派发 pwa:online → OfflineSync 自动 flush()」完成，无需再接线。

## 3. 文字色 / 底色令牌配对清单（请加进 tests/test_contrast_tokens.py）

static/pwa.css 用到的全部配对：

```python
PWA_TEXT_PAIRS = (
    ("--ink", "--soft"),          # 顶部细条 .pwa-net 的文字
    ("--accent", "--soft"),       # 细条 ::before 的状态圆点（同一状态同时有文字，不只靠颜色）
    ("--ink", "--surface"),       # 底部更新条 .pwa-update 的文字
    ("--on-accent", "--accent"),  # 更新条的「刷新」按钮（沿用 style.css 的 button.primary）
)
```

## 4. 依赖的 api() 行为（static/app.js，未改动）

- 2xx：返回解析后的 JSON 对象；
- 非 2xx：抛 `Error`，带数字 `.status`（401/404/409/500 全靠它分发），message 来自 detail；
- 网络失败（fetch 自身 reject）：抛**不带** `.status` 的错误——offline-sync 据此区分
  「网络错误（markFailed 停本轮）」和「HTTP 状态码错误」；
- POST 的 body 是 JSON 字符串，Content-Type 由 api() 统一加。

补交请求体：`{"quality": <1-5>, "client_op_id": <opId>, "reviewed_at": <入队时 ISO 时间>}`，
若预取条目里有 `version` 则一并带上。目标路由 `POST /api/mistakes/{id}/review`。

## 5. 风险最高的三处

1. **后端接口未上线前 flush 会烧 attempts。** ReviewInput 目前只认 quality/version；
   多发的 client_op_id/reviewed_at 若被 pydantic 拒（422），会落入「其他错误 → markFailed
   并停止本轮」，三次后该条变 failed 且不再补交（OfflineSync 未暴露 retryFailed）。
   建议：后端先上线 client_op_id/reviewed_at（幂等），再发布前端；或前端先只接 prefetch，
   暂不在复习界面调 enqueueGrade。
2. **离线首页兜底可能拿不到。** 安装时以 `credentials: "omit"` 抓 `/`，若首页响应总带
   Set-Cookie（或抓到的是未登录落地页），离线导航只能回退到 503 文案或未登录页——
   今日复习的离线可用性主要靠 `readTodayQueue()` 的数据，不依赖 HTML 缓存。
3. **install 等外壳清单最多 8 秒。** 老缓存页面（没引 pwa-register.js）注册的 SW 收不到
   SHELL_LIST，会等满 8 秒并只缓存 `/`；属可接受降级，但首次安装的离线壳可能不全，
   下一次激活新版本（activate 清旧缓存）后自愈。

## 6. 假设（按最保守方案，未逐一确认）

1. 假设后端改造后：带 `client_op_id` 时 `version` 变为可选（现在 ReviewInput 必填 version，
   离线补交只能从预取条目带，带不上就不发）。
2. grade 只支持 1–5（沿用 OfflineQueue 的既有契约），quality 0（完全忘了）不进离线队列。
3. IndexedDB 按用户分库（`ouye-offline-<userId>`），换号时 `forUser` 丢弃的是内存队列，
   各账号持久化数据互不可见。
4. manifest 的 theme_color/background_color 取默认 ink 主题的 --accent（#c23a2b）/
   --paper（#f5f0e6）实际值；qixi 的 accent 跟随场景变化，不适合写进静态 manifest。
   图标红底同样用 #c23a2b（而非 favicon.svg 的 #c0392b），与 theme_color 对齐。
5. `PwaRegister.configure({ version })` 由 app.js 手动维护（与 `?v=` 静态资源版本同节奏），
   没有构建步骤可自动注入。
