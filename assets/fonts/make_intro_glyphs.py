"""Extract the outlines of 欧叶OY for the intro film's brush-written wordmark (see README.md).

usage: python make_intro_glyphs.py   (needs `pip install fonttools`; reads the bundled subset next to this file)
Writes ../../static/intro-glyphs.js: one SVG path per contour, y flipped so the viewBox reads top-down.
Only path data is published, never the font file. The outlines remain under the SIL OFL 1.1 (OFL.txt).
"""
import json
import pathlib

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / "NotoSansSC-Regular-subset.otf"
TARGET = HERE.parent.parent / "static" / "intro-glyphs.js"
TEXT = "欧叶OY"
LATIN_GAP = 60  # 汉字与拉丁字母之间略留一点空


def number(value):
    return str(round(value))


def point(x, y, offset):
    return f"{number(x + offset)} {number(-y)}"


def contours(recording, offset):
    paths, current = [], []
    for operator, arguments in recording.value:
        if operator == "moveTo":
            current = [f"M{point(*arguments[0], offset)}"]
        elif operator == "lineTo":
            current.append(f"L{point(*arguments[0], offset)}")
        elif operator == "curveTo":
            current.append("C" + " ".join(point(x, y, offset) for x, y in arguments))
        elif operator == "qCurveTo":
            current.append("Q" + " ".join(point(x, y, offset) for x, y in arguments))
        elif operator in ("closePath", "endPath"):
            paths.append("".join(current) + "Z")
            current = []
        else:
            raise ValueError(f"unexpected pen operation {operator}")
    return paths


def main():
    font = TTFont(SOURCE)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    ascent, descent = 880, 120  # Noto Sans CJK 的 1000 单位字身框
    glyphs, cursor = [], 0
    for index, character in enumerate(TEXT):
        if index and character.isascii() and not TEXT[index - 1].isascii():
            cursor += LATIN_GAP
        glyph = glyph_set[cmap[ord(character)]]
        recording = RecordingPen()
        glyph.draw(recording)
        glyphs.append({"char": character, "contours": contours(recording, cursor)})
        cursor += glyph.width
    data = {"text": TEXT, "viewBox": f"0 {-ascent} {cursor} {ascent + descent}", "glyphs": glyphs}
    TARGET.write_text(
        '"use strict";\n\n'
        "/* 开场短片品牌幕“欧叶OY”的字形轮廓。由 assets/fonts/make_intro_glyphs.py 从仓库自带的\n"
        "   NotoSansSC-Regular-subset.otf 生成（SIL Open Font License 1.1，见 assets/fonts/OFL.txt），请勿手改。 */\n"
        f"window.IntroGlyphs = Object.freeze({json.dumps(data, ensure_ascii=False, separators=(',', ':'))});\n",
        encoding="utf-8",
    )
    print(f"wrote {TARGET} ({TARGET.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
