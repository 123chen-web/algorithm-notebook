# 浏览器扩展 API

扩展是独立项目，不在主仓代码目录。token 是长期导入凭证，数据库只保存 SHA-256，明文只在签发响应出现。请只配置可信的 HTTPS 站点。

1. 已登录会话调用 `POST /api/users/api-token` 签发或轮换，返回 `{token}`，旧 token 立即失效。
2. 扩展调用 `POST /api/problems/import-from-extension`，带 `Authorization: Bearer <token>` 及 `X-CSRF-Protection: 1`。
3. 已登录会话调用 `DELETE /api/users/api-token` 作废，返回 `{ok:true}`；立即再次导入为 401。注销账号也清空哈希。

请求包含 `source`（leetcode / leetcode-cn / codeforces / nowcoder）、http(s) 的 `source_url`、1–200 字标题、1–100000 字 `content` 题面，以及可选的难度、标签、语言、分区。只建题，不自动建易错点；`tags` 只校验，不落库，题目级标签模型尚未实现。来源字段保存在私人题目记录中。签发/作废要求登录；所有 Bearer 认证失败均为相同的 401，无会话回退。

每用户每小时最多 60 次导入，重新签发 token 不重置限流；超额为 429。复用现有进程内限流，多进程部署会分别计数，这是现有基础设施的限制。

扩展成功提示的「去看看」打开配置的 `homeUrl` 首页。网站是单页应用，没有 `/problems/<id>` 路径；当前不会自动定位导入题目。主站默认不启用 CORS，扩展从 service worker 发请求，manifest 权限保持原样。
