# 守夜人：宕机告警、状态页和每周体检

宿主机上的一个小脚本（`deploy/bin/watchdog.py`，只用标准库），由 cron 运行，**不依赖应用容器**，所以应用挂了它还能报警。

## 它做什么
| 任务 | 频率 | 检查内容 | 报警条件 |
| --- | --- | --- | --- |
| `check` | 每分钟 | 网站 `/healthz`、磁盘占用、最新备份的新鲜度 | 网站连续 2 次失败；磁盘 ≥ 90%；最新备份超过 30 小时 |
| `selfcheck` | 每周日 09:00 | 首页引用的 `/static/` 资源、条款/隐私/`sw.js`/`manifest`、HTTPS 证书有效期 | 任一资源非 200；证书不足 14 天 |

通知只在**变坏、恢复、持续异常满 6 小时**时发，不会刷屏。检查只访问自己的网站，每个请求间隔 0.2 秒。

## 公开状态页
`https://你的域名/status/`：由 Caddy 直接提供静态文件，只显示"正常 / 异常"和最近检查时间，**不含任何数值或内部细节**。

## 安装（服务器上，root）
```bash
cd /srv/algorithm-notebook && git pull && deploy/bin/watchdog-setup.sh
```
脚本会：从第一个已开启微信提醒的管理员账号取出通知密钥写到 `/etc/oy-watchdog.env`（权限 600，**不会打印**）、放好状态页文件、加两条 cron、重启 Caddy。重复运行是安全的。

前提：管理员账号已在网站账号菜单里配好微信提醒并发过测试消息。

## 排查
- 日志：`/var/log/oy-watchdog.log`（只记录异常）。
- 手动跑一次：`python3 deploy/bin/watchdog.py check`。
- 想换接收人：在网站里换管理员的微信提醒配置，再重新运行安装脚本。
- 卸载：`crontab -e` 删掉两行 `watchdog.py`，删除 `/etc/oy-watchdog.env`。
