# 算法错题本 V1

FastAPI + SQLite + 原生网页。

支持邀请码注册、免邀请码体验账号、独立用户数据、题目记录、按易错点
复习、AI 变体题、手动练习结果、密码找回、每日邮件复习提醒。不会执行
用户代码。

## 环境

Python 3.11 或更高版本。

## Windows 启动

在项目目录中执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`：

- 将 `INVITE_CODE` 从 `change-me` 改为自己的内测邀请码。
- 填写 `OPENAI_API_KEY`，启用 AI 生成功能。
- 默认模型是 `gpt-4.1-mini`，可通过 `OPENAI_MODEL` 修改。
- 填写 `SMTP_*`，启用找回密码和每日邮件提醒，见下方"密码找回与邮件提醒"。

然后启动：

```powershell
.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## macOS / Linux 启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env` 后执行：

```bash
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000 。

SQLite 文件和表结构会在第一次启动时自动创建。

## 验证完整闭环

1. 使用 `.env` 中的邀请码注册账号（需要填写用户名、密码和邮箱）。
2. 新增一道题，填写代码、思路和两条易错点。
3. 两条易错点分别出现在“今日复习”中。
4. 给第一条易错点评分，它从今日列表移除，第二条仍保留。
5. 在“全部记录”中查看第一条，确认显示下次日期及复习历史。
6. 点击“生成一道变体题”。
7. 自行解题，保存结果、代码和复盘。
8. 刷新页面，确认变体及练习结果仍然存在。
9. 注册第二个账号，确认看不到第一个账号的记录。

AI 使用真实 OpenAI API，会产生 API 使用费用。
不配置 API Key 时，其余功能仍然可以使用。

## 自动化测试

Windows：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

macOS / Linux：

```bash
.venv/bin/python -m pytest -q
```

测试使用临时数据库，AI 调用被模拟，不会产生 API 费用。

## 调度规则

- 每条易错点独立保存调度状态。
- 新增易错点当天到期，方便立即开始复习。
- 今日列表包含 `due_date <= 用户当地日期` 的全部记录。
- 前端四档评分对应 0、3、4、5。
- 评分小于 3：连续成功次数归零，间隔一天。
- 成功复习：前两次间隔一天、六天，以后使用旧间隔乘旧易度，向上取整。
- 每次评分后按 SM-2 公式更新易度，最低为 1.3。
- 下一次日期从实际评分当天计算。
- 简化版不进行当天重复练习，每条易错点每天最多评分一次。
- 尚未到期的记录可以查看、生成变体，但不能提前评分。
- 变体练习结果不会自动修改易错点的复习日期。
- 每道变体保存一份可更新的练习结果，V1 不保存多次作答历史。

## 连续打卡天数排行榜

登录后可在“打卡排行榜”查看榜单，对应接口为 `GET /api/leaderboard`。
“打卡”只指成功提交复习评分（`POST /api/mistakes/{id}/review`）并写入
`reviews` 的记录，无论评分高低；新增题目、生成变体题或保存变体练习结果
都不算打卡。每条 `reviewed_at` 按所属用户的 `users.timezone` 换算为
自然日，同一天多次复习只计一天。

连续天数从当地今天向前逐日累计，遇到没有复习的日期就停止；今天还没
打卡时，从昨天开始累计。今天和昨天都没打卡时为 0。榜单只列出连续天数
至少为 1 的正式账号，按天数降序、内部用户 ID 升序排列，展示前 50 名。
体验账号不参与排名、不会出现在其他人的榜单中，但仍可查看榜单及自己的天数。

接口返回 `entries`、`leaderboard_size` 和 `me`。`entries` 每项仅含
`rank`（名次）、`display_name`（`用户 #` 加内部用户 ID）、
`streak_days`（连续天数），不返回真实用户名或邮箱；匿名标识中的数字
不是名次，页面分列展示两者。`leaderboard_size` 是当前生效的上榜人数
上限（即 `entries` 最多几条），前端"前 N 名"文案和"是否超出榜单"的
判断都读这个字段，不写死具体数字，避免跟后端配置脱节。`me` 返回自己的
`streak_days`、`rank` 和 `is_trial`：正式账号只要连续天数至少为 1，
就返回全部合格账号中的真实名次，即使超过 `leaderboard_size`；体验账号或
连续天数为 0 时 `rank` 为 `null`，页面分别显示“不参与排名”或“暂无排名”。

