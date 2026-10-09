"""掌握度热力图 + 下周主攻（GROWTH F3）的后端测试。

文件命名说明：tests/test_mastery.py 已被旧的"掌握度趋势"（/api/stats/mastery）
占用且按铁律不得修改，本文件只测 F3 的 /api/mastery/heatmap 与 /api/mastery/focus。

公式约定（见 routers/mastery.py）：
- quality >= 3 记为对(1)，< 3 记为错(0)；
- 题分 = 最近 ≤3 次结果按权重 [0.5, 0.3, 0.2] 加权求和，不足 3 次的缺项按 0 计；
- 标签分 = 有题分的题的算术均值；无数据为 null；
- 周列按用户本地时区的周一划分，只统计该周内发生的复习。

测试里 today 被 test_app.client 冻结在 2026-09-19（周六，上海时区），
所以"本周一"恒为 2026-09-14。
"""
from datetime import date

import pytest

import main
import routers.mastery as mastery_router
from db import connect
from routers.mastery import is_correct, mistake_score, tag_score
from test_app import client, register



def at(day_iso, hour=10, minute=0):
    """UTC 时刻文本；10:00 UTC = 上海 18:00，稳稳落在同一本地日。"""
    return f"{day_iso}T{hour:02d}:{minute:02d}:00+00:00"


def seed_mistake(owner, tags=(), due="2026-09-19"):
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '题', '算法', 'Python', '', '', ?)",
            (owner, at("2026-09-01")),
        ).lastrowid
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, '错因', ?)",
            (problem_id, due),
        ).lastrowid
        for tag in tags:
            conn.execute(
                "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) "
                "VALUES (?, ?, ?, ?)",
                (mistake_id, owner, tag, at("2026-09-01")),
            )
    return mistake_id


def add_review(mistake_id, quality, day_iso):
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, ?, ?, '2026-09-26')",
            (mistake_id, quality, at(day_iso)),
        )


def user_id(client):
    return client.get("/api/me").json()["id"]


# ---------- 公式单元测试 ----------


@pytest.mark.parametrize(
    "quality,expected",
    [(5, True), (4, True), (3, True), (2, False), (1, False), (0, False)],
)
def test_correctness_threshold_is_quality_three(quality, expected):
    assert is_correct(quality) == expected


def test_score_with_no_reviews_is_none():
    assert mistake_score([]) is None


@pytest.mark.parametrize(
    "results,expected",
    [
        ([True], 0.5),                    # 只答对一次：最多拿 0.5
        ([False], 0.0),
        ([True, True], 0.8),              # 0.5 + 0.3
        ([True, False], 0.5),
        ([True, False, True], 0.7),       # 0.5 + 0 + 0.2
        ([True, True, True], 1.0),
        ([False, False, False], 0.0),
        ([False, True], 0.3),
        # 超过 3 次只取最近 3 次（最近的排第一）。
        ([False, True, True, True], 0.5),  # 0 + 0.3 + 0.2
        ([True, True, True, False], 1.0),
    ],
)
def test_weighted_score(results, expected):
    assert mistake_score(results) == pytest.approx(expected)


def test_tag_score_is_mean_of_available_scores():
    assert tag_score([0.7, 0.0]) == pytest.approx(0.35)
    assert tag_score([None, 0.6]) == pytest.approx(0.6), "无复习数据的题不参与均值"
    assert tag_score([]) is None
    assert tag_score([None, None]) is None


# ---------- /api/mastery/heatmap ----------


def test_requires_login(client):
    assert client.get("/api/mastery/heatmap").status_code == 401
    assert client.get("/api/mastery/focus").status_code == 401


def test_empty_account(client):
    register(client)
    data = client.get("/api/mastery/heatmap").json()
    assert data["tags"] == [] and data["cells"] == {}
    assert data["weeks"] == [
        "2026-07-27", "2026-08-03", "2026-08-10", "2026-08-17",
        "2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14",
    ]
    focus = client.get("/api/mastery/focus").json()
    assert focus["tag"] is None and focus["score"] is None and focus["plan"] == []


def test_heatmap_cells_use_only_reviews_within_each_week(client):
    register(client)
    owner = user_id(client)
    # m1：本周内 4 次复习（9-15 错、9-16 对、9-17 错、9-18 对）→ 取最近 3 次 (对,错,对) = 0.7
    m1 = seed_mistake(owner, tags=["二分"])
    add_review(m1, 2, "2026-09-15")
    add_review(m1, 5, "2026-09-16")
    add_review(m1, 2, "2026-09-17")
    add_review(m1, 5, "2026-09-18")
    # m2：上上周（9-02）复习一次且答错 → 0.0；本周没复习，不进本周格子
    m2 = seed_mistake(owner, tags=["二分"])
    add_review(m2, 1, "2026-09-02")

    data = client.get("/api/mastery/heatmap", params={"weeks": 4}).json()
    assert data["weeks"] == ["2026-08-24", "2026-08-31", "2026-09-07", "2026-09-14"]
    assert data["tags"] == ["二分"]
    cells = data["cells"]["二分"]
    # 9-02 落在 08-31 那一周；其余周无数据。
    assert cells == [None, 0.0, None, 0.7]
    # 标签总分 = 最近 ≤3 次：m1 取 (对,错,对) = 0.7，m2 = 0.0 → 均值 0.35
    # （本接口不返回总分，这里只确认格子口径；总分逻辑见 focus 测试）


