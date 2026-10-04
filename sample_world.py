"""A local "sample world": a ready-made database for hands-on testing.

It has its own SQLite file (data/sample.db) and its own port, so it never touches
data/notebook.db (the real local data). Everything in it is invented. Mail and payment
settings are always blanked for this process, and so is the AI key unless you ask for
--with-ai: by default nothing here can spend money or send mail.

    python sample_world.py               build the sample data if it is missing, then serve it
    python sample_world.py --reset       throw the sample data away and rebuild it (dates are
                                         relative to "now", so rebuilding also refreshes them)
    python sample_world.py --build-only  build (or, with --reset, rebuild) without serving
    python sample_world.py --with-ai     serve with the OPENAI_* settings from .env, so "analyze
                                         my weaknesses" makes a real (billed) AI call

The account names and the shared password are written to data/sample-account.txt
(data/ is git-ignored, so the password never reaches the repository). Open the site as
http://127.0.0.1:8001, not localhost: browsers keep one cookie jar per host name, so
localhost:8001 and localhost:8000 would keep logging each other out.
"""

import argparse
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# SAMPLE_WORLD_DIR exists for the automated test only; normally everything lives in data/.
DATA = Path(os.getenv("SAMPLE_WORLD_DIR") or ROOT / "data").resolve()
DB_PATH = DATA / "sample.db"
AVATAR_DIR = DATA / "sample-avatars"
CREDENTIALS = DATA / "sample-account.txt"
HOST, PORT = "127.0.0.1", 8001
INVITE_CODE = "sample-invite"

MAIN, NEWCOMER, ADMIN = "样本同学", "样本新人", "样本管理员"
SPARE_GROUP, FULL_GROUP = "夜读小组", "满分俱乐部"

# Settings that would reach the outside world. They are set (to empty) before the app is
# imported, and python-dotenv never overrides a variable that already exists, so a real
# .env cannot switch them back on. The AI key is blanked too, unless --with-ai is given.
AI_KEY = "OPENAI_API_KEY"
BLANKED_SETTINGS = (
    "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD",
    "ALIPAY_APP_ID", "ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY", "ALIPAY_SELLER_ID",
    "WECHAT_APP_ID", "WECHAT_MCH_ID", "WECHAT_API_V3_KEY", "WECHAT_PRIVATE_KEY",
    "WECHAT_PUBLIC_KEY", "WECHAT_PUBLIC_KEY_ID", "WECHAT_CERT_SERIAL_NO",
)

SNIPPETS = {
    "Python": (
        "def solve(nums, target):\n    left, right = 0, len(nums)\n"
        "    while left < right:\n        mid = (left + right) // 2\n        ...\n"
    ),
    "JavaScript": "for (var i = 0; i < 3; i++) {\n  setTimeout(() => console.log(i), 0);\n}\n",
    "SQL": "SELECT * FROM orders WHERE DATE(created_at) = '2026-09-01';\n",
    "Go": "func get(key string) (string, error) {\n\t// ...\n}\n",
    "": "解：令 f(x)=…，由夹逼准则得…",
}
THINKING = "当时的想法：先写出最直观的版本，边界靠感觉判断，没有逐个验证。"
# How far from "today" the next review of each mistake of the main account falls.
DUE_CYCLE = (-2, -1, 0, 0, 1, 3, -1, 5, 0, 2)

