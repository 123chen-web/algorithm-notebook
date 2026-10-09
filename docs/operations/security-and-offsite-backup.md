# SSH 加固与异地加密备份（零基础操作手册）

这份手册带你完成两件事：

1. **SSH 加固**：服务器只允许密钥登录、关闭密码登录，并安装 fail2ban 自动封禁暴力破解的 IP。
2. **异地加密备份**：把每天的数据库备份加密后传到腾讯云 COS（对象存储），服务器坏了、被黑了、误删了，数据还能找回来。每季度做一次恢复演练，确认备份真的能用。

三个脚本都遵循同一套安全规矩：

- **默认只是演练（dry-run）**：不带参数运行，只打印"将要做什么"，不改任何东西；
- **加 `--apply` 才真正执行**；
- **可重复运行**：跑第二遍不会重复添加东西，内容相同会自动跳过；
- **改动前先备份**：被替换的配置文件会复制到 `/var/backups/oy-ssh-hardening/`；
- **失败不留半成品**：SSH 配置用 `sshd -t` 校验，不通过就自动回滚，绝不 reload 坏配置；
- **必须用 root 运行**（命令前加 `sudo`）。

> 全文所有命令都可以直接复制粘贴。尖括号 `<...>` 包起来的地方需要换成你自己的值。

---

# 第一部分：SSH 加固

## 1.1 先理解原理（30 秒）

- **密码登录**：谁都可以对着你的 22 端口猜密码，机器人 7×24 小时在扫。
- **密钥登录**：你电脑上有一对钥匙——**私钥**（留在自己电脑，绝不能外传）和**公钥**（放到服务器）。登录时服务器用公钥验证你手里的私钥，猜不出来、也无法暴力破解。
- 加固后，没有你私钥的人连登录提示都进不来；fail2ban 还会把连续失败 5 次的 IP 封 1 小时。

## 1.2 第 1 步：在你自己的电脑上生成密钥（已有密钥可跳过）

### Windows（PowerShell）

打开 PowerShell（开始菜单搜 PowerShell），执行：

```powershell
ssh-keygen -t ed25519 -C "oy-server"
```

