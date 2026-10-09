"""Dashboard accessibility and the existing API data used by its counters."""

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest

from db import connect
from test_app import client, new_problem, register


class IndexDocument(HTMLParser):
    """Keep element ancestry and text without adding a browser dependency."""

    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        node = {
            "tag": tag,
            "attrs": dict(attrs),
            "ancestors": tuple(self.stack),
            "text": "",
        }
        self.elements.append(node)
        if tag not in self.VOID_TAGS:
            self.stack.append(len(self.elements) - 1)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[position]]["tag"] == tag:
                del self.stack[position:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        for index in self.stack:
            self.elements[index]["text"] += data

    def by_id(self, element_id):
        matches = [
            node for node in self.elements
            if node["attrs"].get("id") == element_id
        ]
        assert len(matches) == 1, f"Expected one #{element_id}, got {len(matches)}"
        return matches[0]

    def within(self, parent_id):
        parent = self.by_id(parent_id)
        parent_index = next(
            index for index, node in enumerate(self.elements) if node is parent
        )
        return [
            node for node in self.elements if parent_index in node["ancestors"]
        ]


@pytest.fixture(scope="module")
def index_document():
    document = IndexDocument()
    path = Path(__file__).resolve().parents[1] / "static" / "index.html"
    document.feed(path.read_text(encoding="utf-8"))
    document.close()
    return document


def test_dashboard_markup_has_unique_ids_and_starts_without_private_metrics(index_document):
    identifiers = [
        node["attrs"]["id"] for node in index_document.elements
        if "id" in node["attrs"]
    ]
    assert [name for name, count in Counter(identifiers).items() if count > 1] == []

    for element_id in ("app", "home-page", "home-quota", "home-due-count"):
        assert "hidden" in index_document.by_id(element_id)["attrs"]
    assert not index_document.by_id("home-due-count")["text"].strip()
    assert not index_document.by_id("home-quota-text")["text"].strip()
    home = index_document.by_id("home-page")
    heading = index_document.by_id(home["attrs"]["aria-labelledby"])
    assert heading["text"].strip()


def test_sidebar_navigation_preserves_destinations_as_named_native_buttons(index_document):
    entries = [
        node for node in index_document.within("app-sidebar")
        if node["tag"] == "button" and "data-view" in node["attrs"]
    ]
    expected = {
        "home", "today", "all", "print", "new", "insights", "mastery", "clusters", "achievements", "weekly-recap",
        "groups", "forum", "notes", "heatmap", "leaderboard", "plan", "admin",
    }
    assert Counter(entry["attrs"]["data-view"] for entry in entries) == Counter(expected)
    for entry in entries:
        assert entry["attrs"].get("type") == "button"
        assert entry["text"].strip()
        assert "onclick" not in entry["attrs"]
        entry_index = next(
            index for index, node in enumerate(index_document.elements) if node is entry
        )
        children = [
            node for node in index_document.elements if entry_index in node["ancestors"]
        ]
        assert not any(node["tag"] in {"button", "a", "input", "select"} for node in children)
        icons = [node for node in children if node["tag"] == "svg"]
        assert icons and all(icon["attrs"].get("aria-hidden") == "true" for icon in icons)


def test_tab_bar_and_more_sheet_together_reach_every_sidebar_destination(index_document):
    def views(container):
        return {
            node["attrs"]["data-view"] for node in index_document.within(container)
            if node["tag"] == "button" and "data-view" in node["attrs"]
        }

    sidebar = views("app-sidebar")
    tabs = views("app-tabbar")
    sheet = views("more-sheet")
    assert tabs == {"today", "all", "new", "groups"}
    assert "clusters" in sheet
    assert tabs | sheet == sidebar
    assert not tabs & sheet, "an entry should live in exactly one of the tab bar and the sheet"
    for container in ("app-tabbar", "more-sheet"):
        for node in index_document.within(container):
            if node["tag"] == "button":
                assert node["attrs"].get("type") == "button"
                assert node["text"].strip() or node["attrs"].get("aria-label")


def test_shell_has_home_navigation_and_hides_admin_until_authorized(index_document):
    sidebar_nav = [
        node for node in index_document.within("app-sidebar")
        if node["tag"] == "nav"
    ]
    assert [node["attrs"].get("aria-label") for node in sidebar_nav] == ["主导航"]
    home_buttons = [
        node for node in index_document.within("app-sidebar")
        if node["attrs"].get("data-view") == "home"
    ]
    assert len(home_buttons) == 1
    assert home_buttons[0]["tag"] == "button"
    assert home_buttons[0]["attrs"].get("type") == "button"
    assert home_buttons[0]["text"].strip() == "总览"
    for element_id in ("admin-tab", "more-admin"):
        admin = index_document.by_id(element_id)
        assert admin["tag"] == "button"
        assert admin["attrs"].get("data-view") == "admin"
        assert "hidden" in admin["attrs"]
        assert "data-admin-only" in admin["attrs"]


def test_overview_primary_actions_are_native_buttons(index_document):
    for element_id, destination in (
        ("tile-review-start", "today"), ("ov-due-all", "today"), ("ov-weakness", "insights"),
    ):
        node = index_document.by_id(element_id)
        assert node["tag"] == "button"
        assert node["attrs"].get("type") == "button"
        assert node["attrs"].get("data-view") == destination
    focus = index_document.by_id("tile-review-focus")
    assert focus["tag"] == "button" and "hidden" in focus["attrs"]
    # 打开具体帖子/记录的入口自己处理点击，不能再带 data-view 被通用导航抢先处理。
    assert "data-view" not in index_document.by_id("ov-hot")["attrs"]
    assert "data-view" not in index_document.by_id("tile-review-focus")["attrs"]


def test_dashboard_quota_uses_labeled_native_progress(index_document):
    progress = index_document.by_id("home-quota-progress")
    assert progress["tag"] == "progress"
    labels = [
        node for node in index_document.elements
        if node["tag"] == "label"
        and node["attrs"].get("for") == "home-quota-progress"
    ]
    assert labels and all(label["text"].strip() for label in labels)
    descriptions = progress["attrs"].get("aria-describedby", "").split()
    assert descriptions
    for element_id in descriptions:
        index_document.by_id(element_id)


@pytest.mark.parametrize(
    "is_trial,trial_limit,used,expected_limit,expected_remaining",
    [
        pytest.param(False, 1, 0, 2, 2, id="unused"),
        pytest.param(False, 1, 1, 2, 1, id="partially-used"),
        pytest.param(False, 1, 2, 2, 0, id="exhausted"),
        pytest.param(False, 1, 4, 2, 0, id="usage-exceeds-current-limit"),
        pytest.param(True, 0, 0, 0, 0, id="zero-trial-limit"),
        pytest.param(True, 3, 1, 3, 2, id="trial-limit"),
    ],
)
def test_dashboard_me_contract_reports_actual_daily_quota(
    client, monkeypatch, is_trial, trial_limit, used, expected_limit, expected_remaining,
):
    account = register(client)
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", str(trial_limit))
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_trial = ? WHERE id = ?", (is_trial, account["id"]))
        conn.executemany(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, ?)",
            [
                (account["id"], "2026-09-18", 100),
                (account["id"], "2026-09-19", used),
            ],
        )

    response = client.get("/api/me")
    assert response.status_code == 200
    me = response.json()
    assert me["username"] == account["username"]
    assert me["today"] == "2026-09-19"
    assert me["ai_daily_limit"] == expected_limit
    assert me["ai_daily_used"] == used
    assert me["ai_daily_remaining"] == expected_remaining


