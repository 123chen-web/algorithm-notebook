"""用现有学习指标和徽章文案渲染分享卡片，不查询数据库或读取系统时间。"""

from datetime import date
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from learning_stats import LearningMetrics


CARD_SIZE = (1080, 1350)
# 与 static/style.css 的 :root 配色保持一致。
PAPER = "#faf9f5"
INK = "#1f1e1c"
ACCENT = "#bc5b3a"
MUTED = "#7a766d"
SOFT = "#f4ece2"

# 这些路径仅适用于 Windows；部署 Linux 时需换成当地中文字体路径或仓库自带字体文件。
FONT_PATHS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
)


def _load_font(size):
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    raise RuntimeError(
        "无法生成分享卡片：未能加载中文字体，请安装微软雅黑、黑体或宋体。"
        "已尝试：" + "、".join(FONT_PATHS)
    )


def _fit_font(font, text, size, width):
    """按可用宽度缩小字体，保留长用户名和大数字的完整内容。"""
    fitted = font.font_variant(size=size)
    while fitted.getlength(text) > width and size > 1:
        size -= 1
        fitted = font.font_variant(size=size)
    return fitted


def render_achievement_card(
    username: str, metrics: LearningMetrics, achievements: list[dict], today: date,
) -> bytes:
    """today 由调用方按用户时区传入；同一组输入生成相同 PNG 字节。"""
    font = _load_font(40)
    card = Image.new("RGB", CARD_SIZE, PAPER)
    draw = ImageDraw.Draw(card)

    def text(x, y, value, size, color=INK, width=880, anchor="lt"):
        draw.text(
            (x, y), value, font=_fit_font(font, value, size, width),
            fill=color, anchor=anchor,
        )

    draw.rounded_rectangle((40, 40, 1040, 1310), radius=32, outline=SOFT, width=3)
    text(100, 106, "算法错题本", 52, ACCENT)
    text(100, 180, "把错误变成掌握", 30, MUTED)
    draw.line((100, 250, 980, 250), fill=SOFT, width=3)

    # 用户名允许内部空白；归并换行以保持战报标题为一行。
    display_name = " ".join(username.split())
    text(100, 295, f"{display_name} 的学习战报", 42)

    streak = metrics["current_streak_days"]
    text(540, 416, str(streak), 210, ACCENT, anchor="mt")
    text(540, 654, f"连续打卡 {streak} 天", 40, ACCENT, anchor="mt")

    unlocked_count = sum(badge["unlocked"] for badge in achievements)
    stats = (
        (f"{unlocked_count} / {len(achievements)}", "已解锁徽章"),
        (f"{metrics['mistake_count']} 条", "累计易错点"),
        (f"{metrics['recorded_zone_count']} 个", "涉及分区"),
        (f"{metrics['generated_practice_count']} 道", "累计生成练习"),
    )
    draw.rounded_rectangle((80, 756, 1000, 932), radius=24, fill=SOFT)
    for index, (value, label) in enumerate(stats):
        center = 195 + index * 230
        text(center, 795, value, 44, ACCENT, width=202, anchor="mt")
        text(center, 866, label, 26, MUTED, width=202, anchor="mt")

    # min 在 remaining 并列时保留已有徽章顺序，使新账号先看到「第一份收获」。
    next_badge = min(
        (badge for badge in achievements if not badge["unlocked"]),
        key=lambda badge: badge["progress"]["remaining"], default=None,
    )
    if next_badge is None:
        text(100, 1032, f"已点亮全部 {len(achievements)} 枚徽章!", 42, ACCENT)
    else:
        text(100, 996, "下一个目标", 27, MUTED)
        text(100, 1043, next_badge["name"], 40, ACCENT)
        text(100, 1107, next_badge["progress"]["message"], 30)

    draw.line((100, 1210, 980, 1210), fill=SOFT, width=3)
    text(100, 1246, today.isoformat(), 24, MUTED, width=400)
    text(980, 1246, "算法错题本", 24, MUTED, width=400, anchor="rt")

    output = BytesIO()
    card.save(output, format="PNG")
    return output.getvalue()
