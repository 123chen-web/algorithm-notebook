
## 免邀请码体验账号

介绍页有一个"免注册体验"按钮，点击后调用 `POST /api/auth/trial`，
不需要邀请码、用户名或密码，服务端自动生成一个 `trial_` 开头的用户名
和随机密码并直接建立登录态，体验流程和正式账号完全一样（记题、复习、
生成变体题），数据是真实写入数据库的。

这是任何人都能触发的公开入口，做了两层限制：

- 建号本身按客户端 IP 限流（`TRIAL_LIMIT`，默认每小时 3 次），防止
  脚本无限刷号。
- 体验账号的每日 AI 限制见[AI 额度与计费](../../static/ai-billing.html)。

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
schtasks /create /tn "欧叶OY清理体验账号" /sc daily /st 03:00 /tr "C:\dev\algorithm-notebook\.venv\Scripts\python.exe C:\dev\algorithm-notebook\cleanup_trial_accounts.py"
```

macOS / Linux（cron，示例每天凌晨 3 点）：

```
0 3 * * * cd /path/to/algorithm-notebook && .venv/bin/python cleanup_trial_accounts.py
```