# (title, zone, language, mistakes, days ago, [(review days ago, quality)]) for the main account.
MAIN_PROBLEMS = (
    ("二分查找：搜索插入位置", "算法", "Python", ["循环条件写成 left < right，漏掉 left == right 的情况", "mid 更新时 right = mid - 1，数组只剩两个元素时死循环"], 52, [(44, 2), (33, 2), (20, 3), (8, 3), (2, 4)]),
    ("二分查找：旋转数组中的最小值", "算法", "Python", ["没想清 nums[mid] 与 nums[right] 比较的含义，边界写反"], 45, [(38, 1), (26, 2), (14, 3), (5, 3)]),
    ("滑动窗口：最长无重复子串", "算法", "Python", ["移动左指针时忘记同步删除哈希表记录", "窗口长度计算少加 1"], 38, [(30, 2), (19, 3), (9, 3), (3, 4)]),
    ("链表反转", "算法", "Python", ["反转后忘记把原头结点 next 置空，形成环"], 30, [(24, 2), (15, 3), (6, 4)]),
    ("双指针：三数之和去重", "算法", "Python", ["跳过重复元素时用了 if 而不是 while", "排序后没有在 nums[i] > 0 时提前结束"], 24, [(18, 2), (10, 2), (4, 3)]),
    ("单调栈：每日温度", "算法", "Python", ["栈里存了温度而不是下标，算不出天数差"], 16, [(11, 3), (4, 3)]),
    ("动态规划：最长递增子序列", "算法", "Python", ["把 dp[i] 当成前 i 个数的答案，而不是以 nums[i] 结尾的答案"], 9, [(6, 2), (2, 3)]),
    ("回溯：N 皇后", "算法", "Python", ["判断对角线冲突时 row - col 与 row + col 混用", "回溯后忘记撤销占位标记"], 4, [(2, 2)]),
    ("堆：前 K 个高频元素", "算法", "Python", ["用最大堆而不是最小堆，堆的大小失控", "heapq 存元组时忘记把频次放在第一位"], 19, [(13, 2), (6, 3)]),
    ("二叉树层序遍历", "算法", "Python", ["每层循环直接用 len(queue) 的实时值，而不是先固定本层大小"], 12, [(8, 3), (1, 4)]),
    ("接口幂等性设计", "后端", "Go", ["只靠前端按钮防抖，服务端没有幂等键", "幂等键的过期时间比重试窗口还短"], 8, [(5, 2), (1, 3)]),
    ("短链服务的容量估算", "系统设计", "Python", ["把读写比估反了，缓存命中率算得过于乐观", "忽略了热点 key 的单点压力"], 5, [(2, 3)]),
    ("CSS 垂直居中", "前端", "JavaScript", ["flex 容器忘记设置高度，垂直居中不生效"], 34, [(27, 4), (14, 4), (2, 5)]),
    ("闭包与循环", "前端", "JavaScript", ["var 声明的循环变量被所有回调共享"], 20, [(14, 3), (6, 3)]),
    ("Promise 链的错误处理", "前端", "JavaScript", ["catch 之后没有再抛出，下游以为成功了", "在 then 里忘记 return，链断开"], 11, [(7, 2), (2, 3)]),
    ("事件委托", "前端", "JavaScript", ["用 event.target 判断时没有考虑子元素冒泡"], 2, [(1, 4)]),
    ("数据库：慢查询优化", "数据库", "SQL", ["WHERE 里对索引列使用函数，导致索引失效", "联合索引顺序与查询条件不匹配"], 27, [(20, 3), (11, 4), (3, 4)]),
    ("事务隔离级别", "数据库", "SQL", ["把不可重复读和幻读混为一谈"], 13, [(9, 2), (4, 2), (1, 3)]),
    ("主键与唯一索引", "数据库", "SQL", ["以为唯一索引允许出现多个 NULL 就不需要判空"], 6, [(3, 3)]),
    ("极限的夹逼准则", "高等数学", "", ["放缩时方向写反，没有验证两边极限相等"], 22, [(16, 2), (8, 3), (2, 3)]),
    ("定积分换元", "高等数学", "", ["换元后忘记同步修改积分上下限"], 14, [(9, 3), (3, 4)]),
    ("泰勒展开的余项", "高等数学", "", ["把拉格朗日余项和佩亚诺余项的适用条件混用"], 3, [(1, 3)]),
    ("特征值的几何意义", "线性代数", "", ["把特征向量当成了所有被变换后方向不变的向量，漏掉零向量的约定"], 78, [(70, 3), (52, 3)]),
)
# 很久没复习的记录：让"掌握度趋势"里有一个快被遗忘的分区（概率统计），总览和趋势页都能看到提醒。
# 最后一项是每次复习排定的间隔天数；之后再没复习过，所以一直逾期。
FADING_PROBLEMS = (
    ("条件概率与贝叶斯公式", "概率统计", "", ["把 P(A|B) 与 P(B|A) 混为一谈，先验和后验写反"], 41, [(38, 4)], 3),
    ("二项分布的期望与方差", "概率统计", "", ["期望写成 np(1-p)、方差写成 np，两个公式记反了"], 33, [(30, 3), (24, 3)], 3),
)
NEWCOMER_PROBLEMS = (
    ("冒泡排序的终止条件", "算法", "Python", ["没有加“本轮无交换就提前结束”的优化", "内层循环边界多比较了一次"], 6, [(3, 3)]),
    ("CSS 盒模型", "前端", "JavaScript", ["不清楚 box-sizing 对宽度的影响", "忘了 margin 折叠只发生在垂直方向"], 3, [(1, 4)]),
)
# Other people in the forum and in the study groups: name -> (problems, check-in streak in days).
MATES = {
    "周知远": ([("二分查找边界", "算法", "Python", ["把闭区间和半开区间的写法混用"], 12, [(5, 3), (2, 3)])], 3),
    "苏晚": ([
        ("动态规划：爬楼梯", "算法", "Python", ["初始状态 dp[0] 没有想清含义"], 20, [(10, 3)]),
        ("回溯：全排列", "算法", "Python", ["忘记在回溯后撤销选择"], 16, [(9, 2)]),
        ("SQL 连接", "数据库", "SQL", ["LEFT JOIN 的过滤条件放在了 WHERE"], 14, [(8, 3)]),
        ("缓存穿透", "后端", "Go", ["没有给空结果设置短 TTL"], 10, [(6, 4)]),
        ("线性相关", "线性代数", "", ["把行相关当成列相关"], 7, [(3, 4)]),
    ], 7),
    "陈一鸣": ([("链表环检测", "算法", "Python", ["快慢指针起点不一致"], 8, [(4, 3)])], 0),
    "许朝": ([("定积分的几何意义", "高等数学", "", ["面积的正负号没有区分"], 6, [(2, 3)])], 2),
    "何以安": ([("CSS 层叠上下文", "前端", "JavaScript", ["不知道 transform 会创建层叠上下文"], 4, [(1, 4)])], 1),
}
BEGINNERS = ("柳三变", "白露")
SPRINTERS = ("顾北", "江月白", "沈知意", "温如玉", "裴行之", "桑榆")
CLUB = ("闻人", "东方朔", "南宫", "西门", "北堂", "上官", "欧阳", "司马", "慕容")


