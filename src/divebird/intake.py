"""把外部傳來的下載請求（瀏覽器擴充功能、命令列、MCP）轉成下載任務。不依賴 Qt。"""
from __future__ import annotations

from .config import Settings
from .engine.media_engine import is_manifest_url, is_media_site
from .models import Kind, Task
from .utils import sanitize_filename


def task_from_payload(p: dict, settings: Settings) -> Task:
    """在背景執行緒呼叫：判斷網站類型可能需要一點時間（第一次要載入 yt-dlp 的網站清單）。"""
    url = str(p.get("url") or "")
    kind = p.get("kind")
    if kind not in (Kind.HTTP, Kind.MEDIA):
        kind = Kind.MEDIA if (is_manifest_url(url) or is_media_site(url)) else Kind.HTTP
    headers = {str(k): str(v) for k, v in (p.get("headers") or {}).items() if isinstance(v, (str, int))}
    cookies = [c for c in (p.get("cookies") or []) if isinstance(c, dict)][:500]
    title = str(p.get("title") or "")[:300]
    filename = sanitize_filename(str(p["filename"])) if p.get("filename") else ""
    if not filename and kind == Kind.MEDIA and is_manifest_url(url) and title:
        filename = sanitize_filename(title) + "." + settings.merge_format
    return Task(
        url=url, kind=kind, filename=filename, title=title,
        page_url=str(p.get("page_url") or ""),
        referer=str(p.get("referer") or p.get("page_url") or ""),
        user_agent=str(p.get("user_agent") or ""),
        headers=headers, cookies=cookies, save_dir=settings.download_dir,
    )
