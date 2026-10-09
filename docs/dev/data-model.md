
## 数据结构

- users：账号、密码哈希、邮箱（可为空）、时区、上次提醒发送日期、
  是否为体验账号（is_trial）、付费套餐和套餐到期时间、是否被封禁
  （is_banned）、头像版本号（avatar_version，头像文件本身存在磁盘上，
  不进数据库）。
- plans：套餐周期、每日 AI 额度、整数分价格及启用状态。
- orders：订单金额快照、支付渠道、状态和第三方交易号。
- sessions：会话令牌哈希、到期时间。
- study_groups：小组名称、唯一邀请码、创建者和创建时间。
- study_group_members：小组成员及加入时间，按小组和用户联合主键去重。
- password_resets：密码重置令牌哈希、所属用户、过期时间，用后即删。
- problems：题目名、代码、思路。
- mistakes：易错点、SM-2 状态、下次日期、版本。
- reviews：每次复习评分及下次日期。
- variants：AI 题目及手动练习结果。
- ai_usage：每个用户每日 AI 请求次数。
- weakness_insights：每个用户最新一次薄弱点分析（结构化内容、材料快照时间），覆盖更新。
- posts：讨论区帖子标题、正文、发布/编辑/软删时间。
- post_comments：讨论区评论正文、所属帖子、发布/编辑/软删时间。
- reports：举报目标（帖子或评论二选一）、举报人、原因、处理时间。
- avatar_reports：头像举报，结构类似 reports 但目标固定是某个用户的
  头像，单独建表的原因见上文"举报头像"一节。

时间戳使用 UTC；复习日期使用用户注册时选择的时区。

### 数据库迁移版本

数据库用 `PRAGMA user_version` 记录版本，启动时自动按顺序执行未完成的迁移
（机制见 [部署与升级](../../docs/operations/deploy.md)）。各版本的含义：

| 版本 | 内容 |
| --- | --- |
| 1 | 历史数据库基线（兼容 `user_version=0` 的旧库） |
| 2 | AI 调用记账（`ai_calls`） |
| 3 | 复习日志补充列（`reviews` 的用时、计划天数等） |
| 4 | 账号安全与隐私（`users` 的 `deleted_at`、`is_admin`、条款同意字段） |
| 5 | 论坛：采纳、有用、AI 要点（`accepted_comment_id`、`comment_votes`、`post_summaries`） |
| 6 | 手动收款与兑换码（`redeem_codes`、`app_settings`） |
| 7 | 未发布（跳过，保留编号） |
| 8 | 论坛：帖子分区（`posts.zone`） |
| 9 | 榜单：公开参与设置（`users.public_rank_opt_out`）、今日一条（`daily_notices`） |
| 10 | 复习手感：暂停（`suspended_at`）、撤销日志、每日上限 |
| 11 | 手动收款登记（`manual_payment_claims`） |
| 12 | 目标卡（`goals`） |
| 13 | 离线评分补交的幂等记录（`review_ops`） |
| 14 | 错题草稿演算区（`mistake_scratch`） |
| 15 | 微信提醒渠道配置（`user_push`） |
| 16 | 改邮箱待确认记录（`email_changes`） |
| 17 | 今日推荐题（`problem_recommendations`） |
| 18 | 错因待补标记（`mistakes.pending_reason`） |
| 19 | 个人简介（`users.bio`，默认空字符串） |
| 20 | 网页导入预览与幂等确认（`import_previews`） |
| 21 | 累计录入题目计数（`users.lifetime_problem_count`，插入触发器，删除不减少） |
| 22 | 手动付款金额、周期、名称快照与实收/流水核账（历史未知保留 NULL） |
| 23 | 私人笔记（`notes`），本人题目关联、置顶、软删除 |
| 24 | 邮件提醒偏好与退订凭证（`users.reminder_opt_in/reminder_token`），沿用 `last_reminder_sent` 同日幂等 |
| 28 | 扩展 token 哈希（`users.api_token_hash`）与题目来源（`problems.source/source_url/statement/difficulty`） |

25–27 为未发布功能撤回后的空缺，不重用。

数据库版本比程序新时程序会拒绝启动；回退程序版本前请先备份。

