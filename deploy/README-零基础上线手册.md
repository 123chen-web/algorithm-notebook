# 欧叶OY：零基础上线手册

> 给完全不懂服务器的站长看的。照着一步一步做，**只复制粘贴命令**就行。
> 每一步都写了：在哪里敲、敲什么、看到什么算成功、失败怎么办。
>
> ⚠️ 有几处标了「未经真机验证」：意思是这套流程是在没有 Docker 的电脑上写出来的，
> 逻辑都人工检查过，但没有在真实服务器上完整跑过一遍。做到那一步时多留心，
> 报错信息复制下来问技术支持。

**先看总览（预计 1–2 小时）：**

| 步骤 | 做什么 | 在哪里做 |
|---|---|---|
| 1 | 买一台香港轻量云服务器 | 云厂商网页 |
| 2 | 把域名指向服务器 IP | 域名服务商网页 |
| 3 | 第一次登录服务器 | 你自己电脑的终端 |
| 4 | 把代码拉到服务器上 | 服务器里 |
| 5 | 一键装 Docker、开防火墙、配自动备份 | 服务器里 |
| 6 | 创建并填写 `.env`（密钥只在这里填） | 服务器里 |
| 7 | 启动服务，检查 `/healthz` | 服务器里 |
| 8 | 验证 HTTPS（小锁） | 浏览器 |
| 9 | 把旧数据库搬上来（已有数据的站长） | 你自己电脑 + 服务器 |
| 10 | 日常运维：看状态、备份、更新 | 服务器里 |

---

## 第 1 步：买服务器

**在哪里做：** 云厂商官网（腾讯云、阿里云、华为云等都行，选"轻量应用服务器"）。

**怎么选：**

- 地域：**香港**（离内地近，访问快；且不需要备案）。
- 系统镜像：**Ubuntu 22.04** 或 **Ubuntu 24.04**（64 位）。
- 配置：**2 核 2G** 内存（V1 单实例够用）。
- 带宽：按量或固定带宽都行，V1 用户少，3–5M 足够。
- 登录方式：选"设置密码"，设一个足够复杂的 root 密码（密码管理器存好）；
  有"密钥对"选项的也可以选密钥，更安全（见《上线安全清单》）。

**看到什么算成功：** 云厂商控制台显示服务器"运行中"，并给了一个公网 IP 地址。
把它记下来，后面叫它 `<服务器IP>`。

**失败怎么办：** 买的时候卡在实名认证/支付——按页面提示完成即可，和本手册无关。

---

## 第 2 步：域名解析（把域名指向服务器）

**在哪里做：** 你买域名的服务商网站（比如阿里云万网、腾讯云 DNSPod、Cloudflare）。

**敲什么（其实是网页上点）：**

1. 找到"域名解析" / "DNS 解析"。
2. 添加一条记录：
   - 记录类型：`A`
   - 主机记录：`@`（表示裸域名，如 `notebook.example.com` 的根；想用 `www` 开头就填 `www`）
   - 记录值：填第 1 步记下的 `<服务器IP>`
   - TTL：默认（一般 600 秒）
3. 保存。

**看到什么算成功：** 在你自己电脑上打开终端（Windows 用 PowerShell），执行：

```powershell
nslookup 你的域名
```

返回的地址里有 `<服务器IP>` 就算成功。刚添加可能要等几分钟到几十分钟才生效，
不等也可以先继续第 3 步，但**第 8 步申请 HTTPS 证书时必须已经生效**。

**失败怎么办：** `nslookup` 返回的还是旧 IP——等 10 分钟再查；确认 A 记录的值填对了，
主机记录 `@` 和 `www` 别搞混。

---

## 第 3 步：第一次登录服务器

**在哪里敲：** 你自己电脑的终端（Windows 打开 PowerShell，Mac 打开"终端"）。

```powershell
ssh root@<服务器IP>
```

第一次会问 `Are you sure you want to continue connecting?`，输入 `yes` 回车，
再输入第 1 步设的 root 密码（输入时不显示，输完回车）。

