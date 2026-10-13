"""打卡墨点静态契约：CSP、资源版本、主题令牌、两套主题、减少动画、无障碍。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'static'
INDEX = (STATIC / 'index.html').read_text(encoding='utf-8')
JS = (STATIC / 'inkdot.js').read_text(encoding='utf-8')
CSS = (STATIC / 'inkdot.css').read_text(encoding='utf-8')
HEX_COLOR = re.compile(r'#[0-9a-fA-F]{3,8}\b')


def test_inkdot_assets_referenced_once_with_load_order():
    assert INDEX.count('/static/inkdot.css?v=1') == 1
    assert INDEX.count('/static/inkdot.js?v=1') == 1
    assert INDEX.index('overview.js?v=6') < INDEX.index('inkdot.js?v=1')
    assert 'id="streak-ink"' in INDEX
    assert 'id="streak-ticks"' not in INDEX


def test_inkdot_csp_and_theme_tokens():
    for forbidden in ['.style', 'innerHTML', 'insertAdjacentHTML', 'eval(', 'setAttribute("style"']:
        assert forbidden not in JS, f'inkdot.js 含禁用写法: {forbidden}'
    assert not HEX_COLOR.search(JS), 'inkdot.js 里不能写十六进制色，用主题令牌'
    assert not HEX_COLOR.search(CSS), 'inkdot.css 里不能写十六进制色，用主题令牌'
    for token in ['var(--ink)', 'var(--ink-2)', 'var(--accent)']:
        assert token in CSS, f'inkdot.css 缺少主题变量 {token}'


def test_inkdot_animation_rules():
    # 只用 transform / opacity 做动画
    keyframes = re.findall(r'@keyframes\s+([\w-]+)\s*\{([^}]*)\}', CSS, re.S)
    assert {name for name, _ in keyframes} == {'inkdot-fall', 'inkdot-ripple'}
    # 墨滴总时长 ≤ 1 秒：下落 0.42s + 最后一圈涟漪 delay 0.6s + 0.4s = 1.0s
    delays = [float(v) for v in re.findall(r'animation-delay:\s*([\d.]+)s', CSS)]
    assert max(delays) + 0.4 <= 1.0 + 1e-9
    # 减少动画偏好下全部关闭
    assert '@media (prefers-reduced-motion: reduce)' in CSS
    assert '手机' in CSS or '@media (max-width: 599px)' in CSS


def test_inkdot_js_contract():
    assert 'window.InkDot = { render, reset }' in JS
    assert 'aria-label' in JS
    assert 'oy-inkdot:' in JS  # localStorage 命名空间
    assert '墨还在，再点一下就回来了' in JS
    assert '百日' in JS and '一月' in JS and '七日' in JS
