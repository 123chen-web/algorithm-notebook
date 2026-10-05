"""目标卡接口：GET / PUT / DELETE /api/goal。

today 由 client 夹具固定在 2026-09-19（Asia/Shanghai）：可选日期是 2026-09-20 至 2027-09-19。
"""
from datetime import date

from db import connect
from test_app import client, insert_review, new_problem, register

TODAY = date(2026, 9, 19)
PASSWORD = "a-test-password-123"


def set_due(mistake_id, due_date):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET due_date = ? WHERE id = ?", (due_date, mistake_id)
        )


def suspend(mistake_id):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET suspended_at = ? WHERE id = ?",
            ("2026-09-19T08:00:00+00:00", mistake_id),
        )


def goal_rows():
    with connect() as conn:
        return conn.execute("SELECT * FROM goals").fetchall()


def put(client, name="期末冲刺", goal_date="2026-09-21"):
    return client.put("/api/goal", json={"name": name, "goal_date": goal_date})


def test_get_without_goal_returns_null(client):
    register(client)
    assert client.get("/api/goal").json() == {"goal": None}


def test_goal_endpoints_require_sign_in(client):
    assert client.get("/api/goal").status_code == 401
    assert put(client).status_code == 401
    assert client.delete("/api/goal").status_code == 401


def test_put_creates_and_get_returns_the_same_shape(client):
    register(client)
    created = put(client, name="  期末冲刺  ")
    assert created.status_code == 200
    goal = created.json()["goal"]
    assert goal == {
        "name": "期末冲刺",  # 名字去掉首尾空白
        "goal_date": "2026-09-21",
        "days_left": 2,
        "status": "empty",  # 没有任何记录：有目标但没有工作
        "total_workload": 0,
        "daily_target": 0,
        "done_today": 0,
        "remaining_today": 0,
        "on_track": True,
    }
    assert client.get("/api/goal").json()["goal"] == goal


def test_put_validates_name(client):
    register(client)
    assert put(client, name="   ").json()["detail"] == "请填写目标名称"
    assert put(client, name="字" * 31).json()["detail"] == "名称最多 30 字"
    assert put(client, name="字" * 30).status_code == 200
    assert put(client, name="😀" * 30).status_code == 200, "emoji 按码点算一个字"
    assert goal_rows()[0]["name"] == "😀" * 30


def test_put_validates_date_window_and_format(client):
    register(client)
    assert put(client, goal_date="2026-09-19").json()["detail"] == "日期必须是未来 1–365 天内"
    assert put(client, goal_date="2026-09-18").json()["detail"] == "日期必须是未来 1–365 天内"
    assert put(client, goal_date="2027-09-20").json()["detail"] == "日期必须是未来 1–365 天内"
    assert put(client, goal_date="2027-09-19").status_code == 200, "第 365 天可以"
    assert put(client, goal_date="2026/10/01").json()["detail"] == "目标日期必须是 YYYY-MM-DD 格式"
    assert put(client, goal_date="2026-02-30").json()["detail"] == "目标日期不是一个真实存在的日期"


def test_workload_counts_due_and_forecast_but_not_beyond_the_goal(client):
    register(client)
    # new_problem 一次建两条；这里用四条记录铺满四种位置。
    overdue, tomorrow = new_problem(client)
    at_goal, beyond = new_problem(client)
    set_due(overdue, "2026-09-10")   # 已逾期：算进 pending_now
    set_due(tomorrow, "2026-09-20")  # 目标期内第 1 天到期
    set_due(at_goal, "2026-09-21")   # 目标日当天到期
    set_due(beyond, "2026-09-25")    # 目标日之后：不算
    goal = put(client, goal_date="2026-09-21").json()["goal"]
    assert goal["days_left"] == 2
    assert goal["status"] == "active"
    assert goal["total_workload"] == 3
    assert goal["daily_target"] == 2  # ceil(3 / 2)
    assert goal["remaining_today"] == 2
    assert goal["on_track"] is False


