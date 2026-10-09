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


def test_three_mistakes_filtered_and_capped_still_use_whole_problem(client):
    register(client)
    ids = new_problem(client)
    with connect(write=True) as conn:
        pid = conn.execute('SELECT problem_id FROM mistakes WHERE id=?', (ids[0],)).fetchone()[0]
        third = conn.execute('INSERT INTO mistakes(problem_id,description,due_date,interval_days,last_reviewed_at) '
                             'VALUES (?, ?, ?, 21, ?)', (pid, '第三条', '2026-09-19', '2026-09-01')).lastrowid
        conn.execute('UPDATE mistakes SET interval_days=10,last_reviewed_at=? WHERE id=?', ('2026-09-01', ids[1]))
    new_problem(client)
    new_problem(client)
    assert client.put('/api/me/review-settings', json={'daily_review_cap': 5}).status_code == 200
    queue = client.get('/api/review/queue').json()
    assert queue['capped'] and len(queue['items']) == 5
    assert queue['items'][0]['progress'] == 50  # (0 + 10/21 + 1)/3 = 49.2% -> 50%
    assert queue['items'][0]['problem_due_count'] == 3
    assert {m['id'] for m in queue['items'][0]['problem_mistakes']} == {*ids, third}


def test_cached_insight_cards_are_live_without_rewriting_saved_evidence(client):
    import json
    register(client)
    ids = new_problem(client)
    with connect(write=True) as conn:
        pid = conn.execute('SELECT problem_id FROM mistakes WHERE id=?', (ids[0],)).fetchone()[0]
        content = {'summary': '分析', 'patterns': [{'title': '边界', 'evidence': [
            {'mistake_id': mid, 'problem_id': pid, 'title': '二分', 'zone': '算法', 'observation': '观察'} for mid in ids]}]}
        conn.execute('INSERT INTO weakness_insights(user_id,content,created_at) VALUES (1,?,?)',
                     (json.dumps(content), '2026-09-01'))
        conn.execute('UPDATE mistakes SET interval_days=21,last_reviewed_at=? WHERE id=?', ('2026-09-01', ids[1]))
    response = client.get('/api/insights/weakness-analysis').json()
    assert response['problem_cards'][0]['progress'] == 50
    assert response['insight']['content'] == content
    with connect() as conn:
        assert json.loads(conn.execute('SELECT content FROM weakness_insights').fetchone()[0]) == content


def test_problem_metadata_is_owner_scoped(client):
    register(client)
    alice_ids = new_problem(client)
    client.put(f'/api/mistakes/{alice_ids[0]}/tags', json={'tags': ['私有标签']})
    client.post('/api/auth/logout')
    register(client, username='bob')
    bob_ids = new_problem(client)
    items = client.get('/api/mistakes?due_only=false').json()['items']
    assert {i['id'] for i in items} == set(bob_ids)
    assert all('私有标签' not in i['problem_tags'] for i in items)
    assert all(not (set(alice_ids) & {m['id'] for m in i['problem_mistakes']}) for i in items)
