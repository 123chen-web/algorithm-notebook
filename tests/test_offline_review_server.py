"""离线评分补交的服务端规则：幂等、reviewed_at 边界、与撤销/暂停/推迟/每日上限的交互，
以及 /sw.js 与 manifest 的响应头。真实隔离 SQLite；时钟固定为 NOW。

取舍（每日上限的计数口径）：
  每日上限只限制“今日复习队列”里一次放出多少题（GET /api/review/queue），评分接口本身
  从不按上限拒绝。补交的评分按 reviewed_at 所在的“用户本地日期”计入当天的 done_today，
  不按服务器收到的日期：昨天离线做的 3 道题，今天联网补交后属于昨天，不会占用今天的名额；
  上限满了之后补交也照常成功（不会因为“超额”丢掉用户真实做过的复习）。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import threading

from fastapi.testclient import TestClient
import pytest

import main
from db import connect
from scheduler import schedule
from test_app import client, register  # noqa: F401  (client 是 pytest fixture)

NOW = datetime(2026, 9, 19, 4, tzinfo=timezone.utc)  # 上海 2026-09-19 12:00
TODAY = date(2026, 9, 19)
OP = "op-00000001"
NEWER = "这道题之后已经有更新的评分"
RESPONSE_KEYS = {"repetitions", "interval_days", "ease_factor", "due_date", "version"}


@pytest.fixture(autouse=True)
def review_clock(monkeypatch):
    monkeypatch.setattr(main, "utc_now", lambda: NOW.isoformat())


def stamp(moment):
    return moment.isoformat().replace("+00:00", "Z")


def seed(owner, *, due="2026-09-19", interval=6, repetitions=2, ease=2.5,
         version=0, last="2026-09-10T04:00:00+00:00"):
    with connect(write=True) as conn:
        problem = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '补交测试', '算法', 'Python', '', '', ?)",
            (owner, NOW.isoformat()),
        ).lastrowid
        return conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date, interval_days, "
            "repetitions, ease_factor, version, last_reviewed_at) VALUES (?, '边界遗漏', ?, ?, ?, ?, ?, ?)",
            (problem, due, interval, repetitions, ease, version, last),
        ).lastrowid


def stored(mistake):
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM mistakes WHERE id = ?", (mistake,)).fetchone())


def logs(mistake):
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id", (mistake,),
        )]


def ops():
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM review_ops ORDER BY created_at, client_op_id")]


def post(client, mistake, quality=4, **fields):
    return client.post(f"/api/mistakes/{mistake}/review", json={"quality": quality, **fields})


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def second_user(name="bob", zone=None):
    other = TestClient(main.app, headers={"X-CSRF-Protection": "1"})
    user = register(other, name)
    if zone:
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (zone, user["id"]))
    return other, user["id"]


# ---------------------------------------------------------------- 幂等

def test_duplicate_op_returns_the_first_response_and_scores_once(client):
    mistake = seed(register(client)["id"])
    first = post(client, mistake, quality=4, client_op_id=OP, reviewed_at=stamp(NOW))
    assert first.status_code == 200 and set(first.json()) == RESPONSE_KEYS
    after_first = stored(mistake)

    # 重复提交：哪怕这次带了不同评分、过期的版本号、早已超出 7 天的时间，也不再评分、不再校验。
    again = post(client, mistake, quality=4, client_op_id=OP, reviewed_at=stamp(NOW))
    different = post(client, mistake, quality=0, version=999, client_op_id=OP,
                     reviewed_at=stamp(NOW - timedelta(days=30)))
    for response in (again, different):
        assert response.status_code == 200
        assert response.json() == first.json()
    assert stored(mistake) == after_first
    assert len(logs(mistake)) == 1
    row, = ops()
    assert (row["user_id"], row["client_op_id"], row["mistake_id"]) == (1, OP, mistake)
    assert json.loads(row["response"]) == first.json()
    assert row["created_at"] == NOW.isoformat()


def test_op_without_reviewed_at_uses_server_time_and_the_same_response_shape(client):
    mistake = seed(register(client)["id"])
    plain = seed(1)
    with_op = ok(post(client, mistake, client_op_id=OP))
    without = ok(post(client, plain, version=0))
    assert set(with_op) == set(without) == RESPONSE_KEYS
    assert with_op == without
    assert logs(mistake)[0]["reviewed_at"] == NOW.isoformat()
    assert stored(mistake)["last_reviewed_at"] == NOW.isoformat()


def test_same_op_id_on_another_mistake_is_rejected_without_scoring(client):
    owner = register(client)["id"]
    first, other = seed(owner), seed(owner)
    ok(post(client, first, client_op_id=OP))
    clash = post(client, other, client_op_id=OP)
    assert clash.status_code == 422
    assert logs(other) == [] and stored(other)["version"] == 0


def test_op_ids_are_scoped_per_user(client):
    alice_mistake = seed(register(client)["id"])
    bob_client, bob = second_user()
    bob_mistake = seed(bob)
    a = ok(post(client, alice_mistake, quality=5, client_op_id=OP))
    b = ok(post(bob_client, bob_mistake, quality=0, client_op_id=OP))
    assert a != b  # 两人各自评分，互不返回对方的响应
    assert len(logs(alice_mistake)) == len(logs(bob_mistake)) == 1
    assert sorted((row["user_id"], row["mistake_id"]) for row in ops()) == [
        (1, alice_mistake), (bob, bob_mistake),
    ]
    # 别人的 op id 查不到我的记录，也不会泄露：对我不拥有的题仍是 404，且不留记录。
    probe = post(bob_client, alice_mistake, client_op_id="op-00000002")
    assert probe.status_code == 404
    assert len(ops()) == 2


def test_concurrent_duplicates_take_effect_once(client, monkeypatch):
    mistake = seed(register(client)["id"])
    real_schedule = main.schedule
    gate = threading.Barrier(8)

    def slow_schedule(*args, **kwargs):
        # 拉宽“先查后写”的窗口：没有写锁内的幂等判断时，所有线程都会走到这里。
        threading.Event().wait(0.05)
        return real_schedule(*args, **kwargs)

    monkeypatch.setattr(main, "schedule", slow_schedule)

    def submit(_):
        gate.wait()
        return post(client, mistake, client_op_id=OP, reviewed_at=stamp(NOW))

    with ThreadPoolExecutor(8) as pool:
        responses = list(pool.map(submit, range(8)))
    assert [r.status_code for r in responses] == [200] * 8
    assert len({json.dumps(r.json(), sort_keys=True) for r in responses}) == 1
    assert len(logs(mistake)) == 1 and stored(mistake)["version"] == 1
    assert len(ops()) == 1


def test_interleaved_requests_with_different_ops_do_not_share_results(client):
    mistake = seed(register(client)["id"])
    second = seed(1)
    r1 = post(client, mistake, quality=5, client_op_id="op-aaaaaaaa")
    r2 = post(client, second, quality=0, client_op_id="op-bbbbbbbb")
    r1_again = post(client, mistake, quality=0, client_op_id="op-aaaaaaaa")
    r2_again = post(client, second, quality=5, client_op_id="op-bbbbbbbb")
    assert r1_again.json() == r1.json() and r2_again.json() == r2.json() and r1.json() != r2.json()
    assert len(logs(mistake)) == len(logs(second)) == 1


def test_failed_attempts_are_not_remembered(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET suspended_at = ? WHERE id = ?", (NOW.isoformat(), mistake))
    blocked = post(client, mistake, client_op_id=OP)
    assert blocked.status_code == 409 and ops() == []
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET suspended_at = NULL WHERE id = ?", (mistake,))
    assert post(client, mistake, client_op_id=OP).status_code == 200
    assert len(ops()) == 1


def test_records_older_than_thirty_days_are_purged_on_the_next_op(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    rows = {
        "op-expired1": NOW - timedelta(days=30, seconds=1),
        "op-boundary": NOW - timedelta(days=30),
        "op-recent01": NOW - timedelta(days=29),
    }
    with connect(write=True) as conn:
        for op_id, created in rows.items():
            conn.execute(
                "INSERT INTO review_ops(user_id, client_op_id, mistake_id, response, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (owner, op_id, 999, json.dumps({"stale": True}), created.isoformat()),
            )
    ok(post(client, mistake, client_op_id="op-fresh001"))
    assert {row["client_op_id"] for row in ops()} == {"op-boundary", "op-recent01", "op-fresh001"}

    # 过期记录已经不存在：同一个 id 再来，当作新操作正常评分（而不是返回陈旧的响应）。
    other = seed(owner)
    again = ok(post(client, other, client_op_id="op-expired1"))
    assert again != {"stale": True} and len(logs(other)) == 1
    # 未过期的仍然生效（题目 id 不匹配就是 422，而不是重新评分）。
    assert post(client, other, client_op_id="op-recent01").status_code == 422


def test_replay_after_the_mistake_was_deleted_still_returns_the_saved_response(client):
    mistake = seed(register(client)["id"])
    first = ok(post(client, mistake, client_op_id=OP))
    assert client.delete(f"/api/mistakes/{mistake}").status_code == 200
    assert ok(post(client, mistake, client_op_id=OP)) == first


def test_account_deletion_removes_the_idempotency_records(client):
    owner = register(client)["id"]
    ok(post(client, seed(owner), client_op_id=OP))
    assert len(ops()) == 1
    deleted = client.post("/api/me/delete-account", json={"password": "a-test-password-123"})
    assert deleted.status_code == 200, deleted.text
    assert ops() == []


# ---------------------------------------------------------------- 字段校验

@pytest.mark.parametrize("value", [
    "short12", "x" * 65, "has space1", "bad/chars1", "abcdefgh\n", "中文中文中文中文", "", 12345678, ["op-00000001"],
])
def test_client_op_id_must_match_the_pattern(client, value):
    mistake = seed(register(client)["id"])
    assert post(client, mistake, version=0, client_op_id=value).status_code == 422
    assert logs(mistake) == [] and ops() == []


@pytest.mark.parametrize("value", ["a" * 8, "a" * 64, "ab-_AB09", OP])
def test_client_op_id_accepts_valid_ids(client, value):
    mistake = seed(register(client)["id"])
    assert post(client, mistake, client_op_id=value).status_code == 200


@pytest.mark.parametrize("value", [
    "2026-09-19T12:00:00", "2026-09-19", "not-a-time", "", 1789000000, True, "x" * 65,
])
def test_reviewed_at_must_be_an_aware_iso_string(client, value):
    mistake = seed(register(client)["id"])
    assert post(client, mistake, version=0, reviewed_at=value).status_code == 422
    assert logs(mistake) == []


def test_version_is_only_optional_together_with_a_client_op_id(client):
    mistake = seed(register(client)["id"])
    assert post(client, mistake).status_code == 422
    assert post(client, mistake, version=None).status_code == 422
    assert post(client, mistake, reviewed_at=stamp(NOW)).status_code == 422
    assert post(client, mistake, version=5, client_op_id=OP).status_code == 409  # 带了就照常比较
    assert logs(mistake) == [] and ops() == []
    assert post(client, mistake, client_op_id=OP).status_code == 200
    assert post(client, mistake, version=0, extra=1).status_code == 422  # 仍禁止多余字段


def test_null_optional_fields_behave_like_absent_ones(client):
    mistake = seed(register(client)["id"])
    ok(post(client, mistake, version=0, client_op_id=None, reviewed_at=None))
    assert logs(mistake)[0]["reviewed_at"] == NOW.isoformat() and ops() == []


# ---------------------------------------------------------------- reviewed_at 边界

def test_without_reviewed_at_the_behaviour_is_unchanged(client):
    mistake = seed(register(client)["id"], due="2026-09-16")
    result = ok(post(client, mistake, version=0))
    expected = schedule(2, 6, 2.5, 4, TODAY, overdue_days=3)
    assert result == {**expected, "version": 1}
    row, = logs(mistake)
    assert row["reviewed_at"] == NOW.isoformat() and row["elapsed_days"] == 9


@pytest.mark.parametrize("delta,status", [
    (timedelta(seconds=60), 200), (timedelta(seconds=61), 422), (timedelta(hours=3), 422),
])
def test_reviewed_at_cannot_be_more_than_sixty_seconds_in_the_future(client, delta, status):
    mistake = seed(register(client)["id"])
    response = post(client, mistake, client_op_id=OP, reviewed_at=stamp(NOW + delta))
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json() == {"detail": "评分时间不能晚于当前时间"}
        assert logs(mistake) == [] and stored(mistake)["version"] == 0 and ops() == []


@pytest.mark.parametrize("delta,status", [
    (timedelta(days=7), 200), (timedelta(days=7, seconds=1), 422), (timedelta(days=30), 422),
])
def test_reviewed_at_cannot_be_older_than_seven_days(client, delta, status):
    mistake = seed(register(client)["id"], due="2026-09-01", last="2026-08-01T00:00:00+00:00")
    response = post(client, mistake, client_op_id=OP, reviewed_at=stamp(NOW - delta))
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json() == {"detail": "评分时间不能早于 7 天前"}
        assert logs(mistake) == [] and ops() == []


@pytest.mark.parametrize("given,status", [
    ("2026-09-15T03:59:59Z", 409),  # 早于上次评分 1 秒
    ("2026-09-15T04:00:00Z", 200),  # 与上次评分同一秒：不算“早于”
    ("2026-09-15T12:00:01+08:00", 200),  # 同一时刻之后 1 秒，换一种偏移写法
    ("2026-09-15T11:59:59+08:00", 409),  # 同上，早 1 秒
])
def test_reviewed_at_cannot_be_earlier_than_the_previous_review(client, given, status):
    mistake = seed(register(client)["id"], due="2026-09-15", last="2026-09-15T04:00:00+00:00")
    before = stored(mistake)
    response = post(client, mistake, client_op_id=OP, reviewed_at=given)
    assert response.status_code == status, response.text
    if status == 409:
        assert response.json() == {"detail": NEWER}
        assert stored(mistake) == before and logs(mistake) == [] and ops() == []
    else:
        assert stored(mistake)["version"] == 1


def test_a_never_reviewed_mistake_has_no_lower_bound_but_the_window(client):
    mistake = seed(register(client)["id"], last=None, due="2026-09-13")
    assert post(client, mistake, client_op_id=OP,
                reviewed_at=stamp(NOW - timedelta(days=6))).status_code == 200


def test_reviewed_at_drives_schedule_log_and_last_reviewed(client):
    mistake = seed(register(client)["id"], due="2026-09-16")
    given = "2026-09-16T10:30:15.789+08:00"  # 带毫秒和偏移：存成 UTC、秒精度
    result = ok(post(client, mistake, quality=5, client_op_id=OP, reviewed_at=given))
    assert result == {**schedule(2, 6, 2.5, 5, date(2026, 9, 16), overdue_days=0), "version": 1}
    # 对照：不传 reviewed_at 时同样的题按“今天”（逾期 3 天）调度，结果不同。
    control = seed(1, due="2026-09-16")
    assert ok(post(client, control, quality=5, version=0))["due_date"] != result["due_date"]
    row, = logs(mistake)
    assert row["reviewed_at"] == "2026-09-16T02:30:15+00:00"
    assert row["next_due_date"] == result["due_date"]
    assert row["elapsed_days"] == 6 and row["scheduled_days"] == 6
    assert row["last_reviewed_before"] == "2026-09-10T04:00:00+00:00"
    assert stored(mistake)["last_reviewed_at"] == "2026-09-16T02:30:15+00:00"


def test_z_suffix_and_offsets_name_the_same_instant(client):
    first = seed(register(client)["id"], due="2026-09-15")
    second = seed(1, due="2026-09-15")
    a = ok(post(client, first, client_op_id="op-aaaaaaaa", reviewed_at="2026-09-18T06:00:00Z"))
    b = ok(post(client, second, client_op_id="op-bbbbbbbb", reviewed_at="2026-09-18T14:00:00+08:00"))
    assert a == b and logs(first)[0]["reviewed_at"] == logs(second)[0]["reviewed_at"]


@pytest.mark.parametrize("given,local_day", [
    ("2026-09-18T15:59:59Z", date(2026, 9, 18)),  # 上海 23:59:59
    ("2026-09-18T16:00:00Z", date(2026, 9, 19)),  # 上海 00:00:00，已跨日
])
def test_today_is_the_local_day_of_reviewed_at_at_midnight(client, given, local_day):
    mistake = seed(register(client)["id"], due="2026-09-10", last="2026-09-01T00:00:00+00:00")
    result = ok(post(client, mistake, client_op_id=OP, reviewed_at=given))
    assert date.fromisoformat(result["due_date"]) - timedelta(days=result["interval_days"]) == local_day
    assert logs(mistake)[0]["elapsed_days"] == 6 + (local_day - date(2026, 9, 10)).days


def test_cross_timezone_the_same_instant_is_a_different_local_day(client):
    shanghai = seed(register(client)["id"], due="2026-09-10", last="2026-09-01T00:00:00+00:00")
    los_angeles_client, la = second_user(zone="America/Los_Angeles")
    los_angeles = seed(la, due="2026-09-10", last="2026-09-01T00:00:00+00:00")
    instant = "2026-09-18T17:00:00Z"  # 上海 09-19 01:00；洛杉矶 09-18 10:00
    a = ok(post(client, shanghai, client_op_id=OP, reviewed_at=instant))
    b = ok(post(los_angeles_client, los_angeles, client_op_id=OP, reviewed_at=instant))
    assert date.fromisoformat(a["due_date"]) - timedelta(days=a["interval_days"]) == date(2026, 9, 19)
    assert date.fromisoformat(b["due_date"]) - timedelta(days=b["interval_days"]) == date(2026, 9, 18)


def test_backfilled_review_for_a_day_before_due_is_not_due_yet(client):
    # “今天”以 reviewed_at 为准：离线那天这道题还没到期，服务器收到时到期也不能追溯评分。
    mistake = seed(register(client)["id"], due="2026-09-18")
    response = post(client, mistake, client_op_id=OP, reviewed_at="2026-09-17T06:00:00Z")
    assert response.status_code == 409 and "尚未到期" in response.json()["detail"]
    assert ops() == [] and logs(mistake) == []


# ---------------------------------------------------------------- 统计 / 连续打卡 / 每日上限

def queue(client):
    return ok(client.get("/api/review/queue"))


def set_cap(client, cap):
    assert client.put("/api/me/review-settings", json={"daily_review_cap": cap}).status_code == 200


def test_backfilled_reviews_count_on_their_local_day_for_streak_and_heatmap(client):
    owner = register(client)["id"]
    days = {"2026-09-17T06:00:00Z": "2026-09-17", "2026-09-18T06:00:00Z": "2026-09-18",
            "2026-09-19T03:00:00Z": "2026-09-19"}
    for index, given in enumerate(days):
        mistake = seed(owner, due="2026-09-10", last="2026-09-01T00:00:00+00:00")
        ok(post(client, mistake, client_op_id=f"op-streak0{index}", reviewed_at=given))
    activity = ok(client.get("/api/stats/activity"))
    assert activity["streak_days"] == 3
    assert {day["date"]: day["reviews"] for day in activity["days"]} == {d: 1 for d in days.values()}


def test_a_backfill_that_crosses_local_midnight_counts_for_the_next_local_day(client):
    owner = register(client)["id"]
    set_cap(client, 5)
    for index in range(7):
        seed(owner, due="2026-09-10", last="2026-09-01T00:00:00+00:00")
    base = queue(client)
    assert (base["done_today"], base["remaining_today"], base["total_due"]) == (0, 5, 7)
    first, second, third = [item["id"] for item in base["items"][:3]]
    # 上海 09-18 14:00：昨天，不占今天名额。
    ok(post(client, first, client_op_id="op-cap00001", reviewed_at="2026-09-18T06:00:00Z"))
    after_yesterday = queue(client)
    assert (after_yesterday["done_today"], after_yesterday["remaining_today"]) == (0, 5)
    assert after_yesterday["total_due"] == 6
    # 上海 09-19 11:00：今天。
    ok(post(client, second, client_op_id="op-cap00002", reviewed_at="2026-09-19T03:00:00Z"))
    # UTC 还是 09-18，但上海已经是 09-19 01:30：按用户本地日算今天。
    ok(post(client, third, client_op_id="op-cap00003", reviewed_at="2026-09-18T17:30:00Z"))
    after_today = queue(client)
    assert (after_today["done_today"], after_today["remaining_today"]) == (2, 3)


def test_the_daily_cap_never_rejects_a_backfilled_review(client):
    owner = register(client)["id"]
    set_cap(client, 5)
    mistakes = [seed(owner, due="2026-09-10", last="2026-09-01T00:00:00+00:00") for _ in range(8)]
    for index, mistake in enumerate(mistakes):  # 昨天一口气做了 8 道，超过上限 5
        assert post(client, mistake, client_op_id=f"op-over000{index}",
                    reviewed_at="2026-09-18T06:00:00Z").status_code == 200
    state = queue(client)
    assert state["done_today"] == 0 and state["remaining_today"] == 5


# ---------------------------------------------------------------- 撤销 / 暂停 / 推迟

def undo(client, mistake, version):
    return client.post(f"/api/mistakes/{mistake}/review/undo", json={"version": version})


def test_a_replay_after_undo_returns_the_saved_response_and_does_not_rescore(client):
    mistake = seed(register(client)["id"], due="2026-09-15")
    before = stored(mistake)
    first = ok(post(client, mistake, quality=5, client_op_id=OP))
    ok(undo(client, mistake, first["version"]))
    restored = stored(mistake)
    assert restored["version"] == 2 and logs(mistake) == []
    assert restored["due_date"] == before["due_date"]

    replay = post(client, mistake, quality=5, client_op_id=OP)
    assert replay.status_code == 200 and replay.json() == first  # 第一次的响应，不是新评分
    assert stored(mistake) == restored and logs(mistake) == []
    assert len(ops()) == 1  # 记录还在，直到 30 天清理

    # 撤销之后想重新评分，要用新的 client_op_id。
    fresh = ok(post(client, mistake, quality=5, client_op_id="op-00000002"))
    assert fresh["version"] == 3 and len(logs(mistake)) == 1


def test_undo_window_is_measured_from_reviewed_at(client):
    # 现有规则不变：撤销看评分日志里的 reviewed_at。补交的旧评分已经超过 30 分钟，不能撤销；
    # 补交时间在 30 分钟内的可以。
    owner = register(client)["id"]
    old = seed(owner, due="2026-09-18")
    recent = seed(owner, due="2026-09-18")
    old_result = ok(post(client, old, client_op_id="op-old00000", reviewed_at=stamp(NOW - timedelta(hours=2))))
    recent_result = ok(post(client, recent, client_op_id="op-new00000", reviewed_at=stamp(NOW - timedelta(minutes=10))))
    blocked = undo(client, old, old_result["version"])
    assert blocked.status_code == 409 and blocked.json() == {"detail": "超过 30 分钟，不能撤销"}
    assert len(logs(old)) == 1
    assert undo(client, recent, recent_result["version"]).status_code == 200


def test_suspended_mistakes_are_still_rejected_and_nothing_is_recorded(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET suspended_at = ? WHERE id = ?", (NOW.isoformat(), mistake))
    for body in ({"version": 0}, {"client_op_id": OP, "reviewed_at": stamp(NOW)}):
        response = post(client, mistake, **body)
        assert response.status_code == 409
        assert response.json() == {"detail": "这条易错点已暂停，请先恢复"}
    assert logs(mistake) == [] and ops() == []


def test_suspending_after_the_fact_does_not_change_a_saved_response(client):
    mistake = seed(register(client)["id"])
    first = ok(post(client, mistake, client_op_id=OP))
    assert client.post(f"/api/mistakes/{mistake}/suspend", json={"version": first["version"]}).status_code == 200
    before = stored(mistake)
    assert ok(post(client, mistake, client_op_id=OP)) == first  # 重复提交仍是第一次的结果，不是 409
    assert stored(mistake) == before and len(logs(mistake)) == 1


def test_a_snoozed_mistake_is_skipped_by_a_late_offline_grade(client):
    mistake = seed(register(client)["id"])
    snoozed = ok(client.post(f"/api/mistakes/{mistake}/snooze", json={"version": 0, "days": 3}))
    assert snoozed["due_date"] == "2026-09-22"
    response = post(client, mistake, client_op_id=OP, reviewed_at=stamp(NOW - timedelta(hours=1)))
    assert response.status_code == 409 and "尚未到期" in response.json()["detail"]
    assert ops() == [] and logs(mistake) == [] and stored(mistake)["due_date"] == "2026-09-22"


def test_other_users_mistakes_are_not_found_and_leave_no_record(client):
    register(client)
    bob_client, bob = second_user()
    bobs = seed(bob)
    assert post(client, bobs, client_op_id=OP).status_code == 404
    assert ops() == [] and logs(bobs) == []


# ---------------------------------------------------------------- /sw.js 与 manifest

def test_service_worker_is_served_from_the_root_with_the_right_headers(client):
    expected = (main.ROOT / "static" / "sw.js").read_bytes()
    for path in ("/sw.js", "/sw.js?v=3"):
        response = client.get(path)  # 无需登录
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/javascript; charset=utf-8"
        assert response.headers["service-worker-allowed"] == "/"
        assert response.headers["cache-control"] == "no-cache"
        assert response.content == expected
    assert client.post("/sw.js").status_code == 405


def test_manifest_is_served_with_the_manifest_media_type(client):
    path = main.ROOT / "static" / "manifest.webmanifest"
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/manifest+json"
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == path.read_bytes()
    assert response.json()["scope"] == "/"
    assert "service-worker-allowed" not in response.headers


def test_csp_is_not_relaxed_for_the_new_routes(client):
    reference = client.get("/").headers["content-security-policy"]
    assert "'unsafe" not in reference and "script-src 'self'" in reference
    for path in ("/sw.js", "/manifest.webmanifest", "/static/icons/icon-192.png"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == reference
        assert response.headers["x-content-type-options"] == "nosniff"
    # 其余静态文件仍走 /static，/sw.js 之外的脚本没有被特殊放行。
    assert client.get("/static/sw.js").headers.get("service-worker-allowed") is None
