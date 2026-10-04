"""昨日榜单：北京时间边界、同一错题封顶、排除规则、并列、自己的差距、缓存和查询次数。"""
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import db
import rank_board
import rank_cache
from rank_helpers import (
    NOON_YESTERDAY, NOW, add_mistake, add_problem, add_review, add_user,
    review_times, user_with_mistake, user_with_reviews,
)
from test_app import client, register  # noqa: F401  (client 是 pytest fixture)

PUBLIC_ENTRY_KEYS = {
    "rank", "user_id", "username", "avatar_version", "count", "streak_days", "praise", "is_me",
}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(rank_board, "now_utc", lambda: NOW)
    rank_cache.invalidate()
    yield
    rank_cache.invalidate()


def board(client):
    response = client.get("/api/rank/yesterday")
    assert response.status_code == 200
    return response.json()


def names(data):
    return [entry["username"] for entry in data["entries"]]


def test_yesterday_is_the_beijing_calendar_day_even_near_midnight(client):
    register(client)
    # 北京时间 2026-10-04 00:00 之后 1 秒，“昨日”仍是 10-03；用户自己的时区不参与。
    just_after_midnight = datetime(2026, 10, 3, 16, 0, 1, tzinfo=timezone.utc)
    assert rank_board.yesterday(just_after_midnight).isoformat() == "2026-10-03"
    just_before_midnight = datetime(2026, 10, 3, 15, 59, 59, tzinfo=timezone.utc)
    assert rank_board.yesterday(just_before_midnight).isoformat() == "2026-10-02"
    assert board(client)["day"] == "2026-10-03"
    assert board(client)["timezone"] == "Asia/Shanghai"


def test_review_times_around_beijing_midnight_are_counted_on_the_right_day(client):
    register(client)
    user_id = add_user("边界")
    problem = add_problem(user_id)
    for moment in (
        "2026-10-02T15:59:59+00:00",  # 北京 10-02 23:59:59：前天，不算
        "2026-10-02T16:00:00+00:00",  # 北京 10-03 00:00:00：昨天第一秒，算
        "2026-10-03T15:59:59+00:00",  # 北京 10-03 23:59:59：昨天最后一秒，算
        "2026-10-03T16:00:00+00:00",  # 北京 10-04 00:00:00：今天，不算
    ):
        add_review(add_mistake(problem), moment)
    entry = board(client)["entries"][0]
    assert (entry["username"], entry["count"]) == ("边界", 2)


def test_same_mistake_reviewed_many_times_in_a_day_counts_at_most_three(client):
    register(client)
    _, mistake = user_with_mistake("刷分")
    review_times(mistake, 9)
    other = add_mistake(add_problem(add_user("正常")))
    review_times(other, 2)
    data = board(client)
    assert {e["username"]: e["count"] for e in data["entries"]} == {"刷分": 3, "正常": 2}
    # 封顶是对“同一条错题”的：同一个人再复习别的错题仍然累加。
    extra = add_mistake(add_problem(add_user("刷分2")))
    review_times(extra, 4)
    rank_cache.invalidate()
    assert {e["username"]: e["count"] for e in board(client)["entries"]}["刷分2"] == 3


def test_cap_is_per_day_not_per_lifetime(client):
    register(client)
    _, mistake = user_with_mistake("累计")
    review_times(mistake, 5, at="2026-10-01T04:00:00+00:00")  # 前几天复习很多次
    review_times(mistake, 2)
    assert board(client)["entries"][0]["count"] == 2


def test_excluded_accounts_and_zero_review_users_never_appear(client):
    register(client)
    user_with_reviews("正式", 2)
    for name, flags in {
        "体验": {"trial": True}, "封禁": {"banned": True}, "注销": {"deleted": True},
        "不参与": {"opt_out": True},
    }.items():
        user_with_reviews(name, 30, **flags)
    add_user("没复习")
    assert names(board(client)) == ["正式"]


def test_ties_share_a_rank_and_the_next_rank_skips(client):
    register(client)
    for name, count in (("甲", 5), ("乙", 5), ("丙", 3), ("丁", 1)):
        user_with_reviews(name, count)
    ranks = [(e["username"], e["rank"]) for e in board(client)["entries"]]
    assert ranks == [("甲", 1), ("乙", 1), ("丙", 3), ("丁", 4)]


