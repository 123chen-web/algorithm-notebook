"""题目来源链接：存进 source_url 字段，不再塞进思路；旧数据迁移；Anki 与热门题统计读新字段。"""
import pytest

import db
import hot_problems
import main
from test_app import client, register


def body(**extra):
    data = {"title": "两数之和", "zone": "算法", "language": "Python",
            "code": "print(1)", "thinking": "我的思路", "mistakes": ["漏了边界"]}
    data.update(extra)
    return data


def row(problem_id):
    with main.connect() as conn:
        return dict(conn.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone())


def test_create_stores_the_link_in_its_own_field_and_leaves_thinking_alone(client):
    register(client)
    created = client.post("/api/problems", json=body(source_url=" https://leetcode.cn/problems/two-sum/ "))
    assert created.status_code == 201
    saved = row(created.json()["id"])
    assert saved["source_url"] == "https://leetcode.cn/problems/two-sum/"
    assert saved["thinking"] == "我的思路"


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "ftp://x.com/a", "https://user:pw@evil.com/x", "not a url"])
def test_only_plain_http_links_are_accepted(client, bad):
    register(client)
    assert client.post("/api/problems", json=body(source_url=bad)).status_code == 422


def test_edit_keeps_the_link_unless_told_otherwise(client):
    register(client)
    pid = client.post("/api/problems", json=body(source_url="https://www.luogu.com.cn/problem/P1001")).json()["id"]
    edit = {k: v for k, v in body().items() if k != "mistakes"}
    assert client.put(f"/api/problems/{pid}", json=edit).status_code == 200
    assert row(pid)["source_url"] == "https://www.luogu.com.cn/problem/P1001"
    assert client.put(f"/api/problems/{pid}", json={**edit, "source_url": ""}).status_code == 200
    assert row(pid)["source_url"] == ""


def test_mistake_detail_carries_the_link(client):
    register(client)
    created = client.post("/api/problems", json=body(source_url="https://www.luogu.com.cn/problem/P1001"))
    mid = created.json()["mistake_ids"][0]
    assert client.get(f"/api/mistakes/{mid}").json()["source_url"] == "https://www.luogu.com.cn/problem/P1001"


def test_anki_url_prefers_the_field_then_falls_back_to_the_old_thinking_line():
    assert main.anki_url_from_source("https://a.com/x", "题目链接：https://b.com/y") == "https://a.com/x"
    assert main.anki_url_from_source("", "题目链接：https://b.com/y\n我的思路") == "https://b.com/y"
    assert main.anki_url_from_source("javascript:alert(1)", "") == ""


def test_hot_problem_link_text_ignores_extension_imports():
    mine = {"thinking": "思路", "source": "", "source_url": "https://leetcode.cn/problems/two-sum/"}
    assert hot_problems.link_text(mine).startswith("题目链接：https://leetcode.cn/problems/two-sum/")
    imported = {"thinking": "思路", "source": "leetcode", "source_url": "https://leetcode.cn/problems/two-sum/"}
    assert hot_problems.link_text(imported) == "思路"


def test_migration_moves_the_old_link_line_out_of_thinking(client):
    register(client)
    a = client.post("/api/problems", json=body(thinking="题目链接：https://leetcode.cn/problems/two-sum/\n我的思路")).json()["id"]
    b = client.post("/api/problems", json=body(thinking="题目链接：https://leetcode.cn/problems/two-sum/\n")).json()["id"]
    c = client.post("/api/problems", json=body(thinking="我提到了 题目链接：不是单独一行")).json()["id"]
    d = client.post("/api/problems", json=body(thinking="题目链接：https://a.com/1", source_url="https://keep.me/x")).json()["id"]
    with main.connect(write=True) as conn:
        db._apply_move_link_to_source_url(conn)
        db._apply_move_link_to_source_url(conn)  # 重复执行安全
    assert (row(a)["source_url"], row(a)["thinking"]) == ("https://leetcode.cn/problems/two-sum/", "我的思路")
    assert row(b)["source_url"] == "https://leetcode.cn/problems/two-sum/"
    assert row(b)["thinking"] == "（速记：思路待补）"
    assert (row(c)["source_url"], row(c)["thinking"]) == ("", "我提到了 题目链接：不是单独一行")
    assert row(d)["source_url"] == "https://keep.me/x", "已经有来源链接的不覆盖"
