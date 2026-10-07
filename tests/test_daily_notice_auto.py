from datetime import date

import pytest

import daily_notice
import db
import rank_notice
from test_app import client, register

TODAY = date(2026, 10, 7)
PROBLEMS = [{"contestId": 4, "index": "A", "name": "Graph traversal", "rating": 800, "tags": ["graphs"]}]


def test_auto_notice_is_one_per_day_and_links_official_metadata(client):
    with db.connect(write=True) as conn:
        assert daily_notice.publish(conn, PROBLEMS, TODAY) == "created"
        assert daily_notice.publish(conn, PROBLEMS, TODAY) == "preserved"
        assert conn.execute("SELECT COUNT(*) FROM daily_notices").fetchone()[0] == 1
        assert rank_notice.current_notice(conn, TODAY) == {
            "text": "今日练习：CF 4A · Graph traversal",
            "link": "https://codeforces.com/problemset/problem/4/A",
        }


@pytest.mark.parametrize("enabled", [True, False])
def test_admin_content_and_intentional_disable_are_preserved(client, enabled):
    owner = register(client)
    data = rank_notice.NoticeInput(text="管理员今日提醒", start_date=TODAY, end_date=TODAY, is_active=enabled)
    with db.connect(write=True) as conn:
        rank_notice.create_notice(conn, data, owner["id"], "2026-10-07T00:00:00+00:00")
        assert daily_notice.publish(conn, PROBLEMS, TODAY) == "preserved"
        assert conn.execute("SELECT text FROM daily_notices").fetchall()[0][0] == "管理员今日提醒"


def test_missing_candidates_does_not_mark_day_or_prevent_retry(client):
    with db.connect(write=True) as conn:
        assert daily_notice.publish(conn, [], TODAY) == "no_candidates"
        assert conn.execute("SELECT COUNT(*) FROM daily_notices").fetchone()[0] == 0
        assert daily_notice.publish(conn, PROBLEMS, TODAY) == "created"


def test_untrusted_names_are_bounded_and_control_free(client):
    problem = {**PROBLEMS[0], "name": "<script>\n\u202e" + "题" * 200}
    with db.connect(write=True) as conn:
        assert daily_notice.publish(conn, [problem], TODAY) == "created"
        data = rank_notice.current_notice(conn, TODAY)
        assert len(data["text"]) == 80
        assert "\n" not in data["text"] and "\u202e" not in data["text"]
        assert data["link"] == "https://codeforces.com/problemset/problem/4/A"


def test_dry_run_does_not_read_create_files_or_contact_services(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run must not access storage or network")
    monkeypatch.setattr(daily_notice.cf_problems, "load_cache", forbidden)
    monkeypatch.setattr(daily_notice, "connect", forbidden)
    assert daily_notice.main(["--dry-run"]) == 0
    assert "no AI" in capsys.readouterr().out
