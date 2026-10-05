# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包設定（Windows / Linux 共用）：
#   產出 dist/Divebird/，內含 Python 執行環境、Qt、yt-dlp，以及 tools/ffmpeg、tools/deno，
#   使用者電腦不需要安裝任何東西即可執行。
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH)
IS_WIN = sys.platform == "win32"
EXE_SUFFIX = ".exe" if IS_WIN else ""

# ---- 內附外部工具：ffmpeg（影音合併）、deno（yt-dlp 解析 YouTube 需要的 JS 執行環境）
import imageio_ffmpeg
from deno import find_deno_bin

tools = ROOT / "build" / "tools"
tools.mkdir(parents=True, exist_ok=True)
shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), tools / f"ffmpeg{EXE_SUFFIX}")
shutil.copy2(find_deno_bin(), tools / f"deno{EXE_SUFFIX}")
binaries = [(str(tools / f"ffmpeg{EXE_SUFFIX}"), "tools"), (str(tools / f"deno{EXE_SUFFIX}"), "tools")]

datas = collect_data_files("yt_dlp_ejs")
hiddenimports = collect_submodules("yt_dlp_ejs") + ["PySide6.QtSvg"]

a = Analysis(
    [str(ROOT / "src" / "divebird" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "imageio_ffmpeg", "deno", "pytest", "PyInstaller",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Divebird",
    console=False,
    icon=str(ROOT / "assets" / "divebird.ico") if IS_WIN else None,
    upx=False,
)
# ---- divebird-mcp：MCP 的 stdio 橋接（主控台程式），給只能以 stdio 啟動 MCP 伺服器的 AI 應用程式使用。
#      只用到標準函式庫與 divebird 的設定、協定模組，不需要 Qt、yt-dlp 與網路套件
b = Analysis(
    [str(ROOT / "src" / "divebird" / "mcp" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    excludes=["tkinter", "PySide6", "yt_dlp", "yt_dlp_ejs", "requests", "urllib3", "imageio_ffmpeg", "deno",
              "pytest", "PyInstaller"],
    noarchive=False,
)
pyz_mcp = PYZ(b.pure)
exe_mcp = EXE(
    pyz_mcp,
    b.scripts,
    [],
    exclude_binaries=True,
    name="divebird-mcp",
    console=True,
    icon=str(ROOT / "assets" / "divebird.ico") if IS_WIN else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, exe_mcp, b.binaries, b.datas, name="Divebird", upx=False)