def test_suspended_records_leave_the_workload_but_their_reviews_count_as_done(client):
    register(client)
    kept, paused = new_problem(client)
    set_due(kept, "2026-09-20")
    set_due(paused, "2026-09-20")
    suspend(paused)
    # 暂停的记录不参与到期预测，但今天评过分就算今天的进度。
    insert_review(paused, 4, "2026-09-19T01:00:00+00:00")  # 上海时间当天上午
    goal = put(client, goal_date="2026-09-21").json()["goal"]
    assert goal["total_workload"] == 1
    assert goal["done_today"] == 1
    assert goal["daily_target"] == 1
    assert goal["remaining_today"] == 0
    assert goal["on_track"] is True


def test_done_today_follows_the_users_local_day(client):
    register(client)
    mistake = new_problem(client)[0]
    set_due(mistake, "2026-09-25")
    insert_review(mistake, 4, "2026-09-18T16:00:00+00:00")  # 上海 9-19 00:00：算今天
    insert_review(mistake, 3, "2026-09-19T15:59:59+00:00")  # 上海 9-19 23:59：算今天
    insert_review(mistake, 5, "2026-09-18T15:59:59+00:00")  # 上海 9-18 23:59：昨天
    goal = put(client, goal_date="2026-09-25").json()["goal"]
    assert goal["done_today"] == 2


def test_status_today_and_passed(client):
    register(client)
    mistake, later = new_problem(client)
    set_due(later, "2026-09-25")  # 另一条排到目标期外，只留下今天到期的一条
    goal = put(client, goal_date="2026-09-20").json()["goal"]
    assert goal["status"] == "active" and goal["days_left"] == 1
    assert goal["total_workload"] == 1  # 只有今天到期的那条；另一条在目标日之后才到期，不算
    with connect(write=True) as conn:
        conn.execute("UPDATE goals SET goal_date = ?", ("2026-09-19",))
    goal = client.get("/api/goal").json()["goal"]
    assert goal["status"] == "today"
    assert goal["days_left"] == 0
    assert goal["daily_target"] == goal["total_workload"] == 1  # 截止日全压到今天
    with connect(write=True) as conn:
        conn.execute("UPDATE goals SET goal_date = ?", ("2026-09-18",))
    goal = client.get("/api/goal").json()["goal"]
    assert goal["status"] == "passed"
    assert goal["days_left"] == 0
    assert goal["daily_target"] == 0


def test_put_twice_updates_the_same_row(client):
    register(client)
    put(client, name="旧目标", goal_date="2026-09-25")
    updated = put(client, name="新目标", goal_date="2026-10-01").json()["goal"]
    assert updated["name"] == "新目标" and updated["goal_date"] == "2026-10-01"
    assert len(goal_rows()) == 1, "修改不新增行"


def test_delete_ends_the_goal_and_a_new_put_revives_the_row(client):
    register(client)
    put(client)
    assert client.delete("/api/goal").json() == {"goal": None}
    assert client.get("/api/goal").json() == {"goal": None}
    (row,) = goal_rows()
    assert row["ended_at"] is not None, "行保留作历史"
    revived = put(client, name="再出发", goal_date="2026-10-05").json()["goal"]
    assert revived["name"] == "再出发"
    (row,) = goal_rows()
    assert row["ended_at"] is None and len(goal_rows()) == 1, "复活同一行而不是新增"
    # 结束后再次删除是幂等的。
    assert client.delete("/api/goal").status_code == 200


def test_goals_are_per_user(client):
    register(client, "alice")
    put(client, name="alice 的目标")
    other = register(client, "bob")
    assert client.get("/api/goal").json() == {"goal": None}, "bob 看不到 alice 的目标"
    put(client, name="bob 的目标", goal_date="2026-10-01")
    rows = goal_rows()
    assert len(rows) == 2
    assert {row["name"] for row in rows} == {"alice 的目标", "bob 的目标"}


def test_account_deletion_removes_the_goal_row(client):
    register(client)
    put(client)
    assert len(goal_rows()) == 1
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200
    assert goal_rows() == [], "注销账号时目标一并删除"
