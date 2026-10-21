# 前端错误上报

用户浏览器里出现 JavaScript 报错时，页面会自动给服务器发一份脱敏的小报告，站长在服务器日志里就能看到。不建表、不入库、没有迁移；日志滚走就没了。

## 怎么看

```bash
docker compose -f deploy/docker-compose.prod.yml logs app | grep oy.client_error
# 只看最近一小时
docker compose -f deploy/docker-compose.prod.yml logs --since 1h app | grep oy.client_error
```

每行形如：

```
2026-10-21 09:30:12,345 oy.client_error {"message":"Cannot read properties of undefined (reading 'x')","source":"review-list.js","line":10,"col":4,"stack":"TypeError: x\n    at f (/static/app/review-list.js:10:4)","route":"#/app","ua":"Mozilla/5.0 ...","build":"103","user":42}
```

| 字段 | 含义 |
| --- | --- |
| `message` | 报错信息，最多 300 字；未捕获的 Promise 拒绝会带前缀 `Unhandled rejection:` |
| `source` | 出错脚本的文件名（不含域名、路径和查询串） |
| `line` / `col` | 行、列 |
| `stack` | 堆栈，最多 1500 字，已去掉域名和 `?v=` 之类的查询串 |
| `route` | 当前页面的路由名，如 `#/app`；不含 id 和参数 |
| `ua` | 浏览器标识，最多 200 字 |
| `build` | `app.js` 的 `?v=` 版本号，读不到则没有这个字段 |
| `user` | 仅当请求带着有效登录会话时才有，是用户数字 id；匿名则没有 |

`build` 小于当前版本说明用户还在跑旧缓存，多半刷新就好。

## 防滥用

- 接口不要求登录（登录前也会出错），但请求体不能超过 4 KB（超出返回 413），字段全部限长限字符，多出来的字段返回 422。
- 每个 IP 每小时最多 20 条，超出静默返回 429。限流在进程内存里，和其他限流一样只适用于单进程部署。
- 浏览器端每个页面加载最多发 5 条、每秒最多 1 条，相同报错只发一次；忽略跨域的 “Script error.”、浏览器扩展（chrome-extension:// 等）造成的报错和 ResizeObserver 提示。

## 隐私声明

报告只含上面表格里的技术信息，不含用户写的错题、笔记、代码或任何输入内容。日志里不记请求头、Cookie 和 IP 地址（反向代理 Caddy 自己的访问日志另算）。有登录会话时只多记一个数字用户 id，便于复现问题；注销账号后这一行日志不会被追改，日志按 Docker 的日志滚动策略自然过期。

报错信息本身来自浏览器，极少数情况下可能夹带页面上的片段（例如解析失败的文本片段），所以日志请只在运维范围内查看，不要公开贴出。
