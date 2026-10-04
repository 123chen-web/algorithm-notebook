from copy import deepcopy
from datetime import date

import pytest

import main


TODAY = date(2026, 10, 4)


def candidates():
    return [
        {"id": 5, "due_date": "2026-10-04", "interval_days": 0},
        {"id": 3, "due_date": "2026-10-02", "interval_days": 2},
        {"id": 4, "due_date": "2026-10-03", "interval_days": 0},
        {"id": 2, "due_date": "2026-10-02", "interval_days": 2},
        {"id": 1, "due_date": "2026-09-29", "interval_days": 2},
        {"id": 6, "due_date": "2026-10-03", "interval_days": 10},
    ]


def test_queue_orders_by_overdue_ratio_then_date_then_id_without_mutating():
    items = candidates()
    original = deepcopy(items)
    result = main.rvb_review_queue(items, TODAY, None, 12)
    assert [item["id"] for item in result["items"]] == [1, 2, 3, 4, 6, 5]
    assert result == {
        "today": "2026-10-04", "cap": None, "done_today": 12,
        "remaining_today": None, "total_due": 6, "capped": False,
        "items": result["items"],
    }
    assert items == original


@pytest.mark.parametrize("done,remaining,ids", [(2, 3, [1, 2, 3]), (5, 0, []), (8, 0, [])])
def test_queue_cap_tracks_completed_reviews(done, remaining, ids):
    result = main.rvb_review_queue(candidates(), TODAY, 5, done)
    assert [item["id"] for item in result["items"]] == ids
    assert result["remaining_today"] == remaining
    assert result["total_due"] == 6
    assert result["capped"] is True


def test_queue_ignore_cap_and_empty_candidates():
    result = main.rvb_review_queue(candidates(), TODAY, 5, 8, ignore_cap=True)
    assert len(result["items"]) == 6
    assert result["cap"] == 5
    assert result["remaining_today"] is None
    assert result["capped"] is False
    empty = main.rvb_review_queue([], TODAY, 5, 2)
    assert empty["items"] == []
    assert empty["remaining_today"] == 3
    assert empty["total_due"] == 0
    assert empty["capped"] is False