**看到什么算成功：** 命令行前面变成 `root@xxx:~#`，说明你已经在服务器里面了。
后面第 4–7 步的命令**都是在这台服务器里敲**，不是在你自己电脑上。

**备选：** 如果 `ssh` 连不上，云厂商网页上一般有"网页终端 / VNC 登录"，
点进去也能操作服务器，效果一样。

**失败怎么办：**

- `Connection timed out`：检查 IP 对不对、服务器是不是"运行中"、云厂商的安全组
  有没有放行 22 端口（轻量服务器一般默认放行）。
- `Permission denied`：密码输错了（注意大小写），或去控制台重置 root 密码后重试。

---

## 第 4 步：把代码拉到服务器上

**在哪里敲：** 服务器里（`root@xxx:~#`）。

```bash
apt-get update -y && apt-get install -y git
git clone https://github.com/123chen-web/algorithm-notebook /srv/algorithm-notebook
cd /srv/algorithm-notebook
git log --oneline -1
```

**看到什么算成功：** 最后一条命令输出一行类似 `addbbef ...` 的版本信息，
且 `ls` 能看到 `main.py`、`deploy/` 等文件。

**失败怎么办：** `git clone` 报网络错误——服务器到 GitHub 的网络可能不稳，
多试两次；一直不行就问技术支持要一份代码压缩包，解压到 `/srv/algorithm-notebook`。

---

## 第 5 步：一键初始化（装 Docker、防火墙、自动备份）

**在哪里敲：** 服务器里，`/srv/algorithm-notebook` 目录下。

```bash
sudo bash deploy/bin/install.sh
```

这个脚本会做 4 件事（重复运行也没事，不会装两遍）：

1. 安装 Docker 和 Docker Compose 插件（要几分钟，别关窗口）。
2. 开防火墙，只放行 **22（SSH）、80（HTTP）、443（HTTPS）** 三个端口。
   ⚠️ 如果你的 SSH 端口不是 22，脚本开防火墙前会提醒你，先按提示放行你的端口，
   否则会被锁在服务器外面，只能去云厂商网页终端救。
3. 建好数据目录 `/srv/algorithm-notebook/data` 并授权（容器重建、升级都不会丢数据）。
4. 加一个 cron 定时任务：**每天凌晨 02:30 自动备份数据库并校验**。

**看到什么算成功：** 最后输出"初始化完成！"。

**失败怎么办：** 把报错信息完整复制下来问技术支持。常见情况：
`ufw` 相关报错不影响 Docker 安装，记下来继续也行，但务必确认防火墙最终是开着的。

---

## 第 6 步：创建并填写 `.env`（密钥只在服务器上填）

**在哪里敲：** 服务器里，`/srv/algorithm-notebook` 目录下。

```bash
cp deploy/.env.prod.example .env
ln -sf ../.env deploy/.env
nano .env
```

（第二行是让 Docker 也能读到域名：它只在 `deploy/` 目录里找 `.env`，所以做一个链接，不会进 Git。）

`nano` 打开后，按下面的说明逐项填（方向键移动，改完按 `Ctrl+O` 回车保存，
`Ctrl+X` 退出）：

| 配置项 | 必填？ | 填什么 |
|---|---|---|
| `SITE_DOMAIN` | ✅ 必填 | 你的域名，不带 `https://`，如 `notebook.example.com`。Caddy 靠它自动申请证书 |
| `INVITE_CODE` | ✅ 必填 | 注册邀请码，**必须换成强口令**（16 位以上字母+数字，别用 `change-me`/生日/手机号）。在服务器上执行 `python3 -c "import secrets; print(secrets.token_urlsafe(16))"` 生成一个贴进去 |
| `PUBLIC_BASE_URL` | ✅ 必填 | `https://` + 你的域名，如 `https://notebook.example.com`。找回密码邮件里的链接用它拼 |
| `LEGAL_OPERATOR_NAME` / `LEGAL_CONTACT_EMAIL` | 建议填 | 条款页面上显示的运营者名称和联系邮箱，上线前填真实的 |
| `OPENAI_API_KEY` 等 | 可选 | 不填则 AI 功能不可用，其他功能正常。以后想开 AI 再填 |
| `SMTP_*` | 可选 | 不填则找回密码邮件、复习提醒邮件不可用。QQ 邮箱示例见模板注释 |
| 支付相关 | 可选 | V1 先用"手动收款"（个人收款码），这些不用填 |
| `COOKIE_SECURE` | 不用动 | 模板里已经是 `1`（HTTPS 必需） |