一路按回车（默认路径、密码短语可以留空，也可以设置一个——设置后每次登录要输入短语，但密钥丢了别人也用不了）。完成后 `C:\Users\<你的用户名>\.ssh\` 下会有：

- `id_ed25519`：**私钥**，保密；
- `id_ed25519.pub`：公钥，等下放到服务器。

### Mac / Linux（终端）

```bash
ssh-keygen -t ed25519 -C "oy-server"
```

一路回车，密钥生成在 `~/.ssh/` 下。

## 1.3 第 2 步：把公钥放到服务器

在你自己的电脑上执行（把 `<用户名>` 换成你登录服务器的用户名，轻量服务器通常是 `ubuntu` 或 `root`；`<服务器IP>` 换成你的公网 IP）：

```bash
ssh-copy-id <用户名>@<服务器IP>
```

按提示输入一次服务器密码。看到 `Number of key(s) added: 1` 就成功了。

**Windows 没有 ssh-copy-id 时**，在 PowerShell 执行：

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh <用户名>@<服务器IP> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

## 1.4 第 3 步：确认密钥登录已经可用（关键，不能省）

**另开一个新终端**，执行：

```bash
ssh <用户名>@<服务器IP>
```

- 如果**不再要求输入密码**就直接进了服务器 → 成功，进入下一步；
- 如果还要求密码 → 公钥没放对，不要继续，重复 1.2、1.3 排查。

## 1.5 第 4 步：先演练

在服务器上（仓库目录，和《零基础上线手册》一致）：

```bash
cd /srv/algorithm-notebook
sudo git pull
sudo deploy/bin/ssh-harden.sh
```

演练会做这些只读检查并打印计划：检查 `authorized_keys` 里有没有有效公钥、检查 sshd 主配置是否支持 drop-in 目录、展示将要写入的两个配置文件内容。**不会改动任何东西。**

演练输出示例（模拟，实际路径以你的服务器为准）：

```text
== 1/5 检查登录公钥（防止把自己锁在外面）==
在 /home/ubuntu/.ssh/authorized_keys 中找到 1 个有效公钥。
[dry-run] 将要执行：chmod 700 /home/ubuntu/.ssh；chmod 600 /home/ubuntu/.ssh/authorized_keys（修正过松的权限，不动内容）
== 2/5 检查 sshd 主配置是否包含 drop-in 目录 ==
主配置已包含 sshd_config.d，独立配置文件可以生效。
== 3/5 准备 SSH 加固配置 /etc/ssh/sshd_config.d/99-oy-hardening.conf ==
[dry-run] 将要写入 /etc/ssh/sshd_config.d/99-oy-hardening.conf，内容如下：
[dry-run]   # 欧叶OY SSH 加固配置，由 deploy/bin/ssh-harden.sh 写入，请勿手工编辑。
[dry-run]   PasswordAuthentication no
[dry-run]   KbdInteractiveAuthentication no
[dry-run]   PermitRootLogin prohibit-password
[dry-run]   MaxAuthTries 3
[dry-run]   ClientAliveInterval 300
[dry-run]   ClientAliveCountMax 2
[dry-run] 将要执行：sshd -t，通过后 systemctl reload ssh（或 sshd）
== 4/5 安装并配置 fail2ban（sshd 监狱：失败 5 次封 1 小时）==
[dry-run] 将要写入 /etc/fail2ban/jail.d/oy-sshd.local，内容如下：
[dry-run]   [sshd]
[dry-run]   enabled = true
[dry-run]   backend = systemd
[dry-run]   maxretry = 5
[dry-run]   findtime = 10m
[dry-run]   bantime = 1h
[dry-run]   ignoreip = 127.0.0.1/8 ::1
[dry-run] 将要执行：fail2ban-client -t，通过后启用并 reload/restart fail2ban
== 5/5 完成后的必做验证 ==
...
```

如果演练在第 1 步报"没找到公钥文件 / 没有有效公钥"，**不要加 `--apply`**，按报错里的指引把 1.2～1.4 重做一遍。

## 1.6 第 5 步：正式执行

```bash
sudo deploy/bin/ssh-harden.sh --apply
```

脚本会依次：修正 `~/.ssh` 权限 → 写入 `/etc/ssh/sshd_config.d/99-oy-hardening.conf` → `sshd -t` 校验通过后 reload sshd → 安装（如未安装）并配置 fail2ban。

配置内容含义：

| 配置 | 含义 |
| --- | --- |
| `PasswordAuthentication no` | 关闭 SSH 密码登录 |
| `KbdInteractiveAuthentication no` | 关闭键盘交互式认证（密码登录的另一种通道） |
| `PermitRootLogin prohibit-password` | root 只能用密钥登录，不能用密码 |
| `MaxAuthTries 3` | 一次连接最多试 3 次密钥/密码，断开重来 |
| `ClientAliveInterval 300` / `ClientAliveCountMax 2` | 10 分钟无响应的空闲连接自动断开 |

fail2ban 规则（`/etc/fail2ban/jail.d/oy-sshd.local`）：10 分钟内 SSH 失败 **5 次**的 IP，封禁 **1 小时**；本机回环地址默认白名单。

如果你有固定 IP 想永远不封（例如公司网络，`<IP>` 换成实际 IP）：

```bash
sudo deploy/bin/ssh-harden.sh --apply --whitelist 1.2.3.4
# 多个 IP 用逗号或空格分隔：
sudo deploy/bin/ssh-harden.sh --apply --whitelist 1.2.3.4,5.6.7.8
```

## 1.7 第 6 步：验证（最重要的一步）

脚本结束后会再次提示，这里单独强调：

1. **保持当前终端不要关**；
2. **另开一个新终端**执行 `ssh <用户名>@<服务器IP>`；
3. 新终端能用密钥正常登录 → 加固成功，再关旧终端；
4. 新终端连不上 → 旧终端还活着，立刻回滚（见 1.9），然后排查公钥。

验证密码登录确实已关闭（在你自己的电脑上，应被拒绝或直接失败）：

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no <用户名>@<服务器IP>
```

看到 `Permission denied (publickey)` 就是正确结果。

查看 fail2ban 是否在运行：

```bash
sudo fail2ban-client status sshd
```

## 1.8 万一被锁在外面了，怎么救

**不用慌，有两条不经过 SSH 的救援通道：**

1. **腾讯云网页终端 OrcaTerm（首选）**：登录腾讯云控制台 → 轻量应用服务器 → 找到实例 → 点"登录"→ 选择 OrcaTerm / 网页终端。
   OrcaTerm 走腾讯云自己的平台通道，**不经过 sshd**，所以无论 SSH 配置成什么样都能进系统。进去后执行回滚（1.9）即可。