## 讨论区

登录后可在“讨论区”自由发帖、评论，不限主题，发布后立即对所有登录用户
可见，不做事前审核。发帖 `POST /api/posts`、评论
`POST /api/posts/{post_id}/comments` 都要求登录且非体验账号，体验账号
能正常浏览（`GET /api/posts`、`GET /api/posts/{post_id}`），但发帖、评论、
编辑、删除都会被拒绝（403），跟体验账号不能购买套餐、不能上排行榜是
同一条口子。

帖子和评论都显示真实 `username`，不做匿名化处理——这跟打卡排行榜的规则
刚好相反：排行榜是纯数据展示，讨论区是真实的社区互动，知道是谁发的、
谁评论的才有意义；这里是邀请码内测的小圈子，不是陌生人社交平台，隐私
顾虑相对小。

作者本人可以编辑、删除自己的帖子或评论（`PUT`/`DELETE
/api/posts/{id}`、`PUT`/`DELETE /api/comments/{id}`），操作别人的资源
一律返回 404（不用 403，避免暴露资源是否存在，跟 `owned_mistake` 等
既有校验函数的风格一致）。删除是软删除：把 `deleted_at` 标记为当前
时间，不做物理删除，对所有人（包括作者自己）都立即不可见，但数据留在
库里；帖子被软删后，它下面的评论不会被逐条标记删除，只是正常业务路径
再也到达不了它们（详情接口先按 `posts.deleted_at IS NULL` 过滤）。对
已软删的帖子发评论会返回 404。

帖子列表 `GET /api/posts` 按发布时间倒序，最多返回 100 条，每条只带
标题、作者、发布时间、评论数，不含正文，避免列表响应过大；详情接口
`GET /api/posts/{id}` 才返回正文全文和完整评论列表。

### 举报与管理后台

登录用户（非体验账号）可以在帖子详情页、每条评论下举报别人（不是自己）
发布的内容，可选填一段举报原因（`POST /api/posts/{post_id}/report`、
`POST /api/comments/{comment_id}/report`）。举报按 `user_id` 限流（默认
每小时 10 次），跟注册/登录那类未登录场景按 IP 限流不同——举报是登录后
的操作，按账号限流更准确，不会因为同一 IP 下的其他人误伤。同一账号对
同一目标只能有一条"待处理"的举报（部分唯一索引保证），重复举报返回
409；如果这条举报已经被处理过，允许再次举报（万一问题复现）。

管理员账号由环境变量 `ADMIN_USERNAME` 指定用户名，是这一阶段唯一的权限
分级方式，不支持多个管理员、不需要额外的登录方式——`current_user()`
在校验会话时顺带算出 `is_admin`（就是"用户名是否等于这个环境变量"），
写进 `/api/me` 的返回值，前端只有这个字段为真时才显示"管理后台"这个
tab。没设置 `ADMIN_USERNAME` 时，任何人都拿不到管理员权限。

管理员登录后能在"管理后台"看到全部待处理举报（`GET /api/admin/reports`），
每条举报带着被举报内容的快照（帖子标题+正文，或评论正文）、作者和举报
人的用户名、举报原因，可以：

- **删除这条内容**（`DELETE /api/admin/posts/{id}` 或
  `DELETE /api/admin/comments/{id}`）：软删除，同时把所有指向这个目标的
  待处理举报一并标记为已处理——内容都没了，举报自然不用再看。如果作者
  在举报处理之前已经自己删除了这条内容，界面会禁用删除按钮，只能选择
  忽略。
- **忽略举报**（`POST /api/admin/reports/{id}/resolve`）：不动内容，仅
  把这条举报标记为已处理，从队列里移除。
- **封禁作者**（`POST /api/admin/users/{id}/ban`）：不能封禁自己（有
  防呆检查），效果见下一节"封禁"。

处理过的举报不会再出现在队列里，但数据留在 `reports` 表中（`resolved_at`
非空即为已处理），没有物理删除。

这一阶段的管理员能力刚好覆盖"处理举报"这一件事；举报之外的场景（比如
凭空封禁一个没有举报记录的账号、或者不通过举报直接删帖）仍然只能手动
执行 SQL：