def test_top_ten_by_rank_ties_at_the_boundary_are_all_shown(client):
    register(client)
    for index in range(9):
        user_with_reviews(f"u{index}", 20 - index)  # 20 … 12，互不相同
    user_with_reviews("第十甲", 5)
    user_with_reviews("第十乙", 5)
    user_with_reviews("第十二", 4)
    data = board(client)
    assert len(data["entries"]) == 11
    assert [e["rank"] for e in data["entries"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10]
    ranks = {e["username"]: e["rank"] for e in data["entries"]}
    assert ranks["第十甲"] == ranks["第十乙"] == 10
    assert "第十二" not in ranks


def test_me_outside_the_top_ten_sees_the_gap(client):
    register(client)
    for index in range(10):
        user_with_reviews(f"u{index}", 12 - index)  # 12 … 3，第十名 3 次
    me = client.get("/api/me").json()
    mine = add_mistake(add_problem(me["id"]))
    review_times(mine, 2)
    result = board(client)["me"]
    assert result == {
        "count": 2, "rank": None, "in_top": False, "gap": 1, "is_trial": False, "opted_out": False,
    }


def test_me_in_the_top_ten_has_a_rank_and_is_marked(client):
    register(client)
    user_with_reviews("别人", 3)
    me = client.get("/api/me").json()
    review_times(add_mistake(add_problem(me["id"])), 2)
    data = board(client)
    assert data["me"]["in_top"] is True and data["me"]["rank"] == 2 and data["me"]["gap"] is None
    assert [(e["username"], e["is_me"]) for e in data["entries"]] == [("别人", False), ("alice", True)]


def test_me_without_reviews_needs_one_review_when_the_board_is_not_full(client):
    register(client)
    user_with_reviews("别人", 3)
    me = board(client)["me"]
    assert (me["count"], me["in_top"], me["gap"]) == (0, False, 1)


def test_opted_out_user_is_hidden_but_still_sees_own_count(client):
    register(client)
    me = client.get("/api/me").json()
    review_times(add_mistake(add_problem(me["id"])), 2)
    assert names(board(client)) == ["alice"]
    assert client.put("/api/me/public-rank", json={"participate": False}).json() == {"participate": False}
    data = board(client)
    assert data["entries"] == []
    assert data["me"] == {
        "count": 2, "rank": None, "in_top": False, "gap": None, "is_trial": False, "opted_out": True,
    }
    assert client.put("/api/me/public-rank", json={"participate": True}).status_code == 200
    assert names(board(client)) == ["alice"]


def test_trial_user_sees_own_count_but_never_ranks(client):
    register(client)
    with db.connect(write=True) as conn:
        conn.execute("UPDATE users SET is_trial = 1 WHERE username = 'alice'")
    me = client.get("/api/me").json()
    problem = add_problem(me["id"])
    for _ in range(4):
        add_review(add_mistake(problem), NOON_YESTERDAY)
    data = board(client)
    assert data["entries"] == []
    assert (data["me"]["count"], data["me"]["is_trial"], data["me"]["gap"]) == (4, True, None)


def test_response_only_exposes_public_fields(client):
    register(client, "alice", email="secret-mail@example.com")
    user_id = add_user("公开名", tz="America/Los_Angeles")
    problem = add_problem(user_id, thinking="我的私密思路", title="私密标题")
    review_times(add_mistake(problem), 2)
    data = board(client)
    assert {frozenset(entry) for entry in data["entries"]} == {frozenset(PUBLIC_ENTRY_KEYS)}
    text = json.dumps(data, ensure_ascii=False)
    for secret in ("secret-mail", "America/Los_Angeles", "私密", "易错点", "password"):
        assert secret not in text


def test_streak_counts_consecutive_beijing_days_through_yesterday(client):
    register(client)
    user_id = add_user("连续")
    problem = add_problem(user_id)
    for day in ("2026-10-03", "2026-10-02", "2026-10-01", "2026-09-29"):  # 9-30 断了
        add_review(add_mistake(problem), f"{day}T04:00:00+00:00")
    entry = board(client)["entries"][0]
    assert entry["streak_days"] == 3
    # 前一天晚上 23:30（北京）的复习属于前一个北京日。
    add_review(add_mistake(problem), "2026-09-30T15:30:00+00:00")
    rank_cache.invalidate()
    assert board(client)["entries"][0]["streak_days"] == 5


def test_praise_is_templated_deterministic_and_matches_rank_and_data():
    assert rank_board.praise(5, 42, 12) == "昨天复习了 42 次，连续第 12 天，稳！"
    assert rank_board.praise(5, 42, 12) == rank_board.praise(5, 42, 12)
    assert any(word in rank_board.praise(1, 10, 3) for word in ("榜首", "第一"))
    assert any(word in rank_board.praise(3, 10, 3) for word in ("前三", "名列前茅"))
    for rank, count, streak in ((1, 3, 1), (2, 9, 4), (4, 5, 40), (4, 5, 8), (6, 25, 1), (6, 5, 2), (6, 1, 1)):
        text = rank_board.praise(rank, count, streak)
        assert str(count) in text and "{" not in text and "}" not in text
    assert "连续第 1 天" not in rank_board.praise(6, 1, 1)


@contextmanager
def counted_selects(monkeypatch):
    statements = []

    @contextmanager
    def traced(*args, **kwargs):
        with db.connect(*args, **kwargs) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(rank_board, "connect", traced)
    yield statements


def selects(statements):
    return [s for s in statements if s.lstrip().upper().startswith("SELECT")]


def test_query_count_does_not_grow_with_the_number_of_users(client, monkeypatch):
    register(client)
    for index in range(3):
        user_with_reviews(f"少{index}", index + 1)
    with counted_selects(monkeypatch) as few:
        board(client)
    for index in range(40):
        user_with_reviews(f"多{index}", (index % 7) + 1)
    rank_cache.invalidate()
    with counted_selects(monkeypatch) as many:
        board(client)
    assert 0 < len(selects(few)) == len(selects(many)) <= 4


def test_result_is_cached_per_day_and_refreshed_when_the_day_changes(client, monkeypatch):
    register(client)
    user_with_reviews("缓存", 1)
    with counted_selects(monkeypatch) as first:
        board(client)
    with counted_selects(monkeypatch) as second:
        board(client)
        board(client)
    # 榜单本身命中缓存；只剩“我的次数”这一条查询。
    assert len(selects(first)) > len(selects(second)) == 2
    user_with_reviews("新来的", 5)
    assert names(board(client)) == ["缓存"], "同一天内新数据不会让缓存失效"
    # 北京时间日期变化 -> 新的一天重新计算
    monkeypatch.setattr(rank_board, "now_utc", lambda: NOW + timedelta(days=1))
    next_day = board(client)
    assert next_day["day"] == "2026-10-04"
    assert next_day["entries"] == []


def test_settings_change_and_ban_invalidate_the_cache(client):
    register(client)
    me = client.get("/api/me").json()
    review_times(add_mistake(add_problem(me["id"])), 2)
    other = user_with_reviews("会被封", 3)
    assert names(board(client)) == ["会被封", "alice"]
    client.put("/api/me/public-rank", json={"participate": False})
    assert names(board(client)) == ["会被封"], "设置变化立刻生效"
    client.put("/api/me/public-rank", json={"participate": True})
    assert names(board(client)) == ["会被封", "alice"]
    with db.connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (me["id"],))
    assert client.post(f"/api/admin/users/{other}/ban").status_code == 200
    assert names(board(client)) == ["alice"]