2. **控制台重置密码 + VNC**：如果网页终端也进不去（例如系统内账号异常），在控制台"重置密码"（按提示关机重置），再通过 OrcaTerm/VNC 登录排查。

## 1.9 回滚（恢复到加固前）

先演练回滚：

```bash
sudo deploy/bin/ssh-harden.sh --revert
```

确认输出无误后正式回滚：

```bash
sudo deploy/bin/ssh-harden.sh --revert --apply
```

回滚会删除脚本写入的两个配置文件（如加固前存在同名文件会从 `/var/backups/oy-ssh-hardening/` 还原）、`sshd -t` 校验后 reload、重载 fail2ban。回滚后密码登录恢复为系统原配置。**fail2ban 软件本身不会被卸载**；如确需卸载：`sudo apt-get remove fail2ban`（一般不建议）。

## 1.10 fail2ban 常用命令

```bash
sudo fail2ban-client status sshd        # 查看 sshd 监狱和被封 IP
sudo fail2ban-client set sshd unbanip <IP>   # 手动解封某个 IP
sudo systemctl status fail2ban          # 查看服务状态
```

---

# 第二部分：异地加密备份

## 2.1 先理解原理（1 分钟）

现状：`deploy/bin/backup.sh` 每天 02:30 在**服务器本机**生成一份备份（`/srv/algorithm-notebook/data/backups/`，保留 30 份）。但如果服务器整机损坏、被勒索或误删，本机备份会一起没。

本方案在每天 03:30 再做一步：

```text
本机备份 → age 公钥加密 → rclone 上传到腾讯云 COS → 异地只保留最近 14 份
            ↑ 加密用公钥            ↑ COS 密钥只存在 rclone 配置里
```

**关于 age 密钥（务必理解）**：

- age 生成一对钥匙：**公钥**（`age1...` 开头，可以放服务器，只能加密、不能解密）和**私钥**（`AGE-SECRET-KEY-1...` 开头，**只能保存在服务器之外**——你的电脑、密码管理器、U 盘）。
- 服务器被入侵，攻击者只能看到公钥和密文，**没有私钥就无法解密任何备份**。
- 私钥丢了 = 所有异地备份永远无法解密。请至少在两个地方保存私钥（例如密码管理器 + 离线 U 盘）。

## 2.2 第 1 步：在服务器安装 age 和 rclone

```bash
sudo apt-get update
sudo apt-get install -y age
age --version
```

> Ubuntu 20.04 的 apt 源里可能没有 age：到 https://github.com/FiloSottile/age/releases 下载 `age-v*-linux-amd64.tar.gz`，解压后把 `age`、`age-keygen` 放到 `/usr/local/bin/` 即可。

安装 rclone（官方脚本，装的是新版）：

```bash
curl https://rclone.org/install.sh | sudo bash
rclone version
```

## 2.3 第 2 步：在腾讯云创建 COS 存储桶

1. 登录腾讯云控制台，进入 **对象存储 COS**（首次使用按提示开通）。
2. **存储桶列表 → 创建存储桶**：
   - 名称：例如 `oy-backups`（全局唯一，被占用就加后缀，如 `oy-backups-2026`）；
   - 地域：**选和轻量服务器同一个地域**（如服务器在广州就选 `ap-guangzhou`，香港选 `ap-hongkong`），内网传输快且免流量费；
   - 访问权限：**私有读写**；
   - 其他默认，创建。
3. 记下"桶名-APPID"（例如 `oy-backups-1234567890`）和地域，后面要用。
4. 准备一对 COS API 密钥：控制台右上角头像 → **访问管理 CAM → API 密钥管理 → 新建密钥**，得到 `SecretId` 和 `SecretKey`（只显示一次，先存到密码管理器）。

## 2.4 第 3 步：在你自己的电脑上生成 age 密钥对

### Windows

1. 到 https://github.com/FiloSottile/age/releases 下载最新的 `age-v*-windows-amd64.zip`，解压到一个固定文件夹，例如 `C:\age\`。
2. PowerShell 进入该目录，执行：

```powershell
cd C:\age
.\age-keygen.exe -o oy-age-key.txt
```

3. 生成后屏幕会显示一行 `Public key: age1...`，文件 `oy-age-key.txt` 内容形如：

```text
# created: 2026-10-09 ...
# public key: age1qyqszqgp............................................
AGE-SECRET-KEY-1QQQQQQ............................................
```

随时可以重新查看公钥：`.\age-keygen.exe -y oy-age-key.txt`。