```sql
-- 软删一条帖子（连带其下评论一起对所有人不可见，见上文说明）
UPDATE posts SET deleted_at = '2026-01-01T00:00:00+00:00' WHERE id = 123;

-- 软删一条评论
UPDATE post_comments SET deleted_at = '2026-01-01T00:00:00+00:00' WHERE id = 456;

-- 封禁一个账号（完全禁止登录，见下文）
UPDATE users SET is_banned = 1 WHERE username = 'someone';

-- 解封
UPDATE users SET is_banned = 0 WHERE username = 'someone';
```

`deleted_at`/封禁的时间戳没有强制格式要求（代码只用它是否为 `NULL` 来
判断，不解析具体值），但建议统一写 UTC ISO 8601 字符串，跟其他时间戳
字段保持一致，方便以后查证。

### 封禁

`users.is_banned` 为真时，账号“完全禁止登录”，两个位置分别生效：

- `POST /api/auth/login`：密码校验通过之后才检查封禁状态（避免向未认证
  的调用方泄露“这个用户名存在且被封禁”这类额外信息），封禁账号即使
  密码正确也会被拒绝，返回 403。
- `current_user()`（所有需要登录的接口都会经过的会话校验）：即使
  session 还没过期，也会在下一次请求时立即失效，不用等 session 自然
  过期才生效，避免账号在使用中途被封禁后还能继续用到会话过期。两种
  情况都返回 401，但错误文案不同（“账号已被封禁，无法继续使用”
  vs “登录已过期，请重新登录”），前端不用改判断逻辑，具体原因走
  错误消息正常显示。

## 数据结构

- users：账号、密码哈希、邮箱（可为空）、时区、上次提醒发送日期、
  是否为体验账号（is_trial）、付费套餐和套餐到期时间、是否被封禁
  （is_banned）。
- plans：套餐周期、每日 AI 额度、整数分价格及启用状态。
- orders：订单金额快照、支付渠道、状态和第三方交易号。
- sessions：会话令牌哈希、到期时间。
- password_resets：密码重置令牌哈希、所属用户、过期时间，用后即删。
- problems：题目名、代码、思路。
- mistakes：易错点、SM-2 状态、下次日期、版本。
- reviews：每次复习评分及下次日期。
- variants：AI 题目及手动练习结果。
- ai_usage：每个用户每日 AI 请求次数。
- posts：讨论区帖子标题、正文、发布/编辑/软删时间。
- post_comments：讨论区评论正文、所属帖子、发布/编辑/软删时间。
- reports：举报目标（帖子或评论二选一）、举报人、原因、处理时间。

时间戳使用 UTC；复习日期使用用户注册时选择的时区。

## AI 行为

默认对接官方 OpenAI；也可以通过 `OPENAI_BASE_URL` 换成 DeepSeek 等其他
"OpenAI 兼容"服务商，同时把 `OPENAI_API_KEY`、`OPENAI_MODEL` 换成对应
服务商的 Key 和模型名即可，代码不用改。具体模型名和价格以服务商官方
文档为准。

密钥仅存在服务端环境变量中。

生成时会发送当前题目名、语言、代码、思路和选中的易错点。
题目保存到 SQLite；输出只作为文本显示。

每个用户默认每天最多尝试生成 10 次（`AI_DAILY_LIMIT`）；体验账号单独走
`TRIAL_AI_DAILY_LIMIT`。已购买且未过期套餐的用户改用该套餐的
`ai_daily_limit`，具体优先级见下方"套餐额度"一节。
调用失败也消耗次数，服务端不自动重试。

V1 没有后台生成队列。如果生成期间服务进程退出，可能需要重新生成，
之前发出的 API 请求仍可能产生费用。

## 免邀请码体验账号

介绍页有一个"免注册体验"按钮，点击后调用 `POST /api/auth/trial`，
不需要邀请码、用户名或密码，服务端自动生成一个 `trial_` 开头的用户名
和随机密码并直接建立登录态，体验流程和正式账号完全一样（记题、复习、
生成变体题），数据是真实写入数据库的。

这是任何人都能触发的公开入口，做了两层限制：

- 建号本身按客户端 IP 限流（`TRIAL_LIMIT`，默认每小时 3 次），防止
  脚本无限刷号。
