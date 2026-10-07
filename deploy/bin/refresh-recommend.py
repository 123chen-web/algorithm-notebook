#!/usr/bin/env python3
"""Host cron entry point; refresh official metadata inside the existing app container."""
import argparse
from pathlib import Path
import subprocess


def command(repo, script="cf_problems.py"):
    return ["docker", "compose", "--env-file", str(repo / ".env"),
            "-f", str(repo / "deploy" / "docker-compose.prod.yml"),
            "exec", "-T", "app", "python", script] + (["refresh"] if script == "cf_problems.py" else [])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without reading secrets or running Docker")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[2]
    if args.dry_run:
        print("Daily official Codeforces metadata refresh; no AI, email, or push notifications.")
        print("Command arguments:", command(repo))
        print("Then publish 今日一条:", command(repo, "daily_notice.py"))
        return 0
    if not (repo / ".env").is_file() or not (repo / "deploy" / "docker-compose.prod.yml").is_file():
        print("Missing production configuration; set up the existing app before scheduling this task.")
        return 2
    try:
        refresh = subprocess.run(command(repo), cwd=repo, check=False).returncode
        if refresh != 0:
            return refresh
        return subprocess.run(command(repo, "daily_notice.py"), cwd=repo, check=False).returncode
    except OSError:
        print("Could not start Docker Compose; check the host installation and app container.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
