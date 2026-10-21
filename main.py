"""欧叶OY 后端入口：只做应用装配（创建 app、中间件、挂载路由、启动初始化）。

业务逻辑按领域拆分：
- core/：配置、时间、限流、鉴权
- models/：pydantic 输入/输出模型
- routers/：各领域路由（直接引用所属模块，不再经由 main.<name>）
- 顶层 <domain>.py：各领域服务函数
"""
import logging
import mimetypes
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from db import ROOT, init_db

from core import config

logger = logging.getLogger("algorithm_notebook")



# 头像、拍照识别题目都只接受这几种真实解码出来的格式（不看文件名后缀或
# 请求头，防止伪装成图片的其他文件类型）；上传后统一重新编码，顺带清掉
# 原图可能带的 EXIF 等元数据、绝不直接保存或转发用户上传的原始字节。
# 拍照识别：手机拍的一整页手写解题过程可能有好几 MB，上限比头像更宽松；
# 识别前会重新编码压缩，不会把原始大图直接传给 AI。

# 简单校验即可：真正确认邮箱能收到信，靠的是密码找回时能不能收到邮件，
# 而不是注册时的格式检查，所以没有引入额外的邮箱校验依赖。


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


# 字体文件类型显式登记：不依赖系统 MIME 表（Windows 注册表、精简镜像里可能没有 woff2）。
mimetypes.add_type("font/woff2", ".woff2")

