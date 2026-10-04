"""无 SQLite 的窄守卫单测；不能替代事务、并发及 API 集成测试。"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import main
import mastery


NOW = datetime(2026, 10, 4, 4, tzinfo=timezone.utc)


class FirstWriteReached(Exception):
    """合法边界走到了写入阶段；立即停止，避免模拟完整数据库事务。"""


@pytest.fixture
def undo_probe(monkeypatch):
    item = {"version": 7}
    review = {
        "id": 19,
        "due_before": "2026-10-01",
        "last_reviewed_before": None,
        "version_after": 7,
        "reviewed_at": NOW.isoformat(),
        "repetitions_before": 2,
        "scheduled_days": 6,
        "ease_before": 2.5,
    }
    statements = []
    ownership_checks = []

    class ReviewLookup:
        def fetchone(self):
            return review

    class GuardConnection:
        def execute(self, sql, parameters):
            normalized = " ".join(sql.split())
            statements.append((normalized, parameters))
            if normalized.startswith("UPDATE mistakes SET"):
                raise FirstWriteReached
            assert normalized.startswith("SELECT "), f"unexpected write in guard-only test: {normalized}"
            assert parameters == (13,)
            return ReviewLookup()

    connection = GuardConnection()

    @contextmanager
    def fake_connect(*, write=False):
        assert write is True
        yield connection

    def fake_account(conn, owner):
        assert conn is connection and owner == 11
        ownership_checks.append("account")
        return {"id": owner}

    def fake_owned_mistake(conn, mistake_id, owner):
        assert conn is connection and mistake_id == 13 and owner == 11
        ownership_checks.append("mistake")
        return item

    monkeypatch.setattr(main, "connect", fake_connect)
    monkeypatch.setattr(main, "rvb_account", fake_account)
    monkeypatch.setattr(main, "owned_mistake", fake_owned_mistake)
    monkeypatch.setattr(main, "utc_now", lambda: NOW.isoformat())
    return review, statements, ownership_checks


def attempt_undo():
    return main.rvb_undo_review(13, main.RvbVersionInput(version=7), user={"id": 11})


def assert_read_only_rejection(statements, ownership_checks):
    assert ownership_checks == ["account", "mistake"]
    assert len(statements) == 1
    assert statements[0][0].startswith("SELECT ")
    assert not any(sql.startswith(("UPDATE ", "DELETE ")) for sql, _params in statements)


@pytest.mark.parametrize("version_after", [None, 6, 8], ids=["legacy-null", "older", "newer"])
def test_undo_version_after_mismatch_rejects_before_any_write(undo_probe, version_after):
    review, statements, ownership_checks = undo_probe
    review["version_after"] = version_after
    with pytest.raises(HTTPException) as rejection:
        attempt_undo()
    assert rejection.value.status_code == 409
    assert rejection.value.detail == "这条记录之后又被修改过，不能撤销"
    assert_read_only_rejection(statements, ownership_checks)


@pytest.mark.parametrize("elapsed_seconds", [1800.000001, 1801], ids=["microsecond-over", "second-over"])
def test_undo_over_thirty_minutes_rejects_before_any_write(undo_probe, elapsed_seconds):
    review, statements, ownership_checks = undo_probe
    review["reviewed_at"] = (NOW - timedelta(seconds=elapsed_seconds)).isoformat()
    with pytest.raises(HTTPException) as rejection:
        attempt_undo()
    assert rejection.value.status_code == 409
    assert rejection.value.detail == "超过 30 分钟，不能撤销"
    assert_read_only_rejection(statements, ownership_checks)


def test_undo_exact_thirty_minutes_passes_guard_and_stops_at_first_write(undo_probe):
    review, statements, ownership_checks = undo_probe
    review["reviewed_at"] = (NOW - timedelta(minutes=30)).isoformat()
    with pytest.raises(FirstWriteReached):
        attempt_undo()
    assert ownership_checks == ["account", "mistake"]
    assert len(statements) == 2
    assert statements[0][0].startswith("SELECT ")
    assert statements[1][0].startswith("UPDATE mistakes SET")


def test_mastery_suspension_changes_due_workload_but_preserves_memory_and_history(monkeypatch):
    today = date(2026, 10, 4)
    created = today - timedelta(days=21)

    def item(due, events):
        return {
            "zone": "算法", "created": created, "due": due, "suspended": False,
            "events": events, "event_days": [day for day, _interval in events],
        }

    items = {
        1: item(today - timedelta(days=3), [(created, 1)]),
        2: item(today, [(created, 1)]),
        3: item(today + timedelta(days=5), [(created, 1), (today - timedelta(days=1), 6)]),
    }
    connection = object()

    def fake_load_mistakes(conn, owner, timezone_name):
        assert conn is connection and owner == 11 and timezone_name == "Asia/Taipei"
        return items

    monkeypatch.setattr(mastery, "load_mistakes", fake_load_mistakes)
    before = mastery.mastery_report(connection, 11, "Asia/Taipei", today, weeks=4)
    before_zone, = before["zones"]
    assert (before_zone["total"], before_zone["due"], before_zone["overdue"]) == (3, 2, 1)
    assert before["alert"] is not None

    items[1]["suspended"] = True
    items[2]["suspended"] = True
    after = mastery.mastery_report(connection, 11, "Asia/Taipei", today, weeks=4)
    after_zone, = after["zones"]
    assert (after_zone["due"], after_zone["overdue"]) == (0, 0)
    for key in ("total", "mastery", "change", "at_risk", "series"):
        assert after_zone[key] == before_zone[key]
    assert after["overall"] == before["overall"]
    assert after["points"] == before["points"]
    assert after["alert"] is None
