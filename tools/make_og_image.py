"""一次性生成分享预览图 static/og-image.png（1200x630）。

用法：python tools/make_og_image.py
字体沿用 share_card.py 的加载逻辑（仓库自带 Noto Sans SC 子集优先）。
改了文案后重新运行并提交生成的 PNG。
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import share_card  # noqa: E402

W, H = 1200, 630
BG = "#f4f6fa"
INK = "#1f2a44"
MUTED = "#5a6580"
ACCENT = "#c23a2b"
BLUE = "#3b6fd4"
TEAL = "#2aa198"


def main():
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # 左侧渐变色条：蓝到青，和“清爽”主题保持一致
    for y in range(H):
        t = y / (H - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip((0x3B, 0x6F, 0xD4), (0x2A, 0xA1, 0x98)))
        d.line([(0, y), (24, y)], fill=c)
    # 白色圆角卡片
    d.rounded_rectangle([90, 80, W - 90, H - 80], radius=32, fill="#ffffff", outline="#e1e6ef", width=2)

    name_font = share_card._load_font(150)
    tag_font = share_card._load_font(46)
    sub_font = share_card._load_font(30)

    d.text((150, 105), "欧叶", font=name_font, fill=INK)
    x = 150 + name_font.getlength("欧叶")
    d.text((x + 6, 105), "OY", font=name_font, fill=ACCENT)
    d.line([(155, 325), (335, 325)], fill=BLUE, width=6)
    d.text((150, 360), "把每一次出错，变成下一次的把握", font=tag_font, fill=INK)
    d.text((150, 440), "记下错题 · 到期重做 · 看见自己变强", font=sub_font, fill=MUTED)
    d.text((150, 485), "ouyeoy.com", font=sub_font, fill=TEAL)

    out = ROOT / "static" / "og-image.png"
    img.save(out, optimize=True)
    print(out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
