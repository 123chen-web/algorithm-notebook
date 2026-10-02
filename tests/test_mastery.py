"""掌握度趋势：按分区估算保持率 R = 0.9 ** (距上次学习的天数 / 排定间隔)，纯统计。"""

from datetime import date, datetime, timedelta, timezone

import pytest

import ai
from db import connect
from mastery import AT_RISK_BELOW, FADING_BELOW, retention
from test_app import client, register


ENDPOINT = "/api/stats/mastery"
TODAY = date(2026, 9, 19)  # 跟 test_app.client 冻结的用户本地日期一致（上海时区）


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("掌握度趋势是纯统计，不能调用 AI")

    for name in ("generate", "analyze_weaknesses", "recognize_photo"):
        monkeypatch.setattr(ai, name, unexpected_call)


def at(day, hour=4, minute=0):
    """某个 UTC 时刻；默认 04:00 UTC = 上海 12:00，稳稳落在当天。"""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat()


def user_id(client):
    return client.get("/api/me").json()["id"]


def seed(owner, zone="算法", created_days_ago=0, reviews=(), due_in=None):
    """造一条易错点；reviews = [(几天前复习, 排定的间隔天数), ...]。"""
    created = TODAY - timedelta(days=created_days_ago)
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '题', ?, 'Python', '', '', ?)",
            (owner, zone, at(created)),
        ).lastrowid
        if due_in is None:
            # 默认：没复习过就是录入当天到期；复习过就是最后一次排定的日期。
            if reviews:
                last_ago, interval = min(reviews, key=lambda item: item[0])
                due = TODAY - timedelta(days=last_ago) + timedelta(days=interval)
            else:
                due = created
        else:
            due = TODAY + timedelta(days=due_in)
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) VALUES (?, '错因', ?)",
            (problem_id, due.isoformat()),
        ).lastrowid
        for days_ago, interval in reviews:
            reviewed = TODAY - timedelta(days=days_ago)
            conn.execute(
                "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, ?, ?)",
                (mistake_id, at(reviewed), (reviewed + timedelta(days=interval)).isoformat()),
            )
    return mistake_id


def report(client, **params):
    response = client.get(ENDPOINT, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def zone_of(data, name):
    return next(item for item in data["zones"] if item["zone"] == name)


def test_requires_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_empty_account_has_no_zones_and_a_full_date_axis(client):
    register(client)
    data = report(client)
    assert data["zones"] == [] and data["alert"] is None and data["overall"] is None
    assert data["today"] == "2026-09-19" and data["threshold"] == FADING_BELOW
    assert len(data["points"]) == 13
    assert data["points"][0] == (TODAY - timedelta(days=84)).isoformat()
    assert data["points"][-1] == "2026-09-19"


def test_retention_formula_is_ninety_percent_at_the_due_date():
    assert retention(0, 5) == 1
    assert retention(5, 5) == pytest.approx(0.9)
    assert retention(10, 5) == pytest.approx(0.81)
    assert retention(7, 1) == pytest.approx(0.9 ** 7)
    assert retention(3, 0) == pytest.approx(0.9 ** 3), "a zero interval is treated as one day"


@pytest.mark.parametrize("kwargs,expected", [
    (dict(created_days_ago=0), 100.0),                                   # 刚录入
    (dict(created_days_ago=7), 47.8),                                    # 7 天没复习：0.9^7
    (dict(created_days_ago=20, reviews=[(3, 6)]), 94.9),                 # 3 天前复习，间隔 6：0.9^0.5
    (dict(created_days_ago=30, reviews=[(10, 5)]), 81.0),                # 逾期：0.9^2
    (dict(created_days_ago=30, reviews=[(10, 5), (2, 6)]), 96.5),        # 以最近一次复习为准：0.9^(2/6)
])
def test_single_record_mastery_follows_the_forgetting_curve(client, kwargs, expected):
    register(client)
    seed(user_id(client), **kwargs)
    assert zone_of(report(client), "算法")["mastery"] == expected


def test_zone_mastery_is_the_average_of_its_records(client):
    register(client)
    owner = user_id(client)
    seed(owner, created_days_ago=0)
    seed(owner, created_days_ago=7)
    seed(owner, "高等数学", created_days_ago=0)
    data = report(client)
    assert zone_of(data, "算法")["mastery"] == 73.9
    assert zone_of(data, "算法")["total"] == 2
    assert zone_of(data, "高等数学")["mastery"] == 100.0
    assert data["overall"] == round((100 + 0.9 ** 7 * 100 + 100) / 3, 1)
    # 最该先复习的排在最前面。
    assert [item["zone"] for item in data["zones"]] == ["算法", "高等数学"]


def test_series_travels_back_in_time_and_ignores_records_that_did_not_exist_yet(client):
    register(client)
    seed(user_id(client), created_days_ago=21, reviews=[(14, 6)])
    data = report(client, weeks=4)
    assert data["points"] == ["2026-08-22", "2026-08-29", "2026-09-05", "2026-09-12", "2026-09-19"]
    # 21 天前才录入：第一个采样点（28 天前）还没有这条记录；当天保持率 100，
    # 14 天前复习（间隔 6）当天 100，7 天后 0.9^(7/6)，14 天后 0.9^(14/6)。
    assert zone_of(data, "算法")["series"] == [None, 100.0, 100.0, 88.4, 78.2]
    assert zone_of(data, "算法")["mastery"] == 78.2


def test_a_later_review_does_not_leak_into_earlier_points(client):
    register(client)
    seed(user_id(client), created_days_ago=21, reviews=[(1, 6)])
    series = zone_of(report(client, weeks=4), "算法")["series"]
    # 1 天前才复习：之前的采样点仍按"只在录入当天学过"衰减（7 天前距录入 14 天），不会被未来的复习拉高。
    assert series[-2] == round(0.9 ** 14 * 100, 1)
    assert series[-1] == round(0.9 ** (1 / 6) * 100, 1)


def test_change_compares_with_two_weeks_ago(client):
    register(client)
    seed(user_id(client), created_days_ago=21, reviews=[(14, 6)])
    assert zone_of(report(client), "算法")["change"] == pytest.approx(78.2 - 100.0)
    register_new = client.post("/api/auth/logout")
    assert register_new.status_code == 200
    register(client, "bob")
    seed(user_id(client), created_days_ago=3)
    assert zone_of(report(client), "算法")["change"] is None, "no record existed two weeks ago"


def test_local_midnight_decides_which_day_a_review_belongs_to(client):
    register(client)
    owner = user_id(client)
    mistake = seed(owner, created_days_ago=30)
    with connect(write=True) as conn:
        # UTC 9-18 16:30 = 上海 9-19 00:30：属于"今天"，所以今天的保持率是 100。
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, ?, ?)",
            (mistake, "2026-09-18T16:30:00+00:00", "2026-09-25"),
        )
    assert zone_of(report(client), "算法")["mastery"] == 100.0


