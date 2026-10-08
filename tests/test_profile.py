"""Public profiles expose a fixed aggregate allowlist, never notebook contents."""
import pytest
import json
from fastapi import HTTPException

import db
import main
from test_app import client, new_problem, register


def test_profile_requires_login(client):
    assert client.get('/api/users/1/public').status_code == 401
    assert client.put('/api/me/bio', json={'bio': 'hello'}).status_code == 401


def test_bio_limits_owner_and_clear(client):
    owner = register(client)
    exact = '简' * 200
    assert client.put('/api/me/bio', json={'bio': exact}).json() == {'bio': exact}
    for invalid in ('x' * 201, 12, None):
        assert client.put('/api/me/bio', json={'bio': invalid}).status_code == 422
    assert client.put('/api/me/bio', json={'bio': 'x', 'user_id': 99}).status_code == 422
    assert client.get(f"/api/users/{owner['id']}/public").json()['bio'] == exact
    assert client.put('/api/me/bio', json={'bio': ''}).json() == {'bio': ''}


@pytest.mark.parametrize('payload', [{'bio': '\ud800'}, {'bio': '\udfff' * 201}, {'bio': ['\ud800']}])
def test_bio_rejects_surrogate_strings_without_echo_or_write(client, payload):
    owner = register(client)
    assert client.put('/api/me/bio', json={'bio': '旧简介'}).status_code == 200
    response = client.put('/api/me/bio', content=json.dumps(payload), headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert response.json() == {'detail': '简介内容无效，请填写最多 200 字的文字'}
    with main.connect() as conn:
        assert conn.execute('SELECT bio FROM users WHERE id = ?', (owner['id'],)).fetchone()[0] == '旧简介'


def test_profile_safe_aggregates_from_other_account(client, monkeypatch):
    owner = register(client)
    first, _ = new_problem(client)
    client.put('/api/me/bio', json={'bio': '<script>hello</script>'})
    with main.connect(write=True) as conn:
        conn.execute("UPDATE users SET avatar_version = 4, public_rank_opt_out = 1 WHERE id = ?", (owner['id'],))
        conn.execute("INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) VALUES (?, 4, '2026-09-19T04:00:00+00:00', '2026-09-20')", (first,))
    monkeypatch.setattr(main, 'sec_has_avatar', lambda user_id: False)
    client.post('/api/auth/logout')
    register(client, 'bob')
    response = client.get(f"/api/users/{owner['id']}/public")
    assert response.status_code == 200
    assert response.json() == {
        'user_id': owner['id'], 'username': 'alice', 'bio': '<script>hello</script>',
        'avatar_version': 4, 'has_avatar': False, 'problem_count': 1, 'lifetime_problem_count': 1,
        'mistake_count': 2, 'streak_days': 1, 'review_count': 1, 'achievement_count': 1,
    }


@pytest.mark.parametrize('user_id', [-1, 0, 9999, 9223372036854775808, 10 ** 100])
def test_invalid_or_missing_profile_ids_return_404(client, user_id):
    register(client)
    assert client.get(f'/api/users/{user_id}/public').status_code == 404


@pytest.mark.parametrize('field,value', [('is_banned', 1), ('deleted_at', '2026-09-19')])
def test_inactive_profile_hidden(client, field, value):
    owner = register(client)
    client.post('/api/auth/logout')
    register(client, 'bob')
    with main.connect(write=True) as conn:
        conn.execute(f'UPDATE users SET {field} = ? WHERE id = ?', (value, owner['id']))
    assert client.get(f"/api/users/{owner['id']}/public").status_code == 404


def test_delete_account_clears_bio(client):
    owner = register(client)
    client.put('/api/me/bio', json={'bio': 'private after deletion'})
    response = client.post('/api/me/delete-account', json={'password': 'a-test-password-123'})
    assert response.status_code == 200
    with main.connect() as conn:
        assert conn.execute('SELECT bio FROM users WHERE id = ?', (owner['id'],)).fetchone()[0] == ''


def test_bio_write_rechecks_ban_inside_lock(client, monkeypatch):
    register(client)
    def ban(conn, user_id):
        raise HTTPException(401, '账号已被封禁，无法继续使用')
    monkeypatch.setattr(main, 'rvb_account', ban)
    assert client.put('/api/me/bio', json={'bio': 'late'}).status_code == 401


def test_version_18_upgrade_preserves_user_and_pending_reason(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'upgrade.db'))
    with monkeypatch.context() as patch:
        patch.setattr(db, 'MIGRATIONS', [entry for entry in db.MIGRATIONS if entry[0] <= 18])
        patch.setattr(db, 'SCHEMA_VERSION', 18)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO users(id, username, password_hash, timezone, created_at) VALUES (7, 'old', 'hash', 'Asia/Shanghai', '2026-09-19')")
        before = dict(conn.execute('SELECT * FROM users WHERE id = 7').fetchone())
    db.init_db()
    with db.connect(write=True) as conn:
        assert db.schema_version(conn) == 22
        after = dict(conn.execute('SELECT * FROM users WHERE id = 7').fetchone())
        assert after.pop('bio') == ''
        assert after.pop('lifetime_problem_count') == 0
        assert after == before
        column = next(row for row in conn.execute('PRAGMA table_info(users)') if row['name'] == 'bio')
        assert (column['type'], column['notnull'], column['dflt_value']) == ('TEXT', 1, "''")
        with pytest.raises(db.sqlite3.IntegrityError):
            conn.execute('UPDATE users SET bio = NULL WHERE id = 7')
