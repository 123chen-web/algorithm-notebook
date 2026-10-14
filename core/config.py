"""共享常量与环境配置。纯数据/环境读取，不依赖任何业务模块。"""
import os
import re
from datetime import timedelta

from db import ROOT
from legal import PRODUCT_NAME

SESSION_SECONDS = 7 * 24 * 60 * 60

PASSWORD_ITERATIONS = 600_000

DUMMY_PASSWORD = "0" * 32 + ":" + "0" * 64

CODE_ZONES = ("算法", "前端", "后端", "数据库", "系统设计")

NON_CODE_ZONES = ("高等数学", "线性代数", "概率统计")

PROBLEM_ZONES = CODE_ZONES + NON_CODE_ZONES

WEAKNESS_MIN_MISTAKES = 5

WEAKNESS_MAX_MISTAKES = 40

WEAKNESS_RECENT_REVIEWS = 8

PATTERN_DATA_PATH = ROOT / "static" / "data" / "patterns.json"

MAX_PATTERNS_PER_PROBLEM = 3

PATTERN_STATUSES = ("none", "learning", "mastered")

AVATAR_MAX_BYTES = 2 * 1024 * 1024

AVATAR_SIZE = 256

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}

PHOTO_MAX_BYTES = 8 * 1024 * 1024

API_MAX_BODY_BYTES = 10 * 1024 * 1024

PHOTO_MAX_DIMENSION = 1600

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# 刚记下的完整错题，隔几天第一次到期：默认 1（明天）。速记（错因待补）不受影响，仍然当天出现。
# 可用环境变量 FIRST_REVIEW_DELAY_DAYS 调整（0 = 当天就要复习）。
try:
    FIRST_REVIEW_DELAY_DAYS = max(0, min(30, int(os.getenv("FIRST_REVIEW_DELAY_DAYS", "1"))))
except ValueError:
    FIRST_REVIEW_DELAY_DAYS = 1

# 讨论区冻结开关：FORUM_ENABLED=0/false/no/off 关闭讨论区，默认开启（1）。
# 关闭时论坛接口统一返回 404（中文 detail），前端入口隐藏；已有数据一行不删，
# 重新打开即原样回来。测试用 monkeypatch.setattr(config, "FORUM_ENABLED", ...)
# 切换，读取处必须走 config.FORUM_ENABLED 属性（调用时取值），不要 import 时绑定。
def _parse_env_flag(raw, default=True):
    if raw is None:
        return default
    cleaned = raw.strip().lower()
    if not cleaned:
        return default
    return cleaned not in ("0", "false", "no", "off")


FORUM_ENABLED = _parse_env_flag(os.getenv("FORUM_ENABLED"), True)

QUICK_CODE_PLACEHOLDER = "（速记：代码待补）"

QUICK_THINKING_PLACEHOLDER = "（速记：思路待补）"

QUICK_MISTAKE_PLACEHOLDER = "（待补：为什么错）"

PENDING_REASON_DISPLAY = "（待补）"

CLIENT_OP_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"

COMMON_PASSWORDS = frozenset("""
12345678 123456789 1234567890 password password1 password123 qwertyui qwerty123
iloveyou abc12345 11111111 00000000 1q2w3e4r admin123 letmein1 welcome1
12341234 12344321 87654321 987654321 01234567 0123456789 123456abc abcdefgh
abcdefghij qwertyuiop asdfghjk asdfghjkl zxcvbnmm zxcvbnm1 zxcvbnm123
asdf1234 abcd1234 1234abcd 1qaz2wsx 1qazxsw2 1q2w3e4r5t 1q2w3e4r5t6y
qazwsxed qazwsx123 zaq12wsx qazwsx12 password12 password1234 password12345
password! password01 passw0rd passw0rd1 p@ssword p@ssw0rd p@ssw0rd1
changeme changeme1 letmein123 welcome123 welcome1234 welcome! admin1234
admin12345 administrator root1234 root12345 secret123 sunshine sunshine1
princess princess1 football football1 baseball baseball1 basketball superman
superman1 batman123 dragon123 michael1 jennifer computer computer1 internet
internet1 whatever whatever1 trustno1 freedom1 hello123 hello1234 hello12345
test1234 test12345 testing123 guest123 guest1234 login123 login1234 default1
88888888 66666666 99999999 22222222 33333333 44444444 55555555 77777777
12121212 123123123 11223344 1122334455 123456a1 123456q1 123456qq woaini520
woaini1314 nihao123 nihao1234 woaini123 52013145 1314520a 65432100 15975300
14725836 123456789a 123456789! 12345678a 12345678! 12345678910 20202020
""".split())

RESERVED_USERNAMES = frozenset({
    "admin", "administrator", "root", "system", "support", "official",
    "moderator", "staff", "官方", "管理员", "客服", "系统", "站长", "版主",
    "欧叶", "欧叶oy", "算法错题本",
})