def test_due_overdue_and_at_risk_counts(client):
    register(client)
    owner = user_id(client)
    seed(owner, created_days_ago=10)                    # 逾期 10 天，保持率 0.35 → 快忘了
    seed(owner, created_days_ago=0)                      # 今天到期，保持率 100
    seed(owner, created_days_ago=5, reviews=[(1, 6)])    # 还没到期
    entry = zone_of(report(client), "算法")
    assert (entry["total"], entry["due"], entry["overdue"], entry["at_risk"]) == (3, 2, 1, 1)
    assert AT_RISK_BELOW == 0.7


def test_alert_names_the_lowest_fading_zone_with_due_cards(client):
    register(client)
    owner = user_id(client)
    seed(owner, "算法", created_days_ago=2)             # 0.81 → 不算快被遗忘
    seed(owner, "数据库", created_days_ago=10)          # 34.9
    seed(owner, "前端", created_days_ago=6)             # 53.1
    data = report(client)
    assert [item["zone"] for item in data["zones"]] == ["数据库", "前端", "算法"]
    assert data["alert"] == {"zone": "数据库", "mastery": 34.9, "due": 1, "overdue": 1, "at_risk": 1}


def test_no_alert_when_everything_is_fresh_or_nothing_is_due(client):
    register(client)
    owner = user_id(client)
    seed(owner, "算法", created_days_ago=1)             # 90
    assert report(client)["alert"] is None
    # 数据异常的情形：保持率很低（12 天前复习、间隔只有 3 天），但到期日被写在未来、没有到期的 → 不提醒。
    seed(owner, "数据库", created_days_ago=12, reviews=[(12, 3)], due_in=20)
    entry = zone_of(report(client), "数据库")
    assert entry["mastery"] < FADING_BELOW and entry["due"] == 0
    assert report(client)["alert"] is None


def test_other_users_data_never_leaks_in(client):
    register(client, "alice")
    seed(user_id(client), created_days_ago=10)
    client.post("/api/auth/logout")
    register(client, "bob")
    data = report(client)
    assert data["zones"] == [] and data["alert"] is None


def test_trial_accounts_can_read_it(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    assert report(client)["zones"] == []


@pytest.mark.parametrize("weeks", ["3", "27", "0", "-1", "x", "4.5"])
def test_weeks_must_be_between_four_and_twenty_six(client, weeks):
    register(client)
    assert client.get(ENDPOINT, params={"weeks": weeks}).status_code == 422


@pytest.mark.parametrize("weeks,count", [(4, 5), (12, 13), (26, 27)])
def test_the_number_of_sample_points_follows_weeks(client, weeks, count):
    register(client)
    assert len(report(client, weeks=weeks)["points"]) == count


def test_records_created_after_the_requests_today_are_ignored_not_a_server_error(client):
    # 请求开始时取的"今天"是 9-19；读库时跨过午夜，已经读到 9-20 才录入的记录：
    # 它在 9-19 这天还不存在，不能让统计抛 TypeError。
    register(client)
    owner = user_id(client)
    seed(owner, created_days_ago=3)
    seed(owner, created_days_ago=-1)  # 录入日在"今天"之后
    data = report(client)
    entry = zone_of(data, "算法")
    assert entry["total"] == 1 and entry["mastery"] == round(0.9 ** 3 * 100, 1)
    assert data["overall"] == entry["mastery"]
