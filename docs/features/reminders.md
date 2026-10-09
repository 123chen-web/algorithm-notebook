
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
代码不区分具体服务商。国内网络建议用 QQ / 163 邮箱的 465 端口
（`SMTP_SECURITY=ssl`）；三套配置示例和一键自测
`python check_mail.py --to 你的邮箱` 见 [发信配置与自测](../../docs/operations/mail.md)。

### 部署后必须改 PUBLIC_BASE_URL

重置密码邮件里的链接由 `PUBLIC_BASE_URL` 拼接而成，默认是
`http://127.0.0.1:8000`。本机测试可以不改；正式对外访问后必须改成
用户实际访问的地址（带 `https://`），否则邮件里的链接会指向
127.0.0.1，用户点了打不开。

### 每日复习提醒

新提醒入口 `reminders.py` 支持账号菜单开关（`PUT /api/me/reminder`）与免登录退订。退订使用随机 secret 加用户 ID 的凭证和常量时间比较，有效/无效凭证均返回相同成功结果，避免枚举。

题目清单链接和退订链接都使用 `APP_BASE_URL`（或调用时传入的 `base_url`）。清单链接打开站点首页，登录后选择「今日复习」；当前没有单题直达或 `#/today` 路由。

新入口与旧 `send_reminders.py` 共用 `last_reminder_sent`，同一用户当地同日只在发送成功后记录；发送失败可以重试。新入口持有写事务防并发重复，SMTP 发送占用写锁；发送已成功但进程在落库前崩溃仍可能重发，外部 SMTP 与 SQLite 无法跨系统保证恰好一次。`--dry-run` 不发信、不记录发送成功。旧脚本的微信渠道保持独立偏好，邮件会遵守新开关。站长应只选一个提醒任务，避免不必要的锁等待。

`send_reminders.py` 是一个独立脚本，检查当天到期的易错点，
给有邮箱且当天还没提醒过的用户各发一封提醒邮件。它不在 uvicorn
进程里跑，需要系统的计划任务每天调用一次。

先加 `--dry-run` 手动跑一次，确认名单符合预期，不会真的发信：

```bash
.venv/bin/python send_reminders.py --dry-run
```

确认无误后接入计划任务。Windows（任务计划程序，示例每天早上 8 点）：

```powershell
schtasks /create /tn "欧叶OY每日提醒" /sc daily /st 08:00 /tr "C:\dev\algorithm-notebook\.venv\Scripts\python.exe C:\dev\algorithm-notebook\send_reminders.py"
```

macOS / Linux（cron，示例每天早上 8 点）：

```
0 8 * * * cd /path/to/algorithm-notebook && .venv/bin/python send_reminders.py
```

同一天重复运行是安全的：已经收到过提醒的用户当天不会重复收到；
发信失败的用户不会被标记为已发送，下次运行会重试。

### 微信提醒

每日复习提醒除了发邮件，还可以推送到微信。用户在账号菜单的"微信提醒"
卡片里自行配置，支持 Server酱（SendKey）和 PushPlus（token）两个渠道，
都是微信扫码即可申请的免费服务，不需要自己的公众号。`send_reminders.py`
的选人、按用户时区算当地日期、同一天去重逻辑与邮件完全一致；配了微信
渠道的用户走推送，其余用户继续走邮件，没有邮箱但配了微信的用户也能收到。

SendKey / token 只存在服务器数据库，接口只返回尾号 4 位；连续失败 5 次后
系统自动关闭该用户的微信提醒。"发送测试消息"每人每小时最多 5 次。申请步骤
和故障排查见 [微信提醒](../../docs/operations/wechat-push.md)。

