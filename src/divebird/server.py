"""本機 HTTP API：Chrome 擴充功能透過 http://127.0.0.1:<port>/api/... 把下載交給桌面程式。

安全性：
- 只監聽 127.0.0.1，外部機器無法連入。
- 檢查 Host 標頭，防止 DNS rebinding。
- 帶有 Origin 標頭的請求（即瀏覽器發出的）只接受擴充功能來源
  （chrome-extension:// 等），一般網頁無法偷偷呼叫本 API。
- /mcp（AI 工具用的 MCP 端點）：拒絕所有帶 Origin 的請求，必須啟用並帶正確的存取權杖。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlsplit

from . import __version__
from .config import APP_NAME

if TYPE_CHECKING:
    from .mcp.service import McpService

ALLOWED_ORIGIN_SCHEMES = ("chrome-extension://", "moz-extension://", "extension://")
MAX_BODY = 4 * 1024 * 1024
MCP_PATH = "/mcp"
MAX_MCP_BODY = 1024 * 1024


class _ApiHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 的 SO_REUSEADDR 會讓第二個程式也能綁上已被佔用的埠（請求仍全部送到第一個），
    # 造成「瀏覽器整合已啟用」的假象；改用獨佔綁定，埠被佔用時才會正確失敗
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ApiServer:
    def __init__(self, port: int, on_download: Callable[[dict], None], on_show: Callable[[], None],
                 mcp: McpService | None = None):
        self.port = port
        self.on_download = on_download
        self.on_show = on_show
        self.mcp = mcp
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> bool:
        server = self

        class Handler(_Handler):
            api = server

        try:
            self._httpd = _ApiHTTPServer(("127.0.0.1", self.port), Handler)
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
        if self.path.split("?")[0] == MCP_PATH:
            self._mcp_method_not_allowed()
            return
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path == "/api/ping":
            mcp = self.api.mcp
            self._send(200, {"ok": True, "app": APP_NAME, "version": __version__,
                             "share_media": bool(mcp and mcp.share_media())})
        elif path == "/api/media/requests":
            mcp = self.api.mcp
            self._send(200, {"ok": True, "requests": mcp.browser_requests() if mcp else []})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_DELETE(self):  # noqa: N802
        if self.path.split("?")[0] == MCP_PATH:
            self._mcp_method_not_allowed()
        else:
            self._send(405, {"ok": False, "error": "method not allowed"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == MCP_PATH:
            self._handle_mcp()
            return
        if not self._guard():
            return
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
        elif path == "/api/media":
            mcp = self.api.mcp
            self._send(200, {"ok": True, "requests": mcp.browser_media_update(data) if mcp else []})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    # ---------------------------------------------------------------- MCP（AI 工具）
    def _mcp_error(self, code: int, message: str, extra_headers: dict | None = None) -> None:
        body = json.dumps({"jsonrpc": "2.0", "error": {"code": -32600, "message": message}},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _mcp_precheck(self) -> bool:
        if not self._host_ok():
            self._mcp_error(403, "Forbidden host")
            return False
        if self.headers.get("Origin") is not None:     # 網頁發出的請求一律拒絕（AI 工具不會帶 Origin）
            self._mcp_error(403, "Requests from web pages are not allowed")
            return False
        return True

    def _mcp_method_not_allowed(self) -> None:
        # 只支援 POST：GET（舊版的 SSE 串流）與 DELETE（結束 session）回 405，不需要權杖
        if self._mcp_precheck():
            self._mcp_error(405, "Method not allowed: only POST is supported", {"Allow": "POST"})

    def _handle_mcp(self) -> None:
        if not self._mcp_precheck():
            return
        mcp = self.api.mcp
        if mcp is None or not mcp.enabled():
            self._mcp_error(503, "Divebird 的 MCP 功能尚未啟用：請在 Divebird「設定 → AI 整合」啟用。")
            return
        if not mcp.authorized(self.headers.get("Authorization")):
            self._mcp_error(401, "存取權杖不正確或缺少：請帶上 Authorization: Bearer <Divebird 的 MCP 權杖>。",
                            {"WWW-Authenticate": 'Bearer realm="Divebird MCP"'})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_MCP_BODY:
            self._mcp_error(413 if length > 0 else 400, "Invalid or too large request body")
            return
        reply = mcp.handle(self.headers, self.rfile.read(length))
        if reply.body is None:
            self.send_response(reply.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._send(reply.status, reply.body)