**填完后加一道锁：**

```bash
chmod 600 .env
```

**看到什么算成功：** `ls -l .env` 显示 `-rw-------`（只有 root 能读写）。

**最重要的规矩：**

- 真实密钥**只在服务器上的 `.env` 里填**，不要发到微信群、工单、GitHub。
- `.env` 永远不要提交到 Git（仓库的 `.gitignore` 已经把它排除了，双保险）。
- 备份文件里**不含** `.env` 的密钥——请把 `.env` 的内容单独抄一份存到密码管理器，
  服务器丢了要重建时用得上。

**失败怎么办：** `nano` 不会用——也可以用 `vi .env`，或问技术支持。

---

## 第 7 步：启动服务，检查 `/healthz`

**在哪里敲：** 服务器里，`/srv/algorithm-notebook` 目录下。

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

等 1–2 分钟（第一次要下载 Caddy 镜像、构建应用镜像），然后：

```bash
docker compose -f deploy/docker-compose.prod.yml ps
docker compose -f deploy/docker-compose.prod.yml exec -T app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
```

> 应用端口不对公网开放（只有 Caddy 能访问，这是故意的），所以从容器里问应用有没有起来；
> 不要用服务器上的 `curl http://127.0.0.1:8000`，那样会显示"Connection refused"，不是故障。

**看到什么算成功：**

- `ps` 显示 `app` 和 `caddy` 两个容器都是 `Up` / `healthy`。
- `curl` 输出类似 `{"status":"ok","schema_version":15}`（数字以实际为准，
  只要 `status` 是 `ok` 就行）。

**失败怎么办：**

```bash
docker compose -f deploy/docker-compose.prod.yml logs app | tail -50
```

把最后 50 行日志复制下来问技术支持。常见原因：

- `.env` 里 `SITE_DOMAIN` 没填或格式不对。
- 数据目录权限不对（第 5 步的脚本正常跑完不会有这个问题）。
- 端口被占：如果这台服务器以前跑过别的网站，先停掉它们。

---

## 第 8 步：验证 HTTPS（浏览器看到小锁）

**前提（必须）：** 第 2 步的域名解析已经生效（`nslookup` 能查到服务器 IP），
且 80 端口能从公网访问（第 5 步防火墙已放行）。

**在哪里做：** 你自己电脑的浏览器，访问 `https://你的域名`。

**看到什么算成功：**

1. 地址栏有🔒小锁，没有"不安全"警告。
2. 网站能打开、能注册登录。
3. 在服务器里跑一键体检确认证书有效期：

```bash
deploy/bin/status.sh
```

输出里"证书有效期"显示未来很久的日期（Caddy 申请的是 90 天证书，
到期前会自动续，不用你管）。

**失败怎么办：**

- 浏览器说"无法访问此网站"：等 5 分钟再试（证书申请需要一点时间）；
  看 Caddy 日志找原因：`docker compose -f deploy/docker-compose.prod.yml logs caddy | tail -30`，
  搜 `error` 关键字。
- 证书相关的报错，99% 是域名还没解析到这台服务器（回第 2 步检查），
  或 80 端口被云厂商安全组挡了。
- 「未经真机验证」：Caddy 自动申请证书这一步作者没条件实测，
  如果日志里出现看不懂的 ACME/Let's Encrypt 报错，直接把日志贴给技术支持。

