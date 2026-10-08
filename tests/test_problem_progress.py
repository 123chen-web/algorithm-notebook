"""题目完成度实时计算；列表筛选不改变整题的口径。"""
import pytest

from db import connect
from test_app import client, register, new_problem


@pytest.mark.parametrize("intervals,last,suspended,expected", [
    ([0, 21], [None, '2026-09-01'], [None, None], 50),
    ([21, 42], ['2026-09-01'] * 2, [None, None], 100),
    ([21, 21], [None, None], [None, None], 0),
    ([21, 21], ['2026-09-01'] * 2, ['paused', 'paused'], 0),
    ([0, 21], [None, '2026-09-01'], ['paused', None], 100),
    ([1, 2], ['2026-09-01'] * 2, [None, None], 5),
    ([10, 11], ['2026-09-01'] * 2, [None, None], 50),
])
def test_progress_is_shared_by_list_and_detail(client, intervals, last, suspended, expected):
    register(client)
    ids = new_problem(client)
    with connect(write=True) as conn:
        for i, mid in enumerate(ids):
            conn.execute('UPDATE mistakes SET interval_days=?, last_reviewed_at=?, suspended_at=? WHERE id=?',
                         (intervals[i], last[i], suspended[i], mid))
    items = client.get('/api/mistakes?due_only=false').json()['items']
    assert [item['progress'] for item in items] == [expected, expected]
    assert client.get(f'/api/mistakes/{ids[0]}').json()['progress'] == expected
    due = client.get('/api/review/queue').json()['items']
    assert all(item['progress'] == expected for item in due)


def test_filtered_queue_keeps_all_problem_tags_and_progress(client):
    register(client)
    ids = new_problem(client)
    client.put(f'/api/mistakes/{ids[0]}/tags', json={'tags': ['边界']})
    client.put(f'/api/mistakes/{ids[1]}/tags', json={'tags': ['边界', '复杂度']})
    with connect(write=True) as conn:
        conn.execute('UPDATE mistakes SET interval_days=21, last_reviewed_at=?, due_date=? WHERE id=?',
                     ('2026-09-01', '2026-12-01', ids[1]))
    item = client.get('/api/review/queue?tag=边界').json()['items'][0]
    assert item['progress'] == 50
    assert item['problem_tags'] == ['复杂度', '边界']
    assert item['problem_due_count'] == 1
    assert len(item['problem_mistakes']) == 2
    preview = client.get('/api/overview').json()['due_preview']
    assert len(preview) == 1
    assert preview[0]['progress'] == 50


def test_overview_limits_unique_problems_instead_of_mistakes(client):
    register(client)
    for _ in range(6):
        new_problem(client)
    preview = client.get('/api/overview').json()['due_preview']
    assert len(preview) == 5
    assert len({item['problem_id'] for item in preview}) == 5
    assert all(item['problem_due_count'] == 2 for item in preview)