### Mac / Linux

```bash
# Mac: brew install age；Linux 可同 2.1 用 apt 或下载 release
age-keygen -o oy-age-key.txt
age-keygen -y oy-age-key.txt     # 查看公钥
```

**立刻做好两件事：**

- 把 `oy-age-key.txt`（私钥）存进密码管理器，并复制一份到离线 U 盘；
- 复制好那行 `age1...` 公钥，下一步要贴到服务器。

## 2.5 第 4 步：在服务器配置 rclone（连接 COS）

推荐交互式配置（密钥不会出现在命令历史里）：

```bash
sudo rclone config
```

依次这样选（不同 rclone 版本编号可能不同，**看名字选**）：

1. `n`（New remote）
2. name 输入：`oycos`
3. Storage 一项：找到并选择 **Tencent COS / Tencentcloud COS（cos）** 那一项（输入它前面的编号，或直接输入 `cos`）
4. `env_auth`：输入 `false`
5. `secret_id`：粘贴 CAM 的 SecretId
6. `secret_key`：粘贴 CAM 的 SecretKey
7. `region`：输入桶所在地域，例如 `ap-guangzhou`
8. `endpoint`：直接回车留空
9. `acl`：选 `default`（私有）
10. 最后确认，输入 `q` 退出。

验证（应能看到你刚建的桶）：

```bash
sudo rclone lsd oycos:
```

在桶里建一个专门放备份的"文件夹"（前缀）并验证：

```bash
sudo rclone mkdir oycos:oy-backups-1234567890/oy-backups
sudo rclone lsf oycos:oy-backups-1234567890/oy-backups
```

## 2.6 第 5 步：在服务器写配置文件

把 age **公钥**和异地路径写进只有 root 能读的配置文件（把下面两个值换成你自己的）：

```bash
sudo tee /etc/oy-offsite-backup.env >/dev/null <<'EOF'
AGE_RECIPIENT=age1qyqszqgp............................................
RCLONE_REMOTE=oycos:oy-backups-1234567890/oy-backups
KEEP=14
EOF
sudo chmod 600 /etc/oy-offsite-backup.env
```

- `AGE_RECIPIENT`：age 公钥（`age1` 开头）；
- `RCLONE_REMOTE`：`rclone配置名:桶全名/前缀`；
- `KEEP=14`：异地保留最近 14 份加密备份。

> 这个文件里没有私钥、没有 COS 密钥（COS 密钥在 root 的 rclone 配置 `~/.config/rclone/rclone.conf` 中，权限 600）。

## 2.7 第 6 步：演练

```bash
cd /srv/algorithm-notebook
sudo deploy/bin/offsite-backup.sh
```

演练会找到最新本地备份、跑 `gzip -t` 本地校验、只读列出异地当前文件，并打印**将要**执行的加密、上传、清理命令；**不会加密、上传、删除或写状态文件**。

演练输出示例（模拟）：

```text
== 1/5 找到并校验最新本地备份 ==
最新备份：backup-20261009-023000.tar.gz（48213 字节）
[dry-run] gzip -t 已通过；正式执行时还会在 app 容器内运行 backup.py verify 做完整校验。
== 2/5 用 age 加密（公钥加密；服务器上没有私钥就无法解密）==
[dry-run] 将要执行：age -r <AGE_RECIPIENT> -o <临时目录>/backup-20261009-023000.tar.gz.age /srv/algorithm-notebook/data/backups/backup-20261009-023000.tar.gz
== 3/5 经 rclone 上传到 oycos:oy-backups-1234567890/oy-backups ==
[dry-run] 将要执行：rclone copyto <临时目录>/backup-20261009-023000.tar.gz.age oycos:oy-backups-1234567890/oy-backups/backup-20261009-023000.tar.gz.age
[dry-run] 上传后将用 rclone lsl 比对远端文件大小，不一致会判失败且不做清理。
== 4/5 异地保留最近 14 份（只删异地的旧加密备份）==
[dry-run] 当前异地有 0 份加密备份；正式执行后将保留 14 份。
[dry-run] 没有需要删除的旧备份。
== 5/5 写日志与状态文件 ==
[dry-run] 演练结束：没有加密、没有上传、没有删除、没有写状态文件。确认无误后加 --apply 正式执行。
```

如果提示"找不到本地备份目录 / 没有 backup-*.tar.gz"，先手动跑一次本机备份：`sudo deploy/bin/backup.sh`。

## 2.8 第 7 步：正式上传一次

