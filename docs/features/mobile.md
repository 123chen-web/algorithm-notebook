
## 手机安装与离线评分

站点是 PWA：手机浏览器打开后可以"添加到主屏幕"，像 App 一样全屏使用；
Service Worker 会缓存应用外壳，弱网时仍能打开已缓存的页面。

离线时做的复习评分存在本机队列，联网后自动按顺序补交到
`POST /api/mistakes/{id}/review`。补交带 `client_op_id` 保证幂等（重试不会
重复评分），并带做题当时的 `reviewed_at`：连续打卡、热力图按做题当天记，
不占用补交当天的每日上限名额；`reviewed_at` 最多回溯 7 天。服务端规则、
`GET /sw.js` 与 `GET /manifest.webmanifest` 的响应头约定见
[离线评分补交的服务端规则](../../docs/operations/offline-review-sync.md)。

