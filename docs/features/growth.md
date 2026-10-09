# 成长功能

- 链接预填：`POST /api/problems/fetch-from-url` 本地解析 LeetCode / Codeforces 标识，只预填标题与来源，题面、难度、标签由用户补充。没有 LeetCode GraphQL 或其他联网抓题面请求。
- 提醒：见[提醒](reminders.md)，须配置 SMTP 并由站长另行安排任务。
- 热力图与下周主攻：`GET /api/mastery/heatmap` 按标签和用户本地周聚合；近三次对错按 0.5/0.3/0.2 加权，不足三次不归一化。`GET /api/mastery/focus` 选有至少 5 条易错点且得分最低的标签，列最多 7 条主攻计划。只读，无 AI 调用。
- 举一反三：评分后展示题目库的同标签推荐，用户可加入，不调用 AI、不抓取外部题面。Codeforces 推荐沿用当前题目语言，未填写时默认 Python，加入后可编辑。

AI 入口的计费与重试统一见[AI 额度与计费](../../static/ai-billing.html)。退出登录、切换账号或重载模块时，迟到响应不会回填前一个账号的数据。
