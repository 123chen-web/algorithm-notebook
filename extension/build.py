#!/usr/bin/env python3
"""构建欧叶OY 刷题伴侣扩展的发布 zip。

确定性输出：所有条目按路径排序、时间戳固定为 2026-10-10 21:40:00、
权限位统一，保证同一份源码每次构建出的 zip 逐字节一致。

用法：
    python3 extension/build.py            # 构建到 static/downloads/ouye-companion-extension.zip
    python3 extension/build.py --check    # 只校验：构建产物与仓库内提交的 zip 是否逐字节一致
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

TEXT_SUFFIXES = {".js", ".json", ".html", ".css", ".md", ".txt"}

# 固定时间戳（与源码进仓库时的构建产物一致）。
FIXED_DATE_TIME = (2026, 10, 10, 21, 40, 0)

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "extension" / "ouye-extension"
OUT_ZIP = ROOT / "static" / "downloads" / "ouye-companion-extension.zip"


def collect_files() -> list[Path]:
    files = [p for p in SRC_DIR.rglob("*") if p.is_file()]
    # 排序保证确定性；用 posix 风格相对路径排序。
    files.sort(key=lambda p: p.relative_to(SRC_DIR).as_posix())
    return files


def build_zip_bytes() -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in collect_files():
            arcname = "ouye-extension/" + path.relative_to(SRC_DIR).as_posix()
            info = zipfile.ZipInfo(arcname, date_time=FIXED_DATE_TIME)
            # 统一权限：文件 0o644。
            info.external_attr = 0o644 << 16
            # PNG 等二进制按原样读入。
            data = path.read_bytes()
            if path.suffix in TEXT_SUFFIXES:
                # Windows 上签出会把换行变成 CRLF；统一成 LF，保证各平台构建结果逐字节一致。
                data = data.replace(bytes([13, 10]), bytes([10]))
            zf.writestr(info, data)
    return buf.getvalue()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="构建扩展发布 zip（确定性输出）")
    parser.add_argument("--check", action="store_true", help="只校验构建产物与仓库内 zip 是否逐字节一致")
    args = parser.parse_args(argv)

    if not SRC_DIR.is_dir():
        print(f"源码目录不存在：{SRC_DIR}", file=sys.stderr)
        return 2

    built = build_zip_bytes()

    if args.check:
        if not OUT_ZIP.is_file():
            print(f"仓库内 zip 不存在：{OUT_ZIP}", file=sys.stderr)
            return 2
        # 比较“条目名 + 每个文件的内容”，不比压缩后的字节：不同平台的 zlib 版本压出来的字节可能不同，
        # 但内容一致就说明源码和下载包没有漂移。
        import io

        def entries(data: bytes) -> dict:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return {name: archive.read(name) for name in archive.namelist()}

        if entries(built) == entries(OUT_ZIP.read_bytes()):
            print(f"一致：构建产物与 {OUT_ZIP} 的全部条目内容相同（{len(built)} 字节）")
            return 0
        print("不一致：构建产物与仓库内 zip 有差异（源码和下载包已漂移）", file=sys.stderr)
        return 1

    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    OUT_ZIP.write_bytes(built)
    print(f"已写入 {OUT_ZIP}（{len(built)} 字节，{len(collect_files())} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
