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

## 数据结构

- users：账号、密码哈希、邮箱（可为空）、时区、上次提醒发送日期、
  是否为体验账号（is_trial）。
- sessions：会话令牌哈希、到期时间。
- password_resets：密码重置令牌哈希、所属用户、过期时间，用后即删。
- problems：题目名、代码、思路。
- mistakes：易错点、SM-2 状态、下次日期、版本。
- reviews：每次复习评分及下次日期。
- variants：AI 题目及手动练习结果。
- ai_usage：每个用户每日 AI 请求次数。

时间戳使用 UTC；复习日期使用用户注册时选择的时区。

## AI 行为

默认对接官方 OpenAI；也可以通过 `OPENAI_BASE_URL` 换成 DeepSeek 等其他
"OpenAI 兼容"服务商，同时把 `OPENAI_API_KEY`、`OPENAI_MODEL` 换成对应
服务商的 Key 和模型名即可，代码不用改。具体模型名和价格以服务商官方
文档为准。

密钥仅存在服务端环境变量中。

生成时会发送当前题目名、语言、代码、思路和选中的易错点。
题目保存到 SQLite；输出只作为文本显示。

每个用户默认每天最多尝试生成 10 次。
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
常驻一条提示，说明这是体验账号、数据可能被定期清理。

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

V1 暂不包含支付、多实例部署和正式的数据库版本迁移工具。
