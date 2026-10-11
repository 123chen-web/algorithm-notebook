"""变体题 correct_code 透传测试：AI 相关一律 mock，不真调模型。"""
import json

import ai

RESPONSE = (
    "【错因】\n右边界更新漏掉了等号\n\n"
    "【讲解】\n讲解内容\n\n"
    "【核心知识点】\n知识点内容\n\n"
    "【练习题一】\n题目一正文\n\n"
    "【答案一】\n输入：1 2 2\n输出：1\n\n"
    "【练习题二】\n题目二正文\n\n"
    "【答案二】\n输入：a a b\n输出：0"
)


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)
        self.finish_reason = "stop"


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]
        self.model = "gpt-4.1-mini"


class FakeCompletions:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _capture(monkeypatch):
    calls = []
    completions = FakeCompletions(FakeResponse(RESPONSE), calls)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.chat = type("Chat", (), {"completions": completions})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(ai, "OpenAI", FakeClient)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    return calls


def _item(**overrides):
    item = {
        "zone": "算法",
        "title": "二分查找",
        "language": "Python",
        "code": "pass",
        "thinking": "先随便写写",
        "description": "右边界更新漏掉了等号",
    }
    item.update(overrides)
    return item


def _reference_body(request):
    content = request["messages"][1]["content"]
    assert content.startswith("<untrusted_reference>\n")
    assert content.endswith("\n</untrusted_reference>")
    payload = content[len("<untrusted_reference>\n") : -len("\n</untrusted_reference>")]
    return json.loads(payload.replace("\\u003c", "<").replace("\\u003e", ">"))


def test_generate_includes_correct_code_when_present(monkeypatch):
    calls = _capture(monkeypatch)
    result = ai.generate(_item(correct_code="def search(a, t):\n    return -1\n"))
    assert result["questions"]

    request = calls[0]
    body = _reference_body(request)
    assert body["correct_code"] == "def search(a, t):\n    return -1\n"
    # 讲解结构要求在提示词里（纯文本）。
    instructions = request["messages"][0]["content"]
    assert "第 3 行的循环条件" in instructions
    assert "最小例子" in instructions
    assert "30" in instructions


def test_generate_request_body_unchanged_without_correct_code(monkeypatch):
    calls = _capture(monkeypatch)
    result = ai.generate(_item())
    assert result["questions"]

    request = calls[0]
    body = _reference_body(request)
    # 没有正确代码时不带 correct_code 键；reference 只有四个固定键 +
    # 非空才带的 language/mistake——与旧版逐字一致。
    assert "correct_code" not in body
    assert set(body) == {
        "zone",
        "original_title",
        "original_work",
        "original_thinking",
        "language",
        "mistake",
    }


def test_generate_truncates_long_correct_code(monkeypatch):
    calls = _capture(monkeypatch)
    ai.generate(_item(correct_code="x" * 5000))

    body = _reference_body(calls[0])
    assert len(body["correct_code"]) == 3000


def test_generate_parses_seven_sections_with_correct_code(monkeypatch):
    _capture(monkeypatch)
    result = ai.generate(_item(correct_code="def ok():\n    pass\n"))
    # 七段解析仍然通过（generate 只返回错因摘要和两道练习题，其余段落在文本里）。
    assert result["mistake_summary"] == "右边界更新漏掉了等号"
    assert len(result["questions"]) == 2
    assert all(q["question"] and q["answer"] for q in result["questions"])
