"""讨论区主页的数据接口：分区、摘要、状态、筛选、排序、翻页和统计。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import main
from db import connect
from test_app import register
from test_forum import PASSWORD, create_post
from test_forum_threads import client  # noqa: F401  (pytest fixture)


NOW = datetime.now(timezone.utc)
LIST_FIELDS = {
    "id", "title", "created_at", "user_id", "username", "avatar_version", "has_avatar",
    "zone", "excerpt", "comment_count", "last_activity_at", "last_commenter",
    "participants", "participant_count", "has_code", "solved", "helpful_total",
    "hot", "is_mine",
}
PERSON_FIELDS = {"user_id", "username", "avatar_version", "has_avatar"}


def ago(**delta):
    return (NOW - timedelta(**delta)).isoformat(timespec="seconds")


class World:
    """按需注册用户（注册要做密码哈希，很慢）；当前登录用户始终是 viewer，除非 act_as。"""

    def __init__(self, client):
        self.client = client
        self.users = {}
        self.add_user("viewer")
        self.act_as("viewer")

    def add_user(self, name):
        previous = self.client.cookies.get("session")
        self.client.cookies.clear()
        main.reset_rate_limits()  # 注册按 IP 限流，造多个用户时逐个清掉
        info = register(self.client, name)
        self.users[name] = {"id": info["id"], "session": self.client.cookies.get("session")}
        self.client.cookies.clear()
        if previous:
            self.client.cookies.set("session", previous)

    def act_as(self, name):
        if name not in self.users:
            self.add_user(name)
        self.client.cookies.clear()
        self.client.cookies.set("session", self.users[name]["session"])

    def uid(self, name):
        if name not in self.users:
            self.add_user(name)
        return self.users[name]["id"]

    def post(self, author, title, created_at=None, body="正文", zone=None, deleted=False):
        author_id = self.uid(author)  # 先注册（要写库），再开写事务
        with connect(write=True) as conn:
            return conn.execute(
                "INSERT INTO posts(user_id, title, body, zone, created_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (author_id, title, body, zone, created_at or ago(days=1),
                 ago(hours=1) if deleted else None),
            ).lastrowid

    def comment(self, post_id, author, created_at=None, body="评论", deleted=False):
        author_id = self.uid(author)
        with connect(write=True) as conn:
            return conn.execute(
                "INSERT INTO post_comments(post_id, user_id, body, created_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (post_id, author_id, body, created_at or ago(hours=2),
                 ago(minutes=5) if deleted else None),
            ).lastrowid

    def vote(self, comment_id, voter):
        voter_id = self.uid(voter)
        with connect(write=True) as conn:
            conn.execute(
                "INSERT INTO comment_votes(comment_id, user_id, created_at) VALUES (?, ?, ?)",
                (comment_id, voter_id, ago(minutes=1)),
            )

    def accept(self, post_id, comment_id):
        with connect(write=True) as conn:
            conn.execute("UPDATE posts SET accepted_comment_id = ? WHERE id = ?",
                         (comment_id, post_id))

    def list(self, **params):
        response = self.client.get("/api/posts", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def by_title(self, title, **params):
        found = [p for p in self.list(limit=50, **params)["posts"] if p["title"] == title]
        assert len(found) == 1, found
        return found[0]


@pytest.fixture
def world(client):  # noqa: F811
    return World(client)


# ---------------------------------------------------------------- 数据集

def seed_matrix(world):
    """viewer 视角：每个帖子的预期值由 expected_rows() 独立推出。"""
    spec = {}
    p = world.post("viewer", "P1 我的无人回复", ago(days=10), zone="算法")
    spec["P1"] = dict(id=p, comments=[], accepted=None)
    p = world.post("bob", "P2 已解决", ago(days=5), zone="算法")
    mine = world.comment(p, "viewer", ago(days=2))
    world.comment(p, "carol", ago(days=1))
    world.vote(mine, "bob")
    world.vote(mine, "carol")
    world.accept(p, mine)
    spec["P2"] = dict(id=p, accepted=mine, comments=[("viewer", ago(days=2)), ("carol", ago(days=1))],
                      votes=2)
    p = world.post("carol", "P3 热门", ago(days=3), zone="前端")
    comments = []
    for index, name in enumerate(["bob", "dave", "bob", "erin", "dave", "frank"]):
        when = ago(hours=20 - index)
        world.comment(p, name, when)
        comments.append((name, when))
    spec["P3"] = dict(id=p, comments=comments, accepted=None)
    p = world.post("dave", "P4 无分区待回复", ago(days=2))
    spec["P4"] = dict(id=p, comments=[], accepted=None)
    p = world.post("bob", "P5 参与过", ago(days=1), zone="数据库")
    world.comment(p, "viewer", ago(hours=10))
    spec["P5"] = dict(id=p, comments=[("viewer", ago(hours=10))], accepted=None)
    p = world.post("viewer", "P6 我的最近活跃", ago(days=12), zone="前端")
    world.comment(p, "bob", ago(hours=1))
    spec["P6"] = dict(id=p, comments=[("bob", ago(hours=1))], accepted=None)
    p = world.post("erin", "P7 评论全删除", ago(days=4), zone="算法")
    world.comment(p, "bob", ago(days=3), deleted=True)
    spec["P7"] = dict(id=p, comments=[], accepted=None)
    for key, item in spec.items():
        item["title"] = next(t for t in TITLES if t.startswith(key))
    world.post("bob", "P8 已删除的帖子", ago(hours=1), zone="算法", deleted=True)
    return spec


TITLES = ["P1 我的无人回复", "P2 已解决", "P3 热门", "P4 无分区待回复", "P5 参与过",
          "P6 我的最近活跃", "P7 评论全删除"]
OWNERS = {"P1": "viewer", "P2": "bob", "P3": "carol", "P4": "dave", "P5": "bob",
          "P6": "viewer", "P7": "erin"}
CREATED = {"P1": 10, "P2": 5, "P3": 3, "P4": 2, "P5": 1, "P6": 12, "P7": 4}  # 天前


def expected_rows(spec):
    """独立于实现的参考模型：用 Python 重新推出筛选 / 排序所需的值。"""
    rows = []
    for key, item in spec.items():
        created = NOW - timedelta(days=CREATED[key])
        last = max([created] + [NOW - (NOW - datetime.fromisoformat(when)) for _n, when in item["comments"]])
        recent = sum(
            1 for _n, when in item["comments"]
            if datetime.fromisoformat(when) >= NOW - timedelta(days=7)
        )
        rows.append(dict(
            key=key, id=item["id"], owner=OWNERS[key], created=created, last=last,
            count=len(item["comments"]), solved=item["accepted"] is not None,
            score=recent + item.get("votes", 0),
            participated=OWNERS[key] == "viewer" or any(n == "viewer" for n, _w in item["comments"]),
        ))
    return rows


FILTERS = {
    "all": lambda r: True,
    "unanswered": lambda r: r["count"] == 0,
    "solved": lambda r: r["solved"],
    "mine": lambda r: r["owner"] == "viewer",
    "participated": lambda r: r["participated"],
}
SORTS = {
    "activity": lambda r: (r["last"], r["id"]),
    "new": lambda r: (r["created"], r["id"]),
    "hot": lambda r: (r["score"], r["last"], r["id"]),
}


@pytest.mark.parametrize("sort", list(SORTS))
@pytest.mark.parametrize("filter_name", list(FILTERS))
def test_every_filter_and_sort_combination(world, filter_name, sort):
    spec = seed_matrix(world)
    wanted = sorted(
        (r for r in expected_rows(spec) if FILTERS[filter_name](r)),
        key=SORTS[sort], reverse=True,
    )
    data = world.list(filter=filter_name, sort=sort)
    assert [p["id"] for p in data["posts"]] == [r["id"] for r in wanted]
    assert data["total"] == len(wanted)
    assert data["has_more"] is False
    # 已删除的帖子永远不出现
    assert "P8 已删除的帖子" not in {p["title"] for p in data["posts"]}


def test_default_call_is_activity_sorted_all_posts(world):
    seed_matrix(world)
    default = world.list()
    explicit = world.list(sort="activity", filter="all")
    assert default == explicit
    assert default["posts"][0]["title"].startswith("P6")  # 最近一小时有评论


def test_item_shape_is_exactly_the_contract(world):
    seed_matrix(world)
    data = world.list()
    assert set(data) == {"posts", "total", "has_more", "counts", "zone_counts"}
    for post in data["posts"]:
        assert set(post) == LIST_FIELDS
        assert set(post["participants"][0]) == PERSON_FIELDS
        if post["last_commenter"] is not None:
            assert set(post["last_commenter"]) == PERSON_FIELDS
    text = str(data)
    assert "@example.com" not in text and "password" not in text


def test_item_values_for_each_state(world):
    spec = seed_matrix(world)
    solved = world.by_title("P2 已解决")
    assert solved["solved"] is True and solved["helpful_total"] == 2
    assert solved["comment_count"] == 2 and solved["is_mine"] is False
    assert solved["zone"] == "算法"
    mine = world.by_title("P1 我的无人回复")
    assert mine["is_mine"] is True and mine["comment_count"] == 0 and mine["solved"] is False
    assert mine["last_commenter"] is None and mine["last_activity_at"] == mine["created_at"]
    assert world.by_title("P4 无分区待回复")["zone"] is None
    hot = world.by_title("P3 热门")
    assert hot["hot"] is True and hot["comment_count"] == 6
    assert world.by_title("P5 参与过")["hot"] is False
    # 评论全被删除：和没人回复一样
    gone = world.by_title("P7 评论全删除")
    assert gone["comment_count"] == 0 and gone["last_commenter"] is None
    assert gone["participants"][0]["username"] == "erin" and gone["participant_count"] == 1
    assert spec  # 数据集已建立


# ---------------------------------------------------------------- 分区 / 搜索

def test_zone_filter_and_none(world):
    seed_matrix(world)
    assert {p["title"][:2] for p in world.list(zone="算法")["posts"]} == {"P1", "P2", "P7"}
    assert {p["title"][:2] for p in world.list(zone="前端")["posts"]} == {"P3", "P6"}
    assert [p["title"][:2] for p in world.list(zone="none")["posts"]] == ["P4"]
    assert world.list(zone="后端") ["posts"] == []
    assert world.list(zone="后端")["total"] == 0


@pytest.mark.parametrize("zone", ["不存在", "", "None", "算法 ", "算法;DROP", "ALL"])
def test_invalid_zone_is_rejected(world, zone):
    response = world.client.get("/api/posts", params={"zone": zone})
    assert response.status_code == 400
    assert response.json() == {"detail": "分区不存在"}


@pytest.mark.parametrize("params", [
    {"sort": "oldest"}, {"sort": ""}, {"filter": "everyone"}, {"filter": "mine; --"},
    {"limit": 0}, {"limit": 51}, {"limit": "x"}, {"offset": -1}, {"offset": "x"},
])
def test_invalid_parameters_are_422(world, params):
    assert world.client.get("/api/posts", params=params).status_code == 422


def test_query_stacks_with_filter_and_zone(world):
    seed_matrix(world)
    world.post("viewer", "二分查找笔记", ago(hours=3), body="我的二分", zone="算法")
    world.post("bob", "二分查找问题", ago(hours=4), body="别人的二分", zone="算法")
    world.post("bob", "二分查找前端版", ago(hours=5), body="x", zone="前端")
    titles = lambda **p: sorted(x["title"] for x in world.list(**p)["posts"])  # noqa: E731
    assert titles(q="二分") == ["二分查找前端版", "二分查找笔记", "二分查找问题"]
    assert titles(q="二分", filter="mine") == ["二分查找笔记"]
    assert titles(q="二分", zone="算法") == ["二分查找笔记", "二分查找问题"]
    assert titles(q="二分", zone="算法", filter="unanswered") == ["二分查找笔记", "二分查找问题"]
    assert titles(q="二分", zone="前端", filter="mine") == []
    assert titles(q="二分", zone="none") == []
    assert world.list(q="二分", filter="mine")["total"] == 1


def test_query_wildcards_stay_literal_with_other_conditions(world):
    world.post("viewer", "100% 通过率", zone="算法")
    world.post("viewer", "1000 通过率", zone="算法")
    world.post("bob", "a_b 下划线", zone="算法")
    world.post("bob", "axb 下划线", zone="算法")
    assert [p["title"] for p in world.list(q="100%", filter="mine")["posts"]] == ["100% 通过率"]
    assert [p["title"] for p in world.list(q="a_b", zone="算法")["posts"]] == ["a_b 下划线"]
    assert world.list(q="%")["total"] == 1


# ---------------------------------------------------------------- 翻页

def test_pagination_is_stable_and_complete(world):
    # 同一秒创建的帖子：靠 id 保持稳定顺序，翻页不重不漏。
    same_second = ago(hours=5)
    ids = [world.post("viewer", f"批量 {i:02d}", same_second) for i in range(25)]
    for sort in SORTS:
        seen = []
        for offset in range(0, 25, 10):
            page = world.list(sort=sort, limit=10, offset=offset)
            assert page["total"] == 25
            assert page["has_more"] is (offset + 10 < 25)
            assert len(page["posts"]) == min(10, 25 - offset)
            seen += [p["id"] for p in page["posts"]]
        assert seen == sorted(ids, reverse=True)
        assert len(set(seen)) == 25


def test_pagination_edges(world):
    for i in range(5):
        world.post("viewer", f"边界 {i}", ago(hours=i + 1))
    assert world.list(limit=5)["has_more"] is False
    assert world.list(limit=4)["has_more"] is True
    assert world.list(limit=1, offset=4)["has_more"] is False
    beyond = world.list(limit=5, offset=5)
    assert beyond["posts"] == [] and beyond["has_more"] is False and beyond["total"] == 5
    assert world.list(offset=2**63 - 1)["posts"] == []
    assert world.list(limit=50)["total"] == 5
    assert len(world.list()["posts"]) == 5


def test_default_page_size_is_20_and_old_call_still_works(world):
    for i in range(23):
        world.post("viewer", f"旧调用 {i:02d}", ago(minutes=i))
    data = world.client.get("/api/posts").json()
    assert len(data["posts"]) == 20 and data["total"] == 23 and data["has_more"] is True
    legacy_fields = {"id", "title", "created_at", "user_id", "username", "avatar_version",
                     "has_avatar", "comment_count", "solved"}
    assert legacy_fields <= set(data["posts"][0])


def test_requires_login(client):  # noqa: F811
    assert client.get("/api/posts").status_code == 401


# ---------------------------------------------------------------- counts

def test_counts_ignore_current_filters(world):
    seed_matrix(world)
    expected = world.list()
    assert expected["counts"] == {"all": 7, "unanswered": 3, "solved": 1, "mine": 2}
    assert expected["zone_counts"] == {"算法": 3, "前端": 2, "数据库": 1, "none": 1}
    for params in ({"q": "热门"}, {"filter": "solved"}, {"zone": "前端"},
                   {"filter": "mine", "zone": "算法", "q": "我的"}, {"limit": 1, "offset": 3}):
        other = world.list(**params)
        assert other["counts"] == expected["counts"]
        assert other["zone_counts"] == expected["zone_counts"]


def test_counts_exclude_deleted_posts_and_deleted_comments(world):
    post = world.post("bob", "待回复", zone="后端")
    world.comment(post, "carol", deleted=True)
    gone = world.post("bob", "被删的", zone="后端", deleted=True)
    world.comment(gone, "carol")
    data = world.list()
    assert data["counts"] == {"all": 1, "unanswered": 1, "solved": 0, "mine": 0}
    assert data["zone_counts"] == {"后端": 1}
    world.comment(post, "carol")
    assert world.list()["counts"]["unanswered"] == 0


def test_empty_board(world):
    data = world.list()
    assert data == {"posts": [], "total": 0, "has_more": False,
                    "counts": {"all": 0, "unanswered": 0, "solved": 0, "mine": 0},
                    "zone_counts": {}}


# ---------------------------------------------------------------- 摘要

EMOJI_FAMILY = "👩‍💻"
CASES = [
    ("没有代码的正文", "没有代码的正文"),
    ("前文\n```python\nprint(1)\n```\n后文", "前文 后文"),
    ("前文\n```\n未闭合的代码\n还没完", "前文"),
    ("```\nonly code\n```", ""),
    ("```\n未闭合\n", ""),
    ("   \n\t  ", ""),
    ("a   b\n\n\n c\t\td", "a b c d"),
    ("一\r\n二\r三", "一 二 三"),
    ("前\n```js\nx\n``` \n后", "前 后"),               # 关闭围栏允许尾随空白
    ("前\n```js\n```not a close\nstill code\n```\n后", "前 后"),
    ("行内 ```x``` 不算围栏", "行内 ```x``` 不算围栏"),   # 只有行首 ``` 才是围栏（同 thread.js）
    ("一\n```\na\n```\n二\n```\nb\n```\n三", "一 二 三"),
    ("<b>标签</b> & 文字", "<b>标签</b> & 文字"),       # 摘要是纯文本，不做 HTML 处理
]


@pytest.mark.parametrize("body,excerpt", CASES)
def test_excerpt_cases(body, excerpt):
    assert main.post_excerpt(body) == excerpt


def test_excerpt_truncates_at_140_with_ellipsis():
    exact = "字" * 140
    assert main.post_excerpt(exact) == exact
    cut = main.post_excerpt("字" * 141)
    assert cut == "字" * 140 + "…"
    assert main.post_excerpt("字" * 5000).endswith("…")
    assert len(main.post_excerpt("字" * 5000)) == 141


@pytest.mark.parametrize("tail", [EMOJI_FAMILY, "🇨🇳", "👍🏽", "1\ufe0f\u20e3"])
def test_excerpt_never_splits_an_emoji(tail):
    for pad in range(0, 4):
        text = "字" * (139 - pad) + tail + "字" * 10
        excerpt = main.post_excerpt(text)
        assert excerpt.endswith("…")
        body = excerpt[:-1]
        assert len(body) <= 140
        assert text.startswith(body)
        # 要么整个表情都在，要么完全不在，不会留下半个
        assert body.count(tail) in (0, 1)
        if body.count(tail) == 0:
            assert not body.endswith(("\u200d", "\ufe0f", "\u20e3")) and "\U0001F1E8" not in body[-1:]
        nxt = text[len(body)]
        assert nxt not in "\u200d\ufe0f\ufe0e\u20e3" and not 0x1F3FB <= ord(nxt) <= 0x1F3FF


def test_excerpt_in_list_response_and_empty_for_all_code(world):
    world.post("viewer", "有代码", body="先说明\n```py\nprint('x')\n```\n再说明")
    world.post("viewer", "全是代码", body="```py\nprint('x')\n```")
    assert world.by_title("有代码")["excerpt"] == "先说明 再说明"
    assert world.by_title("全是代码")["excerpt"] == ""
    assert "```" not in world.by_title("有代码")["excerpt"]


def test_has_code_looks_at_post_and_visible_comments(world):
    plain = world.post("viewer", "无代码", body="普通正文，行内 ```x``` 不算")
    in_post = world.post("viewer", "帖子里有代码", body="说明\n```\ncode\n```")
    unclosed = world.post("viewer", "未闭合代码", body="说明\n```\ncode")
    in_comment = world.post("viewer", "评论里有代码")
    world.comment(in_comment, "bob", body="试试\n```py\nx\n```")
    in_deleted = world.post("viewer", "已删评论里的代码")
    world.comment(in_deleted, "bob", body="```\nsecret\n```", deleted=True)
    flags = {p["title"]: p["has_code"] for p in world.list()["posts"]}
    assert flags == {"无代码": False, "帖子里有代码": True, "未闭合代码": True,
                     "评论里有代码": True, "已删评论里的代码": False}
    assert plain and in_post and unclosed


# ---------------------------------------------------------------- 热门

def test_hot_threshold_is_five_recent_comments_plus_votes(world):
    def with_comments(title, count, **kw):
        post = world.post("viewer", title)
        ids = [world.comment(post, "bob", ago(hours=1 + i)) for i in range(count)]
        return post, ids

    with_comments("四条评论", 4)
    with_comments("五条评论", 5)
    post, ids = with_comments("四条加一票", 4)
    world.vote(ids[0], "carol")
    post, ids = with_comments("三条加两票", 3)
    world.vote(ids[0], "carol")
    world.vote(ids[1], "dave")
    post, ids = with_comments("四条加已删评论的票", 4)
    extra = world.comment(post, "bob", deleted=True)
    world.vote(extra, "carol")
    hot = {p["title"]: p["hot"] for p in world.list()["posts"]}
    assert hot == {"四条评论": False, "五条评论": True, "四条加一票": True,
                   "三条加两票": True, "四条加已删评论的票": False}
    assert world.by_title("四条加已删评论的票")["helpful_total"] == 0


def test_hot_window_is_seven_days_in_utc(world):
    inside = world.post("viewer", "窗口内")
    outside = world.post("viewer", "窗口外")
    for i in range(5):
        world.comment(inside, "bob", ago(days=6, hours=23, minutes=50 - i))
        world.comment(outside, "bob", ago(days=7, minutes=10 + i))
    assert world.by_title("窗口内")["hot"] is True
    assert world.by_title("窗口外")["hot"] is False
    # 4 条在窗口内 + 1 条刚好在窗口外 = 4 分
    mixed = world.post("viewer", "边界混合")
    for i in range(4):
        world.comment(mixed, "bob", ago(hours=i + 1))
    world.comment(mixed, "bob", ago(days=8))
    assert world.by_title("边界混合")["hot"] is False
    # 窗口外的评论上的“有用”票仍计入（helpful_total 不看时间）
    world.vote(world.comment(mixed, "carol", ago(days=30)), "dave")
    assert world.by_title("边界混合")["hot"] is True


def test_sort_hot_orders_by_score_then_activity_then_id(world):
    low = world.post("viewer", "低分较新", ago(days=1))
    world.comment(low, "bob", ago(minutes=5))
    mid_old = world.post("viewer", "两分较旧", ago(days=3))
    world.comment(mid_old, "bob", ago(hours=9))
    world.comment(mid_old, "carol", ago(hours=8))
    mid_new = world.post("viewer", "两分较新", ago(days=2))
    world.comment(mid_new, "bob", ago(hours=3))
    world.comment(mid_new, "carol", ago(hours=2))
    top = world.post("viewer", "高分", ago(days=9))
    for i in range(3):
        world.comment(top, "bob", ago(days=2, hours=i))
    titles = [p["title"] for p in world.list(sort="hot")["posts"]]
    assert titles == ["高分", "两分较新", "两分较旧", "低分较新"]


# ---------------------------------------------------------------- 参与者

def test_participants_owner_first_deduped_recent_first_max_four(world):
    post = world.post("bob", "多人讨论")
    for index, name in enumerate(["carol", "dave", "carol", "erin", "frank", "gina", "bob"]):
        world.comment(post, name, ago(hours=10 - index))
    item = world.by_title("多人讨论")
    names = [p["username"] for p in item["participants"]]
    # 楼主 bob 在前（虽然他最后一个评论）；其余按最近参与倒序：gina frank erin dave carol
    assert names == ["bob", "gina", "frank", "erin"]
    assert item["participant_count"] == 6
    assert item["last_commenter"]["username"] == "bob"
    assert item["comment_count"] == 7
    assert len({p["user_id"] for p in item["participants"]}) == 4


def test_participants_when_owner_never_commented_and_deleted_comments_ignored(world):
    post = world.post("bob", "楼主不评论")
    world.comment(post, "carol", ago(hours=5))
    world.comment(post, "dave", ago(hours=4), deleted=True)
    world.comment(post, "erin", ago(hours=3))
    world.comment(post, "carol", ago(hours=2))
    item = world.by_title("楼主不评论")
    assert [p["username"] for p in item["participants"]] == ["bob", "carol", "erin"]
    assert item["participant_count"] == 3
    assert item["comment_count"] == 3
    assert item["last_commenter"]["username"] == "carol"


def test_last_commenter_ignores_deleted_latest_comment(world):
    post = world.post("bob", "最新评论被删")
    world.comment(post, "carol", ago(hours=5))
    world.comment(post, "dave", ago(hours=1), deleted=True)
    item = world.by_title("最新评论被删")
    assert item["last_commenter"]["username"] == "carol"
    assert item["last_activity_at"] == ago(hours=5)


def test_last_commenter_and_participant_order_break_ties_by_comment_id(world):
    same = ago(hours=3)
    post = world.post("bob", "同秒评论", ago(days=1))
    world.comment(post, "carol", same)
    world.comment(post, "dave", same)
    item = world.by_title("同秒评论")
    assert item["last_commenter"]["username"] == "dave"
    assert [p["username"] for p in item["participants"]] == ["bob", "dave", "carol"]


def test_deleted_account_shows_anonymous_name_only(world):
    post = world.post("bob", "注销后的参与者")
    world.comment(post, "carol", ago(hours=2))
    world.act_as("carol")
    assert world.client.post("/api/me/delete-account", json={"password": PASSWORD}).status_code == 200
    world.act_as("viewer")
    item = world.by_title("注销后的参与者")
    shown = [p["username"] for p in item["participants"]]
    assert shown[0] == "bob" and shown[1].startswith("已注销用户")
    assert "carol" not in str(item)
    assert item["last_commenter"]["username"].startswith("已注销用户")


def test_last_activity_uses_newer_of_post_and_latest_visible_comment(world):
    post = world.post("bob", "活动时间", ago(days=3))
    assert world.by_title("活动时间")["last_activity_at"] == ago(days=3)
    world.comment(post, "carol", ago(days=2))
    assert world.by_title("活动时间")["last_activity_at"] == ago(days=2)
    # 比帖子还早的评论（脏数据）不会让活动时间倒退
    world.comment(post, "carol", ago(days=9))
    assert world.by_title("活动时间")["last_activity_at"] == ago(days=2)


# ---------------------------------------------------------------- solved / 删除 / mine

def test_solved_requires_a_visible_accepted_comment_and_helpful_total_sums_visible_votes(world):
    post = world.post("viewer", "采纳的帖子")
    first = world.comment(post, "bob")
    second = world.comment(post, "carol")
    hidden = world.comment(post, "dave")
    world.vote(first, "carol")
    world.vote(first, "dave")
    world.vote(second, "bob")
    world.vote(hidden, "bob")
    world.accept(post, first)
    item = world.by_title("采纳的帖子")
    assert item["solved"] is True and item["helpful_total"] == 4
    with connect(write=True) as conn:
        conn.execute("UPDATE post_comments SET deleted_at = ? WHERE id IN (?, ?)",
                     (ago(minutes=1), first, hidden))
    item = world.by_title("采纳的帖子")
    assert item["solved"] is False
    assert item["helpful_total"] == 1 and item["comment_count"] == 1
    assert world.list(filter="solved")["posts"] == []
    assert world.list()["counts"]["solved"] == 0


def test_is_mine_and_mine_filter_only_use_the_logged_in_user(world):
    world.post("viewer", "我的")
    world.post("bob", "他的")
    mine = world.list(filter="mine")["posts"]
    assert [p["title"] for p in mine] == ["我的"]
    # 不接受用别人 id 的参数；多余的查询参数被忽略
    spoof = world.client.get("/api/posts", params={"filter": "mine", "user_id": world.uid("bob")})
    assert [p["title"] for p in spoof.json()["posts"]] == ["我的"]
    assert {p["title"]: p["is_mine"] for p in world.list()["posts"]} == {"我的": True, "他的": False}
    world.act_as("bob")
    assert [p["title"] for p in world.list(filter="mine")["posts"]] == ["他的"]
    assert world.list()["counts"]["mine"] == 1


def test_participated_includes_own_posts_and_visible_comments_only(world):
    own = world.post("viewer", "我发的")
    replied = world.post("bob", "我评论过")
    world.comment(replied, "viewer")
    deleted_reply = world.post("bob", "我评论被删")
    world.comment(deleted_reply, "viewer", deleted=True)
    world.post("bob", "无关")
    titles = {p["title"] for p in world.list(filter="participated")["posts"]}
    assert titles == {"我发的", "我评论过"}
    assert own


def test_trial_account_can_browse_but_nothing_is_mine(world):
    world.post("viewer", "公开帖")
    world.client.cookies.clear()
    assert world.client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"}).status_code == 201
    data = world.list()
    assert [p["is_mine"] for p in data["posts"]] == [False]
    assert data["counts"]["mine"] == 0
    assert world.list(filter="mine")["posts"] == []
    assert world.list(filter="participated")["posts"] == []


def test_deleted_post_disappears_after_api_delete(world):
    post = world.client.post("/api/posts", json={"title": "将被删除", "body": "x", "zone": "后端"}).json()
    assert world.list()["zone_counts"] == {"后端": 1}
    assert world.client.delete(f"/api/posts/{post['id']}").status_code == 200
    data = world.list()
    assert data["posts"] == [] and data["zone_counts"] == {} and data["counts"]["all"] == 0


# ---------------------------------------------------------------- 发帖 / 改帖 / 详情

def test_create_post_with_zone_variants(world):
    client = world.client
    made = client.post("/api/posts", json={"title": "有分区", "body": "x", "zone": "概率统计"})
    assert made.status_code == 201 and made.json()["zone"] == "概率统计"
    assert client.post("/api/posts", json={"title": "省略", "body": "x"}).json()["zone"] is None
    assert client.post("/api/posts", json={"title": "空值", "body": "x", "zone": None}).json()["zone"] is None
    for bad in ("不存在", "", "none", "算法 "):
        response = client.post("/api/posts", json={"title": "非法", "body": "x", "zone": bad})
        assert response.status_code == 400, bad
        assert response.json() == {"detail": "分区不存在"}
    assert client.post("/api/posts", json={"title": "非法", "body": "x", "zone": 5}).status_code == 422
    assert client.post("/api/posts", json={"title": "额外", "body": "x", "zones": "算法"}).status_code == 422
    assert world.list()["total"] == 3  # 非法请求没有留下帖子


def test_every_known_zone_can_be_used(world):
    for zone in main.PROBLEM_ZONES:
        assert world.client.post(
            "/api/posts", json={"title": zone, "body": "x", "zone": zone}
        ).json()["zone"] == zone
    assert set(world.list(limit=50)["zone_counts"]) == set(main.PROBLEM_ZONES)


def test_edit_post_zone_omitted_keeps_null_clears_and_invalid_rejected(world):
    client = world.client
    post = client.post("/api/posts", json={"title": "旧", "body": "旧", "zone": "前端"}).json()
    url = f"/api/posts/{post['id']}"
    kept = client.put(url, json={"title": "新", "body": "新"})
    assert kept.status_code == 200 and kept.json()["zone"] == "前端"
    moved = client.put(url, json={"title": "新", "body": "新", "zone": "数据库"})
    assert moved.json()["zone"] == "数据库"
    assert client.get(url).json()["zone"] == "数据库"
    bad = client.put(url, json={"title": "新", "body": "新", "zone": "不存在"})
    assert bad.status_code == 400 and bad.json() == {"detail": "分区不存在"}
    assert client.get(url).json()["zone"] == "数据库"
    cleared = client.put(url, json={"title": "新", "body": "新", "zone": None})
    assert cleared.status_code == 200 and cleared.json()["zone"] is None
    assert world.by_title("新")["zone"] is None


def test_only_the_author_can_change_the_zone(world):
    post = world.client.post("/api/posts", json={"title": "我的", "body": "x", "zone": "后端"}).json()
    world.act_as("bob")
    response = world.client.put(
        f"/api/posts/{post['id']}", json={"title": "改", "body": "改", "zone": "前端"}
    )
    assert response.status_code == 404
    assert world.by_title("我的")["zone"] == "后端"


def test_trial_account_cannot_set_a_zone(world):
    world.client.cookies.clear()
    world.client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert world.client.post(
        "/api/posts", json={"title": "x", "body": "x", "zone": "算法"}
    ).status_code == 403


def test_post_detail_includes_zone(world):
    tagged = create_post(world.client, "有分区", "正文")
    assert tagged["zone"] is None
    post = world.client.post("/api/posts", json={"title": "详情", "body": "x", "zone": "系统设计"}).json()
    assert world.client.get(f"/api/posts/{post['id']}").json()["zone"] == "系统设计"
    assert world.client.get(f"/api/posts/{tagged['id']}").json()["zone"] is None


# ---------------------------------------------------------------- 性能：查询次数

def count_statements(world, monkeypatch, **params):
    statements = []

    @contextmanager
    def traced_connect(write=False):
        with connect(write=write) as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    with monkeypatch.context() as patch:
        patch.setattr(main, "connect", traced_connect)
        response = world.client.get("/api/posts", params=params)
    assert response.status_code == 200, response.text
    return len(statements), response.json()


def test_query_count_does_not_grow_with_the_number_of_posts(world, monkeypatch):
    names = ["bob", "carol", "dave", "erin"]
    for index in range(3):
        post = world.post("viewer", f"少量 {index}", ago(hours=index + 1), body="```\nx\n```")
        for number in range(3):
            world.comment(post, names[number % 4], ago(minutes=number))
    small, small_data = count_statements(world, monkeypatch, limit=50)
    assert len(small_data["posts"]) == 3

    ids = {name: world.uid(name) for name in names}
    with connect(write=True) as conn:
        for index in range(100):
            post_id = conn.execute(
                "INSERT INTO posts(user_id, title, body, zone, created_at) VALUES (?, ?, ?, ?, ?)",
                (ids[names[index % 4]], f"大量 {index}", "正文\n```\ncode\n```",
                 main.PROBLEM_ZONES[index % 8], ago(minutes=index)),
            ).lastrowid
            conn.executemany(
                "INSERT INTO post_comments(post_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
                [(post_id, ids[names[(index + n) % 4]], f"评论 {n}\n```\nc\n```", ago(minutes=n))
                 for n in range(20)],
            )
    for params in ({"limit": 50}, {"limit": 50, "sort": "hot", "filter": "participated"},
                   {"limit": 5, "offset": 5, "zone": "算法"}, {"limit": 50, "q": "大量"}):
        large, large_data = count_statements(world, monkeypatch, **params)
        assert large_data["posts"], params  # 非空页：查询条数与只有 3 个帖子时一样
        assert large == small, (params, large, small)
    full_count, full = count_statements(world, monkeypatch, limit=50)
    assert len(full["posts"]) == 50 and full["total"] == 103
    assert full["posts"][0]["participant_count"] >= 2
    empty_count, empty = count_statements(world, monkeypatch, limit=50, offset=500)
    assert empty["posts"] == [] and empty_count <= small


def test_large_board_matches_a_python_recount(world):
    """100 个帖子 × 各 20 条评论：统计与逐条重算一致（防止分组子查询串行）。"""
    names = ["bob", "carol", "dave", "erin"]
    ids = {name: world.uid(name) for name in names}
    with connect(write=True) as conn:
        for index in range(100):
            post_id = conn.execute(
                "INSERT INTO posts(user_id, title, body, created_at) VALUES (?, ?, 'x', ?)",
                (ids[names[index % 4]], f"大 {index:03d}", ago(hours=index + 1)),
            ).lastrowid
            conn.executemany(
                "INSERT INTO post_comments(post_id, user_id, body, created_at, deleted_at) "
                "VALUES (?, ?, 'c', ?, ?)",
                [(post_id, ids[names[(index + n) % 4]], ago(minutes=n),
                  ago(seconds=1) if n % 5 == 0 else None) for n in range(20)],
            )
    seen = []
    for offset in range(0, 100, 50):
        seen += world.list(limit=50, offset=offset, sort="new")["posts"]
    assert len(seen) == 100
    for post in seen:
        assert post["comment_count"] == 16  # 20 条里每 5 条删 1 条
        assert post["participant_count"] == 4
        assert len(post["participants"]) == 4
    assert [p["title"] for p in seen] == [f"大 {i:03d}" for i in range(100)]