MISTAKE_SELECT = """
SELECT m.*, p.title, p.zone, p.language, p.code, p.correct_code, p.thinking, p.source_url, p.verdict
FROM mistakes m
JOIN problems p ON p.id = m.problem_id
"""

VARIANT_SELECT = """
SELECT v.id, v.mistake_id, v.description, v.model, v.created_at,
       v.result, v.answer_code, v.answer, v.expected_answer, v.result_updated_at
FROM variants v
"""

SCRATCH_MAX_LINES = 2000

SCRATCH_SIZE_LIMIT = 40000

TRACE_TABLE_MAX_COLS = 20

TRACE_TABLE_MAX_ROWS = 60

TRACE_TABLE_MAX_HEADER_LEN = 20

TRACE_TABLE_MAX_CELL_LEN = 60

TRACE_TABLE_MAX_SERIALIZED = 20000

REGISTER_LIMIT = 5

REGISTER_WINDOW_SECONDS = 15 * 60

GROUP_JOIN_LIMIT = 10

GROUP_JOIN_WINDOW_SECONDS = 15 * 60

TRIAL_LIMIT = 3

TRIAL_WINDOW_SECONDS = 60 * 60

LOGIN_LIMIT = 10

LOGIN_WINDOW_SECONDS = 15 * 60

LOGIN_ACCOUNT_LIMIT = 30

FORGOT_PASSWORD_LIMIT = 5

FORGOT_PASSWORD_WINDOW_SECONDS = 15 * 60

FORGOT_PASSWORD_EMAIL_LIMIT = 3

FORGOT_PASSWORD_EMAIL_WINDOW_SECONDS = 60 * 60

RESET_PASSWORD_LIMIT = 10

RESET_PASSWORD_WINDOW_SECONDS = 15 * 60

RESET_TOKEN_SECONDS = 30 * 60

EMAIL_CHANGE_SECONDS = 30 * 60

REPORT_LIMIT = 10

REPORT_WINDOW_SECONDS = 60 * 60

ANKI_EXPORT_LIMIT = 10

ANKI_EXPORT_WINDOW_SECONDS = 60 * 60

ANKI_MAX_RECORDS = 5000

EXPORT_LIMIT = 10

EXPORT_WINDOW_SECONDS = 60 * 60

EXPORT_MAX_RECORDS = 5000

ROADMAP_LIMIT = 120

ROADMAP_WINDOW_SECONDS = 60

ANKI_SCOPES = ("all", "zone", "weak", "mastered")

PUSH_TEST_LIMIT = 5

PUSH_TEST_WINDOW_SECONDS = 3600

PUSH_TEST_TITLE = f"{PRODUCT_NAME}：微信提醒测试"

PUSH_TEST_BODY = (
    "这是一条测试消息。能在微信里收到它，说明每日复习提醒的推送配置正确。\n"
    "（该消息由账号设置页的「发送测试消息」触发。）"
)

PUSH_RESULT_MESSAGES = {
    "ok": "测试消息已发送，请在微信里确认收到。",
    "network": "无法连接推送服务商，请检查服务器网络后稍后重试。",
    "auth": "服务商拒绝了密钥（认证失败），请核对 SendKey/token 后重新保存。",
    "rate_limited": "推送被服务商限流，请稍后再试。",
    "provider": "推送服务商暂时不可用，请稍后重试或更换渠道。",
}

REDEEM_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

REDEEM_ERROR = "兑换码不正确、已使用或已过期"

PAY_QR_MAX_BYTES = 2 * 1024 * 1024

GROWTH_RECENT_WINDOW_DAYS = 30

GROWTH_QUIET_THRESHOLD_DAYS = 60

COMMUNITY_STRUGGLING_THRESHOLD = 3

COMMUNITY_MIN_COHORT = 5

GROUP_WEAKNESS_MIN_COHORT = 3

REVIEW_OP_RETENTION = timedelta(days=30)

REVIEW_FUTURE_SLACK = timedelta(seconds=60)

REVIEW_BACKFILL_WINDOW = timedelta(days=7)

DUCK_BAD_REPLY = "小黄鸭这次没答好，请再试一次"

