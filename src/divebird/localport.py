"""快速判斷本機的某個埠有沒有程式在監聽。

Windows 連到沒人監聽的本機埠，要重試約 2 秒才會失敗，所以先試著綁定這個埠：
綁得到就是沒人在用（瞬間完成）；綁不到才用短逾時連線確認。
"""
from __future__ import annotations

import socket
import sys


def listening(port: int) -> bool:
    if sys.platform == "win32":
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return False
            except OSError:
                pass
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False
