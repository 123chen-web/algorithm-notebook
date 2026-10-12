"""今日推荐的 AtCoder 来源：数据抓取/裁剪 + 来源切换 + 选题 + 状态接口。

AtCoder 数据来自社区的 AtCoder Problems；测试里绝不碰真实网络。
"""
import io
import json
from datetime import timedelta

import pytest

import ac_problems
import recommend
from db import connect
from test_app import client, register
from test_recommend import TODAY, cf_problem, get, me, seed, write_cache


def ac(contest, letter, difficulty, title=None):
    pid = f"{contest}_{letter}"
    return {
        "id": pid, "contest_id": contest, "index": letter.upper(),
        "title": title or f"{letter.upper()}. 题目 {pid}", "difficulty": difficulty,
    }


def write_ac(problems):
    ac_problems.save_cache(problems)


def set_source(client, source):
    response = client.put("/api/recommend/source", json={"source": source})
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------ 数据层

def test_clip_difficulty_matches_site_display():
    assert ac_problems.clip_difficulty(None) is None
    assert ac_problems.clip_difficulty(1200) == 1200
    assert ac_problems.clip_difficulty(400) == 400
    # 低于 400 的平滑成正数，且单调：原值越小，显示值越小
    assert 0 < ac_problems.clip_difficulty(-800) < ac_problems.clip_difficulty(0) < 400


def test_build_problems_filters_and_trims():
    listing = [
        {"id": "abc100_c", "contest_id": "abc100", "problem_index": "C", "title": "C. Test", "point": 300},
        {"id": "abc100_d", "contest_id": "abc100", "problem_index": "D", "title": "D. Exp"},
        {"id": "abc100_e", "contest_id": "abc100", "problem_index": "E", "title": "E. NoModel"},
        {"id": "dp_a", "contest_id": "dp", "problem_index": "A", "title": "A. Frog"},
        {"id": "../etc", "contest_id": "abc100", "problem_index": "A"},
        "垃圾",
    ]
    models = {
        "abc100_c": {"difficulty": 900, "is_experimental": False, "slope": 1},
        "abc100_d": {"difficulty": 1500, "is_experimental": True},
        "dp_a": {"difficulty": 100, "is_experimental": False},
    }
    assert ac_problems.build_problems(listing, models) == [
        {"id": "abc100_c", "contest_id": "abc100", "index": "C", "title": "C. Test", "difficulty": 900}
    ]


def test_build_problems_rejects_wrong_structure():
    with pytest.raises(ValueError):
        ac_problems.build_problems({"a": 1}, {})


def test_fetch_uses_fixed_urls_and_waits_between_requests():
    seen, slept = [], []

    def opener(request, timeout=None):
        seen.append(request.full_url)
        body = (
            [{"id": "abc100_a", "contest_id": "abc100", "problem_index": "A", "title": "A. x"}]
            if request.full_url == ac_problems.PROBLEMS_URL
            else {"abc100_a": {"difficulty": -200, "is_experimental": False}}
        )

        class Response:
            def __init__(self):
                self.stream = io.BytesIO(json.dumps(body).encode())

            def getcode(self):
                return 200

            def read(self, size=-1):
                return self.stream.read(size)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()

    problems = ac_problems.fetch_problems(opener=opener, sleep=slept.append)
    assert seen == [ac_problems.PROBLEMS_URL, ac_problems.MODELS_URL]
    assert slept == [ac_problems.REQUEST_GAP_SECONDS]
    assert problems[0]["id"] == "abc100_a" and problems[0]["difficulty"] > 0


def test_refresh_failure_keeps_old_cache(client, monkeypatch):
    write_ac([ac("abc100", "c", 900)])
    monkeypatch.setattr(ac_problems, "fetch_problems", lambda: (_ for _ in ()).throw(ValueError("挂了")))
    assert ac_problems.refresh() == 1
    assert ac_problems.load_cache()["problems"][0]["id"] == "abc100_c"
    # 数量异常少也不覆盖旧缓存
    monkeypatch.setattr(ac_problems, "fetch_problems", lambda: [ac("abc100", "a", 100)])
    assert ac_problems.refresh() == 1
    assert len(ac_problems.load_cache()["problems"]) == 1
    assert ac_problems.load_cache()["problems"][0]["id"] == "abc100_c"


