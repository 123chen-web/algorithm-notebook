"""pages routes (split out of main.py; behavior unchanged).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main

from db import ROOT
from db import schema_version
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from legal import render_legal_page
from fastapi import APIRouter


router = APIRouter()


@router.get("/healthz")
def healthz():
    headers = {"Cache-Control": "no-store"}
    try:
        with main.connect(create=False) as conn:
            conn.execute("SELECT 1").fetchone()
            version = schema_version(conn)
        with main.connect(write=True, create=False):
            pass
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"}, headers=headers)
    return JSONResponse(content={"status": "ok", "schema_version": version}, headers=headers)


@router.get("/")
def home():
    # 入口文档不能被浏览器无条件缓存：它引用的 CSS/JS 靠 ?v= 查询参数
    # 手动失效，但前提是浏览器每次都真的重新请求这份 HTML 去看新的
    # ?v= 号。no-cache 允许缓存副本，但强制每次先用 ETag 向服务端验证，
    # 没变就是很快的 304，变了才重新下载，不会让用户长期卡在旧版本。
    return FileResponse(
        ROOT / "static" / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/sw.js")
def service_worker():
    # Service Worker 脚本放在站点根路径才能控制整站（/static/sw.js 只能控制 /static/）。
    # no-cache：浏览器每次先验证，新版本发布后不会被旧缓存卡住；CSP 不需要放宽。
    return FileResponse(
        ROOT / "static" / "sw.js",
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@router.get("/manifest.webmanifest")
def web_manifest():
    return FileResponse(
        ROOT / "static" / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/terms", response_class=HTMLResponse)
def terms():
    return HTMLResponse(
        render_legal_page("terms"), headers={"Cache-Control": "public, max-age=300"}
    )


@router.get("/privacy", response_class=HTMLResponse)
def privacy():
    return HTMLResponse(
        render_legal_page("privacy"), headers={"Cache-Control": "public, max-age=300"}
    )
