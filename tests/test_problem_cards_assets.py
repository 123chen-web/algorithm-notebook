"""题卡静态契约：CSP、资源版本、主题令牌、21 档完成度与手机排版。"""
from pathlib import Path
import re

STATIC = Path(__file__).resolve().parents[1] / 'static'


def test_problem_cards_assets_and_load_order():
    index = (STATIC / 'index.html').read_text(encoding='utf-8')
    assert index.count('/static/problem-cards.css?v=1') == 1
    assert index.count('/static/problem-cards.js?v=1') == 1
    assert index.index('problem-cards.js?v=1') < index.index('focus.js?v=4') < index.index('app.js?v=85')
    for file, version in [('overview.js', 4), ('clusters.js', 7), ('app.js', 85)]:
        assert f'/static/{file}?v={version}' in index


def test_csp_and_server_only_progress():
    source = (STATIC / 'problem-cards.js').read_text(encoding='utf-8')
    for forbidden in ['.style', 'innerHTML', 'insertAdjacentHTML', 'eval(', 'setAttribute("style"']:
        assert forbidden not in source
    assert 'root.dataset.progress' in source
    assert '复习完成度' in source and 'aria-label' in source
    assert 'interval_days /' not in source


def test_progress_css_buckets_theme_tokens_and_mobile():
    css = (STATIC / 'problem-cards.css').read_text(encoding='utf-8')
    buckets = re.findall(r'\[data-progress="(\d+)"\] \{ --problem-fill: (\d+)%; \}', css)
    assert buckets == [(str(n), str(n)) for n in range(0, 101, 5)]
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', css)
    assert 'background: var(--soft)' in css and 'color: var(--ink)' in css
    assert 'min-width: 0' in css and 'overflow-wrap: anywhere' in css
    assert '@media (max-width: 600px)' in css and 'min-height: 44px' in css
    assert re.search(r'@media \(prefers-reduced-motion: no-preference\) \{\s*\.problem-progress-card::before \{ transition:', css)
    assert css.count('transition:') == 1


def test_all_list_views_use_problem_cards_and_detail_remains_independent():
    for file in ['app.js', 'overview.js', 'clusters.js']:
        source = (STATIC / file).read_text(encoding='utf-8')
        assert 'ProblemCards.group' in source and 'ProblemCards.card' in source
    focus = (STATIC / 'focus.js').read_text(encoding='utf-8')
    assert 'ProblemCards?.position' in focus
    assert 'active.order = items.map((item) => item.id)' in focus
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert 'ProblemCards.navigation' in app
    assert '/api/mistakes/${item.id}/review' in app
