"""本周热门题目：阈值、去重、白名单站点、不泄露用户信息、服务端解析与 Capture.parse 等价。"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import hot_problems
import rank_board
import rank_cache
from rank_helpers import NOON_YESTERDAY, NOW, add_mistake, add_problem, add_review, add_user
from test_app import client, register  # noqa: F401

TESTS = Path(__file__).resolve().parent
TWO_SUM = "题目链接：https://leetcode.cn/problems/two-sum/\n"
IN_WINDOW = "2026-09-30T04:00:00+00:00"          # 北京时间 9-30，窗口 9-27 … 10-03 以内
BEFORE_WINDOW = "2026-09-26T15:59:59+00:00"      # 北京时间 9-26 23:59:59，窗口外
AFTER_WINDOW = "2026-10-03T16:00:00+00:00"       # 北京时间 10-04 00:00，窗口外


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(rank_board, "now_utc", lambda: NOW)
    rank_cache.invalidate()
    yield
    rank_cache.invalidate()


def hot(client):
    response = client.get("/api/rank/hot-problems")
    assert response.status_code == 200
    return response.json()


def record(username, thinking, created_at=IN_WINDOW, **flags):
    user_id = add_user(username, **flags)
    return user_id, add_problem(user_id, thinking=thinking, created_at=created_at)


def five_users(thinking=TWO_SUM, **flags):
    return [record(f"人{index}", thinking, **flags)[0] for index in range(5)]


# ---------------- 解析：与 static/capture.js 等价 ----------------
def test_server_parse_matches_capture_js_on_every_shared_case():
    if shutil.which("node") is None:
        pytest.skip("需要 node 来运行 static/capture.js")
    cases = json.loads((TESTS / "capture_parse_cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 100
    run = subprocess.run(
        ["node", str(TESTS / "capture_parse_node.cjs")],
        input=json.dumps(cases), capture_output=True, text=True, check=True, encoding="utf-8",
    )
    expected = json.loads(run.stdout)
    assert sum(item is not None for item in expected) >= 40, "用例里要有足够多能识别的链接"
    assert sum(item is None for item in expected) >= 40, "也要有足够多被拒绝的链接"
    mismatches = [
        (case, js, hot_problems.parse(case))
        for case, js in zip(cases, expected) if hot_problems.parse(case) != js
    ]
    assert mismatches == []


def test_parse_covers_every_whitelisted_site():
    sample = {
        "https://leetcode.cn/problems/two-sum/": ("leetcode", "two-sum", "LeetCode · Two Sum"),
        "https://www.luogu.com.cn/problem/P1001": ("luogu", "P1001", "洛谷 · P1001"),
        "https://www.nowcoder.com/practice/abc123": ("nowcoder", "abc123", "牛客 · 题目"),
        "https://codeforces.com/contest/1234/problem/B1": ("codeforces", "1234B1", "Codeforces · 1234B1"),
        "https://atcoder.jp/contests/abc001/tasks/abc001_a": ("atcoder", "abc001_a", "AtCoder · abc001_a"),
    }
    for url, (source, ident, title) in sample.items():
        found = hot_problems.parse(url)
        assert (found["source"], found["id"], found["title"]) == (source, ident, title)


def test_parse_rejects_unsafe_or_unknown_links():
    for bad in (
        "javascript:alert(1)", "https://evil.com/problems/two-sum/",
        "https://user:pw@leetcode.cn/problems/two-sum/", "https://leetcode.cn:8443/problems/two-sum/",
        "https://leetcode.cn.evil.com/problems/two-sum/", "", None,
    ):
        assert hot_problems.parse(bad) is None


# ---------------- 阈值、窗口、去重 ----------------
def test_problem_needs_enough_distinct_users_in_the_window(client):
    register(client)
    for index in range(4):
        record(f"人{index}", TWO_SUM)
    assert hot(client)["entries"] == [], "4 人不够 5 人"
    record("人4", TWO_SUM)
    rank_cache.invalidate()
    entry = hot(client)["entries"][0]
    assert (entry["source"], entry["name"], entry["users"]) == ("leetcode", "Two Sum", 5)


def test_threshold_is_a_constant_that_tests_can_lower(client, monkeypatch):
    register(client)
    for index in range(2):
        record(f"人{index}", TWO_SUM)
    assert hot(client)["entries"] == []
    monkeypatch.setattr(hot_problems, "HOT_MIN_USERS", 2)
    assert [e["users"] for e in hot(client)["entries"]] == [2]
    assert hot(client)["min_users"] == 2


def test_one_user_counts_once_however_many_records_or_reviews(client):
    register(client)
    for index in range(4):
        record(f"人{index}", TWO_SUM)
    user_id, _ = record("刷屏", TWO_SUM)
    for _ in range(6):
        problem = add_problem(user_id, thinking=TWO_SUM, created_at=IN_WINDOW)
        add_review(add_mistake(problem), NOON_YESTERDAY)
    entry = hot(client)["entries"][0]
    assert entry["users"] == 5, "同一用户多条记录 + 多次复习只算 1 人"
    # 去掉那个人，4 个人就不够了
    rank_cache.invalidate()
    from db import connect
    with connect(write=True) as conn:
        conn.execute("DELETE FROM problems WHERE user_id = ?", (user_id,))
    assert hot(client)["entries"] == []


def test_reviewing_an_older_record_also_counts_as_engaging_with_the_problem(client):
    register(client)
    for index in range(3):
        record(f"新{index}", TWO_SUM)
    for index in range(2):
        user_id, problem = record(f"旧{index}", TWO_SUM, created_at="2026-01-01T00:00:00+00:00")
        add_review(add_mistake(problem), NOON_YESTERDAY)
    assert hot(client)["entries"][0]["users"] == 5


def test_activity_outside_the_seven_beijing_day_window_is_ignored(client):
    register(client)
    for index, moment in enumerate((BEFORE_WINDOW, AFTER_WINDOW, IN_WINDOW, IN_WINDOW, IN_WINDOW)):
        record(f"人{index}", TWO_SUM, created_at=moment)
    assert hot(client)["entries"] == []
    data = hot(client)
    assert (data["from"], data["to"]) == ("2026-09-27", "2026-10-03")


def test_same_problem_written_differently_is_merged(client):
    register(client)
    links = (
        "https://leetcode.cn/problems/two-sum/", "https://leetcode.com/problems/two-sum/description/?envType=daily",
        "http://www.leetcode.cn/problems/Two-Sum", "https://leetcode-cn.com/problems/two-sum/solution/",
        "https://leetcode.cn/problems/two-sum/?utm_source=x",
    )
    for index, link in enumerate(links):
        record(f"人{index}", f"我的思路\n题目链接：{link}\n后面还有话")
    entries = hot(client)["entries"]
    assert len(entries) == 1 and entries[0]["users"] == 5
    assert entries[0]["url"] == "https://leetcode.cn/problems/two-sum/"


def test_only_link_lines_starting_with_the_prefix_count(client):
    register(client)
    five_users("我参考了 https://leetcode.cn/problems/two-sum/ 这道题\n链接：https://leetcode.cn/problems/two-sum/")
    assert hot(client)["entries"] == []


def test_ordering_by_user_count_then_stable_and_top_ten_only(client):
    register(client)
    for index in range(12):
        for person in range(5 + (12 - index)):
            record(f"p{index}-{person}", f"题目链接：https://leetcode.cn/problems/problem-{index:02d}/")
    entries = hot(client)["entries"]
    assert len(entries) == 10
    assert [e["users"] for e in entries] == sorted([e["users"] for e in entries], reverse=True)
    assert entries[0]["name"] == "Problem 00" and entries[0]["users"] == 17


# ---------------- 排除与隐私 ----------------
def test_excluded_users_do_not_count(client):
    register(client)
    for index in range(2):
        record(f"正式{index}", TWO_SUM)
    for flags in ({"trial": True}, {"banned": True}, {"deleted": True}, {"opt_out": True}):
        record(f"排除{list(flags)[0]}", TWO_SUM, **flags)
    assert hot(client)["entries"] == []
    for index in range(3):
        record(f"再来{index}", TWO_SUM)
    rank_cache.invalidate()
    assert hot(client)["entries"][0]["users"] == 5


def test_response_has_no_user_information_or_user_written_text(client):
    register(client, "alice", email="secret-mail@example.com")
    for index in range(5):
        record(f"隐私用户{index}", f"我的私密思路 {index}\n{TWO_SUM}", created_at=IN_WINDOW)
    data = hot(client)
    entry = data["entries"][0]
    assert set(entry) == {"source", "source_label", "name", "title", "users", "url"}
    text = json.dumps(data, ensure_ascii=False)
    for secret in ("隐私用户", "私密思路", "我的标题", "secret-mail"):
        assert secret not in text
    assert set(data) == {"from", "to", "timezone", "min_users", "entries"}


def test_links_are_canonical_and_only_point_at_whitelisted_hosts(client):
    register(client)
    cases = {
        "https://leetcode.cn/problems/two-sum/description/?utm_source=evil&token=SECRET": "https://leetcode.cn/problems/two-sum/",
        "https://leetcode.cn/problems/two-sum/solutions/123/%E9%A2%98%E8%A7%A3/": "https://leetcode.cn/problems/two-sum/",
        "https://www.luogu.com.cn/problem/P1001?contestId=1": "https://www.luogu.com.cn/problem/P1001",
        "https://www.nowcoder.com/practice/abc123?x=1": "https://www.nowcoder.com/practice/abc123",
        "https://codeforces.com/contest/1234/problem/B1": "https://codeforces.com/problemset/problem/1234/B1",
        "https://codeforces.com/gym/100102/problem/c": "https://codeforces.com/gym/100102/problem/C",
        "https://atcoder.jp/contests/abc001/tasks/abc001_a": "https://atcoder.jp/contests/abc001/tasks/abc001_a",
    }
    for number, link in enumerate(cases):
        for index in range(5):
            record(f"站{number}-{index}", f"题目链接：{link}")
    # 用户写的非法 / 非白名单链接不会进入榜单
    for index in range(5):
        record(f"坏{index}", "题目链接：https://evil.example/problems/two-sum/\n题目链接：javascript:alert(1)")
    entries = hot(client)["entries"]
    assert {e["url"] for e in entries} == set(cases.values())
    assert len(entries) == len(set(cases.values()))
    for entry in entries:
        host = re.match(r"https://([^/]+)/", entry["url"]).group(1)
        assert host in hot_problems.ALLOWED_LINK_HOSTS
        assert "?" not in entry["url"] and "SECRET" not in entry["url"]
    by_source = {e["source"]: e for e in entries}
    assert by_source["nowcoder"]["name"] == "abc123"
    assert by_source["leetcode"]["source_label"] == "LeetCode"


def test_hot_problems_are_cached_per_day(client, monkeypatch):
    register(client)
    five_users()
    assert hot(client)["entries"][0]["users"] == 5
    record("新人", TWO_SUM)
    assert hot(client)["entries"][0]["users"] == 5, "同一天内读缓存"
    monkeypatch.setattr(rank_board, "now_utc", lambda: NOW.replace(day=5))
    assert hot(client)["entries"][0]["users"] == 6
