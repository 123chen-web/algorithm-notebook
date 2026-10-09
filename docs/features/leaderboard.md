
## 连续打卡天数排行榜

登录后可在“榜单”查看连续打卡榜，对应接口为 `GET /api/leaderboard`。
“打卡”只指成功提交复习评分（`POST /api/mistakes/{id}/review`）并写入
`reviews` 的记录，无论评分高低；新增题目、生成变体题或保存变体练习结果
都不算打卡。每条 `reviewed_at` 按所属用户的 `users.timezone` 换算为
自然日，同一天多次复习只计一天。

连续天数从当地今天向前逐日累计，遇到没有复习的日期就停止；今天还没
打卡时，从昨天开始累计。今天和昨天都没打卡时为 0。榜单只列出连续天数
至少为 1 的正式账号，按天数降序、内部用户 ID 升序排列，展示前 50 名。
体验账号不参与排名、不会出现在其他人的榜单中，但仍可查看榜单及自己的天数。

接口返回 `entries`、`leaderboard_size` 和 `me`。`entries` 每项仅含
`rank`（名次）、`display_name`（现有用户名）、
`streak_days`（连续天数），不返回邮箱。体验、封禁、注销或退出公开榜单的账号不上榜。
`leaderboard_size` 是当前生效的上榜人数
上限（即 `entries` 最多几条），前端"前 N 名"文案和"是否超出榜单"的
判断都读这个字段，不写死具体数字，避免跟后端配置脱节。`me` 返回自己的
`streak_days`、`rank` 和 `is_trial`：正式账号只要连续天数至少为 1，
就返回全部合格账号中的真实名次，即使超过 `leaderboard_size`；体验账号或
连续天数为 0 时 `rank` 为 `null`，页面分别显示“不参与排名”或“暂无排名”。


## 昨日之星、本周热门题目与今日一条

“榜单”页在原来的连续打卡榜上方新增三块（接口都要求登录）：

- **今日一条** `GET /api/rank/notice`：管理员手写的一句话（≤ 80 字）+ 可选链接（只允许
  http/https，不带账号密码）+ 展示日期范围（北京时间自然日，含首尾）。管理后台“今日一条”
  卡片可发布、编辑、停用/启用并查看历史（`GET/POST /api/admin/daily-notices`、
  `PUT /api/admin/daily-notices/{id}`，写接口走 CSRF，非管理员 403）。可选每日任务从官方
  Codeforces 元信息缓存补充一道练习链接，保留管理员内容（含停用记录），不抓题面；
  配置步骤见 [推荐题任务](../../docs/operations/recommend.md)，不会自动安装或启用任务。
- **昨日之星** `GET /api/rank/yesterday`：北京时间（Asia/Shanghai）昨天复习次数最多的名次 ≤ 10
  的账号，显示名次、头像、用户名、次数、截至昨日的连续天数和一句模板生成的表扬语
  （`rank_board.praise`，不用 AI）。同一天对同一条错题的多次评分最多算 3 次
  （`SAME_MISTAKE_DAILY_CAP`）；复习 0 次不上榜；次数相同并列同一名次；体验、封禁、注销账号和
  选择“不参与公开榜单”的用户不上榜。自己不在榜上时返回“距离前十还差 M 次”。
- **本周热门题目** `GET /api/rank/hot-problems`：统计截至昨日的 7 个北京日内，被至少
  `HOT_MIN_USERS`（5）个不同用户收录或复习过的题目（只认思路里“题目链接：”开头的行，
  `hot_problems.parse` 是 `static/capture.js` `Capture.parse` 的 Python 移植，由
  `tests/capture_parse_cases.json` 同时喂两边保证一致）。只返回站点、题名、人数和白名单站点
  的规范链接，不含任何用户信息。

隐私：账号菜单里的“参与公开榜单”开关（`PUT /api/me/public-rank`，默认参与，数据库列
`users.public_rank_opt_out`）关闭后，用户不再出现在昨日之星、热门题目统计和原来的连续打卡榜里，
自己仍能看到自己的数据。榜单只暴露用户名、头像、次数、连续天数。

缓存：昨日榜单和热门题目在进程内按北京日缓存（`rank_cache.py`），设置变化、封禁、注销时立即失效，
另有 10 分钟 TTL，保证多进程部署时“不参与”也会很快在所有进程生效。聚合查询次数不随用户数增长。
数据库迁移 9 增加 `users.public_rank_opt_out`、`reviews(reviewed_at)` 索引和 `daily_notices` 表。

