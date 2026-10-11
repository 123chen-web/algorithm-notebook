# AI 讲题质量评估（开发用）

`run_eval.py` 把一批错题喂给 `ai.generate`，打印每条的七段输出，方便人眼对比
不同提示词版本的效果。**不进网站**，只是开发调提示词时的尺子。

## 用法

```bash
# 只看提示词，不联网（评审提示词改动时用这个）
python tools/ai_eval/run_eval.py --cases tools/ai_eval/cases.sample.json --dry-run

# 真实调用（读 OPENAI_API_KEY / OPENAI_BASE_URL 环境变量）
python tools/ai_eval/run_eval.py --cases my_cases.json
```

`cases.sample.json` 是 5 条虚构样例（覆盖：有/无 `correct_code`、算法区/
数学区、`mistake` 为空），不要改成真实数据。

## 把自己的真实错题导成 cases 文件

cases 文件是一个 JSON 数组，每条字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `zone` | 是 | 分区，如“算法”“高等数学”，必须和网站分区名一致 |
| `title` | 否 | 题目标题 |
| `language` | 否 | 编程语言（数学区留空） |
| `code` | 否 | 当时的代码 / 解题过程（对应 `original_work`） |
| `thinking` | 否 | 当时的思路（对应 `original_thinking`） |
| `mistake` | 否 | 错因描述，空字符串表示让 AI 自己反推 |
| `correct_code` | 否 | 用户自己补的正确代码（有才给） |
| `mastery_signal` | 否 | `"struggling"` / `"mastering"` / 不填 |

导出时用自己的脚本从数据库读（`mistakes` + `problems` 表），拼成这个格式
存到 `tools/ai_eval/cases.local.json`。

**真实数据不要提交进仓库**：`cases.local.json` 已在 `.gitignore` 里。
`cases.sample.json` 只放虚构样例。