- 体验账号的 AI 生成额度单独由 `TRIAL_AI_DAILY_LIMIT` 控制（默认
  每天 2 次），跟正式账号的 `AI_DAILY_LIMIT` 互不影响——如果两者共用
  一个额度，等于把 `AI_DAILY_LIMIT` 变成"任何人每天可用次数 × 无限个
  体验账号"。

体验账号没有邮箱，不能绑定邮箱、找回密码或接收复习提醒；界面顶部会
常驻一条提示，说明这是体验账号、数据可能被定期清理。体验账号也不能
购买套餐，`POST /api/orders` 会直接拒绝（403），避免匿名脚本靠无限
建号刷套餐相关的支付流程。

### 定期清理体验账号

`cleanup_trial_accounts.py` 是一个独立脚本，删除创建超过一定天数
（默认 7 天，可用 `--max-age-days` 调整）的体验账号。跟
`send_reminders.py` 一样不在 uvicorn 进程里跑，需要系统的计划任务
定期调用；删除 `users` 表里的一行会级联删掉这个账号名下的全部数据
（题目、易错点、复习记录、变体题、会话），不用手动清理关联表。

先加 `--dry-run` 手动跑一次，确认会删哪些账号：

```bash
.venv/bin/python cleanup_trial_accounts.py --dry-run
```

确认无误后接入计划任务。Windows（任务计划程序，示例每天凌晨 3 点）：

```powershell
schtasks /create /tn "算法错题本清理体验账号" /sc daily /st 03:00 /tr "C:\dev\algorithm-notebook\.venv\Scripts\python.exe C:\dev\algorithm-notebook\cleanup_trial_accounts.py"
```

macOS / Linux（cron，示例每天凌晨 3 点）：

```
0 3 * * * cd /path/to/algorithm-notebook && .venv/bin/python cleanup_trial_accounts.py
```

## 密码找回与邮件提醒

注册时需要填写邮箱。忘记密码时，在登录页点击"忘记密码？"，
系统会发一封重置邮件，链接 30 分钟内有效且只能用一次；重置成功后
会强制退出该账号在所有设备上的登录状态。为避免泄露"这个邮箱是否
注册过"，无论邮箱是否存在，接口都返回同样的成功提示。

这个功能上线之前注册的老账号没有邮箱，登录后页面顶部会出现
"还没绑定邮箱"的提示，绑定后才能使用找回密码和每日复习提醒。

### 配置发信（以 Gmail 为例）

不配置 `SMTP_*` 时，找回密码和复习提醒邮件都不可用，其余功能不受
影响。

1. 给用来发信的 Gmail 账号开启两步验证。
2. 打开 https://myaccount.google.com/apppasswords 生成一个
   "应用专用密码"（16 位，不是登录密码本身）。
3. 编辑 `.env`：

   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USERNAME=你的账号@gmail.com
   SMTP_PASSWORD=刚才生成的16位应用专用密码
   SMTP_FROM=你的账号@gmail.com
   ```

也可以填其他服务商（比如 Resend）提供的 SMTP 地址、端口和账号密码，
代码不区分具体服务商。

### 部署后必须改 PUBLIC_BASE_URL

重置密码邮件里的链接由 `PUBLIC_BASE_URL` 拼接而成，默认是
`http://127.0.0.1:8000`。本机测试可以不改；正式对外访问后必须改成
用户实际访问的地址（带 `https://`），否则邮件里的链接会指向
127.0.0.1，用户点了打不开。

### 每日复习提醒

`send_reminders.py` 是一个独立脚本，检查当天到期的易错点，
给有邮箱且当天还没提醒过的用户各发一封提醒邮件。它不在 uvicorn
进程里跑，需要系统的计划任务每天调用一次。

先加 `--dry-run` 手动跑一次，确认名单符合预期，不会真的发信：

```bash
.venv/bin/python send_reminders.py --dry-run
```

确认无误后接入计划任务。Windows（任务计划程序，示例每天早上 8 点）：

```powershell
schtasks /create /tn "算法错题本每日提醒" /sc daily /st 08:00 /tr "C:\dev\algorithm-notebook\.venv\Scripts\python.exe C:\dev\algorithm-notebook\send_reminders.py"
```

macOS / Linux（cron，示例每天早上 8 点）：

```
0 8 * * * cd /path/to/algorithm-notebook && .venv/bin/python send_reminders.py
```

