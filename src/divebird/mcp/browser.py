"""瀏覽器擴充功能偵測到的影音，提供給 AI 挑選（使用者在「設定 → AI 整合」開放後才會收集）。

1. 擴充功能把各分頁偵測到的影音清單（網址、類型、大小；不含 Cookie 與請求標頭）傳到 /api/media
2. AI 用 list_browser_media 看清單：只看得到網址的主機與路徑，看不到常帶有簽章或權杖的查詢參數
3. AI 用 download_browser_media 指定下載後，擴充功能下次詢問時（最多約 30 秒）補上 Cookie 與 Referer
   送出下載：登入資訊只在瀏覽器與 Divebird 之間傳遞，不會經過 AI
"""
from __future__ import annotations

import hashlib
import threading
import time
from urllib.parse import urlsplit

TAB_TTL = 30 * 60            # 分頁這麼久沒有更新就移除
REQUEST_TTL = 90             # 擴充功能這麼久沒有補上資料，就當作失敗
FORGET_AFTER = 10 * 60       # 已處理或已失敗的請求保留多久（讓 AI 查得到結果）
MAX_TABS = 50
MAX_ITEMS = 60
TYPES = ("hls", "dash", "video", "audio")

WAITING, TAKEN, EXPIRED = "waiting", "taken", "expired"


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _key(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


class BrowserMedia:
    def __init__(self):
        self._lock = threading.Lock()
        self._tabs: dict[int, dict] = {}
        self._requests: dict[str, dict] = {}

    # ------------------------------------------------------------------ 擴充功能
    def update(self, payload: dict) -> None:
        """擴充功能傳來某個分頁的完整清單；空清單表示分頁已關閉或換頁。"""
        try:
            tab_id = int(payload.get("tab_id"))
        except (TypeError, ValueError):
            return
        items = payload.get("items")
        now = time.time()
        with self._lock:
            self._expire(now)
            if not isinstance(items, list) or not items:
                self._tabs.pop(tab_id, None)
                return
            media = {}
            for item in items[:MAX_ITEMS]:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                if not url.lower().startswith(("http://", "https://")) or len(url) > 8000:
                    continue
                media_id = hashlib.sha256(f"{tab_id}|{_key(url)}".encode()).hexdigest()[:12]
                media[media_id] = {
                    "media_id": media_id, "url": url,
                    "type": item.get("type") if item.get("type") in TYPES else "video",
                    "size": _int(item.get("size")), "time": _int(item.get("time")) / 1000,
                }
            self._tabs[tab_id] = {"tab_id": tab_id, "page_url": str(payload.get("page_url") or "")[:4000],
                                  "title": str(payload.get("title") or "")[:300], "updated": now, "media": media}
            while len(self._tabs) > MAX_TABS:
                del self._tabs[min(self._tabs, key=lambda t: self._tabs[t]["updated"])]

    def pending(self) -> list[dict]:
        """AI 已指定、等擴充功能補上登入資訊的下載。"""
        with self._lock:
            self._expire(time.time())
            return [{k: r[k] for k in ("request_id", "tab_id", "url", "type", "page_url")}
                    for r in self._requests.values() if r["state"] == WAITING]

    def take(self, request_id: str) -> dict | None:
        """擴充功能送來補上登入資訊的下載：取出請求（只能取一次）。"""
        with self._lock:
            request = self._requests.get(request_id)
            if not request or request["state"] != WAITING:
                return None
            request["state"], request["t"] = TAKEN, time.time()
            return dict(request)

    def clear(self) -> None:
        with self._lock:
            self._tabs.clear()

    # ------------------------------------------------------------------ AI
    def listing(self, query: str = "") -> list[dict]:
        now = time.time()
        with self._lock:
            self._expire(now)
            tabs = sorted(self._tabs.values(), key=lambda t: t["updated"], reverse=True)
            result = []
            for tab in tabs:
                page = urlsplit(tab["page_url"])
                if query and query.lower() not in f"{tab['title']} {page.netloc}{page.path}".lower():
                    continue
                media = []
                for m in sorted(tab["media"].values(), key=lambda m: m["time"], reverse=True):
                    u = urlsplit(m["url"])
                    media.append({
                        "media_id": m["media_id"], "type": m["type"],
                        "name": u.path.rsplit("/", 1)[-1] or u.netloc, "host": u.netloc,
                        "size_bytes": m["size"] or None,
                        "detected_seconds_ago": int(now - m["time"]) if m["time"] else None,
                    })
                result.append({"page_title": tab["title"] or None, "page": f"{page.netloc}{page.path}" or None,
                               "updated_seconds_ago": int(now - tab["updated"]), "media": media})
            return result

    def find(self, media_id: str) -> tuple[dict, dict] | None:
        with self._lock:
            self._expire(time.time())
            for tab in self._tabs.values():
                if media_id in tab["media"]:
                    return dict(tab), dict(tab["media"][media_id])
        return None

    def request(self, request_id: str, media_id: str, options: dict, client: str) -> None:
        found = self.find(media_id)
        if not found:
            raise KeyError(media_id)
        tab, item = found
        with self._lock:
            self._requests[request_id] = {
                "request_id": request_id, "media_id": media_id, "tab_id": tab["tab_id"], "url": item["url"],
                "type": item["type"], "page_url": tab["page_url"], "title": tab["title"],
                "options": options, "client": client, "created": time.time(), "t": time.time(), "state": WAITING,
            }

    def state(self, request_id: str) -> dict | None:
        with self._lock:
            self._expire(time.time())
            request = self._requests.get(request_id)
            return dict(request) if request else None

    # ------------------------------------------------------------------ 內部
    def _expire(self, now: float) -> None:
        for tab_id in [t for t, v in self._tabs.items() if now - v["updated"] > TAB_TTL]:
            del self._tabs[tab_id]
        for rid, r in list(self._requests.items()):
            if r["state"] == WAITING and now - r["created"] > REQUEST_TTL:
                r["state"], r["t"] = EXPIRED, now
            elif r["state"] != WAITING and now - r["t"] > FORGET_AFTER:
                del self._requests[rid]
