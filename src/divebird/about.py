"""程式資訊（作者、專案網址、授權）與檢查更新。

檢查更新只在使用者按下按鈕時查詢 GitHub 上最新的正式版本（不含預先發行版），不會自動連線。
"""
from __future__ import annotations

import re

import requests

from . import __version__

AUTHOR = "acer1204"
AUTHOR_URL = "https://github.com/acer1204"
REPO_URL = "https://github.com/acer1204/Divebird"
RELEASES_URL = REPO_URL + "/releases/latest"
LICENSE_NAME = "MIT License"
LICENSE_URL = REPO_URL + "/blob/main/LICENSE"
LATEST_API = "https://api.github.com/repos/acer1204/Divebird/releases/latest"

_VERSION = re.compile(r"v?(\d+)\.(\d+)(?:\.(\d+))?")


class UpdateCheckError(Exception):
    """查不到最新版本；訊息可以直接顯示給使用者。"""


def parse_version(text: str) -> tuple[int, int, int] | None:
    m = _VERSION.fullmatch(text.strip())
    return tuple(int(x or 0) for x in m.groups()) if m else None


def is_newer(latest: str, current: str = __version__) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return bool(new and cur and new > cur)


def latest_release(timeout: float = 10) -> dict:
    """GitHub 上最新的正式版本：{"version": "1.2.0", "url": 該版本的 Release 頁面}。"""
    try:
        r = requests.get(LATEST_API, timeout=timeout,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": f"Divebird/{__version__}"})
    except requests.RequestException:
        raise UpdateCheckError("連不上 GitHub，請檢查網路連線。") from None
    if r.status_code in (403, 429):
        raise UpdateCheckError("GitHub 暫時限制查詢次數，請稍後再試。")
    if r.status_code == 404:
        raise UpdateCheckError("GitHub 上還沒有正式版本。")
    if not r.ok:
        raise UpdateCheckError(f"GitHub 回應錯誤（HTTP {r.status_code}）。")
    try:
        data = r.json()
        tag = str(data.get("tag_name") or "")
        url = str(data.get("html_url") or "")
    except (ValueError, AttributeError):
        raise UpdateCheckError("GitHub 回應的內容無法辨識。") from None
    if not parse_version(tag):
        raise UpdateCheckError(f"無法辨識最新版本的編號：{tag or '（空白）'}。")
    if not url.startswith(REPO_URL + "/"):      # 只開啟本專案的頁面
        url = RELEASES_URL
    return {"version": tag.strip().lstrip("v"), "url": url}