同一天重复运行是安全的：已经收到过提醒的用户当天不会重复收到；
发信失败的用户不会被标记为已发送，下次运行会重试。

## 导入 shuati-notes 笔记

`import_notes.py` 是独立的批量导入脚本，不需要启动网页服务来执行导入。
数据库和目标账号必须已经存在，可先启动应用建表并注册账号；脚本不会
创建数据库或用户。以下命令在项目目录执行，把 `alice` 换成已有用户名。

先用 `--dry-run` 检查预计新增、重复和失败的记录，不写入数据库：

```powershell
# 单个文件
.venv\Scripts\python.exe import_notes.py --username alice --dry-run "C:\dev\shuati-notes\c\day1.md"

# 目录：递归查找其中的 .md 文件
.venv\Scripts\python.exe import_notes.py --username alice --dry-run "C:\dev\shuati-notes\c"

# glob：加引号，由脚本展开；** 可以跨越子目录
.venv\Scripts\python.exe import_notes.py --username alice --dry-run "C:\dev\shuati-notes\**\day*.md"
```

确认无误后，去掉 `--dry-run` 正式导入；需要指定数据库时加 `--database`：

```powershell
.venv\Scripts\python.exe import_notes.py --username alice --database ".\data\notebook.db" "C:\dev\shuati-notes\**\day*.md"
```

macOS / Linux 把解释器路径换成 `.venv/bin/python`，并替换为本机笔记路径。
末尾可同时提供多个文件、目录或模式，重复文件路径只处理一次。
未传 `--database` 时使用环境变量或 `.env` 中的 `DATABASE_PATH`，默认
为项目内的 `data/notebook.db`。`--database` 的相对路径以命令执行目录
为准；`DATABASE_PATH` 的相对路径以项目目录为准。

脚本按笔记中的 `## 题目 1：题目名` 小节创建题目记录，不把整个
`# Day 1` 标题当成一道题。编号可以不同，中英文冒号均支持。
一个最小示例：

````markdown
# Day 1

## 题目 1：两个整数求和

**思路**
先检查输入范围，再选择能容纳结果的数值类型。

**代码**
```python
a, b = map(int, input().split())
print(a + b)
```

**易错点 / 注意点**
- 忽略输入和结果的数值范围。
- 没有检查输入读取方式。
````

解析与导入规则：

- 字段使用 `**代码**`、`**思路**`、`**易错点**` 或
  `**易错点 / 注意点**`。同名字段不能重复。
- 代码字段必须包含一个带语言标签、正确闭合的代码围栏。支持反引号、
  波浪线围栏和笔记中逐个转义的反引号围栏；保留代码正文缩进，
  `cpp` / `c++` 转成 `C++`，`python` 转成 `Python`。
- 易错点必须是 Markdown 列表；每个最外层列表项独立安排复习，
  续行和嵌套项保留在所属易错点中。每题 1–10 条，每条最多 2000 字符。
  题目名最多 200 字符、语言 40 字符、代码 40000 字符、思路 8000 字符。
- 缺少或留空 `**思路**` 时，使用 `**题目描述**` 代填，并在内容与输出中
  明确标记原笔记未记录思路；两者都没有有效内容时，该文件导入失败。
- `## 知识点 1：...` 等知识点小节跳过并报告，不作为题目导入；
  今日概览、今日总结和标签不会另建记录。完全没有题目或知识点小节的
  文件报错，因此导入整个笔记根目录时，`README.md`、`_sidebar.md`
  也可能计为失败文件；建议用上面的 `**\day*.md` 模式选取日记。
- 按目标用户已有的完整题目名去重，同一用户同名题目直接跳过，
  不更新代码、易错点或已有复习计划；本次批次中的同名题也只新增一次。
  不同用户互不影响。新易错点按该用户时区的导入当天到期。
- 每个文件先完整解析，再用一个事务导入。文件解析或写入失败时，
  该文件不保留部分记录，但其他已成功文件不会回滚。脚本继续处理后续
  文件并汇总结果：全部成功或跳过时退出码为 0，有失败文件为 1，
  初始路径、数据库或账号检查失败为 2。

导入只保存记录，不执行笔记代码、不调用 AI。

## 支付与本地联调

