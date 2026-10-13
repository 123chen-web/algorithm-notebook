"""静态资源测试辅助：app 源码拼接与 index.html 版本读取。

C2：app.js 按领域拆分到 static/app/<领域>/ 后，读源码做字符串断言的测试
改用 app_source()（所有 app 文件按 <script> 顺序拼接），尽量不改断言本身。
C3：写死的 ?v=NN 改用 asset_version() 从 index.html 读当前版本，只断言关系。
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
INDEX_HTML = STATIC / "index.html"


def app_js_files():
    """按 index.html 里 <script defer src=...> 的出现顺序返回 app 相关文件。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    files = []
    for m in re.finditer(r'<script[^>]+src="(/static/[^"]+)"', html):
        src = m.group(1).split("?")[0]  # 去掉 ?v= 版本号
        if src.startswith("/static/app"):
            # /static/app.js 或 /static/app/<领域>.js
            path = STATIC / src[len("/static/"):]
            if path.exists():
                files.append(path)
    return files


def app_source():
    """返回拆分后所有 app 文件按加载顺序拼起来的文本。

    拆分前：只有 static/app.js，返回它的内容。
    拆分后：static/app.js（启动文件）+ static/app/<领域>/*.js 按 index.html 顺序。
    """
    files = app_js_files()
    if not files:
        # 兜底：拆分前只有 app.js
        fallback = STATIC / "app.js"
        if fallback.exists():
            return fallback.read_text(encoding="utf-8")
        return ""
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def asset_version(index_html, name):
    """从 index.html 读出静态资源的 ?v= 版本号（int）。"""
    match = re.search(
        rf'(?:href|src)="/static/{re.escape(name)}\?v=(\d+)"', index_html
    )
    assert match, f"index.html 没有引用带版本号的 {name}"
    return int(match.group(1))


def script_srcs(index_html):
    """按顺序返回 index.html 里所有 <script src=...> 的 src。"""
    return re.findall(r'<script[^>]+src="([^"]+)"', index_html)


def versioned_src(name):
    """返回 /static/<name>?v=<当前版本>，用于构造期望的 script/link href。

    从 index.html 读当前版本号，不再写死数字。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    return f"/static/{name}?v={asset_version(html, name)}"