```bash
sudo deploy/bin/offsite-backup.sh --apply
```

成功后验证：

```bash
# 异地应能看到一份 .age 文件
sudo rclone lsf oycos:oy-backups-1234567890/oy-backups
# 成功状态（JSON）
sudo cat /var/lib/oy-offsite-backup/last-success.json
# 日志
sudo tail -n 20 /var/log/oy-offsite-backup.log
```

`last-success.json` 字段示例（模拟）：

```json
{
  "status": "ok",
  "finished_at": "2026-10-09T03:30:12Z",
  "archive": "backup-20261009-023000.tar.gz",
  "archive_bytes": 48213,
  "encrypted_bytes": 48440,
  "remote": "oycos:oy-backups-1234567890/oy-backups",
  "kept_remote": 1,
  "deleted_remote": []
}
```

失败时会写 `/var/lib/oy-offsite-backup/last-failure.json`（含 `stage` 失败阶段和原因），并在日志里留一行"失败"。**任何一步失败都不会删除异地旧备份。**

## 2.9 第 8 步：设置每天自动执行

本机备份是 02:30 跑，异地备份安排在 03:30（留 1 小时余量）：

```bash
sudo crontab -e
```

在最后新增一行（首次使用会让你选编辑器，选 `nano` 即可：Ctrl+O 回车保存，Ctrl+X 退出）：

```cron
30 3 * * * /srv/algorithm-notebook/deploy/bin/offsite-backup.sh --apply >> /var/log/oy-offsite-backup.log 2>&1
```

第二天检查：

```bash
sudo cat /var/lib/oy-offsite-backup/last-success.json
sudo rclone lsf oycos:oy-backups-1234567890/oy-backups
```

## 2.10 日常检查（每月一次，1 分钟）

```bash
sudo tail -n 5 /var/log/oy-offsite-backup.log    # 每天应有一行"成功"
sudo cat /var/lib/oy-offsite-backup/last-success.json
sudo rclone lsf oycos:oy-backups-1234567890/oy-backups | wc -l   # 份数不超过 14
```

---

# 第三部分：恢复演练（每季度一次）

备份"看起来在传"不等于"真能恢复"。演练会从异地下载最新一份、**用私钥解密**、在临时目录恢复并检查数据库，全程**不停服务、不碰线上数据**。

## 3.1 第 1 步：把私钥临时拷到服务器

在**你自己的电脑**上（Windows 在 PowerShell，Mac/Linux 在终端）：

```bash
scp oy-age-key.txt <用户名>@<服务器IP>:~/oy-age-key.txt
```

登录服务器后把私钥移到 root 家目录并收紧权限：

```bash
ssh <用户名>@<服务器IP>
sudo mv ~/oy-age-key.txt /root/oy-age-key.txt
sudo chmod 600 /root/oy-age-key.txt
```

## 3.2 第 2 步：演练（先 dry-run）

```bash
cd /srv/algorithm-notebook
sudo deploy/bin/restore-drill.sh --identity /root/oy-age-key.txt
```

## 3.3 第 3 步：正式演练

```bash
sudo deploy/bin/restore-drill.sh --identity /root/oy-age-key.txt --apply
```

成功输出示例（模拟）：

```text
== 2/6 列出异地最新一份加密备份 ==
最新异地备份：backup-20261009-023000.tar.gz.age
== 3/6 下载并解密到独立临时目录 ==
下载、解密完成：/var/lib/oy-offsite-backup/drills/drill-20261009-110205-1234/backup-20261009-023000.tar.gz
== 4/6 校验归档并恢复到临时目录（backup.py 禁止恢复到线上数据目录）==
校验通过：...；数据库大小、SHA-256 与完整性检查均通过。
恢复完成：/var/lib/oy-offsite-backup/drills/drill-20261009-110205-1234/restored
== 5/6 SQLite 完整性检查与核心表行数 ==
integrity_check: ok
rows users: 128
rows problems: 903
rows mistakes: 422
rows reviews: 1560
== 6/6 演练结束 ==
```

看到 `integrity_check: ok` 且行数与网站当前规模相符，演练即通过。

## 3.4 第 4 步：清理（必做）

```bash
# 删除演练副本（含用户数据），把命令里的目录换成 3.3 输出的实际目录
sudo rm -rf /var/lib/oy-offsite-backup/drills/drill-20261009-110205-1234
# 【最重要】把私钥从服务器删掉，私钥平时不应留在服务器上
sudo rm -f /root/oy-age-key.txt
# 确认已删除
sudo ls /root/oy-age-key.txt
```

