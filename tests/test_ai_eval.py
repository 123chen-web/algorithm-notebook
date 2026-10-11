"""AI 评估小工具测试：只测 --dry-run 和参数解析，绝不联网。"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ai_eval"))

import run_eval  # noqa: E402


def _write_cases(tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_dry_run_prints_prompt_without_network(tmp_path, monkeypatch):
    # 即使把 ai.OpenAI 换成必炸的桩，--dry-run 也不该碰它。
    def no_network(*args, **kwargs):
        raise AssertionError("dry-run 不得联网")

    monkeypatch.setattr(run_eval.ai, "OpenAI", no_network)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    cases = [
        {
            "zone": "算法",
            "title": "虚构样例",
            "language": "Python",
            "code": "pass",
            "thinking": "随便写写",
            "mistake": "边界漏了等号",
            "correct_code": "def ok():\n    pass\n",
        },
        {"zone": "高等数学", "title": "无正确代码", "code": "x", "mistake": ""},
    ]
    out = io.StringIO()
    with redirect_stdout(out):
        run_eval.main(["--cases", _write_cases(tmp_path, cases), "--dry-run"])
    text = out.getvalue()
    # 两条都打印了提示词：system 指令 + 参考资料。
    assert text.count("===== case") == 2
    assert "【讲解】" in text
    assert "correct_code" in text
    assert "original_work" in text


def test_dry_run_includes_truncated_correct_code(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("dry-run 不得联网")

    monkeypatch.setattr(run_eval.ai, "OpenAI", no_network)
    cases = [{"zone": "算法", "code": "x", "correct_code": "y" * 5000}]
    out = io.StringIO()
    with redirect_stdout(out):
        run_eval.main(["--cases", _write_cases(tmp_path, cases), "--dry-run"])
    text = out.getvalue()
    assert "y" * 3000 in text
    assert "y" * 3001 not in text


def test_missing_cases_argument_errors():
    with pytest.raises(SystemExit):
        run_eval.main([])
