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