# AI 各任务 max_tokens 分档（推理型模型的思考 token 也计入上限，单次上限过大会
# 放大成本和等待时间）。取值依据：线上 ai_calls 表各 feature 的 completion_tokens
# 按“P95 的 3 倍，且不低于 2000”；站长可在生产库执行以下 SQL 后按公式回填：
#   SELECT feature,
#          COUNT(*) AS n,
#          MAX(completion_tokens) AS p100,
#          (SELECT completion_tokens FROM ai_calls a2
#            WHERE a2.feature = a.feature AND a2.ok = 1
#            ORDER BY completion_tokens
#            LIMIT 1 OFFSET CAST(COUNT(*) * 0.95 AS INTEGER)) AS p95
#     FROM ai_calls a WHERE ok = 1 GROUP BY feature;
# 2026-10-13 时无生产数据可查：全部保持改动前现值，标记“待观察”，行为零变化。
AI_MAX_TOKENS = {
    # 生成变体题（ai.generate）：3 道变体题 + 讲解，结构化长输出。现值，待观察。
    "variant": 30000,
    # 拍照识别（ai.recognize_photo）：转录题干/代码/思路，照片内容多时输出长。现值，待观察。
    "photo": 30000,
    # 薄弱点分析（ai.analyze_weaknesses）：画像 + 建议，分析型长输出。现值，待观察。
    "weakness": 30000,
    # 错因归并（ai.cluster_mistakes）：聚类标签 + 说明。现值，待观察。
    "cluster": 30000,
    # 橡皮鸭（ai.duck_ai_reply）：单轮对话回复，短输出。现值，待观察。
    "duck": 4000,
    # 套路归类（ai.suggest_patterns）：候选套路列表；推理型模型要留思考余量。现值，待观察。
    "suggest_patterns": 8000,
    # 今日一读摘要（digest.py）：多条目中文摘要；推理 token 也计入，留足余量。现值，待观察。
    "digest": 16000,
    # AI 讨论要点（thread_summary.py）：帖子 + 评论的提炼摘要，短输出。现值，待观察。
    "thread_summary": 3000,
}

LEADERBOARD_SIZE = 50

GROUP_MAX_MEMBERS = 10

GROUP_MAX_PER_USER = 5

GROUP_INVITE_CODE_LENGTH = 8

GROUP_INVITE_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

GROUP_WEEK_TIMEZONE = "Asia/Shanghai"  # 每周小目标的自然周时区，周一起点。

GROUP_WEEKLY_GOAL_MIN_TARGET = 5

GROUP_WEEKLY_GOAL_MAX_TARGET = 500

GROUP_WEEKLY_GOAL_DAILY_CAP = 20  # 每人每天对小组合计的贡献封顶，防刷数。

GROUP_TODAY_GOAL_REVIEWS = 1  # 今日动态"完成每日目标"：当天至少复习 1 道即达标。

POST_LIST_DEFAULT_LIMIT = 20

POST_LIST_MAX_OFFSET = 10000

POST_LIST_MAX_LIMIT = 50

POST_EXCERPT_LENGTH = 140

POST_HOT_SCORE = 5

POST_HOT_WINDOW = timedelta(days=7)

POST_PARTICIPANT_LIMIT = 4

POST_LIST_SORTS = {
    "activity": "last_activity_at DESC, id DESC",
    "new": "created_at DESC, id DESC",
    "hot": "hot_score DESC, last_activity_at DESC, id DESC",
}

POST_LIST_FILTERS = {
    "all": "1",
    "unanswered": "comment_count = 0",
    "solved": "solved = 1",
    "mine": "user_id = :me",
    "participated": (
        "(user_id = :me OR EXISTS(SELECT 1 FROM post_comments m WHERE m.post_id = board.id "
        "AND m.user_id = :me AND m.deleted_at IS NULL))"
    ),
}

POST_BOARD_CTE = """
WITH comment_stats AS (
    SELECT post_id, COUNT(*) AS comment_count, MAX(created_at) AS last_comment_at,
           SUM(created_at >= :since) AS recent_comments
    FROM post_comments WHERE deleted_at IS NULL GROUP BY post_id
), vote_stats AS (
    SELECT c.post_id, COUNT(*) AS helpful_total
    FROM comment_votes v JOIN post_comments c ON c.id = v.comment_id
    WHERE c.deleted_at IS NULL GROUP BY c.post_id
), board AS (
    SELECT p.id, p.title, p.body, p.created_at, p.user_id, p.zone,
           u.username, u.avatar_version,
           COALESCE(cs.comment_count, 0) AS comment_count,
           CASE WHEN cs.last_comment_at > p.created_at
                THEN cs.last_comment_at ELSE p.created_at END AS last_activity_at,
           EXISTS(
               SELECT 1 FROM post_comments accepted
               WHERE accepted.id = p.accepted_comment_id
                 AND accepted.post_id = p.id AND accepted.deleted_at IS NULL
           ) AS solved,
           COALESCE(vs.helpful_total, 0) AS helpful_total,
           COALESCE(cs.recent_comments, 0) + COALESCE(vs.helpful_total, 0) AS hot_score
    FROM posts p
    JOIN users u ON u.id = p.user_id
    LEFT JOIN comment_stats cs ON cs.post_id = p.id
    LEFT JOIN vote_stats vs ON vs.post_id = p.id
    WHERE p.deleted_at IS NULL
)
"""

ZERO_WIDTH_JOINER = "\u200d"

