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
