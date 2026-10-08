"""Friend feedback contracts; all services remain local/fake."""
import pytest
import main
from test_app import client, register, new_problem


@pytest.mark.parametrize('name', ['张三', '欧叶同学', '', 'Abc123'])
def test_rank_name_accepts_optional_short_names(client, name):
    register(client)
    response = client.put('/api/me/profile-settings', json={'rank_display_name': name})
    assert response.status_code == 200
    assert response.json()['rank_display_name'] == name


@pytest.mark.parametrize('name', [' ', '@张三', 'https://a.cn', 'a.com', '字' * 13, '张\n三', 42])
def test_rank_name_invalid(client, name):
    register(client)
    assert client.put('/api/me/profile-settings', json={'rank_display_name': name}).status_code == 422


def test_public_card_allowlist_and_privacy(client):
    owner = register(client)
    new_problem(client)
    assert client.put('/api/me/profile-settings', json={
        'rank_display_name': '张三', 'profile_public_bio': False,
        'profile_public_count': False, 'profile_public_joined': False,
    }).status_code == 200
    client.post('/api/auth/logout')
    register(client, 'bob')
    card = client.get(f"/api/users/{owner['id']}/public").json()
    assert card['display_name'] == '张三'
    assert set(card) == {'user_id', 'display_name', 'avatar_version', 'has_avatar'}
    with main.connect(write=True) as conn:
        conn.execute('UPDATE users SET public_rank_opt_out = 1 WHERE id = ?', (owner['id'],))
    assert client.get(f"/api/users/{owner['id']}/public").json()['display_name'] != '张三'


def test_old_user_defaults_are_public_safe(client):
    owner = register(client)
    card = client.get(f"/api/users/{owner['id']}/public").json()
    assert card['display_name'] == 'alice'
    assert {'bio', 'lifetime_problem_count', 'joined_at'} <= card.keys()
    assert not {'username', 'email', 'review_count', 'mistake_count', 'streak_days'} & card.keys()


def test_rank_name_used_only_in_both_boards_and_card(client):
    import rank_board
    owner = register(client)
    first, _ = new_problem(client)
    client.put('/api/me/profile-settings', json={'rank_display_name': '张三'})
    with main.connect(write=True) as conn:
        conn.execute("INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, '2026-09-19T04:00:00+00:00', '2026-09-20')", (first,))
    assert client.get('/api/leaderboard').json()['entries'][0]['display_name'] == '张三'
    assert rank_board.compute_board(main.date(2026, 9, 19))['entries'][0]['username'] == '张三'
    client.put('/api/me/public-rank', json={'participate': False})
    assert client.get('/api/leaderboard').json()['entries'] == []
    assert rank_board.compute_board(main.date(2026, 9, 19))['entries'] == []


def test_actual_payment_amount_required_and_mismatch_stays_pending(client):
    owner = register(client)
    with main.connect(write=True) as conn:
        conn.execute("INSERT INTO plans(id, name, price_cents, period_days, ai_daily_limit, created_at) VALUES (1, '测试套餐', 990, 30, 50, '2026-10-09')")
    body = {'plan_id': 1, 'payer_note': '付款人'}
    assert client.post('/api/manual-claims', json=body).status_code == 422
    response = client.post('/api/manual-claims', json={**body, 'actual_paid_cents': 800, 'payer_receipt': '自填凭证'})
    assert response.status_code == 201
    claim = response.json()['claim']
    assert claim['actual_paid_cents'] == 800 and claim['amount_cents'] == 990
    assert claim['status'] == 'pending'
    with main.connect() as conn:
        assert conn.execute('SELECT plan_id FROM users WHERE id = ?', (owner['id'],)).fetchone()[0] is None