def test_heatmap_sorts_weakest_tag_first_and_dataless_last(client):
    register(client)
    owner = user_id(client)
    strong = seed_mistake(owner, tags=["强"])
    add_review(strong, 5, "2026-09-18")
    add_review(strong, 5, "2026-09-17")
    add_review(strong, 4, "2026-09-16")  # 1.0
    weak = seed_mistake(owner, tags=["弱"])
    add_review(weak, 1, "2026-09-18")  # 0.0
    seed_mistake(owner, tags=["没复习过"])  # 一次都没复习：全周 null

    data = client.get("/api/mastery/heatmap", params={"weeks": 2}).json()
    assert data["tags"] == ["弱", "强", "没复习过"]
    assert data["cells"]["没复习过"] == [None, None]
    assert data["cells"]["弱"] == [None, 0.0]
    assert data["cells"]["强"] == [None, 1.0]


@pytest.mark.parametrize("weeks", ["0", "27", "-3", "x", "2.5"])
def test_weeks_must_be_between_one_and_twenty_six(client, weeks):
    register(client)
    assert client.get("/api/mastery/heatmap", params={"weeks": weeks}).status_code == 422


def test_heatmap_ignores_suspended_mistakes(client):
    register(client)
    owner = user_id(client)
    mid = seed_mistake(owner, tags=["暂停"])
    add_review(mid, 5, "2026-09-18")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET suspended_at = ? WHERE id = ?",
            (at("2026-09-19"), mid),
        )
    data = client.get("/api/mastery/heatmap").json()
    assert data["tags"] == [] and data["cells"] == {}


def test_other_users_data_never_leaks_in(client):
    register(client, "alice")
    mid = seed_mistake(user_id(client), tags=["二分"])
    add_review(mid, 5, "2026-09-18")
    client.post("/api/auth/logout")
    register(client, "bob")
    data = client.get("/api/mastery/heatmap").json()
    assert data["tags"] == [] and data["cells"] == {}
    assert client.get("/api/mastery/focus").json()["tag"] is None


# ---------- /api/mastery/focus ----------


def test_focus_picks_lowest_scored_tag_with_five_mistakes(client):
    register(client)
    owner = user_id(client)
    # "强"：6 道错题，全对 → 1.0；"弱"：5 道错题，全错 → 0.0
    for _ in range(6):
        mid = seed_mistake(owner, tags=["强"])
        add_review(mid, 5, "2026-09-18")
    weak_ids = []
    for index in range(5):
        mid = seed_mistake(owner, tags=["弱"], due=f"2026-09-{20 + index}")
        add_review(mid, 1, "2026-09-18")
        weak_ids.append(mid)

    data = client.get("/api/mastery/focus").json()
    assert data["tag"] == "弱"
    assert data["score"] == pytest.approx(0.0)
    assert data["reason"] == "该标签 5 道错题近三轮答对率 0%"
    # plan 按 due 最早排序（这里已按 due 递增创建）。
    assert data["plan"] == weak_ids


def test_focus_reason_and_plan_with_mixed_results(client):
    register(client)
    owner = user_id(client)
    ids = []
    # 7 道错题，每道最近 3 次都是 (对,错,对) → 题分 0.7；标签分 0.7
    for index in range(7):
        mid = seed_mistake(owner, tags=["二分"], due=f"2026-09-{25 - index}")
        add_review(mid, 5, "2026-09-18")
        add_review(mid, 2, "2026-09-17")
        add_review(mid, 5, "2026-09-16")
        ids.append(mid)
    # 错题数 ≥5 的另一个标签分数更高（每道 3 次全对 → 1.0），不被选中。
    for _ in range(5):
        mid = seed_mistake(owner, tags=["动态规划"], due="2026-09-30")
        add_review(mid, 5, "2026-09-18")
        add_review(mid, 4, "2026-09-17")
        add_review(mid, 5, "2026-09-16")

    data = client.get("/api/mastery/focus").json()
    assert data["tag"] == "二分"
    assert data["score"] == pytest.approx(0.7)
    assert data["reason"] == "该标签 7 道错题近三轮答对率 70%"
    # due 最早的 7 道：ids[6](9-19) … ids[0](9-25)。
    assert data["plan"] == [ids[6 - index] for index in range(7)]


def test_focus_plan_capped_at_seven_and_ordered_by_due(client):
    register(client)
    owner = user_id(client)
    ids = []
    for index in range(9):
        mid = seed_mistake(owner, tags=["贪心"], due=f"2026-10-{index + 1:02d}")
        add_review(mid, 1, "2026-09-18")
        ids.append(mid)
    data = client.get("/api/mastery/focus").json()
    assert data["tag"] == "贪心"
    assert len(data["plan"]) == 7
    # due 最早的 7 道。
    assert data["plan"] == ids[:7]


def test_focus_ignores_tags_with_fewer_than_five_mistakes(client):
    register(client)
    owner = user_id(client)
    for _ in range(4):  # 只有 4 道：再低分也不参选
        mid = seed_mistake(owner, tags=["太少"])
        add_review(mid, 0, "2026-09-18")
    data = client.get("/api/mastery/focus").json()
    assert data["tag"] is None
    assert data["plan"] == []
    assert "≥5" in data["reason"]


def test_focus_ignores_tags_without_any_review_data(client):
    register(client)
    owner = user_id(client)
    for _ in range(5):
        seed_mistake(owner, tags=["没复习"])  # 有错题但一次没复习：没有分数
    data = client.get("/api/mastery/focus").json()
    assert data["tag"] is None
