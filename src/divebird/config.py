"""設定與路徑管理。

資料目錄規則：
- 可攜模式：程式目錄（或打包後執行檔目錄）下存在 ``portable`` 檔案時，資料存放於 ``<程式目錄>/data``
- Windows：%APPDATA%\\Divebird
- Linux：$XDG_CONFIG_HOME/divebird（預設 ~/.config/divebird）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_NAME = "Divebird"
DEFAULT_PORT = 17890


def app_dir() -> Path:
    """程式所在目錄（打包後為執行檔所在目錄）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """打包後資源解壓目錄（PyInstaller 的 _MEIPASS），開發時為套件目錄。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


LEGACY_NAMES = ("OpenDM",)   # 改名前的程式名稱：首次啟動時自動搬移舊資料


def data_dir() -> Path:
    base = app_dir()
    if (base / "portable").exists():
        path = base / "data"
    else:
        if sys.platform == "win32":
            parent = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            path = parent / APP_NAME
            legacy = [parent / n for n in LEGACY_NAMES]
        else:
            parent = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            path = parent / APP_NAME.lower()
            legacy = [parent / n.lower() for n in LEGACY_NAMES]
        _migrate_legacy(path, legacy)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _migrate_legacy(path: Path, legacy: list[Path]) -> None:
    """新資料夾還不存在時，複製舊名稱的資料夾（設定、下載清單）。舊資料夾保留不刪，當作備份。"""
    if path.exists():
        return
    for old in legacy:
        if old.is_dir():
            try:
                shutil.copytree(old, path)
            except OSError:
                pass
            return


def default_download_dir() -> Path:
    if sys.platform != "win32":
        # 優先讀取 XDG 使用者目錄設定（中文 Linux 桌面的下載資料夾可能是「下載」）
        try:
            cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "user-dirs.dirs"
            for line in cfg.read_text(encoding="utf-8").splitlines():
                if line.startswith("XDG_DOWNLOAD_DIR="):
                    value = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(Path.home()))
                    if value:
                        return Path(value)
        except OSError:
            pass
    return Path.home() / "Downloads"


@dataclass
class Settings:
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    max_concurrent: int = 3           # 同時進行的下載任務數
    connections: int = 8              # 每個檔案的連線數（分段數上限）
    speed_limit_kbps: int = 0         # 全域限速（KB/s），0 = 不限
    port: int = DEFAULT_PORT          # 本機 API 埠（需與擴充功能設定一致）
    show_dialog: bool = True          # 收到新下載時顯示確認對話框
    minimize_to_tray: bool = True     # 關閉視窗時縮到系統匣
    notify_on_complete: bool = True   # 下載完成時顯示通知
    media_format: str = "bv*+ba/b"    # yt-dlp 預設格式
    merge_format: str = "mp4"         # 影音合併輸出格式
    ffmpeg_path: str = ""             # 自訂 ffmpeg 路徑（空白 = 使用內附）
    max_retries: int = 8              # 每段連線失敗重試次數

    _lock = threading.Lock()

    @classmethod
    def path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
            names = {f.name for f in fields(cls)}
            for k, v in raw.items():
                if k in names:
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        with self._lock:
            tmp = self.path().with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path())
