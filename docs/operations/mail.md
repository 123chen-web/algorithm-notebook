# 发信配置与自测（找回密码 / 复习提醒）

找回密码邮件和每日复习提醒都通过 SMTP 发出。配置在 `.env` 的 `SMTP_*` 变量里，改完需要重启服务。

## 先自测，再上线

```bash
python check_mail.py --to 你自己的邮箱@example.com
```

脚本读取 `.env`，**从不打印密码**，依次测试并给每步计时：

1. DNS 解析 → 2. TCP 连接 → 3. TLS 握手 → 4. 登录 → 5. 发送测试邮件

哪一步失败就停在哪一步，并用中文列出最可能的原因。不加 `--to` 时只测到登录，不发信。
全部通过后，去收件箱和**垃圾邮件箱**找「邮件配置自测」。

## 变量说明

| 变量 | 说明 |
| --- | --- |
| `SMTP_HOST` | SMTP 服务器地址。留空表示不启用发信。 |
| `SMTP_PORT` | 端口。不填时：`ssl` 用 465，其余用 587。 |
| `SMTP_SECURITY` | `starttls`（默认，常用 587）、`ssl`（直接加密，常用 465）、`none`（不加密，仅本机调试）。端口与加密方式必须对应：465 ↔ `ssl`，587 ↔ `starttls`。 |
| `SMTP_TIMEOUT` | 连接、握手、登录、发送每一步的超时秒数，默认 15。 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 登录账号（完整邮箱地址）与密码。QQ / 163 / Gmail 都不能用邮箱登录密码，要用下面说的授权码。 |
| `SMTP_FROM` | 发件人地址，不填则用 `SMTP_USERNAME`。QQ / 163 要求与登录邮箱一致。 |
| `PUBLIC_BASE_URL` | 邮件里重置链接的域名，上线必须改成真实的 `https://` 地址。 |

## 三套示例

示例里的密码都是占位符，请换成你自己生成的授权码；不要把真实密码提交进仓库。

### QQ 邮箱（国内推荐）

授权码：QQ 邮箱网页版 → 设置 → 账号 → 开启 POP3/SMTP 服务 → 生成授权码。

```env
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USERNAME=你的QQ号@qq.com
SMTP_PASSWORD=这里填授权码
SMTP_FROM=你的QQ号@qq.com
```

### 163 邮箱

授权码：163 邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启 SMTP 服务 → 新增「客户端授权密码」。

```env
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USERNAME=你的账号@163.com
SMTP_PASSWORD=这里填客户端授权密码
SMTP_FROM=你的账号@163.com
```

### Gmail

应用专用密码：Google 账号开启两步验证后，到 <https://myaccount.google.com/apppasswords> 生成 16 位密码。
Gmail 在国内网络下通常连不上（自测会停在第 2 步「TCP 连接」），建议改用 QQ / 163。

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=你的账号@gmail.com
SMTP_PASSWORD=这里填16位应用专用密码
SMTP_FROM=你的账号@gmail.com
```

## 找回密码为什么"慢"或"收不到"

- 接口只负责登记重置令牌并**立即返回**，发信在后台进行；不论邮箱是否注册，响应完全一样（防止被探测账号）。所以页面提示"已发送"不代表已经送达，真正的发送结果只会出现在服务端日志里：搜索「发送密码重置邮件失败」，日志里的失败原因分四类——连不上 / 认证失败 / 被拒收 / 配置有误。
- 收件人在日志里是脱敏的（`a***@example.com`），不会记录令牌、链接或密码。
- 页面上提交后按钮会倒计时 60 秒，防止连点发出多封邮件。
- 收不到时按顺序排查：先跑 `python check_mail.py --to 邮箱`；再看服务端日志；最后查收件人的垃圾邮件箱。
- 浏览器端提示的文案：「如果该邮箱已注册，邮件会在几分钟内送达；没收到请查看垃圾邮件箱」。
