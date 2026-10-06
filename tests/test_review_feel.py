"""复习手感 API：真实隔离 SQLite、用户本地日、版本冲突与撤销事务。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import threading

import pytest

import main
from db import connect
from scheduler import schedule, today_in_timezone
from test_app import client, register


TODAY = date(2026, 9, 19)
NOW = datetime(2026, 9, 19, 4, tzinfo=timezone.utc)
SETTINGS = "/api/me/review-settings"
QUEUE = "/api/review/queue"
VERSION_ERROR = "这条记录已更新，请刷新后再操作"


@pytest.fixture(autouse=True)
def review_clock(monkeypatch):
    monkeypatch.setattr(main, "utc_now", lambda: NOW.isoformat())


def seed(owner, *, due=None, interval=6, repetitions=2, ease=2.5,
         version=0, last="2026-09-10T04:00:00+00:00", zone="算法", tags=()):
    with connect(write=True) as conn:
        problem = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '复习测试', ?, 'Python', '', '', ?)",
            (owner, zone, NOW.isoformat()),
        ).lastrowid
        mistake = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date, interval_days, "
            "repetitions, ease_factor, version, last_reviewed_at) VALUES (?, '边界遗漏', ?, ?, ?, ?, ?, ?)",
            (problem, due or TODAY.isoformat(), interval, repetitions, ease, version, last),
        ).lastrowid
        conn.executemany(
            "INSERT INTO mistake_tags(mistake_id, user_id, tag, created_at) VALUES (?, ?, ?, ?)",
            [(mistake, owner, tag, NOW.isoformat()) for tag in tags],
        )
    return mistake


def stored(mistake):
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM mistakes WHERE id = ?", (mistake,)).fetchone())


def logs(mistake):
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM reviews WHERE mistake_id = ? ORDER BY id", (mistake,),
        )]


def assert_ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def rate(client, mistake, quality=4, version=0):
    return client.post(f"/api/mistakes/{mistake}/review", json={"quality": quality, "version": version})


def undo(client, mistake, version=1):
    return client.post(f"/api/mistakes/{mistake}/review/undo", json={"version": version})


def action(client, mistake, name, version=0, **values):
    return client.post(f"/api/mistakes/{mistake}/{name}", json={"version": version, **values})


def test_preview_is_read_only_and_keeps_the_current_version(client):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-09", version=7)
    before = stored(mistake)
    response = client.get(f"/api/mistakes/{mistake}/preview")
    data = assert_ok(response)
    assert data["version"] == 7
    assert set(data["previews"]) == {"0", "2", "3", "4", "5"}
    assert stored(mistake) == before
    assert logs(mistake) == []


@pytest.mark.parametrize("quality", [0, 2, 3, 4, 5])
@pytest.mark.parametrize("overdue", [0, 10, 24])
def test_preview_matches_the_accepted_review_in_every_grade(client, quality, overdue):
    owner = register(client)["id"]
    mistake = seed(owner, due=(TODAY - timedelta(days=overdue)).isoformat())
    previews = assert_ok(client.get(f"/api/mistakes/{mistake}/preview"))["previews"]
    expected = schedule(2, 6, 2.5, quality, TODAY, overdue_days=overdue)
    assert previews[str(quality)]["interval_days"] == expected["interval_days"]
    assert previews[str(quality)]["due_date"] == expected["due_date"]
    accepted = assert_ok(rate(client, mistake, quality))
    assert accepted == {**expected, "version": 1}
    # 明确锁住逾期成功加成及失败仅扣 0.2，避免两条路径同时用错算法。
    if quality < 3:
        assert accepted["interval_days"] == 1
        assert accepted["ease_factor"] == 2.3
    if overdue == 10 and quality == 5:
        assert accepted["interval_days"] == 40


def test_preview_allows_a_record_that_is_not_yet_due(client):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-22")
    before = stored(mistake)
    data = assert_ok(client.get(f"/api/mistakes/{mistake}/preview"))
    for quality in (0, 2, 3, 4, 5):
        expected = schedule(2, 6, 2.5, quality, TODAY)
        assert data["previews"][str(quality)]["interval_days"] == expected["interval_days"]
        assert data["previews"][str(quality)]["due_date"] == expected["due_date"]
    assert stored(mistake) == before
    assert logs(mistake) == []
    assert rate(client, mistake).status_code == 409


@pytest.mark.parametrize("timezone_name,today", [
    ("Asia/Shanghai", date(2026, 9, 20)),
    ("America/Los_Angeles", date(2026, 9, 19)),
])
def test_preview_and_review_use_the_same_local_day(client, monkeypatch, timezone_name, today):
    owner = register(client)["id"]
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (timezone_name, owner))
    boundary = datetime(2026, 9, 19, 16, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "today_for", lambda user: today_in_timezone(user["timezone"], boundary))
    mistake = seed(owner, due="2026-09-19")
    preview = assert_ok(client.get(f"/api/mistakes/{mistake}/preview"))["previews"]["5"]
    expected = schedule(2, 6, 2.5, 5, today, overdue_days=(today - TODAY).days)
    assert preview["due_date"] == expected["due_date"]
    assert preview["interval_days"] == expected["interval_days"]
    assert assert_ok(rate(client, mistake, 5)) == {**expected, "version": 1}


def test_rating_logs_every_field_needed_to_restore_the_previous_state(client):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-09", version=7)
    before = stored(mistake)
    assert_ok(rate(client, mistake, quality=5, version=7))
    row, = logs(mistake)
    assert row["due_before"] == before["due_date"]
    assert row["last_reviewed_before"] == before["last_reviewed_at"]
    assert row["version_after"] == 8
    assert row["ease_before"] == before["ease_factor"]
    assert row["repetitions_before"] == before["repetitions"]
    assert row["scheduled_days"] == before["interval_days"]
    assert row["elapsed_days"] == 16


@pytest.mark.parametrize("previous_review", [None, "2026-09-10T04:00:00+00:00"])
def test_undo_restores_all_scheduler_fields_and_deletes_only_the_new_review(client, previous_review):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-09", interval=9, repetitions=3,
                   ease=1.9, version=7, last=previous_review)
    before = stored(mistake)
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, ?, ?)",
            (mistake, (NOW - timedelta(days=1)).isoformat(), before["due_date"]),
        )
    old_rows = logs(mistake)
    assert_ok(rate(client, mistake, quality=0, version=7))
    restored = assert_ok(undo(client, mistake, version=8))
    fields = ("repetitions", "interval_days", "ease_factor", "due_date", "last_reviewed_at")
    assert {key: restored[key] for key in fields} == {key: before[key] for key in fields}
    assert restored["version"] == 9
    assert stored(mistake) == {**before, "version": 9}
    assert logs(mistake) == old_rows
    second = undo(client, mistake, version=9)
    assert second.status_code == 409
    assert second.json() == {"detail": "这次评分太早，不能撤销"}
    assert logs(mistake) == old_rows


def test_undo_without_history_is_a_conflict_and_changes_nothing(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    rejected = undo(client, mistake, version=0)
    assert rejected.status_code == 409
    assert isinstance(rejected.json()["detail"], str)
    assert stored(mistake) == before and logs(mistake) == []


def test_undo_an_old_review_without_due_before_is_rejected(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    with connect(write=True) as conn:
        conn.execute("UPDATE reviews SET due_before = NULL WHERE mistake_id = ?", (mistake,))
    before, before_logs = stored(mistake), logs(mistake)
    rejected = undo(client, mistake)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "这次评分太早，不能撤销"}
    assert stored(mistake) == before and logs(mistake) == before_logs


@pytest.mark.parametrize("elapsed,expected_status", [(86400, 200), (86401, 409)])
def test_undo_24_hour_window_includes_the_exact_boundary(client, monkeypatch, elapsed, expected_status):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    before, before_logs = stored(mistake), logs(mistake)
    monkeypatch.setattr(main, "utc_now", lambda: (NOW + timedelta(seconds=elapsed)).isoformat())
    response = undo(client, mistake)
    assert response.status_code == expected_status
    if expected_status == 409:
        assert response.json() == {"detail": "超过 24 小时，不能撤销"}
        assert stored(mistake) == before and logs(mistake) == before_logs
    else:
        assert logs(mistake) == []


def test_undo_23_hours_after_rating_restores_scheduler_fields(client, monkeypatch):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-09", interval=9, repetitions=3, ease=1.9, version=7)
    before = stored(mistake)
    assert_ok(rate(client, mistake, quality=0, version=7))
    monkeypatch.setattr(main, "utc_now", lambda: (NOW + timedelta(hours=23)).isoformat())
    restored = assert_ok(undo(client, mistake, version=8))
    fields = ("repetitions", "interval_days", "ease_factor", "due_date", "last_reviewed_at")
    assert {key: restored[key] for key in fields} == {key: before[key] for key in fields}
    assert logs(mistake) == []


def test_undo_25_hours_after_rating_is_rejected(client, monkeypatch):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    before, before_logs = stored(mistake), logs(mistake)
    monkeypatch.setattr(main, "utc_now", lambda: (NOW + timedelta(hours=25)).isoformat())
    rejected = undo(client, mistake)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "超过 24 小时，不能撤销"}
    assert stored(mistake) == before and logs(mistake) == before_logs


def test_undo_only_reaches_the_latest_review_not_an_earlier_one(client):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-09")
    assert_ok(rate(client, mistake))
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET due_date = '2026-09-09' WHERE id = ?", (mistake,))
    assert_ok(rate(client, mistake, version=stored(mistake)["version"]))
    assert len(logs(mistake)) == 2
    assert_ok(undo(client, mistake, version=stored(mistake)["version"]))
    older = logs(mistake)
    assert len(older) == 1
    rejected = undo(client, mistake, version=stored(mistake)["version"])
    assert rejected.status_code == 409
    assert logs(mistake) == older


def test_undo_compares_the_review_snapshot_version_to_the_current_record(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    assert_ok(client.put(f"/api/mistakes/{mistake}", json={"description": "后来改过", "version": 1}))
    before, before_logs = stored(mistake), logs(mistake)
    rejected = undo(client, mistake, version=2)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": "这条记录之后又被修改过，不能撤销"}
    assert stored(mistake) == before and logs(mistake) == before_logs


def test_undo_rejects_the_callers_stale_version_without_mutating_data(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    before, before_logs = stored(mistake), logs(mistake)
    rejected = undo(client, mistake, version=0)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": VERSION_ERROR}
    assert stored(mistake) == before and logs(mistake) == before_logs


def test_two_simultaneous_undos_succeed_only_once(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    assert_ok(rate(client, mistake))
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait(timeout=10)
        return undo(client, mistake)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert stored(mistake) == {**before, "version": 2}
    assert logs(mistake) == []


def test_undo_can_be_followed_by_a_new_review_and_undo(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    assert_ok(rate(client, mistake))
    assert_ok(undo(client, mistake))
    assert_ok(rate(client, mistake, quality=5, version=2))
    assert len(logs(mistake)) == 1
    assert_ok(undo(client, mistake, version=3))
    assert stored(mistake) == {**before, "version": 4}
    assert logs(mistake) == []


def test_undo_rolls_back_checkin_heatmap_weekly_recap_and_today_count(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    assert assert_ok(client.get("/api/overview"))["streak_days"] == 1
    assert assert_ok(client.get("/api/stats/activity"))["totals"]["reviews"] == 1
    assert assert_ok(client.get("/api/insights/weekly-recap"))["reviews_completed"] == 1
    assert assert_ok(client.get("/api/achievements"))["metrics"]["current_streak_days"] == 1
    assert assert_ok(client.get(QUEUE))["done_today"] == 1
    assert_ok(undo(client, mistake))
    assert assert_ok(client.get("/api/overview"))["streak_days"] == 0
    heatmap = assert_ok(client.get("/api/stats/activity"))
    assert heatmap["totals"]["reviews"] == 0
    assert all(day["reviews"] == 0 for day in heatmap["days"])
    assert assert_ok(client.get("/api/insights/weekly-recap"))["reviews_completed"] == 0
    assert assert_ok(client.get("/api/achievements"))["metrics"]["current_streak_days"] == 0
    assert assert_ok(client.get(QUEUE))["done_today"] == 0
    assert assert_ok(client.get("/api/stats/summary"))["reviews"]["current"] == 0


def test_trial_accounts_can_review_undo_snooze_suspend_and_change_their_preference(client):
    response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    assert response.status_code == 201
    owner = assert_ok(client.get("/api/me"))["id"]
    mistake = seed(owner)
    assert_ok(rate(client, mistake))
    assert_ok(undo(client, mistake))
    assert_ok(action(client, mistake, "suspend", version=2))
    assert_ok(action(client, mistake, "unsuspend", version=3))
    assert_ok(action(client, mistake, "snooze", version=4, days=3))
    assert assert_ok(client.put(SETTINGS, json={"daily_review_cap": 20})) == {"daily_review_cap": 20}


@pytest.mark.parametrize("days", [1, 3, 7])
def test_snooze_changes_only_the_due_date_and_version_without_creating_a_review(client, days):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-01", version=7)
    before = stored(mistake)
    result = assert_ok(action(client, mistake, "snooze", version=7, days=days))
    due_date = (TODAY + timedelta(days=days)).isoformat()
    assert result == {"due_date": due_date, "version": 8}
    assert stored(mistake) == {**before, "due_date": due_date, "version": 8}
    assert logs(mistake) == []


def test_snooze_allows_a_record_due_today(client):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert assert_ok(action(client, mistake, "snooze", days=1)) == {"due_date": "2026-09-20", "version": 1}


def test_snooze_rejects_a_future_due_date_without_changes(client):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-20")
    before = stored(mistake)
    response = action(client, mistake, "snooze", days=1)
    assert response.status_code == 409
    assert response.json() == {"detail": "这条还没到期"}
    assert stored(mistake) == before and logs(mistake) == []


def test_snooze_rejects_a_stale_version_without_changes(client):
    owner = register(client)["id"]
    mistake = seed(owner, version=7)
    before = stored(mistake)
    response = action(client, mistake, "snooze", version=6, days=1)
    assert response.status_code == 409
    assert response.json() == {"detail": VERSION_ERROR}
    assert stored(mistake) == before and logs(mistake) == []


def test_snooze_uses_the_local_day_at_the_utc_date_boundary(client, monkeypatch):
    owner = register(client)["id"]
    boundary = datetime(2026, 9, 19, 16, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "today_for", lambda user: today_in_timezone(user["timezone"], boundary))
    mistake = seed(owner, due="2026-09-20")
    result = assert_ok(action(client, mistake, "snooze", days=3))
    assert result == {"due_date": "2026-09-23", "version": 1}


@pytest.mark.parametrize("days", [0, 2, 5, 8, True, False, 1.0, "1", None],
                         ids=["zero", "two", "five", "eight", "true", "false", "float", "string", "null"])
def test_snooze_rejects_invalid_days_as_a_chinese_detail(client, days):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    response = action(client, mistake, "snooze", days=days)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert stored(mistake) == before and logs(mistake) == []


def test_suspend_and_unsuspend_are_idempotent_even_when_the_original_request_is_retried(client):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-01", version=7)
    before = stored(mistake)
    suspended = assert_ok(action(client, mistake, "suspend", version=7))
    assert suspended == {"suspended_at": NOW.isoformat(), "version": 8}
    assert assert_ok(action(client, mistake, "suspend", version=7)) == suspended
    assert assert_ok(action(client, mistake, "suspend", version=8)) == suspended
    assert stored(mistake) == {**before, "suspended_at": NOW.isoformat(), "version": 8}
    restored = assert_ok(action(client, mistake, "unsuspend", version=8))
    assert restored == {"suspended_at": None, "version": 9}
    assert assert_ok(action(client, mistake, "unsuspend", version=8)) == restored
    assert assert_ok(action(client, mistake, "unsuspend", version=9)) == restored
    assert stored(mistake) == {**before, "version": 9}
    assert logs(mistake) == []
    assert [item["id"] for item in assert_ok(client.get(QUEUE))["items"]] == [mistake]


def test_suspend_and_unsuspend_require_current_version_for_a_state_transition(client):
    owner = register(client)["id"]
    mistake = seed(owner, version=7)
    before = stored(mistake)
    rejected = action(client, mistake, "suspend", version=6)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": VERSION_ERROR}
    assert stored(mistake) == before
    assert_ok(action(client, mistake, "suspend", version=7))
    before = stored(mistake)
    rejected = action(client, mistake, "unsuspend", version=7)
    assert rejected.status_code == 409
    assert rejected.json() == {"detail": VERSION_ERROR}
    assert stored(mistake) == before


@pytest.mark.parametrize("operation", ["review", "snooze"])
def test_suspended_records_cannot_be_reviewed_or_snoozed(client, operation):
    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(action(client, mistake, "suspend"))
    before = stored(mistake)
    if operation == "review":
        response = rate(client, mistake, version=1)
    else:
        response = action(client, mistake, "snooze", version=1, days=3)
    assert response.status_code == 409
    assert response.json() == {"detail": "这条易错点已暂停，请先恢复"}
    assert stored(mistake) == before and logs(mistake) == []


def test_suspension_hides_due_counts_and_forecasts_but_keeps_all_record_history(client):
    owner = register(client)["id"]
    overdue = seed(owner, due="2026-09-01", tags=("边界",))
    today = seed(owner)
    future = seed(owner, due="2026-09-20")
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, ?, ?)",
            (overdue, NOW.isoformat(), "2026-09-01"),
        )
    history_before = {
        path: assert_ok(client.get(path))
        for path in ("/api/stats/activity", "/api/insights/weekly-recap", "/api/achievements")
    }
    mastery_before = assert_ok(client.get("/api/stats/mastery"))
    for mistake in (overdue, today, future):
        assert_ok(action(client, mistake, "suspend"))
    assert assert_ok(client.get("/api/mistakes", params={"due_only": "true"}))["items"] == []
    all_items = assert_ok(client.get("/api/mistakes", params={"due_only": "false"}))["items"]
    assert {item["id"] for item in all_items} == {overdue, today, future}
    assert all(item["suspended_at"] == NOW.isoformat() for item in all_items)
    assert_ok(client.get(f"/api/mistakes/{overdue}/preview"))
    queue = assert_ok(client.get(QUEUE))
    assert queue["items"] == [] and queue["total_due"] == 0
    assert queue["done_today"] == 1, "暂停不抹掉历史评分"
    overview = assert_ok(client.get("/api/overview"))
    assert overview["total_mistakes"] == 3
    assert overview["due_count"] == overview["overdue_count"] == 0
    assert overview["due_preview"] == []
    assert overview["zones"] == [{"zone": "算法", "total": 3, "due": 0}]
    summary = assert_ok(client.get("/api/stats/summary"))
    assert summary["due"] == {"today": 0, "overdue": 0}
    assert all(day["due"] == 0 for day in summary["forecast"])
    assert summary["reviews"]["current"] == 1
    for path, before in history_before.items():
        assert assert_ok(client.get(path)) == before
    mastery_after = assert_ok(client.get("/api/stats/mastery"))
    assert mastery_after["overall"] == mastery_before["overall"]
    assert len(mastery_after["zones"]) == len(mastery_before["zones"])
    for old_zone, new_zone in zip(mastery_before["zones"], mastery_after["zones"]):
        assert new_zone["due"] == new_zone["overdue"] == 0
        for key in old_zone.keys() - {"due", "overdue"}:
            assert new_zone[key] == old_zone[key]
    exported = assert_ok(client.get("/api/export"))
    assert sum(len(problem["mistakes"]) for problem in exported["problems"]) == 3
    assert assert_ok(client.get("/api/tags"))["tags"][0]["count"] == 1
    assert_ok(action(client, overdue, "unsuspend", version=1))
    assert stored(overdue)["due_date"] == "2026-09-01"
    assert assert_ok(client.get("/api/overview"))["overdue_count"] == 1


@pytest.mark.parametrize("cap", [None, 5, 20, 200])
def test_review_settings_accept_and_persist_the_full_contract(client, cap):
    register(client)
    assert assert_ok(client.get(SETTINGS)) == {"daily_review_cap": None}
    expected = {"daily_review_cap": cap}
    assert assert_ok(client.put(SETTINGS, json=expected)) == expected
    assert assert_ok(client.get(SETTINGS)) == expected
    assert "daily_review_cap" not in assert_ok(client.get("/api/me"))


@pytest.mark.parametrize("value", [4, 201, -1, True, False, 20.0, 20.5, "20", [], {}],
                         ids=["low", "high", "negative", "true", "false", "float-int", "float", "string", "list", "object"])
def test_review_settings_reject_invalid_and_coerced_values_without_changing_the_preference(client, value):
    register(client)
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 20}))
    response = client.put(SETTINGS, json={"daily_review_cap": value})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert assert_ok(client.get(SETTINGS)) == {"daily_review_cap": 20}


def test_review_settings_can_be_cleared_back_to_unlimited_and_are_per_account(client):
    register(client)
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 20}))
    assert_ok(client.post("/api/auth/logout"))
    register(client, username="bob")
    assert assert_ok(client.get(SETTINGS)) == {"daily_review_cap": None}
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 5}))
    assert_ok(client.post("/api/auth/logout"))
    assert_ok(client.post("/api/auth/login", json={"username": "alice", "password": "a-test-password-123"}))
    assert assert_ok(client.get(SETTINGS)) == {"daily_review_cap": 20}
    assert assert_ok(client.put(SETTINGS, json={"daily_review_cap": None})) == {"daily_review_cap": None}


def queue_item(identity, *, overdue=0, interval=1):
    return {
        "id": identity, "due_date": (TODAY - timedelta(days=overdue)).isoformat(),
        "interval_days": interval, "tags": [], "suspended_at": None,
    }


def test_queue_pure_function_prioritizes_overdue_ratio_then_due_date_then_id_without_mutating_input():
    items = [
        queue_item(4, overdue=6, interval=3),
        queue_item(9, overdue=3, interval=0),
        queue_item(3, overdue=2, interval=1),
        queue_item(2, overdue=6, interval=3),
        queue_item(1, overdue=0, interval=0),
        queue_item(8, overdue=9, interval=9),
    ]
    original = [dict(item) for item in items]
    result = main.rvb_review_queue(items, TODAY, None, 12)
    assert [item["id"] for item in result["items"]] == [9, 2, 4, 3, 8, 1]
    assert result == {
        "today": TODAY.isoformat(), "cap": None, "done_today": 12,
        "remaining_today": None, "total_due": 6, "capped": False,
        "items": sorted(items, key=lambda item: [9, 2, 4, 3, 8, 1].index(item["id"])),
    }
    assert items == original


@pytest.mark.parametrize("done,remaining", [(0, 5), (3, 2), (5, 0), (6, 0)])
def test_queue_pure_function_truncates_at_the_remaining_daily_cap(done, remaining):
    items = [queue_item(identity, overdue=identity) for identity in range(1, 8)]
    result = main.rvb_review_queue(items, TODAY, 5, done)
    assert result["cap"] == 5 and result["done_today"] == done
    assert result["remaining_today"] == remaining and result["total_due"] == 7
    assert len(result["items"]) == remaining and result["capped"] is True
    assert [item["id"] for item in result["items"]] == list(range(7, 7 - remaining, -1))


@pytest.mark.parametrize("cap,ignore", [(None, False), (5, True)])
def test_queue_pure_function_unlimited_and_ignore_cap_keep_all_due_items(cap, ignore):
    items = [queue_item(identity, overdue=identity) for identity in range(7)]
    result = main.rvb_review_queue(items, TODAY, cap, 20, ignore_cap=ignore)
    assert len(result["items"]) == 7
    assert result["remaining_today"] is None and result["capped"] is False
    assert result["cap"] == cap and result["done_today"] == 20 and result["total_due"] == 7


@pytest.mark.parametrize("cap,remaining", [(None, None), (5, 2)])
def test_queue_pure_function_empty_queue_is_never_capped(cap, remaining):
    assert main.rvb_review_queue([], TODAY, cap, 3) == {
        "today": TODAY.isoformat(), "cap": cap, "done_today": 3,
        "remaining_today": remaining, "total_due": 0, "capped": False, "items": [],
    }


def insert_history(mistake, timestamps):
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, ?, ?)",
            [(mistake, value, TODAY.isoformat()) for value in timestamps],
        )


def test_queue_matches_the_normal_list_item_contract_and_sorts_every_candidate(client):
    owner = register(client)["id"]
    slow = seed(owner, due="2026-09-10", interval=9, tags=("边界",))
    tied_late = seed(owner, due="2026-09-17", interval=1)
    tied_early = seed(owner, due="2026-09-13", interval=3)
    tied_early_second = seed(owner, due="2026-09-13", interval=3)
    zero = seed(owner, due="2026-09-16", interval=0)
    today = seed(owner, due=TODAY.isoformat(), interval=0)
    seed(owner, due="2026-09-20")
    suspended = seed(owner, due="2026-09-01")
    assert_ok(action(client, suspended, "suspend"))
    queued = assert_ok(client.get(QUEUE))
    assert [item["id"] for item in queued["items"]] == [zero, tied_early, tied_early_second, tied_late, slow, today]
    normal = assert_ok(client.get("/api/mistakes", params={"due_only": "true"}))["items"]
    assert {item["id"]: item for item in queued["items"]} == {item["id"]: item for item in normal}
    assert queued["total_due"] == 6 and queued["cap"] is None
    assert queued["remaining_today"] is None and queued["capped"] is False


def test_queue_daily_cap_counts_review_rows_and_ignore_cap_is_one_request_only(client):
    owner = register(client)["id"]
    mistakes = [seed(owner) for _ in range(7)]
    insert_history(mistakes[0], [NOW.isoformat()] * 3)
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 5}))
    limited = assert_ok(client.get(QUEUE))
    assert limited == {
        "today": TODAY.isoformat(), "cap": 5, "done_today": 3,
        "remaining_today": 2, "total_due": 7, "capped": True,
        "items": assert_ok(client.get("/api/mistakes"))["items"][:2],
    }
    full = assert_ok(client.get(QUEUE, params={"ignore_cap": "true"}))
    assert len(full["items"]) == 7 and full["total_due"] == 7
    assert full["cap"] == 5 and full["done_today"] == 3
    assert full["remaining_today"] is None and full["capped"] is False
    assert assert_ok(client.get(SETTINGS)) == {"daily_review_cap": 5}
    assert assert_ok(client.get(QUEUE))["remaining_today"] == 2


def test_queue_cap_can_be_exhausted_or_exactly_fit_without_inventing_a_due_count(client):
    owner = register(client)["id"]
    mistakes = [seed(owner) for _ in range(2)]
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 5}))
    insert_history(mistakes[0], [NOW.isoformat()] * 3)
    fitting = assert_ok(client.get(QUEUE))
    assert fitting["total_due"] == 2 and fitting["remaining_today"] == 2
    assert fitting["capped"] is False and len(fitting["items"]) == 2
    insert_history(mistakes[0], [NOW.isoformat()] * 3)
    exhausted = assert_ok(client.get(QUEUE))
    assert exhausted["total_due"] == 2 and exhausted["remaining_today"] == 0
    assert exhausted["capped"] is True and exhausted["items"] == []


@pytest.mark.parametrize("timezone_name,timestamps", [
    ("Asia/Shanghai", ["2026-09-18T15:59:59+00:00", "2026-09-18T16:00:00+00:00",
                       "2026-09-19T15:59:59+00:00", "2026-09-19T16:00:00+00:00"]),
    ("America/Los_Angeles", ["2026-09-19T06:59:59+00:00", "2026-09-19T07:00:00+00:00",
                             "2026-09-20T06:59:59+00:00", "2026-09-20T07:00:00+00:00"]),
], ids=["shanghai-midnight", "los-angeles-midnight"])
def test_queue_done_today_uses_the_users_local_day_and_counts_all_reviews(client, timezone_name, timestamps):
    owner = register(client)["id"]
    due = seed(owner, tags=("边界",))
    unrelated = seed(owner, due="2026-09-25", zone="前端")
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = ? WHERE id = ?", (timezone_name, owner))
    insert_history(unrelated, timestamps)
    assert_ok(action(client, unrelated, "suspend"))
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 5}))
    result = assert_ok(client.get(QUEUE, params={"zone": "算法", "tag": "边界"}))
    assert result["done_today"] == 2
    assert result["remaining_today"] == 3
    assert result["total_due"] == 1 and [item["id"] for item in result["items"]] == [due]


def test_queue_zone_tag_filters_match_list_semantics_before_the_cap_is_applied(client):
    owner = register(client)["id"]
    wanted = [seed(owner, tags=("Boundary",), due="2026-09-17") for _ in range(6)]
    seed(owner, tags=("Boundary",), zone="前端", due="2026-09-01")
    seed(owner, tags=("状态",), due="2026-09-01")
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 5}))
    result = assert_ok(client.get(QUEUE, params={"zone": "算法", "tag": " boundary "}))
    assert result["total_due"] == 6 and result["capped"] is True
    assert [item["id"] for item in result["items"]] == wanted[:5]
    full = assert_ok(client.get(QUEUE, params={"zone": "算法", "tag": "boundary", "ignore_cap": "true"}))
    listed = assert_ok(client.get("/api/mistakes", params={"zone": "算法", "tag": "boundary"}))
    assert full["items"] == listed["items"]
    assert assert_ok(client.get(QUEUE, params={"tag": "不存在"}))["total_due"] == 0


def test_queue_empty_account_has_the_complete_unlimited_response(client):
    register(client)
    assert assert_ok(client.get(QUEUE)) == {
        "today": TODAY.isoformat(), "cap": None, "done_today": 0,
        "remaining_today": None, "total_due": 0, "capped": False, "items": [],
    }


def test_queue_rejects_an_unknown_zone_and_an_overlong_tag(client):
    register(client)
    response = client.get(QUEUE, params={"zone": "未知分区"})
    assert response.status_code == 400
    assert response.json() == {"detail": "分区不存在"}
    response = client.get(QUEUE, params={"tag": "标" * 41})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


RVB_REQUESTS = [
    ("GET", "/api/mistakes/{mistake}/preview", None),
    ("POST", "/api/mistakes/{mistake}/review", {"quality": 4, "version": 0}),
    ("POST", "/api/mistakes/{mistake}/review/undo", {"version": 0}),
    ("POST", "/api/mistakes/{mistake}/snooze", {"version": 0, "days": 1}),
    ("POST", "/api/mistakes/{mistake}/suspend", {"version": 0}),
    ("POST", "/api/mistakes/{mistake}/unsuspend", {"version": 0}),
    ("GET", SETTINGS, None),
    ("PUT", SETTINGS, {"daily_review_cap": 20}),
    ("GET", QUEUE, None),
]
RVB_REQUEST_IDS = ["preview", "review", "undo", "snooze", "suspend", "unsuspend", "get-settings", "put-settings", "queue"]
RVB_WRITE_REQUESTS = [entry for entry in RVB_REQUESTS if entry[0] != "GET"]
RVB_WRITE_IDS = [name for name, entry in zip(RVB_REQUEST_IDS, RVB_REQUESTS) if entry[0] != "GET"]


@pytest.mark.parametrize("method,path,payload", RVB_REQUESTS, ids=RVB_REQUEST_IDS)
def test_review_feel_routes_require_a_logged_in_account(client, method, path, payload):
    response = client.request(method, path.format(mistake=1), json=payload)
    assert response.status_code == 401
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("method,path,payload", RVB_REQUESTS[:6], ids=RVB_REQUEST_IDS[:6])
def test_review_feel_mistake_routes_hide_another_users_record(client, method, path, payload):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    assert_ok(client.post("/api/auth/logout"))
    register(client, username="bob")
    response = client.request(method, path.format(mistake=mistake), json=payload)
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)
    assert stored(mistake) == before and logs(mistake) == []


@pytest.mark.parametrize("method,path,payload", RVB_REQUESTS[:6], ids=RVB_REQUEST_IDS[:6])
def test_review_feel_mistake_routes_return_404_for_missing_records(client, method, path, payload):
    register(client)
    response = client.request(method, path.format(mistake=123456789), json=payload)
    assert response.status_code == 404
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("method,path,payload", RVB_WRITE_REQUESTS, ids=RVB_WRITE_IDS)
def test_every_review_feel_write_requires_csrf_protection(client, method, path, payload):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    client.headers.pop("X-CSRF-Protection")
    response = client.request(method, path.format(mistake=mistake), json=payload)
    assert response.status_code == 403
    assert response.json() == {"detail": "请求缺少必要的安全校验"}
    assert stored(mistake) == before and logs(mistake) == []
    with connect() as conn:
        assert conn.execute("SELECT daily_review_cap FROM users WHERE id = ?", (owner,)).fetchone()[0] is None


@pytest.mark.parametrize("method,path,payload", RVB_REQUESTS, ids=RVB_REQUEST_IDS)
@pytest.mark.parametrize("state", ["deleted", "banned"])
def test_review_feel_rechecks_the_account_after_authentication(client, method, path, payload, state):
    owner = register(client)["id"]
    mistake = seed(owner)
    stale_authenticated_user = assert_ok(client.get("/api/me"))
    before = stored(mistake)
    with connect(write=True) as conn:
        if state == "deleted":
            conn.execute("UPDATE users SET deleted_at = ? WHERE id = ?", (NOW.isoformat(), owner))
        else:
            conn.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (owner,))
    # 模拟 current_user 已成功之后账号才失效；各路由必须在自己的事务内重读。
    main.app.dependency_overrides[main.current_user] = lambda: stale_authenticated_user
    try:
        response = client.request(method, path.format(mistake=mistake), json=payload)
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)
    assert response.status_code == 401
    assert isinstance(response.json()["detail"], str)
    assert stored(mistake) == before and logs(mistake) == []
    with connect() as conn:
        assert conn.execute("SELECT daily_review_cap FROM users WHERE id = ?", (owner,)).fetchone()[0] is None


@pytest.mark.parametrize("operation", ["review/undo", "snooze", "suspend", "unsuspend"])
@pytest.mark.parametrize("version", [-1, True, False, 0.0, "0", None],
                         ids=["negative", "true", "false", "float", "string", "null"])
def test_review_feel_versions_are_nonnegative_strict_integers(client, operation, version):
    owner = register(client)["id"]
    mistake = seed(owner)
    before = stored(mistake)
    payload = {"version": version}
    if operation == "snooze":
        payload["days"] = 1
    response = client.post(f"/api/mistakes/{mistake}/{operation}", json=payload)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert stored(mistake) == before and logs(mistake) == []


def test_queue_never_includes_another_users_candidates_or_reviews(client):
    alice = register(client)["id"]
    alice_mistake = seed(alice)
    insert_history(alice_mistake, [NOW.isoformat()] * 5)
    assert_ok(client.put(SETTINGS, json={"daily_review_cap": 20}))
    assert_ok(client.post("/api/auth/logout"))
    bob = register(client, username="bob")["id"]
    bob_mistake = seed(bob)
    insert_history(bob_mistake, [NOW.isoformat()] * 2)
    queued = assert_ok(client.get(QUEUE))
    assert [item["id"] for item in queued["items"]] == [bob_mistake]
    assert queued["total_due"] == 1 and queued["done_today"] == 2
    assert queued["cap"] is None and queued["remaining_today"] is None


def test_review_preview_reads_the_latest_timezone_rather_than_the_auth_snapshot(client, monkeypatch):
    owner = register(client)["id"]
    mistake = seed(owner, due="2026-09-19")
    stale_authenticated_user = assert_ok(client.get("/api/me"))
    boundary = datetime(2026, 9, 19, 16, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "today_for", lambda user: today_in_timezone(user["timezone"], boundary))
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET timezone = 'America/Los_Angeles' WHERE id = ?", (owner,))
    main.app.dependency_overrides[main.current_user] = lambda: stale_authenticated_user
    try:
        preview = assert_ok(client.get(f"/api/mistakes/{mistake}/preview"))["previews"]["5"]
    finally:
        main.app.dependency_overrides.pop(main.current_user, None)
    expected = schedule(2, 6, 2.5, 5, TODAY, overdue_days=0)
    assert preview["interval_days"] == expected["interval_days"]
    assert preview["due_date"] == expected["due_date"]


def test_reminder_queries_and_emails_exclude_suspended_records(client, monkeypatch):
    import mailer
    import send_reminders

    owner = register(client)["id"]
    active = seed(owner)
    hidden = seed(owner, due="2026-09-01")
    with connect(write=True) as conn:
        conn.execute("UPDATE mistakes SET description = '可见待复习' WHERE id = ?", (active,))
        conn.execute("UPDATE mistakes SET description = '暂停不发信' WHERE id = ?", (hidden,))
    assert_ok(action(client, hidden, "suspend"))
    with connect() as conn:
        assert send_reminders.due_count(conn, owner, TODAY.isoformat()) == 1
        rows = send_reminders.due_mistakes(conn, owner, TODAY.isoformat())
        assert [row["description"] for row in rows] == ["可见待复习"]
    sent = []
    monkeypatch.setattr(send_reminders, "today_in_timezone", lambda name: TODAY)
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append((to, body)))
    assert send_reminders.main([]) == 0
    assert len(sent) == 1 and sent[0][0] == "alice@example.com"
    assert "今天有 1 条" in sent[0][1]
    assert "可见待复习" in sent[0][1] and "暂停不发信" not in sent[0][1]


def test_suspended_only_account_gets_no_reminder_and_no_sent_marker(client, monkeypatch):
    import mailer
    import send_reminders

    owner = register(client)["id"]
    mistake = seed(owner)
    assert_ok(action(client, mistake, "suspend"))
    sent = []
    monkeypatch.setattr(send_reminders, "today_in_timezone", lambda name: TODAY)
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))
    assert send_reminders.main([]) == 0
    assert sent == []
    with connect() as conn:
        assert conn.execute("SELECT last_reminder_sent FROM users WHERE id = ?", (owner,)).fetchone()[0] is None
