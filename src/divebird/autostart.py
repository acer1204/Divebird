r"""開機 / 登入時自動啟動（縮小到系統匣），讓瀏覽器擴充功能隨時能交付下載。

- Windows：HKCU\Software\Microsoft\Windows\CurrentVersion\Run
  （使用者在「工作管理員 → 啟動應用程式」停用時，Windows 另外記在 StartupApproved\Run）
- Linux：~/.config/autostart/divebird.desktop（XDG Autostart）
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from .config import APP_NAME, LEGACY_NAMES

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"


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


# ---------------------------------------------------------------- Windows
def _win_run_exists(name: str) -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def _win_disabled_by_user(name: str) -> bool:
    """工作管理員中被停用的項目：StartupApproved 值的第一個位元組為奇數。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as k:
            data, _ = winreg.QueryValueEx(k, name)
            return bool(data) and data[0] % 2 == 1
    except OSError:
        return False


def _win_delete(key: str, name: str) -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


# ---------------------------------------------------------------- Linux
def _desktop_file(name: str = APP_NAME) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{name.lower()}.desktop"


def desktop_disabled(text: str) -> bool:
    """XDG autostart 項目被停用：Hidden=true 或 X-GNOME-Autostart-enabled=false。"""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if (key == "hidden" and value == "true") or (key == "x-gnome-autostart-enabled" and value == "false"):
            return True
    return False


def _desktop_enabled(path: Path) -> bool:
    try:
        return not desktop_disabled(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False


# ---------------------------------------------------------------- 公開介面
def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_run_exists(APP_NAME) and not _win_disabled_by_user(APP_NAME)
    return _desktop_enabled(_desktop_file())


def set_enabled(enabled: bool) -> None:
    cmd = launch_command()
    if sys.platform == "win32":
        import winreg
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
            _win_delete(APPROVED_KEY, APP_NAME)     # 使用者在設定中重新勾選：清除「已停用」標記
        else:
            _win_delete(RUN_KEY, APP_NAME)
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
    """改名前（OpenDM）設定的自動啟動：移除舊項目；只有舊項目原本是「啟用中」才以新名稱重新建立，
    不會打開使用者已經在系統中停用的自動啟動。"""
    was_active = False
    if sys.platform == "win32":
        for name in LEGACY_NAMES:
            if _win_run_exists(name):
                was_active = was_active or not _win_disabled_by_user(name)
                _win_delete(RUN_KEY, name)
                _win_delete(APPROVED_KEY, name)
    else:
        for name in LEGACY_NAMES:
            old = _desktop_file(name)
            if old.exists():
                was_active = was_active or _desktop_enabled(old)
                old.unlink()
    if was_active and not is_enabled():
        set_enabled(True)