app = FastAPI(
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


class ApiBodySizeLimit:
    """按“实际收到的字节”限制 /api/ 请求体，补上 Content-Length 检查的缺口。

    下面 request_protection 只看请求头声明的 Content-Length；分块传输
    （Transfer-Encoding: chunked）没有这个头，multipart 解析会先把整个上传
    写进临时文件，然后才轮到登录校验——未登录的人也能用一个超大上传写满磁盘。
    这里包一层 receive，累计超过 API_MAX_BODY_BYTES 立刻抛 413，解析随之中止。
    （import_wizard 的 _bounded_request 是同一思路的单接口版本。）
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > config.API_MAX_BODY_BYTES:
                    raise HTTPException(413, "请求内容太大")
            return message

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except HTTPException as exc:
            # 正常情况下 FastAPI 自己会把 413 转成响应；这里兜住在路由之外
            # （例如中间件层）读取请求体时抛出的情况。
            if exc.status_code != 413 or started:
                raise
            response = JSONResponse(status_code=413, content={"detail": exc.detail})
            await response(scope, receive, send)


app.add_middleware(ApiBodySizeLimit)

# 画板静态页 CSP：以全站策略为基线，仅把 frame-ancestors 从 'none' 放宽到 'self'
# （允许同源笔记页以 iframe 嵌入）；其余逐项放宽均来自浏览器实测，清单与理由见
# NOTES_DRAW_REPORT.md。该策略只作用于 /static/draw/ 路径，全站脚本指令保持不变。
def _draw_inline_csp_hashes():
    """计算 static/draw/draw.html 内联 <script>/<style> 块的 CSP sha256 白名单。

    自托管 Excalidraw 的 draw.html 含两段厂商静态内联内容：一段必须先于 bundle
    执行的配置脚本（设置 window.EXCALIDRAW_ASSET_PATH 指向本地字体目录，否则字体会
    回退到外链 esm.sh），一段全屏布局样式。这里按文件内容计算 hash 精确放行这两段
    静态内容——不使用 unsafe-inline、不放宽任何其它内联脚本，且仅作用于 /static/draw/。
    dist 升级导致内联块变化时 hash 自动跟随，无需手改。"""
    import base64 as _b64
    import hashlib as _hl
    import re as _re

    result = {"script": [], "style": []}
    draw_html = ROOT / "static" / "draw" / "draw.html"
    if not draw_html.exists():
        return result
    html = draw_html.read_text(encoding="utf-8")

    def _digest(text):
        raw = _hl.sha256(text.encode("utf-8")).digest()
        return "'sha256-" + _b64.b64encode(raw).decode("ascii") + "'"

    for match in _re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, _re.S | _re.I):
        if match.group(1).strip():
            result["script"].append(_digest(match.group(1)))
    for match in _re.finditer(r"<style[^>]*>(.*?)</style>", html, _re.S | _re.I):
        if match.group(1).strip():
            result["style"].append(_digest(match.group(1)))
    return result


_DRAW_INLINE_HASHES = _draw_inline_csp_hashes()
DRAW_CSP = (
    "default-src 'self'; "
    "script-src 'self' " + " ".join(_DRAW_INLINE_HASHES["script"]) + "; "
    "style-src 'self' " + " ".join(_DRAW_INLINE_HASHES["style"]) + "; "
    # img-src 多放行 data:：Excalidraw 用 data: 图片画插入的图片和自己的图标，
    # 不放行时画板里插入的图片一律显示成坏图（只放宽画板这一个路径，脚本指令不变）。
    "connect-src 'self'; img-src 'self' blob: data:; "
    "base-uri 'none'; frame-ancestors 'self'; form-action 'self'"
)


@app.middleware("http")
async def request_protection(request, call_next):
    # 前端与 API 同源，且本应用不启用 CORS。
    # 跨站表单不能携带这个自定义请求头。
    # 支付宝/微信支付服务器的通知不携带浏览器请求头，由渠道各自的验签保护。
    provider_notification = request.method == "POST" and request.url.path in (
        "/api/payments/alipay/callback",
        "/api/payments/wechat/callback",
    )
    if (
        request.url.path.startswith("/api/")
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.headers.get("X-CSRF-Protection") != "1"
        and not provider_notification
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "请求缺少必要的安全校验"},
        )

    if request.url.path.startswith("/api/"):
        declared = request.headers.get("content-length", "")
        # 全局上限留足照片识别（8 MiB + multipart 开销）的余量；头像等各自的上限仍在接口内。
        if declared.isdigit() and int(declared) > config.API_MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "请求内容太大"},
            )

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/static/draw/"):
        # 画板静态页（自托管 Excalidraw）：全站唯一允许被同源 iframe 嵌入的路径。
        # 仅对该路径把 frame-ancestors 放宽为 self，并按实测逐项追加画板自身
        # 必需的指令（放宽清单与理由见 NOTES_DRAW_REPORT.md）；脚本指令不放宽。
        response.headers["Content-Security-Policy"] = DRAW_CSP
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' blob:; frame-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
    manual_qr_response = (
        request.method == "GET" and response.status_code == 200
        and request.url.path in (
            "/api/manual-payment/qr/alipay", "/api/manual-payment/qr/wechat"
        )
    )
    # 笔记附件图片、画板缩略图是同源 GET、仅本人可读，路由已给 private 缓存策略，
    # 这里不再覆盖成 no-store；其余 /api/ 响应一律 no-store。
    note_attachment_response = (
        request.method == "GET" and response.status_code == 200
        and re.fullmatch(r"/api/notes/attachments/\d+", request.url.path) is not None
    )
    drawing_thumb_response = (
        request.method == "GET" and response.status_code == 200
        and request.url.path.startswith("/api/drawings/")
        and request.url.path.endswith("/thumb")
    )
    if (
        (
            request.url.path.startswith("/api/")
            and not manual_qr_response
            and not note_attachment_response
            and not drawing_thumb_response
        )
        or request.url.path in ("/", "/static/index.html")
    ):
        response.headers["Cache-Control"] = "no-store"
    return response


# ==================== 微信提醒（Server酱 / PushPlus） ====================


# ---- 榜单页：昨日之星、本周热门题目、今日一条（逻辑在 rank_board / hot_problems / rank_notice） ----


# 统一大写，省去容易混淆的 0/O、1/I/L。

# 学习小组新功能（任务 D）的限额与口径常量。


# 筛选条件只来自这张白名单（值是写死的 SQL 片段，用户输入只作为绑定参数）。
# 每个帖子的统计在一次分组查询里取齐，列表翻页和各项统计共用。


@app.exception_handler(RequestValidationError)
async def forum_action_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/me/bio" and request.method == "PUT":
        # Validation errors must not echo malformed Unicode input into UTF-8 JSON.
        return JSONResponse(
            status_code=422, content={"detail": "简介内容无效，请填写最多 200 字的文字"},
        )
    if re.fullmatch(
        r"/api/posts/[^/]+/(?:accepted|summary)|/api/comments/[^/]+/helpful"
        r"|/api/mistakes/[^/]+/(?:preview|review(?:/undo)?|snooze|suspend|unsuspend|run)"
        r"|/api/me/review-settings|/api/review/queue|/api/import/confirm"
        r"|/api/admin/manual-claims/[^/]+/confirm",
        request.url.path,
    ):
        return JSONResponse(
            status_code=422, content={"detail": "请求参数不正确，请检查后重试"},
        )
    return await request_validation_exception_handler(request, exc)


# ===== [generated] routers 挂载（main.py 拆分脚本生成，请勿手改） =====
# 路由 handlers 已按领域拆分到 routers/；此处按原有相对顺序挂载。
# 共享依赖保留在 main 命名空间，routers 内以 main.xxx 引用，
# 以保证测试中的 monkeypatch.setattr(main, ...) 继续生效。
import routers.pages
import routers.auth
import routers.account
import routers.payments
import routers.stats
import routers.problems
import routers.mistakes
import routers.review
import routers.briefing
import routers.rank
import routers.groups
import routers.group_extras
import routers.forum
import routers.admin
import routers.recommend
import routers.note_history
import routers.note_connections
import routers.notes
import routers.note_files
import routers.drawings
import routers.import_problem
import routers.similar
import routers.reminder
import routers.mastery
import routers.plugin_api
import routers.profile
import routers.import_wizard
import routers.roadmap

app.include_router(routers.pages.router)
app.include_router(routers.auth.router)
app.include_router(routers.account.router)
app.include_router(routers.payments.router)
app.include_router(routers.stats.router)
app.include_router(routers.problems.router)
app.include_router(routers.mistakes.router)
app.include_router(routers.review.router)
app.include_router(routers.briefing.router)
app.include_router(routers.rank.router)
app.include_router(routers.groups.router)
app.include_router(routers.group_extras.router)
app.include_router(routers.forum.router)
app.include_router(routers.admin.router)
app.include_router(routers.recommend.router)
# 回收站 /api/notes/trash 必须排在 notes 的 /api/notes/{note_id} 前面，否则会被当成 id 解析。
app.include_router(routers.note_history.router)
app.include_router(routers.note_connections.router)
app.include_router(routers.notes.router)
app.include_router(routers.note_files.router)
app.include_router(routers.drawings.router)
app.include_router(routers.import_problem.router)
app.include_router(routers.similar.router)
app.include_router(routers.reminder.router)
app.include_router(routers.mastery.router)
app.include_router(routers.plugin_api.router)
app.include_router(routers.profile.router)
app.include_router(routers.import_wizard.router)


# Optional isolated code execution is registered outside the generated block.
import routers.code_run
app.include_router(routers.code_run.router)
app.include_router(routers.roadmap.router)
