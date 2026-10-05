"""把設定、存取權杖與 MCP 協定端點組在一起，交給本機 HTTP 伺服器（server.py）使用。"""
from __future__ import annotations

from typing import Mapping

from .. import __version__
from ..config import Settings
from . import token
from .protocol import Endpoint, Reply
from .tools import INSTRUCTIONS, Backend, DivebirdTools


class McpService:
    def __init__(self, settings: Settings, backend: Backend):
        self.settings = settings
        self.tools = DivebirdTools(backend)
        self.endpoint = Endpoint(self.tools, name="divebird", title="Divebird", version=__version__,
                                 instructions=INSTRUCTIONS)

    def enabled(self) -> bool:
        return bool(self.settings.mcp_enabled)

    def authorized(self, authorization: str | None) -> bool:
        return token.matches(authorization, token.load())

    def handle(self, headers: Mapping[str, str], body: bytes) -> Reply:
        return self.endpoint.handle(headers, body)

    # ---------------------------------------------------------------- 瀏覽器偵測到的影音
    def share_media(self) -> bool:
        """擴充功能是否要回報偵測到的影音（MCP 已啟用且使用者開放）；關閉時立刻清掉已收到的清單。"""
        on = bool(self.settings.mcp_enabled and self.settings.mcp_share_browser_media)
        if not on:
            self.tools.browser.clear()
        return on

    def browser_media_update(self, payload: dict) -> list[dict]:
        """擴充功能傳來某個分頁的影音清單；回傳等擴充功能補上登入資訊的下載。"""
        if not self.share_media():
            return []
        self.tools.browser.update(payload)
        return self.tools.browser.pending()

    def browser_requests(self) -> list[dict]:
        return self.tools.browser.pending() if self.share_media() else []
