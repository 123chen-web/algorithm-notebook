
## 手机安装与离线评分

站点是 PWA：手机浏览器打开后可以"添加到主屏幕"，像 App 一样全屏使用；
Service Worker 会缓存应用外壳，弱网时仍能打开已缓存的页面。

离线时做的复习评分存在本机队列，联网后自动按顺序补交到
`POST /api/mistakes/{id}/review`。补交带 `client_op_id` 保证幂等（重试不会
重复评分），并带做题当时的 `reviewed_at`：连续打卡、热力图按做题当天记，
不占用补交当天的每日上限名额；`reviewed_at` 最多回溯 7 天。服务端规则、
`GET /sw.js` 与 `GET /manifest.webmanifest` 的响应头约定见
[离线评分补交的服务端规则](../../docs/operations/offline-review-sync.md)。


## 手机分享直录（分享到欧叶）

装好 PWA（Android Chrome / Edge）后，系统“分享”面板里会出现“欧叶OY”。在浏览器或力扣 App 里
分享题目页，会直接打开欧叶的新增记录页，题目链接（和页面标题）已经带好，只差用户确认后保存。

- 清单里的 `share_target`：`GET /`，参数 `title` / `text` / `url`（`static/manifest.webmanifest`）。
- 前端 `static/share-target.js`：`parseShare(search)` 是纯函数，取 `url` 参数里的第一个 http(s)
  链接，没有就从 `text` 里找（安卓常把链接放在 `text`）；`javascript:` 等其他协议、带账号密码的链接一律忽略。
  启动时把这三个参数从地址栏去掉（刷新不会重复触发），其他查询参数（如 `email_token`）保留。
- 带入走原有的收录通道 `Capture`（`stashShared` → `take` → `applyIntake`）：未登录时存进
  `sessionStorage`（30 分钟有效），登录后切到“新增记录”并填入题名和来源链接，提示“已带入分享的题目链接”。
  力扣/洛谷等已识别的站点沿用原来的题名与链接清洗；其他站点只填来源链接和分享来的标题。
- 只对已安装的 PWA 生效；iOS Safari 不支持 Web Share Target。永远不会自动提交，也不抓取页面。
- 测试：`tests/share_target_behaviour.cjs`（Node 假浏览器）、`tests/test_share_target.py`。
