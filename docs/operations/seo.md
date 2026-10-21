# 搜索收录与分享预览（SEO）

## 现有内容

- `GET /robots.txt`、`GET /sitemap.xml`：在 `routers/pages.py`，无需登录、不查数据库、不在 `/api` 下，也不出现在 OpenAPI 里。
- 站点地址取环境变量 `PUBLIC_BASE_URL`（生产必须是 `https://ouyeoy.com`）；未设置时退回请求自身的地址
  （反向代理没传 `X-Forwarded-Proto` 时会变成 `http://`，所以线上请务必设置）。
- 收录范围由 `routers/pages.py` 里的 `SITEMAP_PATHS` 决定：首页、`/static/patterns.html`（套路库）、
  `/static/help.html`、`/static/extension.html`、`/terms`、`/privacy`。套路库目前是单页，没有可收录的单条套路网址，所以没有深链接。
  登录后的应用视图、AI 额度与计费说明（`ai-billing.html`）、管理与支付相关页面一律不收录；`robots.txt` 禁止 `/api/`。
- 四个公开页面（`index` / `patterns` / `help` / `extension`）的 `<head>` 里有 description、canonical、Open Graph 与 Twitter 卡片；
  `og:url`、canonical、`og:image` 是写死的 `https://ouyeoy.com/...` 静态字符串。换域名时要全局替换。
- 分享图 `static/og-image.png`（1200x630）由 `python tools/make_og_image.py` 生成；改文案后重跑并提交 PNG。

## 新增一个公开页面

1. 在 `SITEMAP_PATHS` 追加路径（必须是未登录就能打开、返回 200 的地址）。
2. 页面 `<head>` 里补 description、canonical、og:*、twitter:*（可参考 `static/help.html`），不要写行内脚本。
3. 在 `tests/test_seo_public_pages.py` 的 `PAGES` 里加上文件名（仅限 `static/` 下的页面）。

## 检查链接预览

- 本地：`curl http://127.0.0.1:8000/robots.txt`、`/sitemap.xml`，确认地址都是正确域名。
- 上线后：
  - 微信：把链接发给“文件传输助手”看卡片（微信会缓存，改图后可能要等一阵）。
  - Slack / Discord：直接粘贴链接。
  - Facebook 分享调试器、Google Search Console 提交 `https://ouyeoy.com/sitemap.xml`。
  - `curl -s https://ouyeoy.com/ | grep og:` 看标签是否到位；`curl -sI https://ouyeoy.com/static/og-image.png` 应返回 200。
