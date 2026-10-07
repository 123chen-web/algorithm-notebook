"""Publish one official practice link into the existing '今日一条' slot."""
import argparse
from datetime import datetime, timezone
import sqlite3
import unicodedata

import cf_problems
from db import connect, schema_version
import rank_board
import rank_notice


def publish(conn, problems, today):
    """Called inside a write transaction; preserve manual content and disabled rows."""
    day = today.isoformat()
    if conn.execute("SELECT 1 FROM daily_notices WHERE start_date <= ? AND end_date >= ? LIMIT 1",
                    (day, day)).fetchone():
        return "preserved"
    eligible = sorted((p for p in problems if p.get("rating") is not None and 800 <= p["rating"] <= 1200),
                      key=lambda p: (p["contestId"], p["index"]))
    if not eligible:
        return "no_candidates"
    problem = eligible[today.toordinal() % len(eligible)]
    prefix = f"今日练习：CF {problem['contestId']}{problem['index']} · "
    safe_name = "".join(c for c in problem["name"] if unicodedata.category(c) not in ("Cc", "Cf", "Cs"))
    text = (prefix + " ".join(safe_name.split()))[:rank_notice.NOTICE_MAX_CHARS]
    data = rank_notice.NoticeInput(text=text, link=f"https://codeforces.com/problemset/problem/{problem['contestId']}/{problem['index']}",
                                   start_date=today, end_date=today, is_active=True)
    # NULL author denotes an automated job; no user is impersonated.
    rank_notice.create_notice(conn, data, None, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return "created"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without reading or writing storage")
    args = parser.parse_args(argv)
    if args.dry_run:
        print("Publish one cached official practice link; preserve admin content. no AI, email, push, network, or storage access.")
        return 0
    try:
        payload = cf_problems.load_cache()
        if payload is None:
            print("Official problem cache is unavailable; no notice was written.")
            return 1
        with connect(write=True, create=False) as conn:
            if schema_version(conn) < 9:
                print("Daily notices require an existing upgraded database; no notice was written.")
                return 2
            result = publish(conn, payload["problems"], rank_board.beijing_today())
        print("Daily notice:", result)
        return 1 if result == "no_candidates" else 0
    except (OSError, sqlite3.Error, ValueError):
        print("Daily notice failed; no change was committed. Check the existing database and cache.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
