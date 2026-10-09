from pathlib import Path
import subprocess


def test_problem_cards_node():
    result = subprocess.run(["node", "--test", "tests/problem_cards_behaviour.cjs"],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
