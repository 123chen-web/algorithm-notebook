"""成就分享卡片：复用只读统计，输出可解码、确定性的中文 PNG。"""

from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

import ai
import main
import share_card
from achievements import evaluate_achievements
from db import connect
from learning_stats import learning_metrics
from share_card import render_achievement_card
from test_app import client, register


ENDPOINT = "/api/achievements/share-card"
TODAY = date(2026, 9, 19)  # 跟 test_app.client 冻结的用户本地日期一致。
EMPTY_METRICS = {
    "current_streak_days": 0,
    "mistake_count": 0,
    "recorded_zone_count": 0,
    "generated_practice_count": 0,
    "has_weakness_analysis": False,
}
FULL_METRICS = {
    "current_streak_days": 100,
    "mistake_count": 100,
    "recorded_zone_count": 5,
    "generated_practice_count": 100,
    "has_weakness_analysis": True,
}
FONT_PATHS = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]
BUNDLED_FONT = (
    Path(share_card.__file__).resolve().parent / "assets" / "fonts"
    / "NotoSansSC-Regular-subset.otf"
)
MISSING_FONT_DETAIL = "分享卡片暂时不可用：服务器缺少中文字体，请联系管理员。"


@pytest.fixture(autouse=True)
def block_real_ai(monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("成就分享卡片只渲染现有统计，不能调用 AI")

    monkeypatch.setattr(ai, "generate", unexpected_call)
    monkeypatch.setattr(ai, "analyze_weaknesses", unexpected_call)
    monkeypatch.setattr(ai, "recognize_photo", unexpected_call)


@pytest.fixture
def drawn_text(monkeypatch):
    texts = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, xy, text, *args, **kwargs):
        texts.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    return texts


def assert_png(content):
    assert isinstance(content, bytes)
    with Image.open(BytesIO(content)) as image:
        assert image.format == "PNG"
        assert image.size == (1080, 1350)
        image.load()  # 同时验证像素数据，不能只有合法文件头。


