"""開機 / 登入時自動啟動（縮小到系統匣），讓瀏覽器擴充功能隨時能交付下載。

- Windows：HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
- Linux：~/.config/autostart/divebird.desktop（XDG Autostart）
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from .config import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    exe = Path(sys.executable)
    if sys.platform == "win32":
        # 優先用 divebird-gui.exe（CPython 的無視窗 venv 啟動器，見 scripts/win_gui_launcher.py）；
        # uv 建立的 venv 裡 pythonw.exe 其實會開主控台視窗，登入時會跳出黑色視窗
        for name in ("divebird-gui.exe", "pythonw.exe"):
            cand = exe.with_name(name)
            if cand.exists():
                exe = cand
                break
    return [str(exe), "-m", "divebird", "--minimized"]


def _desktop_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "divebird.desktop"


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False
    return _desktop_file().exists()


def set_enabled(enabled: bool) -> None:
    cmd = launch_command()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return
    f = _desktop_file()
    if enabled:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(
            "[Desktop Entry]\nType=Application\nName=Divebird\n"
            f"Exec={' '.join(shlex.quote(c) for c in cmd)}\nTerminal=false\nX-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )
    else:
        f.unlink(missing_ok=True)


def migrate_legacy() -> None:
    """改名前（OpenDM）設定過的自動啟動：移除舊項目並以新名稱重新建立。"""
    from .config import LEGACY_NAMES

    had_legacy = False
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
                for name in LEGACY_NAMES:
                    try:
                        winreg.QueryValueEx(k, name)
                    except FileNotFoundError:
                        continue
                    winreg.DeleteValue(k, name)
                    had_legacy = True
        except OSError:
            return
    else:
        for name in LEGACY_NAMES:
            old = _desktop_file().with_name(f"{name.lower()}.desktop")
            if old.exists():
                old.unlink()
                had_legacy = True
    if had_legacy and not is_enabled():
        set_enabled(True)
