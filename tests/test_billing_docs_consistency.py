"""Keep public billing copy and example defaults tied to executable defaults."""
import ast
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "static/ai-billing.html"
OLD_COPY = re.compile(
    r"调用失败也(?:占用|消耗|计入)次数|失败也(?:计次|计入次数)|"
    r"服务端不自动重试|失败仍消耗|调用失败或识别不出有效内容也计入次数|"
    r"调用失败不扣次数|AI 服务出错时不扣次数|AI 失败也可能占额"
)


def public_files():
    return sorted({
        *ROOT.glob("README*"),
        *(p for p in (ROOT / "docs").rglob("*") if p.suffix in {".md", ".txt", ".rst", ".html", ".js", ".json"}),
        *(p for p in (ROOT / "static").rglob("*") if p.suffix in {".html", ".js", ".json", ".webmanifest"}),
        *ROOT.rglob(".env*.example"),
    })


def test_public_copy_has_no_obsolete_or_overbroad_billing_claims():
    violations = []
    for path in public_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if OLD_COPY.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert violations == []


def env_default(function, variable):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function)
    calls = [n for n in ast.walk(body) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "getenv"
             and n.args and ast.literal_eval(n.args[0]) == variable]
    assert len(calls) == 1
    return int(ast.literal_eval(calls[0].args[1]))


def code_limits():
    tree = ast.parse((ROOT / "seed_plans.py").read_text(encoding="utf-8"))
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "DEFAULT_PLANS" for t in n.targets))
    plans = ast.literal_eval(assignment.value)
    return {
        "免费账号": env_default("ai_limit", "AI_DAILY_LIMIT"),
        "体验账号": env_default("trial_ai_limit", "TRIAL_AI_DAILY_LIMIT"),
        **{name: limit for name, _, limit, _ in plans},
    }


def test_canonical_defaults_match_code_and_agreed_values():
    text = CANONICAL.read_text(encoding="utf-8")
    documented = {name: int(value) for name, value in re.findall(
        r'<tr><td>(免费账号|体验账号|标准版|进阶版)</td><td>(\d+)</td>', text)}
    assert documented == code_limits() == {"免费账号": 20, "体验账号": 4, "标准版": 50, "进阶版": 120}
    assert documented["体验账号"] <= documented["免费账号"] / 5
    frontend = (ROOT / "static/plan.js").read_text(encoding="utf-8")
    assert re.findall(r"const DEFAULT_FREE_LIMIT = (\d+);", frontend) == [str(documented["免费账号"])]


@pytest.mark.parametrize("relative", [".env.example", "deploy/.env.prod.example"])
def test_example_defaults_match_code(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    limits = code_limits()
    for variable, name in [("AI_DAILY_LIMIT", "免费账号"), ("TRIAL_AI_DAILY_LIMIT", "体验账号")]:
        values = re.findall(rf"^{variable}=(\d+)$", text, re.MULTILINE)
        assert values == [str(limits[name])]


def test_complete_policy_has_one_location_and_links_resolve():
    text = CANONICAL.read_text(encoding="utf-8")
    assert '<h1>AI 额度与计费</h1>' in text
    for requirement in ["体验账号 &gt; 有效套餐 &gt; 免费默认", "原子扣除 1 次",
                        "502/503/504", "422", "模型拒答", "越界拒绝", "没发现可靠规律",
                        "429", "refund_on_server_failure", "call_with_retry",
                        "重试 1 次", "等待 2 秒", "鉴权失败", "请求格式错误", "额度不足",
                        "输出解析失败", "1 个并发名额", "只扣 1 次", "系统级调用", "不扣用户额度"]:
        assert requirement in text, requirement
    for relative in ["README.md", "docs/operations/deploy.md", "docs/operations/daily-digest.md",
                     "docs/integration/duck.md", ".env.example", "deploy/.env.prod.example",
                     "static/index.html", "static/app.js", "static/plan.js", "static/duck-panel.js"]:
        copy = (ROOT / relative).read_text(encoding="utf-8")
        assert "ai-billing.html" in copy, relative
        if relative.endswith(".md"):
            for target in re.findall(r'\]\(([^)]+ai-billing\.html)\)', copy):
                assert ((ROOT / relative).parent / target).resolve() == CANONICAL.resolve()
    for path in [ROOT / "README.md", *(ROOT / "docs").rglob("*.md")]:
        copy = path.read_text(encoding="utf-8")
        assert "refund_on_server_failure" not in copy
        assert "传输层临时错误" not in copy


def test_public_policy_stylesheet_is_versioned_and_csp_safe():
    text = CANONICAL.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/ai-billing.css?v=1">' in text
    assert (ROOT / "static/ai-billing.css").is_file()
    assert not re.search(r"<script\b|\bstyle\s*=|\bon\w+\s*=", text)
