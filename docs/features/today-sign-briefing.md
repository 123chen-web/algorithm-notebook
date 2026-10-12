# 今日一签 与 今日一读（总览页的两张“开门卡片”）

## 今日一签（今日宜忌）

像撕日历一样的卡片：大号日期 + 一个红色“签”字印章，点一下印章，印章盖下去，卡片展开显示今天的**宜**和**忌**。

- 内容来自**用户自己的数据**（`today_sign.py`）：今天到期最多的套路 →“宜复习《X》”；记过错题但还没学的套路 →“宜学习《X》”；最近最常犯的错因标签 →“忌：想当然的边界”之类；没有数据时用一份精选小提示补上。
- 同一个人同一天内容固定（按用户和日期生成种子），刷新不会变；每条点开能看到“为什么”。
- 纯本地计算，不联网、不用 AI；措辞避免绝对化，卡片上标明“仅供学习提示与娱乐”。
- 当天打开过就记在浏览器本地（`localStorage`），再来直接是展开状态，不重复播动画；减少动画偏好下没有动效。
- 接口：`GET /api/today-sign`（登录后，每分钟 60 次）。

## 今日一读（今日简报）

横向滑动的 5 张小卡片：2 篇 arXiv 论文（cs.DS / cs.AI）+ 3 条 Hacker News，每张一句话中文摘要，点“阅读原文”新标签页打开；“标为已看”只记在本机。

- 服务器每天跑一次 `python digest.py --cache-only`：抓取 → 让 AI 写一句话摘要（一天一次，所有人共用）→ 缓存到 `data/digest/日期.json`。网站接口只读这份缓存，**请求时不联网、不调用 AI**。
- 缓存最多回退 3 天；没有缓存（还没装定时任务、抓取失败）就不显示这张卡片，不影响别的内容。
- 接口：`GET /api/briefing/today`（登录后，每分钟 60 次）。

### 在服务器上装每天自动抓取的定时任务

```bash
(crontab -l 2>/dev/null; echo '5 6 * * * cd /srv/algorithm-notebook && /usr/bin/timeout 300 /usr/bin/docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T app python digest.py --cache-only >> /var/log/oy-briefing.log 2>&1 # oy-briefing') | crontab -
```

立即手动抓一次（验证用）：

```bash
cd /srv/algorithm-notebook && docker compose --env-file .env -f deploy/docker-compose.prod.yml exec -T app python digest.py --cache-only
```

成本：每天 1 次摘要请求，约几千 token；某个来源或 AI 失败只会少几条，不会报错。原来的“给管理员发微信简报”（`docs/operations/daily-digest.md`）不受影响，同一份缓存也共用。