def test_dashboard_due_contract_counts_all_zones_and_overdue_but_not_other_users(client):
    register(client)
    algorithm_ids = new_problem(client, zone="算法")
    frontend_ids = new_problem(client, zone="前端")
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET due_date = ? WHERE id = ?",
            ("2026-09-01", algorithm_ids[0]),
        )
        conn.execute(
            "UPDATE mistakes SET due_date = ? WHERE id = ?",
            ("2026-09-20", frontend_ids[0]),
        )

    client.post("/api/auth/logout")
    register(client, "bob")
    new_problem(client)
    client.post("/api/auth/logout")
    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    )
    assert login.status_code == 200

    response = client.get("/api/mistakes", params={"due_only": "true"})
    assert response.status_code == 200
    data = response.json()
    assert data["today"] == "2026-09-19"
    assert {item["id"] for item in data["items"]} == set(algorithm_ids) | {frontend_ids[1]}
    assert len(data["items"]) == 3
    assert {item["zone"] for item in data["items"]} == {"算法", "前端"}


def test_dashboard_due_contract_has_real_zero_for_a_new_account(client):
    register(client)
    response = client.get("/api/mistakes", params={"due_only": "true"})
    assert response.status_code == 200
    assert response.json() == {"today": "2026-09-19", "items": []}
