"""AI 讲题质量评估小工具（开发用，不进网站）。

用法：
    python tools/ai_eval/run_eval.py --cases tools/ai_eval/cases.sample.json --dry-run
    python tools/ai_eval/run_eval.py --cases my_cases.json   # 真调用，需 OPENAI_API_KEY

--dry-run 只打印将要发送的提示词，不联网、不读 key；不带 --dry-run 时才
读环境变量里的 key 做真实调用，把每条的七段输出打印出来方便人眼对比。
"""
import argparse
import json
import os
import sys

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

import ai  # noqa: E402


def build_messages(case):
    """按 ai.generate 的拼装逻辑构造 messages（只构造，不发送）。"""
    mistake = {
        "zone": case["zone"],
        "title": case.get("title", ""),
        "language": case.get("language", ""),
        "code": case.get("code", ""),
        "thinking": case.get("thinking", ""),
        "description": case.get("mistake", ""),
        "correct_code": case.get("correct_code", ""),
        "mastery_signal": case.get("mastery_signal"),
    }
    guidance = ai.MASTERY_GUIDANCE.get(mistake.get("mastery_signal"))
    instructions = f"{ai.INSTRUCTIONS}\n\n{guidance}" if guidance else ai.INSTRUCTIONS
    reference = {
        "zone": mistake["zone"],
        "original_title": mistake["title"],
        "original_work": mistake["code"],
        "original_thinking": mistake["thinking"],
    }
    if mistake["language"]:
        reference["language"] = mistake["language"]
    if mistake["description"]:
        reference["mistake"] = mistake["description"]
    if mistake.get("correct_code"):
        reference["correct_code"] = str(mistake["correct_code"])[:3000]
    escape_lt = chr(92) + "u003c"
    escape_gt = chr(92) + "u003e"
    reference_json = (
        json.dumps(reference, ensure_ascii=False)
        .replace("<", escape_lt)
        .replace(">", escape_gt)
    )
    messages = [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": f"<untrusted_reference>\n{reference_json}\n</untrusted_reference>",
        },
        {"role": "system", "content": ai.BOUNDARY_REMINDER},
    ]
    return messages, mistake


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI 讲题质量评估（开发用）")
    parser.add_argument("--cases", required=True, help="cases JSON 文件路径")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印提示词，不联网调用"
    )
    args = parser.parse_args(argv)
    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)
    for index, case in enumerate(cases, 1):
        messages, mistake = build_messages(case)
        print(f"===== case {index}: {case.get('title', '')} =====")
        if args.dry_run:
            for message in messages:
                print(f"--- {message['role']} ---")
                print(message["content"])
        else:
            result = ai.generate(mistake)
            print("【错因】\n" + result["mistake_summary"])
            for i, question in enumerate(result["questions"], 1):
                print(f"【练习题{i}】\n{question['question']}")
                print(f"【答案{i}】\n{question['answer']}")
            print(f"(model: {result['model']})")
        print()


if __name__ == "__main__":
    main()