def stored_password():
    if CREDENTIALS.is_file():
        match = re.search(r"^密码[：:]\s*(\S+)", CREDENTIALS.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    return None


def read_password():
    """Keep the password across rebuilds so that both of us keep knowing it."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return stored_password() or "yangben-" + "".join(secrets.choice(alphabet) for _ in range(6))


def configure_environment(with_ai=False):
    os.environ.update(
        DATABASE_PATH=str(DB_PATH), AVATAR_DIR=str(AVATAR_DIR), INVITE_CODE=INVITE_CODE,
        COOKIE_SECURE="0", ADMIN_USERNAME=ADMIN,
    )
    for name in BLANKED_SETTINGS:
        os.environ[name] = ""
    if not with_ai:
        os.environ[AI_KEY] = ""


def wipe():
    """Delete only the sample files; the real notebook.db is never named here."""
    for suffix in ("", "-wal", "-shm"):
        (DATA / f"sample.db{suffix}").unlink(missing_ok=True)
    shutil.rmtree(AVATAR_DIR, ignore_errors=True)


class Builder:
    """Creates the cast through the application's own API, then back-dates what the API can't."""

    def __init__(self, password):
        sys.path.insert(0, str(ROOT))
        import main
        from db import connect
        from fastapi.testclient import TestClient

        self.main, self.connect, self.password = main, connect, password
        self.http = lambda: TestClient(main.app, headers={"X-CSRF-Protection": "1"})
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.clients, self.user_ids, self.first_mistake = {}, {}, {}
        self.due_step = 0
        main.init_db()

    @staticmethod
    def local_date(moment):
        """样本账号都在 Asia/Shanghai（固定 UTC+8）：排期日期要按本地日算，凌晨生成时才不会差一天。"""
        return moment.astimezone(timezone(timedelta(hours=8))).date()

    def ts(self, days=0, hours=0, minutes=0):
        return (self.now - timedelta(days=days, hours=hours, minutes=minutes)).isoformat()

    def register(self, name):
        self.main.reset_rate_limits()
        client = self.http()
        response = client.post("/api/auth/register", json={
            "username": name, "password": self.password, "invite_code": INVITE_CODE,
            "accept_terms": True,
            "email": f"sample{len(self.clients) + 1}@example.com", "timezone": "Asia/Shanghai",
        })
        assert response.status_code in (200, 201), (name, response.text)
        self.clients[name] = client
        self.user_ids[name] = client.get("/api/me").json()["id"]

    def problem(self, name, title, zone, language, mistakes, days_ago, reviews, schedule_due=False, interval=None):
        response = self.clients[name].post("/api/problems", json={
            "title": title, "zone": zone, "language": language,
            "code": SNIPPETS[language], "thinking": THINKING, "mistakes": mistakes,
        })
        assert response.status_code == 201, (title, response.text)
        created = response.json()
        problem_id = created["id"]
        with self.connect(write=True) as conn:
            conn.execute("UPDATE problems SET created_at = ? WHERE id = ?", (self.ts(days_ago, 2), problem_id))
            for index, mistake_id in enumerate(created["mistake_ids"]):
                if schedule_due:
                    offset_days = DUE_CYCLE[self.due_step % len(DUE_CYCLE)]
                    self.due_step += 1
                else:
                    offset_days = (index % 3) - 1
                due_day = self.local_date(self.now + timedelta(days=offset_days))
                stamps = [self.now - timedelta(days=max(offset - index, 0), hours=1) for offset, _ in reviews]
                if interval is not None and stamps:
                    # 固定间隔：每次复习都排定 interval 天后再复习，之后再没复习过 → 一直逾期。
                    due_day = self.local_date(stamps[-1]) + timedelta(days=interval)
                last = None
                for position, (offset, quality) in enumerate(reviews):
                    last = stamps[position].isoformat()
                    if interval is not None:
                        next_day = self.local_date(stamps[position]) + timedelta(days=interval)
                    elif position + 1 < len(stamps):
                        # 按时复习：下一次复习日就是这次排定的日期，掌握度趋势才有合理的历史。
                        next_day = self.local_date(stamps[position + 1])
                    else:
                        next_day = due_day
                    next_day = max(next_day, self.local_date(stamps[position]) + timedelta(days=1))
                    conn.execute(
                        "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)",
                        (mistake_id, quality, last, next_day.isoformat()),
                    )
                conn.execute(
                    "UPDATE mistakes SET repetitions = ?, interval_days = ?, last_reviewed_at = ?, due_date = ? WHERE id = ?",
                    (len(reviews), interval or 2, last, due_day.isoformat(), mistake_id),
                )
        self.first_mistake.setdefault(name, created["mistake_ids"][0])
        return problem_id, created["mistake_ids"]

    def streak(self, name, days, quality=4):
        """One review per day for the last `days` days: a visible check-in streak."""
        with self.connect(write=True) as conn:
            for day in range(days):
                reviewed = self.now - timedelta(days=day, minutes=30)
                conn.execute(
                    "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)",
                    (self.first_mistake[name], quality, reviewed.isoformat(), (self.local_date(reviewed) + timedelta(days=1)).isoformat()),
                )

    def pump(self, name, days, reviews_per_day, problems_per_day):
        """Bulk activity for a group member (what makes a study group climb its levels)."""
        user_id = self.user_ids[name]
        with self.connect(write=True) as conn:
            for day in range(days):
                for k in range(reviews_per_day):
                    reviewed = self.now - timedelta(days=day, hours=2 + (k % 8) * 0.1)
                    conn.execute(
                        "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, ?, ?)",
                        (self.first_mistake[name], reviewed.isoformat(), (self.local_date(reviewed) + timedelta(days=1)).isoformat()),
                    )
                for k in range(problems_per_day):
                    conn.execute(
                        "INSERT INTO problems(user_id, title, language, code, thinking, created_at, zone) "
                        "VALUES (?, ?, 'Python', 'pass', '略', ?, '算法')",
                        (user_id, f"练习 {day}-{k}", self.ts(day, 3)),
                    )

    def newcomer_with_first_problem(self, name):
        self.register(name)
        self.problem(name, f"{name} 的第一道错题", "算法", "Python", ["示例错因"], 1, [])

    def group(self, owner, name, members, joined_days_ago):
        response = self.clients[owner].post("/api/groups", json={"name": name})
        assert response.status_code in (200, 201), (name, response.text)
        group = response.json()
        for member in members:
            self.main.reset_rate_limits()
            joined = self.clients[member].post("/api/groups/join", json={"invite_code": group["invite_code"]})
            assert joined.status_code == 200, (name, member, joined.text)
        with self.connect(write=True) as conn:
            for member in (owner, *members):
                conn.execute(
                    "UPDATE study_group_members SET joined_at = ? WHERE group_id = ? AND user_id = ?",
                    (self.ts(joined_days_ago.get(member, joined_days_ago["*"])), group["id"], self.user_ids[member]),
                )
        return group

    def comment(self, post_id, name, body, reply_to=None):
        payload = {"body": body}
        if reply_to is not None:
            payload["reply_to_id"] = reply_to
        response = self.clients[name].post(f"/api/posts/{post_id}/comments", json=payload)
        assert response.status_code == 201, response.text
        return response.json()["id"]


def build(password):
    world = Builder(password)

    # ---- the cast -------------------------------------------------------------------------------
    for name in (MAIN, NEWCOMER, ADMIN, *MATES, *BEGINNERS):
        world.register(name)
    world.main.reset_rate_limits()

    # ---- the main account: plenty of mistakes with varied review histories -----------------------
    main_problem_ids, main_mistakes = {}, {}
    # 错因标签：几种常见的，让侧栏的标签筛选和详情页的编辑器一开始就有东西可看。
    algorithm_tags = (["边界"], ["边界", "粗心"], ["复杂度"], ["思路错误"], ["概念混淆"], ["没读清题"], [])
    math_tags = (["公式记错"], ["概念混淆"], ["粗心"], [])
    tag_position = 0
    for title, zone, language, mistakes, days_ago, reviews in MAIN_PROBLEMS:
        problem_id, mistake_ids = world.problem(MAIN, title, zone, language, mistakes, days_ago, reviews, schedule_due=True)
        main_problem_ids[title], main_mistakes[title] = problem_id, mistake_ids
        pool = math_tags if zone in ("高等数学", "线性代数", "概率统计") else algorithm_tags
        for mistake_id in mistake_ids:
            tags = pool[tag_position % len(pool)]
            tag_position += 1
            if tags:
                response = world.clients[MAIN].put(f"/api/mistakes/{mistake_id}/tags", json={"tags": tags})
                assert response.status_code == 200, response.text
    for title, zone, language, mistakes, days_ago, reviews, interval in FADING_PROBLEMS:
        problem_id, mistake_ids = world.problem(MAIN, title, zone, language, mistakes, days_ago, reviews, interval=interval)
        main_problem_ids[title], main_mistakes[title] = problem_id, mistake_ids
    world.streak(MAIN, 12)

    for title, zone, language, mistakes, days_ago, reviews in NEWCOMER_PROBLEMS:
        world.problem(NEWCOMER, title, zone, language, mistakes, days_ago, reviews)

    for name, (problems, streak_days) in MATES.items():
        for problem in problems:
            world.problem(name, *problem)
        if streak_days:
            world.streak(name, streak_days)
    for name in BEGINNERS:
        world.problem(name, f"{name} 的第一道错题", "算法", "Python", ["示例错因"], 1, [])

    # ---- a saved weakness report (the AI is off here, so the report is hand-made) ---------------
    def evidence(title, observation):
        zone = next(item[1] for item in MAIN_PROBLEMS if item[0] == title)
        return {"mistake_id": main_mistakes[title][0], "observation": observation,
                "problem_id": main_problem_ids[title], "title": title, "zone": zone}

    with world.connect(write=True) as conn:
        rows = conn.execute(
            "SELECT m.id, p.id AS problem_id, p.created_at FROM mistakes m JOIN problems p ON p.id = m.problem_id "
            "WHERE p.user_id = ? ORDER BY p.created_at DESC, m.id DESC LIMIT 40", (world.user_ids[MAIN],)).fetchall()
        review_count = conn.execute(
            f"SELECT COUNT(*) FROM reviews WHERE mistake_id IN ({','.join('?' for _ in rows)})", [r["id"] for r in rows]).fetchone()[0]
        report = {
            "summary": "【样本报告】最近的题目里，有一批易错点集中在“边界与终止条件”：二分查找、滑动窗口、双指针和链表反转都出现了“差一”或“漏掉相等情况”，并且在复习里反复拿到低分。另一条相对独立的线索，是数据库里相近概念的混用。",
            "patterns": [
                {
                    "title": "边界与终止条件总是差一点",
                    "confidence": "较明确",
                    "explanation": "不同题目里的错因表述各不相同，但本质一致：循环何时结束、区间是开还是闭、指针更新后是否还满足不变量，没有在动笔前先定下来，而是边写边凭感觉调整。",
                    "evidence": [
                        evidence("二分查找：搜索插入位置", "两条易错点都与区间定义有关，复习评分连续出现 2 分，近两次才升到 3 分。"),
                        evidence("二分查找：旋转数组中的最小值", "第一次复习得 1 分，说明比较方向没有真正理解，而不只是边界写错。"),
                        evidence("滑动窗口：最长无重复子串", "“窗口长度少加 1”是典型的差一错误，和上面两题同源。"),
                        evidence("双指针：三数之和去重", "用 if 代替 while 跳过重复元素，同样是循环边界没有想清。"),
                    ],
                    "action": "每次写循环前先用一句话写下不变量（例如“[left, right) 内是尚未判断的区间”），再用长度为 1、2 的数组手动走一遍。接下来的 3 道二分题都这样做，复习时只看自己写的这句话是否成立。",
                },
                {
                    "title": "相近概念容易混在一起",
                    "confidence": "待验证",
                    "explanation": "事务隔离级别里的“不可重复读”和“幻读”，以及索引失效的几种触发条件，都是需要靠对比记忆的知识点；目前只有两处记录，样本偏少，还需要后续记录来验证。",
                    "evidence": [
                        evidence("事务隔离级别", "把两个现象当成同一件事，复习评分 2 分后才回升。"),
                        evidence("数据库：慢查询优化", "联合索引的列顺序与查询条件不匹配，与“索引失效条件”属于同一类细节混淆。"),
                    ],
                    "action": "做一张两列对比表：现象、触发条件、对应的隔离级别和索引写法。再有一条同类记录出现时，用这张表核对，并把结论补回错因里。",
                },
            ],
            "sample": {
                "mistake_count": len(rows), "problem_count": len({r["problem_id"] for r in rows}),
                "review_count": review_count,
                "period_start": min(r["created_at"] for r in rows), "period_end": max(r["created_at"] for r in rows),
            },
        }
        conn.execute(
            "INSERT OR REPLACE INTO weakness_insights(user_id, content, created_at) VALUES (?, ?, ?)",
            (world.user_ids[MAIN], json.dumps(report, ensure_ascii=False), world.ts(1)),
        )

        # The saved topic cards also work when the sample site's AI key is blank.
        cluster_rows = conn.execute(
            "SELECT m.id AS mistake_id, p.id AS problem_id, p.title, p.zone, "
            "m.description, p.thinking, m.due_date, p.created_at AS problem_created_at "
            "FROM mistakes m JOIN problems p ON p.id = m.problem_id "
            "WHERE p.user_id = ? ORDER BY p.created_at DESC, m.id DESC LIMIT 60",
            (world.user_ids[MAIN],),
        ).fetchall()
        cluster_members = {
            row["mistake_id"]: {
                "mistake_id": row["mistake_id"], "title": row["title"][:200], "zone": row["zone"],
                "problem_id": row["problem_id"], "description": (row["description"] or row["thinking"])[:300],
                "due_date": row["due_date"], "problem_created_at": row["problem_created_at"],
            }
            for row in cluster_rows
        }
        cluster_order = {row["mistake_id"]: index for index, row in enumerate(cluster_rows)}

        def members(*choices):
            ids = [main_mistakes[title][index] for title, index in choices]
            return [cluster_members[mistake_id] for mistake_id in sorted(ids, key=cluster_order.__getitem__)]

        cluster_report = {
            "summary": "【样本报告】这些错因可以先按三个共同根因一起复习：区间的边界没有先定下来、相近概念的含义混用，以及状态改变后漏掉同步更新。专题横跨不同题目和分区，先练到期的条目，再用一个小例子解释自己这次为什么不会再犯。",
            "clusters": [
                {
                    "title": "区间边界没想清",
                    "explanation": "二分的相等情况、窗口的长度和连续重复元素的处理，都依赖先说清区间里到底包含哪些元素。只凭熟悉的代码模板调整循环，容易漏掉最后一个元素或少算一步。",
                    "tip": "在纸上写下区间是否包含左右端点，用长度为 1、2 和全是重复元素的数组各走一遍，逐步核对循环结束时还剩哪些候选。",
                    "members": members(("二分查找：搜索插入位置", 0), ("二分查找：搜索插入位置", 1),
                                       ("滑动窗口：最长无重复子串", 1), ("双指针：三数之和去重", 0)),
                },
                {
                    "title": "相近概念的含义混淆",
                    "explanation": "状态的定义、读到的数据现象、余项条件和条件概率方向，都是外形相似但含义不同的概念。把名称记住以后，仍需要用具体情景辨认它指的是什么。",
                    "tip": "给每对概念写一个最小例子，再写一句只有其中一个概念成立的条件；盖住名称，尝试从例子反推出正确的定义。",
                    "members": members(("动态规划：最长递增子序列", 0), ("事务隔离级别", 0),
                                       ("泰勒展开的余项", 0), ("条件概率与贝叶斯公式", 0)),
                },
                {
                    "title": "状态变更后漏掉同步更新",
                    "explanation": "移动窗口、反转链表、撤销回溯选择和积分换元，都改变了当前状态，却还沿用旧的辅助记录或边界。操作本身做对了，依赖它的另一部分没有一起更新。",
                    "tip": "为每次操作列一张“改变了什么／还要同步改什么”的两列表，按先前状态、操作、操作后三行手动核对一次。",
                    "members": members(("滑动窗口：最长无重复子串", 0), ("链表反转", 0),
                                       ("回溯：N 皇后", 1), ("定积分换元", 0)),
                },
            ],
            "sample": {"mistake_count": len(cluster_rows)},
        }
        conn.execute(
            "INSERT OR REPLACE INTO mistake_clusters(user_id, content, created_at) VALUES (?, ?, ?)",
            (world.user_ids[MAIN], json.dumps(cluster_report, ensure_ascii=False), world.ts(1)),
        )

    # ---- people who only exist to fill study groups ---------------------------------------------
    for name in (*SPRINTERS, *CLUB):
        world.newcomer_with_first_problem(name)

    # ---- study groups: four for the main account (one slot of five stays free) ------------------
    world.group("柳三变", "新手村", ["白露", MAIN], {"*": 3})
    for name in ("柳三变", "白露"):
        world.pump(name, 3, 3, 1)
    world.group(MAIN, "周末算法共学", ["周知远", "苏晚", "陈一鸣"], {"*": 14})
    world.group(MAIN, "金榜冲刺队", list(SPRINTERS), {"*": 20})
    for name in SPRINTERS:
        world.pump(name, 18, 12, 3)
    full = world.group(CLUB[0], FULL_GROUP, [*CLUB[1:], MAIN], {"*": 40})
    for name in CLUB:
        world.pump(name, 35, 12, 3)
    spare = world.group(SPRINTERS[0], SPARE_GROUP, [SPRINTERS[1]], {"*": 5})

    # ---- forum: a lively thread (with quotes and a deleted floor), the main account's own post, an empty one ----
    thread = world.clients["周知远"].post("/api/posts", json={
        "title": "二分查找的边界总写错，大家是怎么自查的？",
        "body": "最近三道二分题都栽在边界上：有时候死循环，有时候漏掉最后一个元素。\n\n我现在的做法是先写出区间定义，再写循环，但写到 mid 更新那一步还是容易凭感觉改。\n\n想听听大家有没有固定的检查清单，或者能一眼看出区间到底是开还是闭的小技巧。",
    }).json()
    script = [
        (MAIN, "我也踩过这个坑 😅。我的办法是：动笔前先写下“[left, right) 里是还没判断的区间”，然后所有更新都围绕这句话。", 300, None),
        ("苏晚", "补充一个检查清单：\n1. 区间是开还是闭，写在注释里；\n2. 循环条件是否和区间一致；\n3. mid 更新后区间是否一定缩小；\n4. 用长度 1、2 的数组各手动走一遍。\n基本能挡住 90% 的边界错误 👍", 260, None),
        ("陈一鸣", "+1，长度为 2 的数组特别容易暴露死循环 🔥", 200, None),
        ("周知远", "谢谢！我今晚就按苏晚这份清单重做那三道题，再回来更新 🙏", 150, None),
        ("何以安", "有没有可能把二分封装成一个模板函数，只改判定条件？这样边界只需要对一次 🤔", 90, None),
        (MAIN, "可以的，不过模板里的区间定义也要先固定，不然换题时还是会混 💡 @何以安", 50, 5),
        ("周知远", "清单我收下了，今晚就按它复盘 ✅", 30, 2),
        ("苏晚", "长度为 2 的数组确实最容易暴露问题，附议 🙌", 15, 3),
    ]
    comment_ids = []
    for who, body, _, reply_floor in script:
        comment_ids.append(world.comment(thread["id"], who, body, comment_ids[reply_floor - 1] if reply_floor else None))
    own = world.clients[MAIN].post("/api/posts", json={
        "title": "我整理了一份二分查找自查清单", "body": "动笔前写不变量、用长度 1 和 2 的数组各走一遍、确认每次循环区间都在缩小。欢迎补充。",
    }).json()
    own_comments = [
        world.comment(own["id"], "苏晚", "第三条很关键，我之前就因为区间没缩小死循环过 😭"),
        world.comment(own["id"], "周知远", "已收藏，今晚按这份清单复盘 📌"),
    ]
    quiet = world.clients["苏晚"].post("/api/posts", json={
        "title": "整理了一份数据库索引失效的清单", "body": "函数、隐式类型转换、联合索引最左前缀……整理在这里，欢迎补充。",
    }).json()
    with world.connect(write=True) as conn:
        for comment_id, (_, _, minutes, _) in zip(comment_ids, script):
            conn.execute("UPDATE post_comments SET created_at = ? WHERE id = ?", (world.ts(0, 0, minutes), comment_id))
        for comment_id, minutes in zip(own_comments, (1300, 1200)):
            conn.execute("UPDATE post_comments SET created_at = ? WHERE id = ?", (world.ts(0, 0, minutes), comment_id))
        for post_id, hours in ((thread["id"], 6), (own["id"], 26), (quiet["id"], 50)):
            conn.execute("UPDATE posts SET created_at = ? WHERE id = ?", (world.ts(0, hours), post_id))
    # Floor 3 is deleted by its author after floor 8 quoted it: the quote must say "deleted" and reveal nothing.
    assert world.clients["陈一鸣"].delete(f"/api/comments/{comment_ids[2]}").status_code == 200
    reported = world.clients["何以安"].post(f"/api/comments/{comment_ids[1]}/report", json={"reason": "（样本）演示举报队列，管理员可以直接忽略"})
    assert reported.status_code == 201, reported.text

    # Use the same HTTP writes as a real visitor; nobody votes for their own comment.
    accepted = world.clients["周知远"].put(
        f"/api/posts/{thread['id']}/accepted", json={"comment_id": comment_ids[1]},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json() == {"accepted_comment_id": comment_ids[1]}
    for comment_index, voters in (
        (1, ("周知远", MAIN, "陈一鸣")),
        (0, ("苏晚", "许朝")),
        (4, ("周知远",)),
    ):
        for count, name in enumerate(voters, 1):
            vote = world.clients[name].put(f"/api/comments/{comment_ids[comment_index]}/helpful")
            assert vote.status_code == 200, vote.text
            assert vote.json() == {"helpful_count": count, "viewer_helpful": True}

    # A manually written sample shows the feature without any provider request.
    from thread_summary import thread_signature

    summary = {
        "tldr": "（样本）二分边界自查先固定区间含义，再检查循环条件、更新后区间缩小，并手动验证长度 1、2 的数组。",
        "points": [
            {"text": "动笔前写下不变量，所有边界更新都围绕同一份区间定义。", "floors": [1, 2]},
            {"text": "检查循环条件与开闭区间是否一致，确认每次 mid 更新都会缩小区间。", "floors": [2]},
            {"text": "手动走长度 1、2 的数组；使用模板函数时也要先固定模板的区间定义。", "floors": [2, 5, 6]},
        ],
        "open_questions": ["楼主按清单重做三道题后，哪些边界错误仍会出现？"],
    }
    with world.connect(write=True) as conn:
        current_post = dict(conn.execute("SELECT * FROM posts WHERE id = ?", (thread["id"],)).fetchone())
        current_comments = [
            {**dict(row), "floor": floor}
            for floor, row in enumerate(conn.execute(
                "SELECT * FROM post_comments WHERE post_id = ? ORDER BY id", (thread["id"],),
            ), 1)
        ]
        conn.execute(
            "INSERT INTO post_summaries(post_id, signature, content, comment_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (thread["id"], thread_signature(current_post, current_comments),
             json.dumps(summary, ensure_ascii=False),
             sum(comment["deleted_at"] is None for comment in current_comments), world.ts()),
        )


def invite_codes():
    """The join codes change on every rebuild, so the notes always read them from the database."""
    with closing(sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)) as conn:
        return dict(conn.execute(
            "SELECT name, invite_code FROM study_groups WHERE name IN (?, ?)", (SPARE_GROUP, FULL_GROUP)))


def write_credentials(password, with_ai):
    codes = invite_codes()
    ai_note = (
        "AI 分析：已开启（用 --with-ai 启动的）。点“分析”会使用 .env 里的 OPENAI_API_KEY，是真实调用，会产生费用。"
        if with_ai else
        "AI 分析：默认关闭（点“分析”会提示未配置，不会花钱）。想试真实的 AI 分析，用 python sample_world.py --with-ai 启动，"
        "它会使用 .env 里的 OPENAI_API_KEY（真实调用，会产生费用）。"
    )
    CREDENTIALS.write_text("\n".join([
        "样本环境账号（只在你自己的电脑上有效；这个文件放在 data/ 里，不会被提交到 GitHub）",
        "",
        f"地址：http://{HOST}:{PORT}   （请用 127.0.0.1 打开，不要用 localhost，否则会和 8000 的真实应用互相顶掉登录）",
        f"密码：{password}   （下面所有样本账号共用同一个密码）",
        f"注册邀请码：{INVITE_CODE}",
        "",
        "账号：",
        f"  {MAIN} — 主测试号：30 多条错题（12 天连续打卡、各分区都有，还有一个很久没新增的“安静”分区）、4 个小组"
        "（金榜冲刺队 Lv.6 和周末算法共学是自己创建的；满分俱乐部 Lv.8 已满 10 人；新手村 Lv.1）、讨论区发过帖、有一份示例分析报告；"
        "还空着 1 个小组名额，可以测试创建 / 加入",
        f"  {NEWCOMER} — 新人号：只有 4 条错题（不够 5 条，用来测试“再积累几条”的状态），没有小组，用来测试加入 / 满员 / 权限",
        f"  {ADMIN} — 管理员：用来测试管理后台和举报队列（队列里已有一条示例举报）",
        "  其余成员（周知远、苏晚、陈一鸣、许朝、何以安、顾北、江月白、闻人……）也用同一个密码，可以换角色测试，比如组长移除成员",
        "",
        "测试加入小组用的邀请码（每次重建都会变）：",
        f"  {SPARE_GROUP}（还有空位，{MAIN} 没加入）：{codes[SPARE_GROUP]}",
        f"  {FULL_GROUP}（已满员，用 {NEWCOMER} 加入会被拒绝）：{codes[FULL_GROUP]}",
        "",
        ai_note,
        "邮件和支付在这个环境里一直是关闭的。分析页里预置了一份示例报告，不用调用 AI 也能看到完整版面。",
        "想恢复原样：python sample_world.py --reset（数据里的日期会重新按“今天”计算，密码不变）。",
        "",
    ]), encoding="utf-8")


def serve(with_ai):
    import uvicorn

    print(f"样本环境已就绪：http://{HOST}:{PORT}")
    print(f"账号和密码在 {CREDENTIALS}")
    print("AI 分析：已开启，使用 .env 里的 OPENAI_API_KEY（真实调用）" if with_ai else "AI 分析：关闭（需要时加 --with-ai）")
    uvicorn.run("main:app", host=HOST, port=PORT, reload=True, reload_dirs=[str(ROOT)])


def cli():
    parser = argparse.ArgumentParser(description="Build and serve the local sample world.")
    parser.add_argument("--reset", action="store_true", help="delete the sample data and rebuild it")
    parser.add_argument("--build-only", action="store_true", help="do not start the server")
    parser.add_argument("--with-ai", action="store_true",
                        help="use OPENAI_* from .env: the weakness analysis then makes real, billed AI calls")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    os.chdir(ROOT)
    DATA.mkdir(parents=True, exist_ok=True)
    configure_environment(args.with_ai)
    password = read_password()
    # Without a readable password in the notes nobody could log in, so that also forces a rebuild.
    if args.reset or not DB_PATH.exists() or stored_password() is None:
        try:
            wipe()
        except PermissionError:
            sys.exit("样本数据正在被使用（样本服务器还开着？）。请先把它关掉再重建。")
        try:
            build(password)
        except BaseException:
            wipe()
            raise
        print("样本数据已建好。")
    # The notes are rewritten on every start so they always match the database and the AI switch.
    write_credentials(password, args.with_ai)
    if not args.build_only:
        serve(args.with_ai)


if __name__ == "__main__":
    cli()
