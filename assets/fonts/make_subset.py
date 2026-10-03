"""Rebuild NotoSansSC-Regular-subset.otf from the original Noto Sans CJK SC Regular (see README.md).

usage: python make_subset.py <output.otf>   (needs `pip install fonttools`; the 16 MB original sits next to this file)
Subset = ASCII + Latin-1 + CJK/full-width punctuation + the whole GB2312 set (6763 hanzi + symbols).
"""
import pathlib
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / "NotoSansCJKsc-Regular.otf"
TARGET = pathlib.Path(sys.argv[1])

codepoints = set()
codepoints.update(range(0x20, 0x7F))            # ASCII
codepoints.update(range(0xA0, 0x100))           # Latin-1 supplement
codepoints.update(range(0x2010, 0x2028))        # dashes, quotes, bullets, ellipsis
codepoints.update(range(0x2030, 0x2045))        # per mille, primes, etc.
codepoints.update(range(0x2190, 0x2194))        # arrows
codepoints.update(range(0x3000, 0x3040))        # CJK punctuation
codepoints.update(range(0xFF01, 0xFF5F))        # full-width forms
codepoints.update((0xFFE0, 0xFFE1, 0xFFE5))
gb_count = 0
for lead in list(range(0xA1, 0xAA)) + list(range(0xB0, 0xF8)):
    for trail in range(0xA1, 0xFF):
        try:
            character = bytes((lead, trail)).decode("gb2312")
        except UnicodeDecodeError:
            continue
        codepoints.add(ord(character))
        gb_count += 1
print("code points requested:", len(codepoints), "(GB2312 characters:", gb_count, ")")

options = subset.Options()
options.layout_features = ["kern", "ccmp"]
options.hinting = False
options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]
options.notdef_outline = True
options.glyph_names = False
options.legacy_kern = False

font = TTFont(str(SOURCE))
subsetter = subset.Subsetter(options)
subsetter.populate(unicodes=sorted(codepoints))
subsetter.subset(font)
TARGET.parent.mkdir(parents=True, exist_ok=True)
font.save(str(TARGET))
saved = TTFont(str(TARGET))
cmap = saved.getBestCmap()
print("glyphs in subset:", len(saved.getGlyphOrder()), "| cmap entries:", len(cmap))
print("size:", TARGET.stat().st_size, "bytes")
missing = [chr(c) for c in sorted(codepoints) if c not in cmap]
print("requested but absent from the font:", len(missing), "".join(missing[:40]))