当前提供套餐列表、下单、订单查询、自助全额退款、支付宝当面付及 Mock 回调。
本地 Mock 模式下 `alipay` 和 `wechat` 都由 `MockChannel` 模拟；返回的
`mock://` 二维码地址仅占位，不能扫码付款。默认关闭 Mock；在本地 `.env` 设置：

```dotenv
PAYMENTS_MOCK_ENABLED=1
PAYMENTS_MOCK_SECRET=填写独立随机密钥
```

可用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成密钥。
密钥只用于服务端和本地联调脚本，不发送给浏览器。正式部署保持 Mock 关闭。

以下浏览器及 Mock 接口均需已有的 Session Cookie；POST 还需
`X-CSRF-Protection: 1`。真实支付宝通知入口见“支付宝当面付”一节。

| 接口 | 请求或返回 |
| --- | --- |
| `GET /api/plans` | `{"plans": [...]}`，只包含启用套餐 |
| `POST /api/orders` | 请求 `{"plan_id": 1, "channel": "alipay"}`；返回 `order` 和 `payment` |
| `GET /api/orders` | `{"orders": [...]}`，自己的全部订单，按创建时间倒序，包含 `plan_name` |
| `GET /api/orders/{order_id}` | `{"order": {...}}`，只能查询自己的订单 |
| `POST /api/orders/{order_id}/refund` | 无需正文；退自己的已支付订单，返回 `{"order": {...}}` |
| `POST /api/payments/mock/{channel}/callback` | 原始 JSON 正文加 `X-Mock-Signature`；只能处理自己的订单 |

套餐由管理员直接维护数据库；本阶段没有面向普通用户的套餐编辑接口。
周期或额度变化时新建套餐、停用旧套餐，保留原记录。订单以落库时的价格
作为金额快照，不接受前端自行指定金额，也不会因套餐停用而拒绝已有订单
的有效支付回调。

Mock 回调正文包含以下字段，`amount_cents` 必须与订单金额相同：

```json
{
  "order_id": "下单接口返回的订单号",
  "channel": "alipay",
  "provider_trade_no": "本渠道内唯一的模拟交易号",
  "amount_cents": 990,
  "status": "paid"
}
```

用 `MockChannel(channel, secret).sign_callback(raw_body)` 得到签名头，提交时
必须使用签名时完全相同的原始字节。`verify_callback` 对签名、结构和金额
类型做校验，业务层再核对订单渠道、金额、交易号和归属。

支付回调允许 `pending → paid / failed / closed`；同一终态的相同回调幂等，
不允许通过支付回调在终态之间互相转换。自助退款单独允许 `paid → refunded`。
只有首次转为 `paid` 时更新用户订阅，订单与订阅在同一事务提交：当前
套餐未过期则从 `plan_expires_at` 顺延本次套餐的 `period_days`，允许
提前续费不浪费剩余时长；无套餐或已过期则从支付时刻重新起算，不倒扣
已经过去的时间。不同套餐之间切换按上述规则直接顺延或重新起算，V1
不做按剩余天数折算价格的处理。
渠道发起支付若报错，会返回包含 `order_id` 的 502；订单保留 `pending`，
先轮询该订单，避免渠道实际已受理但客户端重复下单。

### 自助退款

登录后在“我的套餐 → 我的订单”查看全部订单（包括停用套餐的历史订单），
对 `paid` 订单点击“申请退款”，二次确认后立即发起该订单的全额退款。
不需要人工审核，不支持部分退款，退款原因固定为“用户自助申请全额退款”。
退款接口沿用登录和 CSRF 校验：不存在或不属于自己的订单返回 404，
非 `paid` 状态（包括已退款）返回 409，渠道未配置返回 503。

渠道确认退款成功后，在同一个 SQLite 写事务中将订单改为 `refunded`、
写入 UTC `refunded_at`，并清空用户的 `plan_id` 和 `plan_expires_at`。
`paid_at` 保留作历史记录；AI 当天已经使用的次数不退回、不重置，
后续请求按免费额度计算。新库直接建列，旧库启动时按 `ORDER_COLUMN_MIGRATIONS`
检查并补上 `refunded_at`，重复初始化不会重复迁移。

**V1 的已知简化：退款会清空当前整体套餐，不追踪被退订单贡献的时长。**
例如先买 A 又续费 B，随后只退 A 的款，也会清空当前套餐的全部剩余时长，
B 订单仍保留已支付状态。退款金额取该订单保存的整数分金额，不按剩余天数折算，
不补偿退款当天已经使用的 AI 次数。这与 V1 续费价格不按剩余天数折算的规则一致。

