"""我的三大典型失误 GET /api/stats/typical：基于错因标签 + 复习失败表现选取，
沿用 mastery.py 的"未掌握"判定；模板生成提醒句与自查清单，不调用 AI。"""

from datetime import date, datetime, timedelta, timezone

import pytest

import ai
import typical_templates
from db import connect
from test_app import client, register


ENDPOINT = "/api/stats/typical"
TODAY = date(2026, 9, 19)  # 与 test_app.client 冻结的用户本地日期一致（上海时区）


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("典型失误是纯统计 + 模板，不能调用 AI")

    for name in ("generate", "analyze_weaknesses", "cluster_mistakes", "recognize_photo"):
        monkeypatch.setattr(ai, name, unexpected_call)


def at(day, hour=4):
    return datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).isoformat()


def user_id(client):
    return client.get("/api/me").json()["id"]


def seed(owner, title="题", tags=(), created_days_ago=10, reviews=(), zone="算法"):
    """造一条易错点并贴标签。reviews = [(几天前, 质量, 间隔天数), ...]；质量 <3 算复习失败。"""
    created = TODAY - timedelta(days=created_days_ago)
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, ?, ?, 'Python', '', '', ?)",
            (owner, title, zone, at(created)),
        ).lastrowid
        if reviews:
            last_ago, _quality, interval = min(reviews, key=lambda item: item[0])
            due = TODAY - timedelta(days=last_ago) + timedelta(days=interval)
        else:
            due = created
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, ?, ?)",
            (problem_id, f"{title}的错因", due.isoformat()),
        ).lastrowid
        for days_ago, quality, interval in reviews:
            reviewed = TODAY - timedelta(days=days_ago)
            conn.execute(
                "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, ?, ?, ?)",
                (mistake_id, quality, at(reviewed), (reviewed + timedelta(days=interval)).isoformat()),
            )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for tag in tags:
            conn.execute(
                "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) VALUES (?, ?, ?, ?)",
                (mistake_id, owner, tag, now),
            )
    return mistake_id


