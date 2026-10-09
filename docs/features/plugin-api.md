# 浏览器扩展 API

扩展是独立项目，不在主仓代码目录。token 是长期导入凭证，数据库只保存 SHA-256，明文只在签发响应出现。请只配置可信的 HTTPS 站点。

1. 已登录会话调用 `POST /api/users/api-token` 签发或轮换，返回 `{token}`，旧 token 立即失效。
2. 扩展调用 `POST /api/problems/import-from-extension`，带 `Authorization: Bearer <token>` 及 `X-CSRF-Protection: 1`。
3. 已登录会话调用 `DELETE /api/users/api-token` 作废，返回 `{ok:true}`；立即再次导入为 401。注销账号也清空哈希。

请求包含 `source`（leetcode / leetcode-cn / codeforces / nowcoder）、http(s) 的 `source_url`、1–200 字标题、1–100000 字 `content` 题面，以及可选的难度、标签、语言、分区。只建题，不自动建易错点；`tags` 只校验，不落库，题目级标签模型尚未实现。来源字段保存在私人题目记录中。签发/作废要求登录；所有 Bearer 认证失败均为相同的 401，无会话回退。

每用户每小时最多 60 次导入，重新签发 token 不重置限流；超额为 429。复用现有进程内限流，多进程部署会分别计数，这是现有基础设施的限制。

扩展成功提示的「去看看」打开配置的 `homeUrl` 首页。网站是单页应用，没有 `/problems/<id>` 路径；当前不会自动定位导入题目。主站默认不启用 CORS，扩展从 service worker 发请求，manifest 权限保持原样。

## 扩展 v2 新增接口

全部使用同一个 Bearer token（`Authorization: Bearer <token>` 与 `X-CSRF-Protection: 1`），只能访问本人数据，别人的数据一律 404。

| 接口 | 说明 |
| --- | --- |
| `POST /api/problems/import-from-extension` | `source` 现在还接受 `luogu`、`atcoder`（只允许各自官方域名 `luogu.com.cn`、`atcoder.jp`；原有来源规则不变）。新增可选字段 `pending_reason`（1–500 字）：有则建题后再建一条带这句错因的错题，响应多一个 `mistake_id`（没有则为 `null`） |
| `GET /api/problems/by-source?source=&source_id=` | 按来源和题目标识查询本人是否已收录，返回 `{exists, problem_id}`。标识规则：洛谷、Codeforces 大写（Codeforces 为 contestId+题号，如 `1730A`），力扣为 slug，牛客为数字 id，AtCoder 为 task id；服务端从已保存的 `source_url` 推出标识来比较，不需要新增列 |
| `POST /api/problems/{id}/mistakes` | 给本人已有的题追加一条错因，请求 `{reason(1–500 字), quick}`，成功 201 返回 `{id, problem_id, status:"recorded"}`；不影响该题已有的复习计划 |
| `GET /api/review/due-count` | 今天到期且未暂停的易错点数，返回 `{due_count}`（不含每日复习上限的折算） |

限流：查询类 300 次/小时/用户，追加错因 120 次/小时/用户，导入沿用 60 次/小时/用户；超出返回 429。
