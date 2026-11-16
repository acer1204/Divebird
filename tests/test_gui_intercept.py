"""從瀏覽器攔截下來的下載：伺服器不讓 Divebird 下載時，不建立任務，交還給瀏覽器。"""
import os
import socket
import time

from divebird.config import Settings


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_intercepted_download_is_checked_before_it_is_taken(tmp_path, monkeypatch, server):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    from PySide6.QtWidgets import QApplication

    from divebird.gui.app import Controller

    server.add("/ok.zip", 50_000)
    server.add("/browser-only.png", 1000)
    server.refuse.add("/browser-only.png")
    app = QApplication.instance() or QApplication([])
    ctl = Controller(app, Settings(download_dir=str(tmp_path / "dl"), port=_free_port(), show_dialog=False,
                                   minimize_to_tray=False))
    try:
        refused = ctl._api_download({"url": server.url("/browser-only.png"), "kind": "http", "source": "intercept"})
        assert "403" in refused
        assert ctl._api_download({"url": server.url("/ok.zip"), "kind": "http", "source": "intercept"}) is None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ctl.manager.tasks:
            app.processEvents()
            time.sleep(0.02)
        assert [t.url for t in ctl.manager.tasks.values()] == [server.url("/ok.zip")]
    finally:
        ctl.manager.shutdown()
        if ctl.server:
            ctl.server.stop()
        if ctl.tray:
            ctl.tray.hide()
        ctl.window.deleteLater()
        app.processEvents()
