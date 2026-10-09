# 每日推荐候选题定时刷新

已有 `cf_problems.py refresh` 只拉 Codeforces 公开元信息，不调用 AI、邮件或支付。
应用推荐接口从缓存选题；刷新不是运行用户代码，也不保证每天都发布一条公告。
已有 systemd 方案见 `recommend-schedule.py`；两种方式只选一种，避免重复抓取。

服务器已完成 Docker 生产部署后，以 root 运行：

```sh
cd /srv/algorithm-notebook
sudo bash deploy/bin/recommend-cron-setup.sh
sudo bash deploy/bin/recommend-cron-setup.sh --apply
sudo crontab -l
sudo tail -n 50 /var/log/oy-recommend.log
```

默认只打印计划。`--apply` 先以 600 权限备份 root crontab 至
`/var/backups/oy-recommend-crontab.*`，只替换带 `# oy-recommend-refresh` 的行。
重复运行不会重复添加任务；其他 cron 保留。每天按**服务器时区** 03:20 运行。
确认 cron 服务正在运行（Debian/Ubuntu：`systemctl status cron`）。
宿主机仅需 Docker Compose、cron、GNU timeout、bash，不需要 Python 依赖。
应用容器必须名为 `app`，缓存所在 `data` 目录需按现有生产 compose 挂载持久卷。

自定义路径请每次传入相同环境变量：

```sh
sudo env APP_DIR=/srv/algorithm-notebook RECOMMEND_LOG=/var/log/oy-recommend.log bash deploy/bin/recommend-cron-setup.sh --apply
sudo bash deploy/bin/recommend-cron-setup.sh --remove
sudo bash deploy/bin/recommend-cron-setup.sh --remove --apply
```

输出追加日志，HTTP 请求超时为 20 秒，整条容器命令最多 180 秒。
抓取、格式校验或空题库失败时返回非零，不覆盖旧缓存，不阻塞其他 cron。
安装脚本不会立即抓取或启动 Docker；需要首刷时可在服务器手动运行：

```sh
docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T app python cf_problems.py refresh
```

日志会持续增长，按服务器现有 logrotate 策略轮转 `oy-recommend.log`。