def card(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert_png(response.content)
    return response.content


def seed_activity(user_id):
    timestamp = TODAY.isoformat() + "T12:00:00+00:00"
    with connect(write=True) as conn:
        problem_id = conn.execute(
            "INSERT INTO problems(user_id, title, zone, language, code, thinking, created_at) "
            "VALUES (?, '二分边界', '算法', 'Python', '', '', ?)",
            (user_id, timestamp),
        ).lastrowid
        mistake_id = conn.execute(
            "INSERT INTO mistakes(problem_id, description, due_date) "
            "VALUES (?, '遗漏边界条件', ?)",
            (problem_id, TODAY.isoformat()),
        ).lastrowid
        conn.execute(
            "INSERT INTO reviews(mistake_id, quality, reviewed_at, next_due_date) "
            "VALUES (?, 4, ?, ?)",
            (mistake_id, timestamp, TODAY.isoformat()),
        )
        conn.executemany(
            "INSERT INTO variants(mistake_id, description, model, created_at) "
            "VALUES (?, '练习题', 'mock-model', ?)",
            [(mistake_id, timestamp)] * 2,
        )


def test_share_card_requires_login(client):
    assert client.get(ENDPOINT).status_code == 401


def test_new_user_gets_png_and_easiest_next_goal(client, drawn_text):
    private_email = "private-share-card@example.com"
    register(client, email=private_email)
    data = client.get("/api/achievements").json()
    assert data["metrics"] == EMPTY_METRICS
    assert not any(badge["unlocked"] for badge in data["achievements"])

    card(client)
    text = "\n".join(drawn_text)
    assert "算法错题本" in text
    assert "把错误变成掌握" in text
    assert "alice 的学习战报" in text
    assert "下一个目标" in text
    assert "第一份收获" in text
    assert "再记录 1 条易错点" in text
    assert private_email not in text


def test_next_goal_uses_smallest_remaining_instead_of_first_locked_badge(drawn_text):
    metrics = {
        **EMPTY_METRICS, "generated_practice_count": 1, "has_weakness_analysis": True,
    }
    badges = evaluate_achievements(metrics)
    # 首个锁定项还差 3 天；易错点和练习各差 1，沿原顺序选择易错点。
    assert_png(render_achievement_card("alice", metrics, badges, TODAY))
    text = "\n".join(drawn_text)
    assert "第一份收获" in text
    assert "再记录 1 条易错点" in text
    assert "三日启程" not in text


def test_all_fourteen_badges_show_congratulations(drawn_text):
    badges = evaluate_achievements(FULL_METRICS)
    assert len(badges) == 14
    assert all(badge["unlocked"] for badge in badges)

    assert_png(render_achievement_card("alice", FULL_METRICS, badges, TODAY))
    text = "\n".join(drawn_text)
    assert "全部 14 枚徽章" in text
    assert "下一个目标" not in text


@pytest.mark.parametrize(
    "metrics", [EMPTY_METRICS, FULL_METRICS], ids=["new", "all-unlocked"],
)
def test_rendering_same_data_and_supplied_date_is_deterministic(metrics, drawn_text):
    badges = evaluate_achievements(metrics)
    first = render_achievement_card("alice", metrics, badges, TODAY)
    assert_png(first)
    assert render_achievement_card("alice", metrics, badges, TODAY) == first
    assert TODAY.isoformat() in "\n".join(drawn_text)


def test_recorded_activity_changes_the_image(client):
    user_id = register(client)["id"]
    before = card(client)
    seed_activity(user_id)
    after = card(client)
    assert after != before
    assert card(client) == after


@pytest.mark.parametrize("is_trial", [False, True], ids=["no-plan", "trial"])
def test_share_card_is_read_only_and_available_without_ai_quota(
    client, monkeypatch, is_trial,
):
    if is_trial:
        response = client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
        assert response.status_code == 201
        user_id = response.json()["id"]
    else:
        user_id = register(client)["id"]
    seed_activity(user_id)
    with connect(write=True) as conn:
        plan_id = conn.execute(
            "SELECT plan_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()[0]
        assert plan_id is None
        conn.execute(
            "INSERT INTO ai_usage(user_id, day, attempts) VALUES (?, ?, 100)",
            (user_id, TODAY.isoformat()),
        )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "0")

    def unexpected_quota(*args, **kwargs):
        pytest.fail("分享卡片不检查 AI 配额或套餐")

    monkeypatch.setattr(main, "ai_quota", unexpected_quota)
    with connect() as conn:
        before = list(conn.iterdump())
    first = card(client)
    assert card(client) == first
    with connect() as conn:
        assert list(conn.iterdump()) == before


def test_endpoint_reuses_metrics_badges_and_one_user_local_date(
    client, monkeypatch, drawn_text,
):
    user_id = register(client)["id"]
    seed_activity(user_id)
    timezone_name = "America/Los_Angeles"
    local_today = TODAY + timedelta(days=1)
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE id = ?", (timezone_name, user_id),
        )
    with connect() as conn:
        expected = learning_metrics(conn, user_id, timezone_name, local_today)
    observed = {}
    date_calls = []

    def frozen_today(user):
        date_calls.append(user["timezone"])
        return local_today

    def capture_metrics(conn, actual_id, actual_timezone, actual_today):
        assert conn.in_transaction
        assert (actual_id, actual_timezone, actual_today) == (
            user_id, timezone_name, local_today,
        )
        observed["metrics"] = learning_metrics(
            conn, actual_id, actual_timezone, actual_today,
        )
        return observed["metrics"]

    def capture_badges(metrics):
        assert metrics is observed["metrics"]
        observed["badges"] = evaluate_achievements(metrics)
        return observed["badges"]

    def capture_render(username, metrics, achievements, today):
        assert username == "alice"
        assert metrics is observed["metrics"]
        assert achievements is observed["badges"]
        assert today == local_today
        return render_achievement_card(username, metrics, achievements, today)

    monkeypatch.setattr(main, "today_for", frozen_today)
    monkeypatch.setattr(main, "learning_metrics", capture_metrics)
    monkeypatch.setattr(main, "evaluate_achievements", capture_badges)
    monkeypatch.setattr(main, "render_achievement_card", capture_render)
    content = card(client)
    assert date_calls == [timezone_name]
    assert observed["metrics"] == expected
    assert local_today.isoformat() in "\n".join(drawn_text)
    assert content == render_achievement_card(
        "alice", expected, evaluate_achievements(expected), local_today,
    )


def test_endpoint_uses_one_explicit_read_snapshot(client, monkeypatch):
    user_id = register(client)["id"]
    inserted = False

    def metrics_with_concurrent_insert(conn, *args):
        assert conn.in_transaction

        class InsertAfterFirstRead:
            def execute(self, sql, parameters=()):
                nonlocal inserted
                cursor = conn.execute(sql, parameters)
                if not inserted and sql.lstrip().upper().startswith("SELECT"):
                    inserted = True
                    # 聚合 SELECT 已建立快照；其他连接写入易错点、复习和练习。
                    seed_activity(user_id)
                return cursor

            def __getattr__(self, name):
                return getattr(conn, name)

        return learning_metrics(InsertAfterFirstRead(), *args)

    monkeypatch.setattr(main, "learning_metrics", metrics_with_concurrent_insert)
    first = card(client)
    assert inserted
    assert first == render_achievement_card(
        "alice", EMPTY_METRICS, evaluate_achievements(EMPTY_METRICS), TODAY,
    )
    assert card(client) != first