**额外验证（建议做）：** 登录后按 F12 打开开发者工具 → Application → Cookies，
看登录 Cookie 有 `Secure` 标记；再确认你没动过 `COOKIE_SECURE`（模板里是 `1`）。
这是《上线安全清单》里的一条。

---

## 第 9 步：把旧数据库搬上来（已有数据的站长看这节）

> 你电脑上现在跑的旧版本，数据库是 `data/notebook.db`（结构版本 2）。
> 服务器上是新版程序（结构版本 15），启动时会自动升级数据库。
> 按下面 5 步走，**先演练再正式启用**，数据不会丢。

**9.1 在你自己电脑上先备份**

把 `C:\dev\work-muse\data\notebook.db` 复制一份到别处（比如桌面建个 `备份-日期` 文件夹）。
这是你的"后悔药"，后面任何一步搞砸了都能重来。

**9.2 算一下校验值（防传坏）**

在你自己电脑的 PowerShell 里：

```powershell
Get-FileHash C:\dev\work-muse\data\notebook.db -Algorithm SHA256
```

把输出的那串字符记下来（存记事本）。

**9.3 上传到服务器**

先在服务器里停一下应用（避免正在写入）：

```bash
docker compose -f deploy/docker-compose.prod.yml stop app
```

然后在**你自己电脑**的 PowerShell 里上传：

```powershell
scp C:\dev\work-muse\data\notebook.db root@<服务器IP>:/srv/algorithm-notebook/data/notebook.db
```

传完后在服务器里校验：

```bash
sha256sum /srv/algorithm-notebook/data/notebook.db
```

**两边的校验值必须一模一样**，不一样就重新传。

**9.4 先在副本上演练一次升级**

别急着直接用，先复制一份做演练：

```bash
mkdir -p /tmp/drill && cp /srv/algorithm-notebook/data/notebook.db /tmp/drill/notebook.db
chown -R 10001:10001 /tmp/drill
docker run --rm \
  -v /tmp/drill:/drill \
  -e DATABASE_PATH=/drill/notebook.db \
  algorithm-notebook:prod \
  python -c "import db; db.init_db(); print('演练升级成功，schema_version =', db.SCHEMA_VERSION)"
```

**看到什么算成功：** 输出 `演练升级成功，schema_version = 15`（数字以实际为准），
且没有报错。这说明你的旧库能被新版程序正常打开和升级。

**失败怎么办：** 把报错贴给技术支持，**不要**继续往下做。你的原文件还在，
什么都没丢。

**9.5 正式启用 + 授予管理员 + 上传收款码**

演练通过后，启动服务（启动时会自动对 `data/notebook.db` 做真正的升级）：

```bash
docker compose -f deploy/docker-compose.prod.yml up -d app
sleep 30
docker compose -f deploy/docker-compose.prod.yml exec -T app python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
```

确认 `status` 为 `ok` 后，做两件事：

**① 授予管理员身份**（新版不再按用户名自动授权，必须手动授）：

```bash
# 先看有哪些用户（找到你自己的用户 ID，记下来）
docker compose -f deploy/docker-compose.prod.yml exec -T app python admin_tool.py list
# 授予（把 42 换成你的用户 ID）
docker compose -f deploy/docker-compose.prod.yml exec -T app python admin_tool.py grant --id 42
```

> 用户 ID 也可以在旧电脑上查：`python admin_tool.py list`。
> 详细规则见 `docs/operations/accounts-and-privacy.md`。

**② 上传收款码**（用"手动收款"收钱，不用接支付平台）：

用管理员账号登录网站 → 管理后台 → "手动收款设置" → 上传支付宝 / 微信收款码，
并填写联系方式。用户在套餐页扫码付款后登记，你在后台点"确认收款"即可开通。
完整流程见 `docs/operations/manual-payment.md`。

---

## 第 10 步：日常运维（以后就这几个命令）

**在哪里敲：** 服务器里，`/srv/algorithm-notebook` 目录下。