def report(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200, response.text
    return response.json()


def keys(payload):
    return [item["key"] for item in payload["items"]]


def test_requires_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_below_five_effective_records_is_not_enough_and_empty(client):
    register(client)
    owner = user_id(client)
    for index in range(4):
        seed(owner, title=f"题{index}", tags=("边界",), created_days_ago=10 + index)
    data = report(client)
    assert data["enough"] is False
    assert data["items"] == []
    assert data["checklist"] == []


def test_enough_but_everything_mastered_has_empty_items_but_still_a_checklist(client):
    register(client)
    owner = user_id(client)
    # 5 条都是今天录入 / 刚复习过：保持率 100%，不算未掌握。
    for index in range(5):
        seed(owner, title=f"熟题{index}", tags=("边界",), created_days_ago=0)
    data = report(client)
    assert data["enough"] is True
    assert data["items"] == []
    assert 6 <= len(data["checklist"]) <= 8


def test_picks_the_three_most_frequent_unmastered_tag_categories(client):
    register(client)
    owner = user_id(client)
    for _ in range(4):
        seed(owner, title="边界题", tags=("边界",), created_days_ago=10)
    for _ in range(3):
        seed(owner, title="混淆题", tags=("概念混淆",), created_days_ago=10)
    for _ in range(2):
        seed(owner, title="粗心题", tags=("粗心",), created_days_ago=10)
    seed(owner, title="公式题", tags=("公式记错",), created_days_ago=10)
    data = report(client)
    assert data["enough"] is True
    assert keys(data) == ["边界", "概念混淆", "粗心"], "最多 3 类，按未掌握记录数从多到少"
    counts = {item["key"]: item["count"] for item in data["items"]}
    assert counts == {"边界": 4, "概念混淆": 3, "粗心": 2}


def test_mastered_records_are_excluded_from_category_counts(client):
    register(client)
    owner = user_id(client)
    # 3 条贴着"边界"但已经练熟（2 天前复习、间隔 30 天：保持率 ≈ 0.99）。
    for _ in range(3):
        seed(owner, title="熟了", tags=("边界",), created_days_ago=30, reviews=((2, 5, 30),))
    # 2 条贴着"粗心"仍未掌握（10 天没碰）。
    for _ in range(2):
        seed(owner, title="还错", tags=("粗心",), created_days_ago=10)
    data = report(client)
    assert keys(data) == ["粗心"]
    assert data["items"][0]["count"] == 2


def test_ties_break_by_most_recent_wrong_time_then_tag_name(client):
    register(client)
    owner = user_id(client)
    # 两个标签都是 2 条未掌握；"粗心"最近一次复习失败在 4 天前，"边界"在 6 天前。
    # （失败复习间隔只排 1 天：4 天后保持率 0.9^4≈0.66 < 0.7，仍算未掌握。）
    seed(owner, title="粗1", tags=("粗心",), created_days_ago=12, reviews=((4, 1, 1),))
    seed(owner, title="粗2", tags=("粗心",), created_days_ago=11)
    seed(owner, title="边1", tags=("边界",), created_days_ago=12, reviews=((6, 2, 1),))
    seed(owner, title="边2", tags=("边界",), created_days_ago=11)
    seed(owner, title="已练熟的一条", tags=(), created_days_ago=0)  # 凑够 5 条有效记录
    data = report(client)
    assert keys(data) == ["粗心", "边界"]
    assert data["items"][0]["last_wrong_at"] > data["items"][1]["last_wrong_at"]


def test_each_item_carries_hint_and_up_to_three_representative_records(client):
    register(client)
    owner = user_id(client)
    for index in range(5):
        # 失败复习发生在 4–8 天前、间隔只排 1 天：保持率都低于 0.7，仍未掌握。
        seed(owner, title=f"边界题{index}", tags=("边界",), created_days_ago=10 + index,
             reviews=((4 + index, 1, 1),))
    data = report(client)
    item = data["items"][0]
    assert item["name"] == "边界" and item["key"] == "边界"
    assert isinstance(item["hint"], str) and item["hint"].strip()
    representatives = item["ids"]
    assert len(representatives) == 3, "最多 3 条代表性记录"
    assert all(set(entry) == {"id", "title", "description"} for entry in representatives)
    # 代表性记录按最近出错排序：第一条是最近失败的（4 天前）。
    assert representatives[0]["title"] == "边界题0"
    assert representatives[0]["description"] == "边界题0的错因"


def test_a_record_without_failed_reviews_falls_back_to_created_time(client):
    register(client)
    owner = user_id(client)
    for _ in range(5):
        seed(owner, title="没复习过", tags=("复杂度",), created_days_ago=8)
    data = report(client)
    assert keys(data) == ["复杂度"]
    assert data["items"][0]["last_wrong_at"]


def test_other_users_data_never_leaks(client):
    register(client, "alice")
    seed(user_id(client), title="爱丽丝的题", tags=("边界",), created_days_ago=10)
    client.post("/api/auth/logout")
    register(client, "bob")
    data = report(client)
    assert data["items"] == []


def test_templates_are_complete_distinct_and_deterministic():
    # 每个已知错因类型至少 2 条备选提醒句。
    for key in typical_templates.KNOWN_HINTS:
        alternatives = typical_templates.KNOWN_HINTS[key]
        assert len(alternatives) >= 2
        assert len(set(alternatives)) == len(alternatives)
        assert all(isinstance(text, str) and text.strip() for text in alternatives)
    assert len(typical_templates.GENERIC_HINTS) >= 2
    # 按 key 稳定选择：同一 key 多次结果一致，不同 key 会分散到备选。
    assert typical_templates.hint_for("边界") == typical_templates.hint_for("边界")
    chosen = {typical_templates.hint_for(key) for key in typical_templates.KNOWN_HINTS}
    assert len(chosen) >= 2, "备选应被实际使用，而不是永远只取第一条"
    # 未知标签走通用模板，并把标签名带进句子。
    hint = typical_templates.hint_for("我自己编的错因")
    assert "我自己编的错因" in hint
    checklist = typical_templates.checklist()
    assert 6 <= len(checklist) <= 8
    assert all(isinstance(line, str) and line.strip() for line in checklist)
    assert checklist == typical_templates.checklist()


def test_aggregation_query_count_does_not_grow_with_record_count(client):
    from typical import typical_report

    def count_queries(record_count):
        register(client, f"user{record_count}")
        owner = user_id(client)
        for index in range(record_count):
            seed(owner, title=f"题{index}", tags=("边界", "粗心"), created_days_ago=10 + (index % 20),
                 reviews=((1 + (index % 9), 1 + (index % 3), 1),))
        statements = []
        with connect() as conn:
            conn.set_trace_callback(lambda _sql: statements.append(_sql))
            typical_report(conn, owner, "Asia/Shanghai", TODAY)
        return len(statements)

    small = count_queries(5)
    client.post("/api/auth/logout")
    large = count_queries(120)
    assert small == large, f"查询数随记录数增长：{small} vs {large}"
    assert small <= 6, f"聚合应当是常数级少量查询，实际 {small} 条"
