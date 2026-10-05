"""Generate the PWA home-screen icons (红底白字「错」印章风，与 static/favicon.svg 同源).

usage: python assets/make_pwa_icons.py   (needs `pip install Pillow`; reads the bundled subset font)
Writes static/icons/icon-{192,512}.png (purpose "any", 圆角纸面章)
and static/icons/icon-maskable-{192,512}.png (purpose "maskable", 满出血红底，字在安全区内).

颜色取站点主题令牌的实际值（见 static/style.css）：
  --accent #c23a2b（章面红）、--on-accent #fff（章面字）、--paper #f5f0e6（衬底）。
字体沿用仓库自带的 assets/fonts/NotoSansSC-Regular-subset.otf（SIL OFL 1.1，见 OFL.txt）。
"""
import pathlib

from PIL import Image, ImageDraw, ImageFont

HERE = pathlib.Path(__file__).resolve().parent
FONT_PATH = HERE / "fonts" / "NotoSansSC-Regular-subset.otf"
OUT_DIR = HERE.parent / "static" / "icons"

RED = (194, 58, 43, 255)        # --accent #c23a2b
RED_DARK = (165, 47, 34, 255)   # --accent-hover #a52f22（印章内圈线）
WHITE = (255, 255, 255, 255)    # --on-accent #fff
PAPER = (245, 240, 230, 255)    # --paper #f5f0e6
CHAR = "错"

SIZES = (192, 512)
SAFE_RATIO = 0.8  # maskable 安全区：内容收在中间 80% 内


def draw_seal(size, *, maskable):
    """红底白字「错」：any 版带圆角与纸面细边，maskable 版满出血并把字收进安全区。"""
    scale = 4  # 超采样，抗锯齿
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    content = canvas if not maskable else round(canvas * SAFE_RATIO)
    inset = (canvas - content) // 2

    if maskable:
        draw.rectangle((0, 0, canvas, canvas), fill=RED)
        frame = (inset, inset, inset + content, inset + content)
        radius = 0
    else:
        draw.rectangle((0, 0, canvas, canvas), fill=PAPER)
        margin = round(canvas * 0.04)
        frame = (margin, margin, canvas - margin, canvas - margin)
        radius = round(canvas * 0.20)  # 与 favicon.svg 的 rx≈13/64 同比例
        draw.rounded_rectangle(frame, radius=radius, fill=RED)

    # 印章内圈细线（深色一点的红），强化「章」的观感
    line = max(2, round(canvas * 0.012))
    inner = round(content * 0.055)
    box = (frame[0] + inner, frame[1] + inner, frame[2] - inner, frame[3] - inner)
    if radius:
        draw.rounded_rectangle(box, radius=max(1, radius - inner), outline=RED_DARK, width=line)
    else:
        draw.rectangle(box, outline=RED_DARK, width=line)

    font_size = round(content * 0.58)
    font = ImageFont.truetype(str(FONT_PATH), font_size)
    left, top, right, bottom = font.getbbox(CHAR)
    width, height = right - left, bottom - top
    center_x = (frame[0] + frame[2]) / 2
    center_y = (frame[1] + frame[3]) / 2
    draw.text((center_x - width / 2 - left, center_y - height / 2 - top), CHAR, font=font, fill=WHITE)

    return image.resize((size, size), Image.LANCZOS)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for size in SIZES:
        for maskable in (False, True):
            name = f"icon-maskable-{size}.png" if maskable else f"icon-{size}.png"
            target = OUT_DIR / name
            draw_seal(size, maskable=maskable).save(target, "PNG", optimize=True)
            written.append((name, target.stat().st_size))
    for name, bytes_ in written:
        print(f"wrote static/icons/{name} ({bytes_} bytes)")


if __name__ == "__main__":
    main()