支付宝退款使用现有 SDK 的同步 `api_alipay_trade_refund`，不新增退款回调。
每笔订单固定使用 `refund-{订单号}` 作为 `out_request_no`，并发请求和重试
都复用它，避免重复出款。金额用整数除法和余数转为两位小数字符串，避免浮点误差。
直接响应须核对业务成功码、订单号、支付宝交易号及全额退款金额；
`fund_change=N` 表示本次没有新增资金变化，可能是同号重试，不单独视为失败。

已核对本仓库 `.venv` 中 SDK 3.4.0 源码：同步验签后返回内层
`alipay_trade_refund_response` / `alipay_trade_fastpay_refund_query_response` 字典，
无需再次解包。业务字段定义对照支付宝官方 SDK：
[退款响应](https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/response/AlipayTradeRefundResponse.java)
中的 `refund_fee` 是累计成功退款金额字符串；
[查询响应](https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/response/AlipayTradeFastpayRefundQueryResponse.java)
中的 `refund_amount` 是本次请求金额字符串，`refund_status` 表示退款结果；
[退款请求](https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/domain/AlipayTradeRefundModel.java)
明确同一个 `out_request_no` 的重试只退款一次。

若退款请求异常或响应不能确认成功，适配器再调用
`api_alipay_trade_fastpay_refund_query`，核对请求号、订单号、交易号、金额以及
`refund_status=REFUND_SUCCESS`。仍无法确认时返回含 `order_id` 的 502，
保留 `paid` 和套餐，不假定退款成功或失败。可等待至少 10 秒后重新申请同一笔退款；
支付宝查询结果可能有延迟，同号重试可以核实并补齐本地状态。
渠道已退款但本地提交失败时也通过相同方式恢复。
这一阶段没有后台自动对账任务：结果一直未知且用户没有重试时，
渠道与本地状态可能暂时不一致；普通订单查询只读本地状态。

渠道网络调用不占用 SQLite 写锁。本地用 `WHERE status = 'paid'` 和
`cursor.rowcount` 保证只成功迁移一次；并发请求中的后完成者返回 409，
不会再次清空用户之后新购买的套餐。订单变更与套餐收回要么一起提交，要么一起回滚。
Mock 退款仅同步模拟成功，不调用外部渠道，也不需要退款签名。

### 套餐额度

`ai_daily_limit` 已接入 AI 生成配额，按以下优先级判断每日上限：

1. 体验账号（`is_trial`）：始终用 `TRIAL_AI_DAILY_LIMIT`，跟套餐无关。
2. 正式账号且 `plan_id` 非空、`plan_expires_at` 严格晚于当前 UTC 时刻：用对应
   `plans.ai_daily_limit`。
3. 其余情况（未购买套餐或套餐已过期）：退回 `AI_DAILY_LIMIT`。

到期判断只看请求处理当下的 UTC 时间与 `plan_expires_at` 的大小关系，不依赖任何
定时任务提前清空过期用户的 `plan_id`——过期套餐的记录留在 `users` 表里，只是
不再生效。查询套餐与原子扣减配额在同一个 `BEGIN IMMEDIATE` 写事务内完成
（`ai_quota()` 与 `ai_usage` 的 `INSERT ... ON CONFLICT ... WHERE` 共用一个连接），
避免两步操作之间被并发请求或支付回调插队造成套餐信息和实际扣减不一致。

`GET /api/me` 会返回 `plan_name`、`plan_active`、`ai_daily_limit`、
`ai_daily_used`、`ai_daily_remaining` 等字段，“我的套餐”展示这些信息并在退款后刷新。

### 支付宝当面付