def test_missing_yahei_tries_the_next_chinese_font(monkeypatch):
    original_truetype = ImageFont.truetype
    attempted = []
    bundled_path = str(BUNDLED_FONT).replace("\\", "/")

    def unavailable_yahei(font, *args, **kwargs):
        path = str(font).replace("\\", "/")
        attempted.append(path)
        if path in (bundled_path, FONT_PATHS[0]):
            raise OSError("模拟自带字体和微软雅黑不可用")
        # 用自带字体模拟系统备选，测试不依赖 Windows 字体是否安装。
        return original_truetype(BUNDLED_FONT, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", unavailable_yahei)
    content = render_achievement_card(
        "alice", EMPTY_METRICS, evaluate_achievements(EMPTY_METRICS), TODAY,
    )
    assert attempted[:3] == [bundled_path, *FONT_PATHS[:2]]
    assert_png(content)


def test_missing_all_chinese_fonts_raises_clear_error_without_default_font(monkeypatch):
    attempted = []

    def unavailable_font(font, *args, **kwargs):
        attempted.append(str(font).replace("\\", "/"))
        raise OSError("模拟中文字体不可用")

    def unexpected_default(*args, **kwargs):
        pytest.fail("不能退回不支持中文的默认字体")

    monkeypatch.setattr(ImageFont, "truetype", unavailable_font)
    monkeypatch.setattr(ImageFont, "load_default", unexpected_default)
    with pytest.raises(HTTPException) as error:
        render_achievement_card(
            "alice", EMPTY_METRICS, evaluate_achievements(EMPTY_METRICS), TODAY,
        )
    assert error.value.status_code == 503
    assert error.value.detail == MISSING_FONT_DETAIL
    assert attempted == [str(BUNDLED_FONT).replace("\\", "/"), *FONT_PATHS]


def test_bundled_font_is_first_and_independent_of_working_directory(monkeypatch):
    original_truetype = ImageFont.truetype
    attempted = []

    def record_font(font, *args, **kwargs):
        attempted.append(Path(font))
        return original_truetype(font, *args, **kwargs)

    monkeypatch.chdir(Path(__file__).resolve().parent)
    monkeypatch.setattr(ImageFont, "truetype", record_font)
    assert share_card.FONT_PATHS[0] == BUNDLED_FONT
    assert_png(render_achievement_card(
        "alice", EMPTY_METRICS, evaluate_achievements(EMPTY_METRICS), TODAY,
    ))
    assert attempted == [BUNDLED_FONT]


def test_bundled_font_renders_mixed_chinese_and_rare_characters(monkeypatch, drawn_text):
    monkeypatch.setattr(share_card, "FONT_PATHS", (
        BUNDLED_FONT,
        "/missing/windows/msyh.ttc",
        "/missing/windows/simhei.ttf",
        "/missing/windows/simsun.ttc",
    ))
    username = "中文 学习者 Alice_123-é龘𠮷"
    badges = evaluate_achievements(EMPTY_METRICS)
    first = render_achievement_card(username, EMPTY_METRICS, badges, TODAY)
    assert_png(first)
    assert render_achievement_card(username, EMPTY_METRICS, badges, TODAY) == first
    assert f"{username} 的学习战报" in drawn_text


def test_missing_all_fonts_returns_503_from_real_endpoint(client, monkeypatch):
    register(client)
    # 文件名必须是任何系统都不会有的：Pillow 找不到路径时会按文件名去系统字体目录里再找一遍，
    # 用 msyh.ttc 之类真实字体名，在装了微软雅黑的 Windows 上会"假装缺字体"失败。
    monkeypatch.setattr(share_card, "FONT_PATHS", (
        "/missing/no-such-font-1.otf",
        "/missing/no-such-font-2.ttc",
        "/missing/no-such-font-3.ttf",
        "/missing/no-such-font-4.ttc",
    ))
    response = client.get(ENDPOINT)
    assert response.status_code == 503
    assert response.json() == {"detail": MISSING_FONT_DETAIL}


def test_bundled_font_and_license_are_present():
    assert BUNDLED_FONT.is_file()
    assert 500 * 1024 < BUNDLED_FONT.stat().st_size < 5 * 1024 * 1024
    assert (BUNDLED_FONT.parent / "OFL.txt").is_file()
