"""Integrated Muse contracts: host API, inert DOM, theme colors and routing."""
from pathlib import Path
import re
from html.parser import HTMLParser

import pytest
from test_contrast_tokens import THEMES, contrast_ratio

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'static'


@pytest.mark.parametrize('name', ['notes', 'heatmap', 'import-problem', 'reminder', 'similar'])
def test_integrated_modules_use_host_api_and_inert_dom(name):
    source = (STATIC / f'{name}.js').read_text(encoding='utf-8')
    for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'fetch(', 'XMLHttpRequest', 'eval('):
        assert forbidden not in source, (name, forbidden)
    assert 'api(' in source
    assert 'getEpoch' in source
    assert 'reset' in source


@pytest.mark.parametrize('name', ['notes', 'heatmap'])
def test_new_styles_use_defined_theme_tokens(name):
    source = (STATIC / f'{name}.css').read_text(encoding='utf-8')
    defined = set(re.findall(r'(--[\w-]+)\s*:', (STATIC / 'style.css').read_text(encoding='utf-8')))
    assert set(re.findall(r'var\((--[\w-]+)', source)) <= defined
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', source)


@pytest.mark.parametrize('theme', THEMES)
def test_new_text_colors_have_adequate_contrast(theme):
    for fg, bg in [('ink', 'surface'), ('ink-2', 'surface'), ('azurite', 'surface'),
                   ('azurite', 'paper'), ('danger', 'paper'), ('on-accent', 'azurite'),
                   ('ink', 'danger-soft'), ('ink', 'growth-soft'), ('ink', 'field-surface'),
                   ('success-ink', 'surface'), ('danger', 'surface'), ('code-ink', 'code-surface')]:
        assert contrast_ratio(THEMES[theme][f'--{fg}'], THEMES[theme][f'--{bg}']) >= 4.5, (theme, fg, bg)


def test_all_new_production_routes_are_mounted_without_test_side_effects():
    import main
    paths = set(main.app.openapi()['paths'])
    assert {'/api/mastery/heatmap', '/api/mastery/focus', '/api/notes',
            '/api/users/api-token', '/api/problems/import-from-extension'} <= paths


def test_focus_card_is_revealed_and_prefill_status_does_not_conflict_with_file_import():
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    page = (STATIC / 'index.html').read_text(encoding='utf-8')
    assert '$("#ov-focus-card").hidden = false;' in app
    assert 'id="problem-import-status"' in page
    assert '$("#import-status")' not in app
    learn = page.split('aria-labelledby="sidebar-g-learn"', 1)[1].split('</div>', 1)[0]
    assert 'data-view="notes"' in learn
    assert '$("#detail").append(similarHost);' in app
    assert 'reviewSection.append(similarHost)' not in app


def test_notes_page_is_a_main_view_outside_the_forum():
    class ViewParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.notes_ancestors = None
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if values.get('id') == 'notes-page':
                self.notes_ancestors = list(self.stack)
            if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:
                self.stack.append((tag, values.get('id')))
        def handle_endtag(self, tag):
            for index in range(len(self.stack)-1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break
    parser = ViewParser()
    parser.feed((STATIC / 'index.html').read_text(encoding='utf-8'))
    assert parser.notes_ancestors is not None
    assert parser.notes_ancestors[-1][0] == 'main'
    assert ('section', 'forum-page') not in parser.notes_ancestors
