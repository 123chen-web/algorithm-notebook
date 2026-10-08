"""Authenticated public profiles; account helpers stay in main's namespace."""
import main
import re
import unicodedata
import rank_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from pydantic import StrictBool, field_validator


router = APIRouter()


class BioUpdate(main.InputModel):
    bio: str = Field(max_length=200)


class ProfileSettings(main.InputModel):
    rank_display_name: str | None = Field(default=None, max_length=12)
    profile_public_bio: StrictBool | None = None
    profile_public_count: StrictBool | None = None
    profile_public_joined: StrictBool | None = None

    @field_validator('rank_display_name')
    @classmethod
    def valid_name(cls, value):
        if value is None:
            raise ValueError('榜单显示名不能为 null')
        if value == '':
            return value
        if (not value.strip() or any(c.isspace() or unicodedata.category(c).startswith('C') for c in value)
                or re.search(r'@|://|www\.|\.[a-z]{2,}(?:\b|/)', value, re.I)):
            raise ValueError('请填写 1~12 个字，不含网址、@、空白或控制字符；留空则使用昵称')
        return value


@router.put('/api/me/profile-settings')
def update_profile_settings(data: ProfileSettings, user=Depends(main.current_user)):
    values = data.model_dump(exclude_unset=True)
    if any(value is None for value in values.values()):
        raise HTTPException(422, '公开设置不能为 null')
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user['id'])
        if values:
            conn.execute('UPDATE users SET ' + ', '.join(f'{key} = ?' for key in values) + ' WHERE id = ?',
                         (*values.values(), user['id']))
        row = conn.execute('SELECT rank_display_name, profile_public_bio, profile_public_count, profile_public_joined FROM users WHERE id = ?', (user['id'],)).fetchone()
    rank_cache.invalidate()
    return dict(row)


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
            'SELECT id, username, avatar_version, bio, lifetime_problem_count, created_at, '
            'rank_display_name, public_rank_opt_out, profile_public_bio, profile_public_count, profile_public_joined FROM users '
            'WHERE id = ? AND deleted_at IS NULL AND is_banned = 0', (user_id,),
        ).fetchone()
        if target is None:
            raise HTTPException(404, '用户不存在')
    card = {
        'user_id': user_id,
        'display_name': (target['rank_display_name'] if not target['public_rank_opt_out'] else '') or target['username'],
        'avatar_version': target['avatar_version'], 'has_avatar': main.sec_has_avatar(user_id),
    }
    for flag, public_field, source in [('bio', 'bio', 'bio'), ('count', 'lifetime_problem_count', 'lifetime_problem_count'), ('joined', 'joined_at', 'created_at')]:
        if target[f'profile_public_{flag}']:
            card[public_field] = target[source]
    if user_id == user['id']:
        card['settings'] = {key: target[key] for key in ('bio', 'rank_display_name', 'profile_public_bio', 'profile_public_count', 'profile_public_joined')}
    return card