使用第三方 [python-alipay-sdk](https://github.com/fzlee/alipay) 3.4.0，
其 `api_alipay_trade_precreate` 支持生成二维码、请求 RSA2 签名和同步响应
验签，`verify` 支持异步通知验签；这里固定已核对的版本。
仅接入 RSA2 公钥模式，暂不接证书模式、微信真实支付或支付主动查单补偿；
自助退款及退款结果查询见上节。

安装 `requirements.txt` 后，关闭 Mock，并按 [.env.example](.env.example)
填写 `ALIPAY_APP_ID`、`ALIPAY_PRIVATE_KEY`（应用私钥）、
`ALIPAY_PUBLIC_KEY`（支付宝公钥）、`ALIPAY_SELLER_ID`（收款商户 PID）和
`ALIPAY_NOTIFY_URL`。五项均必填；配置缺失或无效时下单返回 503。
密钥支持完整多行 PEM 或单引号包裹的单行 PEM（用字面量 `\n` 表示换行）。
`ALIPAY_SANDBOX=1` 使用支付宝沙箱，默认 `0` 使用正式网关；两套账户、
APPID 和密钥不能混用。申请应用、签约当面付和取密钥的位置见配置文件注释。

下单仍为 `POST /api/orders`，`channel` 填 `alipay`。服务端调用
`alipay.trade.precreate`，返回：

```json
{"provider": "alipay", "qr_code_url": "https://qr.alipay.com/...", "redirect_url": null}
```

这里的 `qr_code_url` 是需要编码成二维码的内容，不是二维码图片。
“我的套餐”展示支付链接和支付文本，并轮询自己的订单状态。

`ALIPAY_NOTIFY_URL` 指向公开的 `POST /api/payments/alipay/callback`，
无需 Session Cookie 或 CSRF 头；仅此 POST 路径豁免浏览器 CSRF 检查。
通知必须是 UTF-8 表单编码，适配器验 RSA2 签名、APPID、商户 PID，
业务层继续核对订单金额、渠道和交易号。事务提交后返回纯文本 `success`；
验签或业务处理失败返回非 2xx，不确认付款，以便支付宝重试。
开启 Mock 时该公开通知入口关闭，Mock 回调仍需登录。

`TRADE_SUCCESS` 和 `TRADE_FINISHED` 均视为 `paid`，重复通知不会再次续期；
`TRADE_CLOSED` 映射 `closed`。已付款订单收到全额退款导致的 CLOSED 通知时，
支付回调状态机会拒绝 `paid/refunded → closed`；退款与订阅撤销由上节同步流程完成。
等待付款和未知状态不作为支付成功处理。

本地测试用临时生成的两对 RSA 密钥模拟应用与支付宝，真实执行 SDK 验签；
预下单、退款与退款查询的网络请求被替换，不需要真实商户账号，也不访问支付宝服务器。

## 部署说明

这是面向少量邀请用户的 V1。

部署使用持久化磁盘保存 SQLite 文件，先采用单实例服务。
对外访问配置 HTTPS，并设置 `COOKIE_SECURE=1`，启动时去掉 `--reload`。
前端和 API 保持同源；不要为当前 Cookie 认证随意开放跨域。

登录、注册中的 PBKDF2 密码计算以及 AI 生成目前都在同步 `def` 端点中
执行，会占用 Starlette / AnyIO 的线程池令牌。默认容量是 40 个令牌，
同步依赖等操作也共享这份容量；AI 等待网络返回时仍占用令牌，饱和后
新的同步任务会排队，因此高并发下登录和生成请求可能相互影响。
这一限制见 [Starlette 官方线程池说明](https://www.starlette.io/threadpool/)。
当前仍按 SQLite 单实例、单 worker 部署。负载增长后可评估多 worker
或把密码计算、AI 生成隔离执行；届时需同时处理 SQLite 写入竞争及
下述内存限流的共享问题，不能只增加 worker 数量。

备份时停止服务后复制整个 `data` 目录，
或使用 SQLite 自带的在线备份接口。

不要提交 `.env` 或用户数据库到 GitHub。

题目和易错点支持编辑、删除。删除题目会级联删除它名下的
全部易错点、复习记录和变体题，不可恢复；删除单条易错点
不影响同一道题的其他记录。

注册、登录、忘记密码、重置密码、体验账号创建接口都按客户端 IP 做了
基础防刷：默认 15 分钟内最多 5 次注册尝试、10 次登录尝试、5 次忘记
密码请求、10 次重置密码尝试、每小时 3 次体验账号创建，超过返回 429。
计数只存在单进程内存里，重启即清零；部署多实例或反向代理之后需要
改成共享存储，并确认拿到的是真实客户端 IP。

支付宝接口尚需商户账号开通后完成沙箱/实网联调；V1 不包含多实例部署和
正式的数据库版本迁移工具。