## 3.5 真正出事时怎么恢复

演练验证的是"备份可解密、可打开、数据完整"。真正灾难（数据库损坏/整机重建）时的**线上替换**流程有额外的停服、替换、重启步骤，见 [`backup-and-restore.md`](backup-and-restore.md) 的"恢复与演练"，不要用演练脚本直接替换线上数据（脚本也会拒绝恢复到线上数据目录）。

---

# 第四部分：回滚与常见错误

## 4.1 一键回滚汇总

| 操作 | 命令 |
| --- | --- |
| 回滚 SSH 加固（恢复密码登录原配置） | `sudo deploy/bin/ssh-harden.sh --revert --apply` |
| 停止每天异地备份 | `sudo crontab -e` 删除 `offsite-backup.sh` 那一行 |
| 删除异地备份配置 | `sudo rm -f /etc/oy-offsite-backup.env` |
| 删除 COS 里的备份 | COS 控制台手动删除，或 `sudo rclone delete oycos:桶名/前缀` |
| 卸载 fail2ban（一般不必） | `sudo apt-get remove fail2ban` |

## 4.2 常见错误排查

| 报错 / 现象 | 原因与处理 |
| --- | --- |
| `没找到公钥文件 .../authorized_keys` | 公钥还没放上服务器，重做 1.2～1.4，确认新终端能免密登录后再 `--apply` |
| `authorized_keys 里没有一行是有效的公钥` | 文件内容损坏或粘贴时断行；公钥必须是**一整行**。重新 `ssh-copy-id` |
| `sshd -t 校验没通过，已回滚` | 脚本已自动恢复，SSH 未受影响；把完整报错发给技术支持，不要手工改配置硬上 |
| `主配置已手写 PasswordAuthentication ... 会覆盖独立配置` | 编辑 `/etc/ssh/sshd_config`，注释或删除 Include 之前的冲突行，再运行脚本 |
| 加固后新终端 `Permission denied (publickey)` | 用 OrcaTerm 进服务器，执行 `sudo deploy/bin/ssh-harden.sh --revert --apply`，修好密钥后重来 |
| 自己的 IP 被 fail2ban 封了 | OrcaTerm 登录后 `sudo fail2ban-client set sshd unbanip <你的IP>`，或加 `--whitelist` 重跑 |
| `AGE_RECIPIENT 格式不正确` | 必须是 `age1` 开头的**公钥**；不要把 `AGE-SECRET-KEY-` 私钥贴进去 |
| 解密失败：`私钥与加密公钥不配对` | 拿错私钥；换用与服务器公钥成对的那份 `oy-age-key.txt`（`age-keygen -y 私钥` 可反查公钥比对） |
| `rclone: directory not found` / `403` | 桶名/APPID/地域/Secret 有误；重跑 `sudo rclone config` 核对，`sudo rclone lsd oycos:` 验证 |
| `rclone 上传失败` | 看网络与 COS 密钥；脚本已判失败且不会清理异地旧文件，修好后重跑即可 |
| `找不到本地备份目录 / 没有 backup-*.tar.gz` | 本机每日备份还没跑过；先 `sudo deploy/bin/backup.sh` |
| `backup.py 完整校验没通过，本次不上传` | 当天备份损坏；保留现场，查看 `docker compose -f deploy/docker-compose.prod.yml logs app`，并重跑一次本机备份 |
| `KEEP（异地保留份数）必须是不小于 1 的整数` | 修正 `/etc/oy-offsite-backup.env` 里的 `KEEP` |
| 演练后行数明显偏小 | 可能恢复到了很旧的版本；检查 COS 里文件的日期，必要时换一份重新演练 |
| Windows 双击私钥文件乱码 | 正常，私钥是文本文件，用记事本/VS Code 打开即可 |

## 4.3 安全注意事项

- **私钥 `AGE-SECRET-KEY-...` 永远不放进服务器、不进 Git、不发聊天窗口**；只在演练时临时拷入，演练完立即删除（3.4）。
- COS 桶保持**私有读写**；CAM 密钥建议只授予该桶的读写权限，不要用主账号全权限密钥。
- 本方案不碰 `.env`；`.env` 里的应用密钥仍需按《上线安全清单》另行备份——备份归档里**不含** `.env`。
- 脚本不会把任何密钥写进日志或状态文件；`last-success.json` 只有文件名、大小、份数等运维信息。
