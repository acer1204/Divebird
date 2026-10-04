from __future__ import annotations

import sys
import threading

import requests
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import autostart
from ..config import APP_NAME, Settings
from ..engine.manager import DownloadManager
from ..engine.media_engine import _site_extractors, is_manifest_url, is_media_site
from ..models import Kind, Task
from ..server import ApiServer
from ..utils import sanitize_filename
from . import icons
from .dialogs import NewDownloadDialog
from .main_window import MainWindow


class Bridge(QObject):
    """把下載引擎（背景執行緒）的事件轉成 Qt 訊號，安全地送回 GUI 執行緒。"""

    added = Signal(object)
    removed = Signal(str)
    changed = Signal(object)
    finished = Signal(object)
    download_requested = Signal(object, bool)   # (Task, 是否詢問)
    show_requested = Signal()

    def task_added(self, t):
        self.added.emit(t)

    def task_removed(self, tid):
        self.removed.emit(tid)

    def task_changed(self, t):
        self.changed.emit(t)

    def task_finished(self, t):
        self.finished.emit(t)


def task_from_payload(p: dict, settings: Settings) -> Task:
    """把擴充功能送來的 JSON 轉成下載任務（在背景執行緒呼叫，判斷網站類型可能需要一點時間）。"""
    url = str(p.get("url") or "")
    kind = p.get("kind")
    if kind not in (Kind.HTTP, Kind.MEDIA):
        kind = Kind.MEDIA if (is_manifest_url(url) or is_media_site(url)) else Kind.HTTP
    headers = {str(k): str(v) for k, v in (p.get("headers") or {}).items() if isinstance(v, (str, int))}
    cookies = [c for c in (p.get("cookies") or []) if isinstance(c, dict)][:500]
    title = str(p.get("title") or "")[:300]
    filename = sanitize_filename(str(p["filename"])) if p.get("filename") else ""
    if not filename and kind == Kind.MEDIA and is_manifest_url(url) and title:
        filename = sanitize_filename(title) + "." + settings.merge_format
    return Task(
        url=url, kind=kind, filename=filename, title=title,
        page_url=str(p.get("page_url") or ""),
        referer=str(p.get("referer") or p.get("page_url") or ""),
        user_agent=str(p.get("user_agent") or ""),
        headers=headers, cookies=cookies, save_dir=settings.download_dir,
    )


def is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return QGuiApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128


def stylesheet(dark: bool) -> str:
    line = "#2b3544" if dark else "#e5e9f0"
    side = "#161b22" if dark else "#f6f8fb"
    hover = "#253041" if dark else "#e9eef6"
    sel_bg = "#1e3a6e" if dark else "#dbe7fe"
    sel_fg = "#e2e8f0" if dark else "#0f172a"
    accent = "#60a5fa" if dark else "#1d4ed8"
    # 明確定義核取方塊外觀：部分深色主題下 Fusion 會把「未勾選」也畫成整塊強調色，難以分辨
    box_bg = "#111827" if dark else "#ffffff"
    box_border = "#4b5563" if dark else "#9ca3af"
    check = icons.check_mark_path()
    return f"""
    QToolBar#main-toolbar {{ border: none; border-bottom: 1px solid {line}; spacing: 2px; padding: 4px 8px; }}
    QToolBar#main-toolbar QToolButton {{ padding: 4px 8px; border-radius: 6px; min-width: 56px; }}
    QToolBar#main-toolbar QToolButton:hover {{ background: {hover}; }}
    QToolBar#main-toolbar QToolButton:disabled {{ color: palette(placeholder-text); }}
    QLineEdit#search {{ padding: 5px 8px; border-radius: 6px; }}
    QListWidget#categories {{ border: none; background: {side}; padding-top: 6px; outline: 0; }}
    QListWidget#categories::item {{ padding: 7px 8px; margin: 1px 6px; border-radius: 6px; }}
    QListWidget#categories::item:hover {{ background: {hover}; }}
    QListWidget#categories::item:selected {{ background: {sel_bg}; color: {accent}; }}
    QTableView#tasks {{ border: none; outline: 0; selection-background-color: {sel_bg};
                        selection-color: {sel_fg}; }}
    QTableView#tasks::item {{ border-bottom: 1px solid {line}; padding-left: 6px; }}
    QTableView#tasks::item:selected {{ background: {sel_bg}; color: {sel_fg}; }}
    QHeaderView::section {{ border: none; border-bottom: 1px solid {line}; padding: 6px 8px;
                           background: palette(base); font-weight: 600; }}
    QSplitter::handle {{ background: {line}; width: 1px; }}
    QStatusBar {{ border-top: 1px solid {line}; }}
    QStatusBar QLabel {{ padding: 2px 6px; }}
    QPushButton#primary {{ background: #2563eb; color: white; border: none; padding: 6px 18px;
                          border-radius: 6px; font-weight: 600; }}
    QPushButton#primary:hover {{ background: #1d4ed8; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {box_border};
                           background: {box_bg}; }}
    QCheckBox::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked {{ background: #2563eb; border-color: #2563eb; image: url({check}); }}
    QCheckBox::indicator:disabled {{ background: transparent; border-color: {line}; }}
    """


