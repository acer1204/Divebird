"""Windows：建立 .venv\\Scripts\\divebird-gui.exe —— 不會開主控台視窗的 Divebird 啟動程式。

uv 建立的 .venv\\Scripts\\pythonw.exe 其實和 python.exe 是同一個「主控台」轉接程式，
用它啟動 Divebird 會多出一個一直開著的黑色主控台視窗。
CPython 內附的 venv GUI 啟動器（Lib\\venv\\scripts\\nt\\pythonw.exe）會讀取 pyvenv.cfg，
以無主控台的 pythonw.exe 執行，並保留 venv 環境；這裡把它複製成 divebird-gui.exe。

用專案的 venv 執行：  .venv\\Scripts\\python.exe scripts\\win_gui_launcher.py
"""
from __future__ import annotations

import os
import shutil
import sys
import venv
from pathlib import Path

NAME = "divebird-gui.exe"


def ensure() -> Path | None:
    if sys.platform != "win32" or sys.prefix == sys.base_prefix:
        return None
    src = Path(venv.__file__).parent / "scripts" / "nt" / "pythonw.exe"
    dst = Path(sys.prefix) / "Scripts" / NAME
    if not src.is_file():
        return None
    if dst.is_file() and dst.read_bytes() == src.read_bytes():
        return dst
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        # 舊的啟動程式正在執行（Divebird 開著）：Windows 允許改名執行中的檔案
        old = dst.with_name(f"{dst.stem}.old-{os.getpid()}.exe")
        dst.rename(old)
        shutil.copy2(src, dst)
    return dst


if __name__ == "__main__":
    path = ensure()
    if path is None:
        print("略過：不是 Windows 或不在專案 venv 中執行", file=sys.stderr)
        sys.exit(1)
    print(path)
