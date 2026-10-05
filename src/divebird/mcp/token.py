"""MCP 存取權杖：AI 工具連線時要在 Authorization 標頭帶上 "Bearer <權杖>"。

權杖另存成資料夾中的 mcp-token（不放進 settings.json），重設時直接換一個新的。
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from ..config import data_dir

FILENAME = "mcp-token"


def path() -> Path:
    return data_dir() / FILENAME


def load() -> str:
    try:
        return path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write(token: str) -> str:
    p = path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(token + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return token


def ensure() -> str:
    """回傳目前的權杖；還沒有就產生一個。"""
    return load() or _write(secrets.token_urlsafe(32))


def reset() -> str:
    return _write(secrets.token_urlsafe(32))


def matches(authorization: str | None, token: str) -> bool:
    """Authorization 標頭是否為 "Bearer <權杖>"（以固定時間比較，避免時間差攻擊）。"""
    if not token or not authorization:
        return False
    scheme, _, value = authorization.strip().partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), token.encode())
