"""本機 HTTP API：Chrome 擴充功能透過 http://127.0.0.1:<port>/api/... 把下載交給桌面程式。

安全性：
- 只監聽 127.0.0.1，外部機器無法連入。
- 檢查 Host 標頭，防止 DNS rebinding。
- 帶有 Origin 標頭的請求（即瀏覽器發出的）只接受擴充功能來源
  （chrome-extension:// 等），一般網頁無法偷偷呼叫本 API。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlsplit

from . import __version__
from .config import APP_NAME

ALLOWED_ORIGIN_SCHEMES = ("chrome-extension://", "moz-extension://", "extension://")
MAX_BODY = 4 * 1024 * 1024


class ApiServer:
    def __init__(self, port: int, on_download: Callable[[dict], None], on_show: Callable[[], None]):
        self.port = port
        self.on_download = on_download
        self.on_show = on_show
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> bool:
        server = self

        class Handler(_Handler):
            api = server

        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            return False
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="api-server").start()
        return True

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


class _Handler(BaseHTTPRequestHandler):
    api: ApiServer
    server_version = f"{APP_NAME}/{__version__}"

    def log_message(self, format, *args):  # noqa: A002 - 靜音
        pass

    # ---------------------------------------------------------------- 驗證
    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin.startswith(ALLOWED_ORIGIN_SCHEMES)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.api.port}", f"localhost:{self.api.port}")

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin and origin.startswith(ALLOWED_ORIGIN_SCHEMES):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self) -> bool:
        if not self._host_ok() or not self._origin_ok():
            self._send(403, {"ok": False, "error": "forbidden"})
            return False
        return True

    # ---------------------------------------------------------------- 路由
    def do_OPTIONS(self):  # noqa: N802
        if not self._guard():
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if not self._guard():
            return
        if self.path.split("?")[0] == "/api/ping":
            self._send(200, {"ok": True, "app": APP_NAME, "version": __version__})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            self._send(413, {"ok": False, "error": "payload too large"})
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            self._send(400, {"ok": False, "error": "invalid json"})
            return

        if path == "/api/download":
            url = str(data.get("url") or "")
            if urlsplit(url).scheme not in ("http", "https"):
                self._send(400, {"ok": False, "error": "只支援 http / https 網址"})
                return
            self.api.on_download(data)
            self._send(200, {"ok": True})
        elif path == "/api/show":
            self.api.on_show()
            self._send(200, {"ok": True})
        else:
            self._send(404, {"ok": False, "error": "not found"})
