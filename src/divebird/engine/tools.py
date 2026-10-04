"""尋找內附的外部工具（ffmpeg、deno）。

程式「自帶環境」：ffmpeg 來自 imageio-ffmpeg 套件內附的靜態執行檔，
deno（yt-dlp 解析 YouTube 需要的 JavaScript 執行環境）來自 PyPI 的 deno 套件，
兩者都會一起被 PyInstaller 打包，不依賴系統安裝。找不到時才退回系統 PATH。
"""
from __future__ import annotations

import os
import shlex
import shutil
import sys
from functools import lru_cache
from pathlib import Path

from ..config import app_dir, bundle_dir

EXE = ".exe" if sys.platform == "win32" else ""


def _packaged_tool(name: str) -> str | None:
    """打包版：建置腳本會把 ffmpeg / deno 放在 tools/ 目錄。"""
    for base in (bundle_dir() / "tools", app_dir() / "tools"):
        p = base / (name + EXE)
        if p.is_file():
            return str(p)
    return None


def ffmpeg_path(custom: str = "") -> str | None:
    if custom and Path(custom).is_file():
        return custom
    return _bundled_ffmpeg() or shutil.which("ffmpeg")


@lru_cache(maxsize=1)
def _bundled_ffmpeg() -> str | None:
    exe = _packaged_tool("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001
            exe = None
    if not exe or not Path(exe).is_file():
        return None
    ensure_executable(exe)
    return _linux_gconv_safe(exe)


def _linux_gconv_safe(exe: str) -> str:
    """Linux：內附的 ffmpeg 是靜態連結 glibc 的版本。它解析 MPEG-TS（HLS 片段）時會呼叫 iconv，
    進而動態載入「系統」的 gconv 模組；系統 glibc 版本不同時（例如 Ubuntu 26.04 的 glibc 2.43）
    會直接 segfault。這裡產生一個包裝腳本，只對 ffmpeg 把 GCONV_PATH 指向空目錄，
    iconv 便改用 glibc 內建的轉換，不影響其他程式。
    """
    if not sys.platform.startswith("linux"):
        return exe
    try:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "divebird"
        empty = base / "empty-gconv"
        empty.mkdir(parents=True, exist_ok=True)
        wrapper = base / "ffmpeg-wrapper" / "ffmpeg"     # 檔名必須是 ffmpeg，yt-dlp 依檔名判斷
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        content = f'#!/bin/sh\nGCONV_PATH={shlex.quote(str(empty))} exec {shlex.quote(exe)} "$@"\n'
        if not wrapper.exists() or wrapper.read_text(encoding="utf-8") != content:
            wrapper.write_text(content, encoding="utf-8")
        wrapper.chmod(0o755)
        return str(wrapper)
    except OSError:
        return exe


@lru_cache(maxsize=1)
def deno_path() -> str | None:
    packaged = _packaged_tool("deno")
    if packaged:
        return packaged
    # 開發環境：PyPI deno 套件把執行檔裝在 venv 的 Scripts/（Windows）或 bin/（Linux）
    try:
        from deno import find_deno_bin  # type: ignore

        p = find_deno_bin()
        if p and Path(p).is_file():
            return str(p)
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("deno")


def ensure_executable(path: str | None) -> None:
    """PyInstaller 解壓後在 Linux 上可能遺失執行權限。"""
    if path and sys.platform != "win32":
        try:
            mode = os.stat(path).st_mode
            if not mode & 0o111:
                os.chmod(path, mode | 0o755)
        except OSError:
            pass
