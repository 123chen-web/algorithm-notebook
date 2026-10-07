"""Authenticated public profiles; account helpers stay in main's namespace."""
import main

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field


router = APIRouter()


class BioUpdate(main.InputModel):
    bio: str = Field(max_length=200)


@router.put('/api/me/bio')
def update_my_bio(data: BioUpdate, user=Depends(main.current_user)):
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user['id'])
        conn.execute('UPDATE users SET bio = ? WHERE id = ?', (data.bio, user['id']))
    return {'bio': data.bio}


@router.get('/api/users/{user_id}/public')
def get_public_profile(user_id: int, user=Depends(main.current_user)):
    if not 1 <= user_id <= 9223372036854775807:
        raise HTTPException(404, '用户不存在')
    with main.connect() as conn:
        conn.execute('BEGIN')
        target = conn.execute(
            'SELECT id, username, timezone, avatar_version, bio FROM users '
            'WHERE id = ? AND deleted_at IS NULL AND is_banned = 0', (user_id,),
        ).fetchone()
        if target is None:
            raise HTTPException(404, '用户不存在')
        metrics = main.learning_metrics(conn, user_id, target['timezone'], main.today_for(dict(target)))
        problem_count = conn.execute('SELECT COUNT(*) FROM problems WHERE user_id = ?', (user_id,)).fetchone()[0]
        review_count = conn.execute(
            'SELECT COUNT(*) FROM reviews r JOIN mistakes m ON m.id = r.mistake_id '
            'JOIN problems p ON p.id = m.problem_id WHERE p.user_id = ?', (user_id,),
        ).fetchone()[0]
    return {
        'user_id': user_id, 'username': target['username'], 'bio': target['bio'],
        'avatar_version': target['avatar_version'], 'has_avatar': main.sec_has_avatar(user_id),
        'problem_count': problem_count, 'mistake_count': metrics['mistake_count'],
        'review_count': review_count, 'streak_days': metrics['current_streak_days'],
        'achievement_count': sum(badge['unlocked'] for badge in main.evaluate_achievements(metrics)),
    }
