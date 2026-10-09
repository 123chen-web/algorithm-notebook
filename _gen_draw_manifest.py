"""一次性脚本：生成 static/draw-manifest.json（sw.js 预缓存画板资源用）。
dist 升级后重跑：.venv/Scripts/python.exe _gen_draw_manifest.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
draw_dir = ROOT / "static" / "draw"
urls = []
for p in sorted(draw_dir.rglob("*")):
    if p.is_file():
        rel = p.relative_to(ROOT / "static").as_posix()
        urls.append("/static/" + rel)
# 同源化前置脚本在 dist 之外，随画板资源一起预缓存。
shim = ROOT / "static" / "draw-font-shim.js"
if shim.exists():
    urls.insert(0, "/static/draw-font-shim.js")
out = ROOT / "static" / "draw-manifest.json"
out.write_text(json.dumps({"urls": urls}, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
print("files:", len(urls), "bytes:", out.stat().st_size)
