"""Duck quota policy stays synchronized across server, examples, UI and operations."""
from pathlib import Path
import pytest
from ai_limits import duck_daily_limit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('value,expected', [(None, 10), ('10', 10), ('3', 3), ('0', 0), ('bad', 10), ('-1', 10)])
def test_duck_limit_configuration(monkeypatch, value, expected):
    monkeypatch.delenv('DUCK_DAILY_LIMIT', raising=False)
    if value is not None:
        monkeypatch.setenv('DUCK_DAILY_LIMIT', value)
    assert duck_daily_limit() == expected


def test_duck_billing_documentation_consistent():
    for file in ('.env.example', 'deploy/.env.prod.example'):
        assert 'DUCK_DAILY_LIMIT=10' in (ROOT / file).read_text(encoding='utf-8')
    for file in ('README.md', 'docs/operations/deploy.md'):
        text = (ROOT / file).read_text(encoding='utf-8')
        assert 'DUCK_DAILY_LIMIT' in text and '10 次' in text
        assert '502/503/504' in text
    ui = (ROOT / 'static/duck-panel.js').read_text(encoding='utf-8')
    assert '独立每日额度（默认 10 次' in ui and '不占其他 AI 额度' in ui
    assert '服务端失败退还，并发繁忙不扣' in ui
    assert '每日额度沿用当前套餐' not in (ROOT / 'README.md').read_text(encoding='utf-8')
