"""提供給 AI 的 MCP 工具。

設計原則：
- 每次呼叫都立即回傳：download 交給 Divebird 後馬上回傳 task_id，下載在背景進行，
  AI 用 get_download（可等待最多 25 秒）查進度。下載的生命週期與 MCP 請求無關，請求斷線不影響下載
- 權限由 Divebird 設定決定（設定 →「AI 整合」）：子資料夾、Cookie／登入資訊、內網位址、刪除檔案。
  不允許的操作回傳 isError 結果並說明原因，讓模型能自行調整
- 輸出精簡，不含 Cookie 或標頭等敏感資料；網頁標題與檔名一律當作資料
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit
from typing import Any, Callable, Protocol

from .. import __version__
from ..config import Settings
from ..engine.http_engine import DownloadError, make_session, probe
from ..engine.manager import DownloadManager
from ..engine.media_engine import AUDIO_BEST, AUDIO_MP3, extract_info, format_choices, is_manifest_url, is_media_site
from ..engine.tools import ffmpeg_path
from ..intake import task_from_payload
from ..models import Kind, Status, Task
from ..netpolicy import BlockedAddress, check_url
from ..utils import sanitize_filename
from .browser import EXPIRED, BrowserMedia
from .protocol import CallContext
from .schema import INSTRUCTIONS, MAX_WAIT, TOOL_DEFINITIONS  # noqa: F401

AWAITING = "awaiting_confirmation"     # 等使用者在 Divebird 確認
REJECTED = "rejected"                  # 使用者按了取消
WAITING_FOR_BROWSER = "waiting_for_browser"    # 等瀏覽器擴充功能補上登入資訊
TERMINAL = (Status.COMPLETED, Status.ERROR, Status.PAUSED, REJECTED)
MAX_PENDING = 3                        # 同時等待確認的下載上限，避免被洗版
RATE_LIMIT = 60                        # 每分鐘最多呼叫次數
PROBE_CACHE_SECONDS = 300
QUALITY_PRESETS = {
    "best": "bv*+ba/b",
    "2160p": "bv*[height<=2160]+ba/b[height<=2160]/bv*+ba/b",
    "1440p": "bv*[height<=1440]+ba/b[height<=1440]/bv*+ba/b",
    "1080p": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
    "720p": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
    "480p": "bv*[height<=480]+ba/b[height<=480]/bv*+ba/b",
    "360p": "bv*[height<=360]+ba/b[height<=360]/bv*+ba/b",
    "audio": AUDIO_BEST,
    "mp3": AUDIO_MP3,
}
_CREDENTIAL_HEADERS = {"cookie", "authorization", "proxy-authorization"}
_DROPPED_HEADERS = {"host", "content-length", "range", "connection", "transfer-encoding", "accept-encoding"}


class ToolError(Exception):
    """給模型看的錯誤：以 isError 結果回傳，讓模型可以修正參數或改用其他方法。"""


class Backend(Protocol):
    settings: Settings
    manager: DownloadManager

    def confirm(self, task: Task, start: bool, client: str) -> None:
        """在 Divebird 跳出確認視窗；使用者決定後呼叫 DivebirdTools.confirmation_done。"""

    def announce(self, title: str, message: str) -> None:
        """顯示系統匣通知（可由任何執行緒呼叫）。"""


# ---------------------------------------------------------------------- 參數檢查
def _string(args: dict, key: str, *, required: bool = False, max_len: int = 2000) -> str:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ToolError(f"缺少參數 {key}。")
        return ""
    if not isinstance(value, str):
        raise ToolError(f"參數 {key} 必須是字串。")
    if len(value) > max_len:
        raise ToolError(f"參數 {key} 太長（上限 {max_len} 字元）。")
    return value.strip()


def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError(f"參數 {key} 必須是數字。")
    return max(lo, min(hi, int(value)))


def _choice(args: dict, key: str, default: str, choices: tuple[str, ...]) -> str:
    value = args.get(key, default)
    if value not in choices:
        raise ToolError(f"參數 {key} 必須是 {', '.join(choices)} 其中之一。")
    return value


def _http_url(value: str, key: str) -> str:
    if not re.match(r"^https?://[^\s/?#]+", value, re.I):
        raise ToolError(f"{key} 必須是 http:// 或 https:// 開頭的完整網址。")
    return value


def _safe_subdir(base: str, subdir: str) -> Path:
    """下載資料夾底下的子資料夾：只接受相對路徑，每一段都清理成合法名稱，不可跳出下載資料夾。"""
    raw = subdir.replace("\\", "/").strip().strip("/")
    if not raw or PureWindowsPath(subdir).drive or PureWindowsPath(subdir).root or subdir.startswith(("/", "\\")):
        raise ToolError("subdir 必須是下載資料夾底下的相對路徑，例如「課程/第一週」。")
    parts = []
    for part in raw.split("/"):
        part = part.strip()
        if part in ("", ".", ".."):
            raise ToolError("subdir 不可包含「.」或「..」。")
        parts.append(sanitize_filename(part, fallback="folder"))
    if len(parts) > 5:
        raise ToolError("subdir 最多 5 層。")
    root = Path(base).resolve()
    target = root.joinpath(*parts).resolve()
    if not target.is_relative_to(root):
        raise ToolError("subdir 必須位於下載資料夾之內。")
    return target


def _headers(value: Any, allow_credentials: bool) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 20:
        raise ToolError("headers 必須是最多 20 個「名稱: 值」的物件。")
    out = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}", k) \
                or len(v) > 4000 or any(c in v for c in "\r\n\0"):
            raise ToolError(f"標頭 {k!r} 的名稱或值不合法。")
        lower = k.lower()
        if lower in _DROPPED_HEADERS:
            continue
        if lower in _CREDENTIAL_HEADERS and not allow_credentials:
            raise ToolError(f"Divebird 設定不允許 AI 傳入登入資訊（{k}）。請省略這個標頭，"
                            "或請使用者改用瀏覽器擴充功能的下載按鈕。")
        out[k] = v
    return out


def _cookies(value: Any, allow: bool) -> list[dict]:
    if not value:
        return []
    if not allow:
        raise ToolError("Divebird 設定不允許 AI 傳入 Cookie。請省略 cookies，或請使用者改用瀏覽器擴充功能的下載按鈕。")
    if not isinstance(value, list) or len(value) > 200:
        raise ToolError("cookies 必須是最多 200 個物件的陣列。")
    out = []
    for c in value:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not isinstance(c.get("value"), str):
            raise ToolError("每個 cookie 都要有字串型別的 name 與 value。")
        out.append({k: c[k] for k in ("name", "value", "domain", "path", "secure", "hostOnly") if k in c})
    return out


def _with_extension(filename: str, url: str) -> str:
    """一般檔案的檔名沒有副檔名時（AI 常只說「第三課」），沿用網址的副檔名；影片交給 yt-dlp 自動決定。"""
    if not filename or Path(filename).suffix:
        return filename
    ext = Path(urlsplit(url).path).suffix.lower()
    return filename + ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else filename


def _result(data: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "structuredContent": data, "isError": False}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# ---------------------------------------------------------------------- 工具實作
class DivebirdTools:
    def __init__(self, backend: Backend):
        self.backend = backend
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}            # task_id -> {"task", "state", "t"}
        self._calls: deque[float] = deque()
        self._probe_slots = threading.BoundedSemaphore(2)
        self._probes: dict[tuple, dict] = {}
        self.browser = BrowserMedia()
        self._handlers: dict[str, Callable[[dict, CallContext], Any]] = {
            d["name"]: getattr(self, "_" + d["name"]) for d in TOOL_DEFINITIONS}

    # ------------------------------------------------------------------ 給協定層
    def definitions(self) -> list[dict]:
        return json.loads(json.dumps(TOOL_DEFINITIONS))     # 複本，避免被外部修改

    def call(self, name: str, arguments: dict, ctx: CallContext) -> dict:
        if not self._rate_ok():
            return _error(f"呼叫太頻繁：每分鐘最多 {RATE_LIMIT} 次，請稍候再試。")
        try:
            return _result(self._handlers[name](arguments, ctx))
        except ToolError as e:
            return _error(str(e))

    # ------------------------------------------------------------------ 給 GUI（確認視窗的結果）
    def confirmation_done(self, task_id: str, accepted: bool) -> None:
        with self._lock:
            entry = self._pending.get(task_id)
            if not entry:
                return
            if accepted:
                del self._pending[task_id]
            else:
                entry["state"], entry["t"] = REJECTED, time.monotonic()

    # ------------------------------------------------------------------ 共用
    @property
    def settings(self) -> Settings:
        return self.backend.settings

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= RATE_LIMIT:
                return False
            self._calls.append(now)
            # 順便清掉過期的「已取消」紀錄與分析快取
            for tid in [k for k, v in self._pending.items() if v["state"] == REJECTED and now - v["t"] > 600]:
                del self._pending[tid]
            for key in [k for k, v in self._probes.items() if now - v["t"] > PROBE_CACHE_SECONDS]:
                del self._probes[key]
        return True

    def _check_target(self, url: str) -> None:
        if self.settings.mcp_allow_private:
            return
        try:
            check_url(url)
        except BlockedAddress as e:
            raise ToolError(f"{e}。Divebird 設定不允許 AI 下載內網或本機位址。") from None
        except (OSError, UnicodeError) as e:
            raise ToolError(f"無法解析網址的主機名稱：{e}") from None

    def _quality(self, args: dict) -> str:
        value = _string(args, "quality", max_len=300)
        if not value:
            return ""
        if value.lower() in QUALITY_PRESETS:
            return QUALITY_PRESETS[value.lower()]
        if not value.isprintable():
            raise ToolError("quality 含有不合法的字元。")
        return value        # probe_url 回傳的 yt-dlp 格式字串

    def _snapshot(self, task_id: str) -> dict:
        with self._lock:
            entry = self._pending.get(task_id)
        task = self.backend.manager.get(task_id)
        if task is None and entry is None:
            request = self.browser.state(task_id)
            if request:
                return self._browser_snapshot(request)
            raise ToolError(f"找不到任務 {task_id}（可能已被移除）。可以用 list_downloads 查看目前的任務。")
        status = task.status if task is not None else entry["state"]
        t = task or entry["task"]
        total = t.total if t.total > 0 else None
        active = status in Status.ACTIVE
        data = {
            "task_id": t.id, "status": status, "kind": t.kind,
            "url": t.url[:500], "title": t.title[:200] or None, "filename": t.filename or None,
            "save_dir": t.save_dir,
            "downloaded_bytes": t.downloaded, "total_bytes": total,
            "progress_percent": 100.0 if status == Status.COMPLETED else
            (round(t.progress * 100, 1) if total else None),
            "speed_bytes_per_sec": round(t.speed) if active else 0,
            "eta_seconds": round(t.eta) if active and t.eta >= 0 else None,
            "error": t.error or None,
            "source": "ai" if t.source.startswith("mcp") else "user",
        }
        if status == Status.COMPLETED and t.filename:
            data["path"] = str(t.filepath)
        if status == AWAITING:
            data["message"] = "等待使用者在 Divebird 視窗確認。"
        elif status == REJECTED:
            data["message"] = "使用者在 Divebird 取消了這個下載。"
        return data

    # ------------------------------------------------------------------ get_status
    def _get_status(self, args: dict, ctx: CallContext) -> dict:
        s = self.settings
        counts: dict[str, int] = {}
        for t in list(self.backend.manager.tasks.values()):
            counts[t.status] = counts.get(t.status, 0) + 1
        with self._lock:
            awaiting = sum(1 for v in self._pending.values() if v["state"] == AWAITING)
        return {
            "app": "Divebird", "version": __version__, "download_dir": s.download_dir,
            "permissions": {
                "confirm_each_download": s.mcp_confirm != "never",
                "subfolders": s.mcp_allow_subdir,
                "cookies_and_login_headers": s.mcp_allow_cookies,
                "private_network": s.mcp_allow_private,
                "delete_files": s.mcp_allow_delete,
                "browser_media": s.mcp_share_browser_media,
            },
            "ffmpeg_available": bool(ffmpeg_path(s.ffmpeg_path)),
            "downloads": {
                "active": counts.get(Status.DOWNLOADING, 0) + counts.get(Status.PROCESSING, 0),
                "queued": counts.get(Status.QUEUED, 0), "paused": counts.get(Status.PAUSED, 0),
                "completed": counts.get(Status.COMPLETED, 0), "error": counts.get(Status.ERROR, 0),
                "awaiting_confirmation": awaiting,
            },
        }

    # ------------------------------------------------------------------ download
    def _download(self, args: dict, ctx: CallContext) -> dict:
        s = self.settings
        url = _http_url(_string(args, "url", required=True, max_len=8000), "url")
        self._check_target(url)
        referer = _string(args, "referer", max_len=8000)
        if referer:
            _http_url(referer, "referer")
        headers = _headers(args.get("headers"), s.mcp_allow_cookies)
        cookies = _cookies(args.get("cookies"), s.mcp_allow_cookies)
        kind = _choice(args, "kind", "auto", ("auto", "file", "media"))
        options = self._download_options(args)
        quality, save_dir, start = options["quality"], options["save_dir"], options["start"]

        task = task_from_payload({"url": url, "referer": referer, "headers": headers, "cookies": cookies,
                                  "filename": options["filename"]}, s)
        task.restrict_private = not s.mcp_allow_private     # 先設定：下面的探測也不可轉址到內網
        if kind != "auto":
            task.kind = Kind.MEDIA if kind == "media" else Kind.HTTP
        elif task.kind == Kind.HTTP:
            task.kind = self._guess_kind(task)
        if quality:
            task.media_format = quality
        if task.kind == Kind.HTTP:
            task.filename = _with_extension(task.filename, url)
        task.save_dir = save_dir
        task.source = f"mcp:{ctx.client}"

        with self._lock:
            if s.mcp_confirm != "never" and \
                    sum(1 for v in self._pending.values() if v["state"] == AWAITING) >= MAX_PENDING:
                raise ToolError(f"已有 {MAX_PENDING} 個下載在等使用者於 Divebird 確認，請等使用者處理後再試。")
        status, message = self._submit(task, start, ctx.client)
        return {"task_id": task.id, "status": status, "kind": task.kind, "filename": task.filename or None,
                "save_dir": task.save_dir, "message": message,
                "next_step": "用 get_download 查詢進度（wait_seconds 最多 25 秒）。"}

    def _submit(self, task: Task, start: bool, client: str) -> tuple[str, str]:
        """依「AI 發起的下載要不要確認」的設定，跳出確認視窗或直接加入下載清單。"""
        if self.settings.mcp_confirm != "never":
            with self._lock:
                self._pending[task.id] = {"task": task, "state": AWAITING, "t": time.monotonic()}
            self.backend.confirm(task, start, client)
            return AWAITING, "已在 Divebird 跳出確認視窗，使用者確認後就會開始下載。"
        self.backend.manager.add(task, start)
        self.backend.announce("AI 已新增下載", task.filename or task.title or task.url)
        return task.status, "已加入 Divebird 的下載清單。"

    def _download_options(self, args: dict) -> dict:
        """filename／quality／subdir／start：download 與 download_browser_media 共用的檢查。"""
        s = self.settings
        start = args.get("start", True)
        if not isinstance(start, bool):
            raise ToolError("start 必須是 true 或 false。")
        save_dir = s.download_dir
        subdir = _string(args, "subdir", max_len=500)
        if subdir:
            if not s.mcp_allow_subdir:
                raise ToolError("Divebird 設定不允許 AI 指定子資料夾，請省略 subdir。")
            save_dir = str(_safe_subdir(s.download_dir, subdir))
        return {"filename": _string(args, "filename", max_len=255), "quality": self._quality(args),
                "save_dir": save_dir, "start": start}

    def _guess_kind(self, task: Task) -> str:
        """沒有副檔名可判斷時探測一下：播放清單或一般網頁交給 yt-dlp（AI 要的通常是網頁裡的影片，不是 HTML）。"""
        session = make_session(task)
        try:
            res = probe(session, task.url, timeout=(5, 10))
        except BlockedAddress as e:
            raise ToolError(f"{e}。Divebird 設定不允許 AI 下載內網或本機位址。") from None
        except Exception:  # noqa: BLE001 - 探測失敗就照一般檔案處理，下載時會回報真正的錯誤
            return Kind.HTTP
        finally:
            session.close()
        if res.manifest or res.content_type.split(";")[0].strip().lower() in ("text/html", "application/xhtml+xml"):
            return Kind.MEDIA
        return Kind.HTTP

    # ------------------------------------------------------------------ get_download / list / control / remove
    def _get_download(self, args: dict, ctx: CallContext) -> dict:
        task_id = _string(args, "task_id", required=True, max_len=64)
        deadline = time.monotonic() + _int(args, "wait_seconds", 0, 0, MAX_WAIT)
        while True:
            data = self._snapshot(task_id)
            if data["status"] in TERMINAL or time.monotonic() >= deadline:
                return data
            time.sleep(0.25)

    def _list_downloads(self, args: dict, ctx: CallContext) -> dict:
        status = _choice(args, "status", "all", ("all", "active", "queued", "paused", "completed", "error"))
        source = _choice(args, "source", "all", ("all", "mcp"))
        limit = _int(args, "limit", 20, 1, 50)
        offset = _int(args, "offset", 0, 0, 1_000_000)
        tasks = sorted(self.backend.manager.tasks.values(), key=lambda t: t.created_at, reverse=True)
        with self._lock:
            pending = [v["task"] for v in self._pending.values() if v["state"] == AWAITING]
        if status == "all":
            tasks = pending + tasks
        elif status == "active":
            tasks = [t for t in tasks if t.status in Status.ACTIVE]
        else:
            tasks = [t for t in tasks if t.status == status]
        if source == "mcp":
            tasks = [t for t in tasks if t.source.startswith("mcp")]
        items = []
        for t in tasks[offset:offset + limit]:
            snap = self._snapshot(t.id)
            items.append({k: snap[k] for k in ("task_id", "status", "filename", "title", "progress_percent",
                                               "total_bytes", "source") if snap.get(k) is not None})
        return {"total": len(tasks), "offset": offset, "items": items}

    def _require_task(self, task_id: str) -> Task:
        with self._lock:
            entry = self._pending.get(task_id)
        if entry and entry["state"] == AWAITING:
            raise ToolError("這個下載還在等使用者於 Divebird 確認。")
        task = self.backend.manager.get(task_id)
        if task is None:
            raise ToolError(f"找不到任務 {task_id}（可能已被移除或被使用者取消）。")
        return task

    def _control_download(self, args: dict, ctx: CallContext) -> dict:
        task_id = _string(args, "task_id", required=True, max_len=64)
        action = _choice(args, "action", "", ("pause", "resume"))
        task = self._require_task(task_id)
        if action == "pause":
            if task.status == Status.PROCESSING:
                raise ToolError("正在合併影音，暫時不能暫停，請稍候。")
            self.backend.manager.pause(task_id)
        else:
            if task.status == Status.COMPLETED:
                raise ToolError("這個下載已經完成了。")
            self.backend.manager.start(task_id)
        return self._snapshot(task_id)

    def _remove_download(self, args: dict, ctx: CallContext) -> dict:
        task_id = _string(args, "task_id", required=True, max_len=64)
        delete_file = args.get("delete_file", False)
        if not isinstance(delete_file, bool):
            raise ToolError("delete_file 必須是 true 或 false。")
        if delete_file and not self.settings.mcp_allow_delete:
            raise ToolError("Divebird 設定不允許 AI 刪除檔案。可以省略 delete_file，只從清單移除。")
        with self._lock:
            entry = self._pending.get(task_id)
            if entry and entry["state"] == REJECTED:
                del self._pending[task_id]
                return {"task_id": task_id, "removed": True, "file_deleted": False}
        task = self._require_task(task_id)
        completed = task.status == Status.COMPLETED
        self.backend.manager.remove(task_id, delete_files=delete_file)
        return {"task_id": task_id, "removed": True, "file_deleted": bool(delete_file and completed)}

    # ------------------------------------------------------------------ 瀏覽器偵測到的影音
    def _require_sharing(self) -> None:
        if not self.settings.mcp_share_browser_media:
            raise ToolError("使用者沒有開放這項功能：請使用者在 Divebird「設定 → AI 整合」勾選"
                            "「提供瀏覽器擴充功能偵測到的影音給 AI」。")

    def _list_browser_media(self, args: dict, ctx: CallContext) -> dict:
        self._require_sharing()
        tabs = self.browser.listing(_string(args, "query", max_len=200))[: _int(args, "limit", 5, 1, 20)]
        result: dict[str, Any] = {"tabs": tabs}
        if not tabs:
            result["message"] = ("目前沒有偵測到影音。請使用者在瀏覽器播放影片後再試，並確認 Divebird 的瀏覽器擴充功能"
                                 "已更新到支援 AI 的版本（1.1.0 以上）。")
        return result

    def _download_browser_media(self, args: dict, ctx: CallContext) -> dict:
        self._require_sharing()
        media_id = _string(args, "media_id", required=True, max_len=64)
        found = self.browser.find(media_id)
        if not found:
            raise ToolError("找不到這個 media_id（分頁可能已關閉或換頁）。請重新呼叫 list_browser_media。")
        self._check_target(found[1]["url"])
        options = self._download_options(args)
        task_id = uuid.uuid4().hex[:12]
        self.browser.request(task_id, media_id, options, ctx.client)
        return {"task_id": task_id, "status": WAITING_FOR_BROWSER,
                "message": "已交給瀏覽器擴充功能補上登入資訊，通常 30 秒內開始。",
                "next_step": "用 get_download 查詢進度（wait_seconds 最多 25 秒）。"}

    def fulfill_browser_request(self, payload: dict) -> bool:
        """擴充功能送來 AI 指定的下載（含 Cookie 與 Referer）：套用 AI 的選項後送出。由 API 執行緒呼叫。"""
        request = self.browser.take(str(payload.get("mcp_request") or ""))
        if not request:
            return False
        s, options = self.settings, request["options"]
        payload = dict(payload)
        if options["filename"]:
            payload["filename"] = options["filename"]
        task = task_from_payload(payload, s)
        task.id = request["request_id"]
        if options["quality"]:
            task.media_format = options["quality"]
        if task.kind == Kind.HTTP:
            task.filename = _with_extension(task.filename, task.url)
        task.save_dir = options["save_dir"]
        task.source = f"mcp:{request['client']}"
        task.restrict_private = not s.mcp_allow_private
        self._submit(task, options["start"], request["client"])
        return True

    def _browser_snapshot(self, request: dict) -> dict:
        url = urlsplit(request["url"])
        data = {"task_id": request["request_id"], "status": WAITING_FOR_BROWSER, "kind": None,
                "url": f"{url.scheme}://{url.netloc}{url.path}", "title": request["title"] or None,
                "filename": request["options"]["filename"] or None, "save_dir": request["options"]["save_dir"],
                "downloaded_bytes": 0, "total_bytes": None, "progress_percent": None, "speed_bytes_per_sec": 0,
                "eta_seconds": None, "error": None, "source": "ai",
                "message": "等瀏覽器擴充功能補上登入資訊（通常 30 秒內）。"}
        if request["state"] == EXPIRED:
            data["status"] = Status.ERROR
            data["error"] = ("瀏覽器擴充功能沒有回應：分頁可能已關閉，或擴充功能還沒更新到支援 AI 的版本（1.1.0 以上）。"
                             "可以改用 download 直接下載網址。")
            data.pop("message")
        return data

    # ------------------------------------------------------------------ probe_url
    def _probe_url(self, args: dict, ctx: CallContext) -> dict:
        s = self.settings
        url = _http_url(_string(args, "url", required=True, max_len=8000), "url")
        self._check_target(url)
        referer = _string(args, "referer", max_len=8000)
        if referer:
            _http_url(referer, "referer")
        headers = _headers(args.get("headers"), s.mcp_allow_cookies)
        key = (url, referer, tuple(sorted(headers.items())))
        with self._lock:
            entry = self._probes.get(key)
            if entry is None:
                entry = {"t": time.monotonic(), "done": threading.Event(), "result": None, "error": None}
                self._probes[key] = entry
                task = Task(url=url, referer=referer, headers=headers, save_dir=s.download_dir,
                            restrict_private=not s.mcp_allow_private)
                threading.Thread(target=self._analyze, args=(task, entry), daemon=True, name="mcp-probe").start()
        if not entry["done"].wait(MAX_WAIT):
            return {"status": "analyzing", "message": "還在解析（大型影音網站可能需要較久），請稍後再呼叫一次 probe_url。"}
        if entry["error"]:
            raise ToolError(f"無法分析這個網址：{entry['error']}")
        return entry["result"]

    def _analyze(self, task: Task, entry: dict) -> None:
        try:
            with self._probe_slots:
                entry["result"] = self._analyze_now(task)
        except BlockedAddress as e:
            entry["error"] = f"{e}（Divebird 設定不允許內網或本機位址）"
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e) or e.__class__.__name__
        finally:
            entry["done"].set()

    def _analyze_now(self, task: Task) -> dict:
        s = self.settings
        if is_manifest_url(task.url) or is_media_site(task.url):
            return self._media_result(extract_info(task, s))
        session = make_session(task)
        try:
            res = probe(session, task.url, timeout=(10, 20))
        finally:
            session.close()
        ctype = res.content_type.split(";")[0].strip().lower()
        if res.manifest:
            return self._media_result(extract_info(task, s))
        if ctype in ("text/html", "application/xhtml+xml"):
            try:
                return self._media_result(extract_info(task, s))
            except DownloadError:
                return {"status": "ok", "kind": "page", "content_type": ctype,
                        "message": "這是一般網頁，沒有找到可下載的影片。請改用瀏覽器擴充功能的下載按鈕，"
                                   "或找出影片的串流網址（.m3u8／.mpd）後再呼叫 download。"}
        return {"status": "ok", "kind": "file", "filename": res.filename,
                "size_bytes": res.total if res.total >= 0 else None,
                "content_type": res.content_type or None, "resumable": res.resumable}

    def _media_result(self, info: dict) -> dict:
        formats = info.get("formats") or []
        drm = bool(info.get("_has_drm")) or any(f.get("has_drm") for f in formats if isinstance(f, dict))
        warnings = []
        if info.get("is_live"):
            warnings.append("這是直播，Divebird 目前不建議下載直播。")
        if drm:
            warnings.append("偵測到 DRM 加密，無法下載。")
        title = str(info.get("title") or "")[:200]
        return {
            "status": "ok", "kind": "media", "title": title or None,
            "site": info.get("extractor_key") if info.get("extractor_key") != "Generic" else None,
            "duration_seconds": info.get("duration"),
            "is_live": bool(info.get("is_live")), "drm": drm,
            "qualities": [{"quality": fmt, "label": label} for label, fmt in format_choices(info)],
            "suggested_filename": (sanitize_filename(title) + "." + (self.settings.merge_format or "mp4"))
            if title else None,
            "warnings": warnings,
        }
