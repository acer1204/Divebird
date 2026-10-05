"""MCP 協定層：Streamable HTTP 上的 JSON-RPC。一律回單一 JSON，不開 SSE，也不核發 session。

同一個端點服務兩個世代（dual-era，規格 basic/versioning）：
- 2026-07-28（modern）：每個請求在 params._meta 帶 io.modelcontextprotocol/protocolVersion 等欄位，
  並以 MCP-Protocol-Version、Mcp-Method、Mcp-Name 標頭鏡射；標頭與內容不一致回 400（-32020）；
  所有結果都帶 resultType。用戶端以「先送 modern 請求、看 400 的內容」判斷伺服器世代，
  所以這裡回的都是規格認得的 modern 錯誤，讓用戶端留在 modern
- 2025-03-26 ～ 2025-11-25（legacy）：先 initialize 握手；不核發 Mcp-Session-Id（規格允許），
  每個請求都能獨立處理

傳輸層（server.py）負責 Host／Origin 檢查與存取權杖；這裡只處理 JSON-RPC 本身。
規格：https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022

LIST_TTL_MS = 3_600_000    # 工具清單固定不變，可快取一小時


@dataclass
class Reply:
    status: int
    body: dict | None = None       # None = 202 Accepted，不帶內容


@dataclass
class CallContext:
    client: str                    # 顯示用的 AI 工具名稱（自我回報，不能當作安全判斷依據）
    protocol_version: str


class Tools(Protocol):
    def definitions(self) -> list[dict]: ...

    def call(self, name: str, arguments: dict, ctx: CallContext) -> dict: ...


class RpcError(Exception):
    def __init__(self, code: int, message: str, status: int = 200, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.status, self.data = code, message, status, data


def _error_body(rid, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    body: dict[str, Any] = {"jsonrpc": "2.0", "error": err}
    if rid is not None:
        body["id"] = rid
    return body


def _decode_header(value: str | None) -> str | None:
    """Mcp-Name 等標頭的值：非 ASCII 時以 =?base64?...?= 包裝（規格 Value Encoding）。"""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return value


def _display_name(info: Any, user_agent: str) -> str:
    name = ""
    if isinstance(info, dict):
        name = str(info.get("title") or info.get("name") or "")
    if not name and user_agent:
        name = user_agent.split("/", 1)[0].split(" ", 1)[0]
    name = "".join(ch for ch in name if ch.isprintable()).strip()[:60]
    return name or "AI 工具"


class Endpoint:
    def __init__(self, tools: Tools, *, name: str, title: str, version: str, instructions: str):
        self.tools = tools
        self.server_info = {"name": name, "title": title, "version": version}
        self.instructions = instructions

    # ------------------------------------------------------------------ 入口
    def handle(self, headers: Mapping[str, str], body: bytes, *, stdio: bool = False) -> Reply:
        """stdio=True：stdio 傳輸沒有 HTTP 標頭，略過標頭與內容一致的檢查。"""
        h = {str(k).lower(): str(v) for k, v in headers.items()}
        try:
            msg = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return Reply(400, _error_body(None, PARSE_ERROR, "Parse error"))
        if isinstance(msg, list):
            return Reply(400, _error_body(None, INVALID_REQUEST, "JSON-RPC batches are not supported"))
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return Reply(400, _error_body(None, INVALID_REQUEST, "Invalid Request"))
        method = msg.get("method")
        if "id" not in msg:                       # 通知：接受，不回內容
            if isinstance(method, str):
                return Reply(202)
            return Reply(400, _error_body(None, INVALID_REQUEST, "Invalid Request"))
        rid = msg["id"]
        if not isinstance(method, str):           # 用戶端送來的回應（我們不會發請求給用戶端）
            if "result" in msg or "error" in msg:
                return Reply(202)
            return Reply(400, _error_body(None, INVALID_REQUEST, "Invalid Request"))
        if isinstance(rid, bool) or not isinstance(rid, (str, int)):
            return Reply(400, _error_body(None, INVALID_REQUEST, "Request id must be a string or an integer"))
        params = msg.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return Reply(400, _error_body(rid, INVALID_PARAMS, "params must be an object"))
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        try:
            if META_VERSION in meta:
                result = self._modern(h, method, params, meta, stdio)
            else:
                result = self._legacy(h, method, params)
        except RpcError as e:
            return Reply(e.status, _error_body(rid, e.code, e.message, e.data))
        return Reply(200, {"jsonrpc": "2.0", "id": rid, "result": result})

    # ------------------------------------------------------------------ 2026-07-28
    def _modern(self, h: dict, method: str, params: dict, meta: dict, stdio: bool = False) -> dict:
        version = meta.get(META_VERSION)
        header_version = version if stdio else h.get("mcp-protocol-version")
        if header_version != version:
            raise RpcError(HEADER_MISMATCH, f"Header mismatch: MCP-Protocol-Version header {header_version!r} "
                                            f"does not match _meta protocolVersion {version!r}", 400)
        if version not in MODERN_VERSIONS:
            raise RpcError(UNSUPPORTED_VERSION, "Unsupported protocol version", 400,
                           {"supported": list(SUPPORTED_VERSIONS), "requested": version})
        if not stdio and h.get("mcp-method") != method:
            raise RpcError(HEADER_MISMATCH, f"Header mismatch: Mcp-Method header {h.get('mcp-method')!r} "
                                            f"does not match body method {method!r}", 400)
        if not stdio and method == "tools/call" and _decode_header(h.get("mcp-name")) != params.get("name"):
            raise RpcError(HEADER_MISMATCH, "Header mismatch: Mcp-Name header does not match params.name", 400)
        if not isinstance(meta.get(META_CLIENT_CAPS), dict):
            raise RpcError(INVALID_PARAMS, f"Missing required _meta field {META_CLIENT_CAPS}", 400)
        ctx = CallContext(_display_name(meta.get(META_CLIENT_INFO), h.get("user-agent", "")), version)

        if method == "server/discover":
            # 只列能以「每個請求自帶版本」方式使用的版本；舊世代要走 initialize，不在此列
            result = {"supportedVersions": list(MODERN_VERSIONS),
                      "capabilities": {"tools": {"listChanged": False}},
                      "instructions": self.instructions, "ttlMs": LIST_TTL_MS, "cacheScope": "public"}
        elif method == "tools/list":
            result = {"tools": self.tools.definitions(), "ttlMs": LIST_TTL_MS, "cacheScope": "public"}
        elif method == "tools/call":
            result = self._call(params, ctx)
        else:
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}", 404)
        result["resultType"] = "complete"
        result["_meta"] = {META_SERVER_INFO: dict(self.server_info)}
        return result

    # ------------------------------------------------------------------ 2025-03-26 ～ 2025-11-25
    def _legacy(self, h: dict, method: str, params: dict) -> dict:
        user_agent = h.get("user-agent", "")
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
            return {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": dict(self.server_info), "instructions": self.instructions}
        # 2025-06-18 起每個請求都帶 MCP-Protocol-Version；沒帶的視為 2025-03-26（規格允許）
        version = h.get("mcp-protocol-version") or "2025-03-26"
        if version not in LEGACY_VERSIONS:
            raise RpcError(UNSUPPORTED_VERSION, "Unsupported protocol version", 400,
                           {"supported": list(SUPPORTED_VERSIONS), "requested": version})
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools.definitions()}
        if method == "tools/call":
            return self._call(params, CallContext(_display_name(None, user_agent), version))
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    # ------------------------------------------------------------------ tools/call
    def _call(self, params: dict, ctx: CallContext) -> dict:
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise RpcError(INVALID_PARAMS, "tools/call needs a string name and an object of arguments")
        if name not in {d["name"] for d in self.tools.definitions()}:
            raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            return self.tools.call(name, arguments, ctx)
        except Exception as e:  # noqa: BLE001 - 工具內部錯誤回給模型，不讓整個請求失敗
            return {"content": [{"type": "text", "text": f"Divebird 內部錯誤：{e}"}], "isError": True}
