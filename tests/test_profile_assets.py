"""Public-profile integration, inert output and theme contrast contracts."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from test_contrast_tokens import THEMES, contrast_ratio

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'static'


def test_profile_resources_and_author_entries():
    page = (STATIC / 'index.html').read_text(encoding='utf-8')
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    board = (STATIC / 'board.js').read_text(encoding='utf-8')
    assert '/static/profile.css?v=2' in page
    assert page.index('/static/profile.js?v=3') < page.index('/static/app.js?v=')
    assert 'id="my-profile"' in page and 'id="profile-dialog"' in page
    assert 'window.Profile?.reset();' in app
    assert 'getEpoch: () => sessionEpoch, getView: () => view, avatar: avatarElement' in app
    assert 'profileAuthor(member.id, member.username)' in app
    assert 'profileAuthor(post.user_id, post.username)' in app
    assert 'profileAuthor(comment.user_id, comment.username)' in app
    assert 'hooks.author?.(model.author.userId, model.author.username)' in board


def test_profile_inert_text_and_api_only():
    source = (STATIC / 'profile.js').read_text(encoding='utf-8')
    for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write',
                      'eval(', 'new Function', 'fetch(', 'XMLHttpRequest', '.style.',
                      'setAttribute("style"', 'localStorage'):
        assert forbidden not in source
    assert 'hooks.api(' in source
    assert 'textContent' in source
    assert not re.search(r'https?://', source)


def test_profile_uses_defined_theme_tokens():
    source = (STATIC / 'profile.css').read_text(encoding='utf-8')
    assert '!important' not in source
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', source)
    defined = set(re.findall(r'(--[\w-]+)\s*:', (STATIC / 'style.css').read_text(encoding='utf-8')))
    assert set(re.findall(r'var\((--[\w-]+)', source)) <= defined
    assert 'color: var(--ink)' in source
    assert 'color: var(--azurite)' in source
    assert 'color: var(--muted)' in source
    assert 'background: var(--surface)' in source


@pytest.mark.parametrize('context,tokens', list(THEMES.items()), ids=[str(c) for c in THEMES])
@pytest.mark.parametrize('foreground', ['--ink', '--muted', '--azurite'])
def test_profile_text_contrast(context, tokens, foreground):
    ratio = contrast_ratio(tokens[foreground], tokens['--surface'])
    assert ratio >= 4.5, f'{context}: {foreground}/--surface={ratio:.6f}:1'


@pytest.mark.skipif(shutil.which('node') is None, reason='需要 Node.js')
def test_profile_async_behaviour():
    result = subprocess.run(['node', '--test', 'tests/profile_behaviour.cjs'], cwd=ROOT,
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-1000:]
    assert re.search(r'(?m)^(?:ℹ|#) fail 0$', result.stdout)
    assert re.search(r'(?m)^(?:ℹ|#) pass 18$', result.stdout)