class Controller(QObject):
    def __init__(self, app: QApplication, settings: Settings):
        super().__init__()
        self.app = app
        self.settings = settings
        self.dark = is_dark()
        self.bridge = Bridge()
        self.manager = DownloadManager(settings, listener=self.bridge)
        self._dialogs: set[NewDownloadDialog] = set()
        self.tray: QSystemTrayIcon | None = None

        self.server: ApiServer | None = None
        self.api_ok = False
        self._start_server()

        self.bridge.download_requested.connect(self.handle_request)
        self.bridge.show_requested.connect(lambda: self.window.bring_to_front())
        self.bridge.finished.connect(self._on_finished)

        self.window = MainWindow(self)
        self._setup_tray()
        # 預先載入 yt-dlp 的網站清單，讓第一次判斷網址類型時不會卡頓
        threading.Thread(target=_site_extractors, daemon=True).start()

    # ---------------------------------------------------------------- API
    def _start_server(self):
        self.server = ApiServer(self.settings.port, on_download=self._api_download,
                                on_show=self.bridge.show_requested.emit)
        self.api_ok = self.server.start()

    def restart_server(self):
        if self.server:
            self.server.stop()
        self._start_server()

    def _api_download(self, payload: dict):
        """由 API 執行緒呼叫：另開執行緒判斷網址類型，讓擴充功能立即收到回應。"""
        def work():
            self.bridge.download_requested.emit(task_from_payload(payload, self.settings), True)
        threading.Thread(target=work, daemon=True).start()

    def request_url(self, url: str, ask: bool = True):
        def work():
            task = task_from_payload({"url": url}, self.settings)
            self.bridge.download_requested.emit(task, ask)
        threading.Thread(target=work, daemon=True).start()

    def handle_request(self, task: Task, ask: bool):
        if ask and self.settings.show_dialog:
            dlg = NewDownloadDialog(task, self.settings)
            dlg.submitted.connect(lambda t, start: self.manager.add(t, start))
            dlg.finished.connect(lambda _r, d=dlg: self._dialogs.discard(d))
            self._dialogs.add(dlg)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
        else:
            self.manager.add(task)
            if not self.window.isVisible():
                self.notify("已開始下載", task.filename or task.title or task.url)

    # ---------------------------------------------------------------- 系統匣
    def tray_available(self) -> bool:
        return self.tray is not None and QSystemTrayIcon.isSystemTrayAvailable()

    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(icons.app_icon(), self)
        menu = QMenu()
        a_show = QAction("顯示主視窗", menu)
        a_show.triggered.connect(self.window.bring_to_front)
        menu.addAction(a_show)
        menu.addSeparator()
        menu.addAction("全部開始", self.manager.start_all)
        menu.addAction("全部暫停", self.manager.pause_all)
        menu.addSeparator()
        menu.addAction("結束 Divebird", self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: reason in (QSystemTrayIcon.ActivationReason.Trigger,
                                                         QSystemTrayIcon.ActivationReason.DoubleClick)
                               and self.window.bring_to_front())
        tray.setToolTip(APP_NAME)
        tray.show()
        self._tray_menu = menu
        self.tray = tray

    def update_tray_tooltip(self, active: int, speed: float):
        if self.tray:
            from ..utils import human_size
            self.tray.setToolTip(f"{APP_NAME} — 下載中 {active} 個，{human_size(speed)}/s" if active else APP_NAME)

    def notify(self, title: str, msg: str):
        if self.tray:
            self.tray.showMessage(title, msg, icons.app_icon(), 4000)

    def _on_finished(self, t: Task):
        if self.settings.notify_on_complete:
            self.notify("下載完成", t.filename)

    def quit(self):
        self.window._quitting = True
        for d in list(self._dialogs):
            d.close()
        self.manager.shutdown()
        if self.server:
            self.server.stop()
        if self.tray:
            self.tray.hide()
        self.app.quit()


def _forward_to_running(port: int, urls: list[str]) -> bool:
    """若已有 Divebird 在執行，把網址交給它並喚出視窗。"""
    base = f"http://127.0.0.1:{port}"
    try:
        r = requests.get(base + "/api/ping", timeout=1.5)
        if not (r.ok and r.json().get("app") == APP_NAME):
            return False
        for u in urls:
            requests.post(base + "/api/download", json={"url": u}, timeout=3)
        requests.post(base + "/api/show", json={}, timeout=3)
        return True
    except (requests.RequestException, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    urls = [a for a in argv[1:] if a.lower().startswith(("http://", "https://"))]
    settings = Settings.load()
    if _forward_to_running(settings.port, urls):
        return 0

    if sys.platform == "win32":
        try:   # 讓 Windows 工作列使用本程式的圖示而非 python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Divebird.DownloadManager")
        except Exception:  # noqa: BLE001
            pass
    try:   # 改名前（OpenDM）設定的開機自動啟動，換成新名稱
        autostart.migrate_legacy()
    except OSError:
        pass

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    app = QApplication(argv)
    app.setStyle("Fusion")
    app.setWindowIcon(icons.app_icon())
    app.setQuitOnLastWindowClosed(False)

    ctl = Controller(app, settings)
    app.setStyleSheet(stylesheet(ctl.dark))
    if "--minimized" not in argv:
        ctl.window.show()
    elif not ctl.tray_available():
        ctl.window.showMinimized()     # 沒有系統匣時至少留在工作列
    for u in urls:
        ctl.request_url(u)
    return app.exec()