def test_cache_expires_after_the_ttl(client, monkeypatch):
    register(client)
    user_with_reviews("甲", 1)
    clock = [1000.0]
    monkeypatch.setattr(rank_cache, "_clock", lambda: clock[0])
    assert names(board(client)) == ["甲"]
    user_with_reviews("乙", 2)
    assert names(board(client)) == ["甲"]
    clock[0] += rank_cache.CACHE_TTL_SECONDS + 1
    assert names(board(client)) == ["乙", "甲"]


def test_public_rank_setting_requires_login_csrf_and_a_real_boolean(client):
    anonymous = type(client)(client.app)  # 没有 cookie、没有 CSRF 头
    try:
        assert anonymous.get("/api/rank/yesterday").status_code == 401
        assert anonymous.put("/api/me/public-rank", json={"participate": False}).status_code == 403
    finally:
        anonymous.close()
    client.cookies.clear()
    assert client.put("/api/me/public-rank", json={"participate": False}).status_code == 401
    register(client)
    for bad in ({"participate": "false"}, {"participate": 0}, {}, {"participate": True, "x": 1}):
        assert client.put("/api/me/public-rank", json=bad).status_code == 422


def test_streak_leaderboard_also_hides_opted_out_users(client):
    register(client)
    other = add_user("匿名榜")
    mine = client.get("/api/me").json()
    # 今天按 test_app 的固定日期 2026-09-19 计算：给两个人各写一条当天复习。
    for user_id in (other, mine["id"]):
        add_review(add_mistake(add_problem(user_id)), "2026-09-19T04:00:00+00:00")
    assert [e["streak_days"] for e in client.get("/api/leaderboard").json()["entries"]] == [1, 1]
    client.put("/api/me/public-rank", json={"participate": False})
    result = client.get("/api/leaderboard").json()
    assert len(result["entries"]) == 1
    assert result["me"]["streak_days"] == 1 and result["me"]["rank"] is None