| 想做什么 | 命令 | 说明 |
|---|---|---|
| 看一眼服务状态 | `deploy/bin/status.sh` | 容器、健康、磁盘、最近备份、证书有效期，一屏显示 |
| 手动备份一次 | `deploy/bin/backup.sh` | 在线备份（不用停服务）+ 自动校验 |
| 更新到新版本 | `deploy/bin/update.sh` | 自动备份 → 拉代码 → 重建 → 健康检查；失败自动回滚 |
| 从备份恢复 | `deploy/bin/restore.sh [备份文件]` | 不填则用最新的；恢复前会再备份一份当前数据 |
| 看日志 | `docker compose -f deploy/docker-compose.prod.yml logs app \| tail -50` | 出问题时先看这个 |

**自动备份：** 第 5 步已经配好 cron，每天凌晨 02:30 自动跑，结果记在
`/var/log/notebook-backup.log`。每月看一眼有没有报错：

```bash
tail -20 /var/log/notebook-backup.log
ls -lt /srv/algorithm-notebook/data/backups/ | head -5
```

**把备份下载到自己电脑（异地再存一份，强烈建议）：**

方法一（scp，在你自己电脑的 PowerShell 里）：

```powershell
scp root@<服务器IP>:/srv/algorithm-notebook/data/backups/backup-*.tar.gz D:\备份\
```

方法二（网页文件管理）：云厂商控制台一般有"文件管理"功能，
直接找到 `/srv/algorithm-notebook/data/backups/` 下载最新的 `.tar.gz`。

> 备份里有密码哈希和用户数据，下载到自己电脑后放好，别传到网盘公开分享。
> 备份里**没有** `.env` 的密钥，密钥另存密码管理器（第 6 步说过）。

---

## 第 11 步：出问题怎么办（对照表）

| 现象 | 先查 | 常见原因 |
|---|---|---|
| 网站打不开 | `deploy/bin/status.sh` | 容器没起来 / 域名没解析 / 防火墙 |
| `update.sh` 失败并回滚了 | `docker compose -f deploy/docker-compose.prod.yml logs app` | 新版启动报错；数据没动，放心排查 |
| 回滚后还是不健康 | 日志 + 备份目录 | 可能是数据库已被新版升级，见 `update.sh` 输出的恢复指引 |
| 备份 cron 没跑出新文件 | `/var/log/notebook-backup.log` | 磁盘满了 / 容器没运行 |
| 证书快到期了还没续 | `logs caddy` | 80 端口被挡，Let's Encrypt 连不进来 |
| 忘了 `.env` 里填了什么 | `grep -v '^#' .env \| grep -v '^$'` | 只显示填了的项（别截图发给别人） |

实在搞不定：把**出错的命令 + 完整报错**复制下来问技术支持。
**不要**把 `.env` 的内容、备份文件发给任何人。

---

## 附：未经真机验证的事项（诚实清单）

作者的电脑上没有 Docker，也没有真实的 Ubuntu 服务器，所以下面这些**没有实际跑过**，
只做了能做的静态检查（YAML 解析、bash 语法、人工复核逻辑）：

1. `docker compose config` / 容器真实启动 / `/healthz` 联调 —— 没跑过。
2. `install.sh` 在 Ubuntu 22.04/24.04 上的完整执行 —— 没跑过。
   （Docker 官方安装脚本、ufw 命令都是标准用法，但组合起来没实测）
3. Caddy 自动申请 Let's Encrypt 证书 —— 没跑过。
4. `update.sh` 的回滚分支、`restore.sh` 的恢复分支 —— 没跑过（逻辑已反复检查）。
5. 旧库（schema v2）升级到 v15 的真实演练 —— 没跑过（迁移机制本身有测试覆盖，
   但你的那份旧库没试过，所以第 9.4 步的演练**必须做**）。

第一次上线时，建议找个懂一点技术的朋友在旁边看着，
或者每做完一步把输出截图存档，方便回头排查。
