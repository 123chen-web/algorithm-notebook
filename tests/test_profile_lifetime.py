"""Lifetime intake survives notebook deletion without retaining deleted notes."""
import pytest

import db
import main
from test_app import client, register


def create_quick(client, title):
    response = client.post('/api/problems', json={'title': title, 'zone': '算法', 'language': 'Python', 'quick': True})
    assert response.status_code == 201
    return client.get(f"/api/mistakes/{response.json()['mistake_ids'][0]}").json()['problem_id']


def test_lifetime_count_survives_deletion_and_remains_owner_scoped(client):
    owner = register(client)
    first = create_quick(client, '第一题')
    create_quick(client, '第二题')
    profile_url = f"/api/users/{owner['id']}/public"
    before = client.get(profile_url).json()
    assert before['lifetime_problem_count'] == 2
    assert client.delete(f'/api/problems/{first}').status_code == 200
    after = client.get(profile_url).json()
    assert after['lifetime_problem_count'] == 2
    client.post('/api/auth/logout')
    second_owner = register(client, 'bob')
    second = client.get(f"/api/users/{second_owner['id']}/public").json()
    assert second['lifetime_problem_count'] == 0
    assert client.get(profile_url).json()['lifetime_problem_count'] == 2


def test_lifetime_counter_rolls_back_and_is_erased_on_account_deletion(client):
    owner = register(client)
    create_quick(client, '计数题')
    with pytest.raises(RuntimeError, match='rollback'):
        with main.connect(write=True) as conn:
            conn.execute('INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (owner['id'], '未提交', '算法', 'Python', '', '', '2026-10-08'))
            raise RuntimeError('rollback')
    assert client.get(f"/api/users/{owner['id']}/public").json()['lifetime_problem_count'] == 1
    assert client.post('/api/me/delete-account', json={'password': 'a-test-password-123'}).status_code == 200
    with main.connect() as conn:
        assert conn.execute('SELECT lifetime_problem_count FROM users WHERE id = ?', (owner['id'],)).fetchone()[0] == 0


def test_version_20_backfills_retained_baseline_once(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'upgrade.db'))
    with monkeypatch.context() as patch:
        patch.setattr(db, 'MIGRATIONS', [entry for entry in db.MIGRATIONS if entry[0] <= 20])
        patch.setattr(db, 'SCHEMA_VERSION', 20)
        db.init_db()
    with db.connect(write=True) as conn:
        conn.execute("INSERT INTO users(id, username, password_hash, timezone, created_at) "
                     "VALUES (7, 'old', 'hash', 'Asia/Taipei', '2026-10-08')")
        for title in ('旧题一', '旧题二'):
            conn.execute('INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)', (7, title, '算法', 'Python', '', '', '2026-10-08'))
    db.init_db()
    db.init_db()
    with db.connect(write=True) as conn:
        assert conn.execute('SELECT lifetime_problem_count FROM users WHERE id = 7').fetchone()[0] == 2
        conn.execute('DELETE FROM problems WHERE user_id = 7')
        assert conn.execute('SELECT lifetime_problem_count FROM users WHERE id = 7').fetchone()[0] == 2
        with pytest.raises(db.sqlite3.IntegrityError):
            conn.execute('UPDATE users SET lifetime_problem_count = -1 WHERE id = 7')