def test_corrupt_cache_is_none(client):
    ac_problems.cache_path().write_text("{坏", encoding="utf-8")
    assert ac_problems.load_cache() is None


# ------------------------------------------------------------ 接口

def test_default_source_is_codeforces_and_unchanged(client):
    register(client)
    write_ac([ac("abc100", "c", 800)])
    assert get(client)["source"] == "cf"
    assert all(item.get("oj") != "atcoder" for item in get(client)["items"])


def test_source_endpoints_require_login_and_validate(client):
    assert client.put("/api/recommend/source", json={"source": "cf"}).status_code == 401
    assert client.post("/api/recommend/atcoder/abc100_c", json={"state": "done"}).status_code == 401
    register(client)
    assert client.put("/api/recommend/source", json={"source": "leetcode"}).status_code == 422
    assert client.post("/api/recommend/atcoder/abc100_c", json={"state": "x"}).status_code == 422


def test_atcoder_source_gives_one_stable_item_in_band(client):
    register(client)
    write_ac([ac("abc100", "c", 800), ac("abc101", "c", 600), ac("abc102", "d", 2400), ac("abc103", "a", 50)])
    set_source(client, "atcoder")
    first = get(client)
    assert first["source"] == "atcoder" and len(first["items"]) == 1
    item = first["items"][0]
    assert item["oj"] == "atcoder" and item["tags"] == []
    assert item["name"] in {"ABC100 C · 题目 abc100_c", "ABC101 C · 题目 abc101_c"}
    assert item["id"] in {"abc100_c", "abc101_c"}  # 2400 太难、50 太简单，都不在难度带里
    assert item["url"] == f"https://atcoder.jp/contests/{item['contest_id']}/tasks/{item['id']}"
    assert item["reason"] and "标签" in item["reason"]
    assert get(client) == first


def test_atcoder_next_day_never_repeats_and_runs_out_gracefully(client):
    register(client)
    owner = me(client)["id"]
    write_ac([ac("abc100", "c", 800), ac("abc101", "c", 700)])
    set_source(client, "atcoder")
    seen = []
    with connect(write=True) as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (owner,)).fetchone()
        for offset in range(3):
            items, hint = recommend.recommend_for_today(conn, user, TODAY + timedelta(days=offset))
            if offset < 2:
                assert len(items) == 1 and hint == ""
                seen.append(items[0]["id"])
            else:
                assert items == [] and hint
    assert sorted(seen) == ["abc100_c", "abc101_c"]


def test_atcoder_skips_tasks_already_in_the_notebook(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, title="已经错过的")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE problems SET source_url = ? WHERE user_id = ?",
            ("https://atcoder.jp/contests/abc100/tasks/abc100_c", owner),
        )
    write_ac([ac("abc100", "c", 800), ac("abc101", "c", 700)])
    set_source(client, "atcoder")
    assert get(client)["items"][0]["id"] == "abc101_c"


def test_both_gives_codeforces_and_atcoder(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",))
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    write_ac([ac("abc100", "c", 800)])
    set_source(client, "both")
    data = get(client)
    assert [item["id"] for item in data["items"]] == ["4A", "abc100_c"]
    assert data["source"] == "both"


def test_atcoder_without_cache_gives_hint(client):
    register(client)
    set_source(client, "atcoder")
    assert get(client) == {"items": [], "hint": recommend.HINT_NO_AC_CACHE, "source": "atcoder"}


def test_atcoder_state_update_is_per_user(client):
    register(client)
    write_ac([ac("abc100", "c", 800)])
    set_source(client, "atcoder")
    item = get(client)["items"][0]
    assert client.post(f"/api/recommend/atcoder/{item['id']}", json={"state": "done"}).status_code == 200
    assert get(client)["items"][0]["state"] == "done"
    assert client.post("/api/recommend/atcoder/abc999_z", json={"state": "done"}).status_code == 404


def test_switching_source_midday_shows_the_other_source(client):
    register(client)
    owner = me(client)["id"]
    seed(owner, tags=("BFS",))
    write_cache([cf_problem(4, "A", 800, ["graphs"])])
    write_ac([ac("abc100", "c", 800)])
    assert [i["id"] for i in get(client)["items"]] == ["4A"]
    set_source(client, "atcoder")
    assert [i["id"] for i in get(client)["items"]] == ["abc100_c"]
    set_source(client, "cf")
    assert [i["id"] for i in get(client)["items"]] == ["4A"]
