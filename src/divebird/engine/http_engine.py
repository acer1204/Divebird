"""多連線分段下載引擎（仿 IDM 的動態分段）。

運作方式：
1. 先以 ``Range: bytes=0-`` 探測檔案大小與是否支援續傳。
2. 支援續傳時，預先配置 ``<檔名>.part``，切成數個分段，每段一條連線並行下載，
   直接寫入檔案中對應的位移。
3. 某條連線完成自己的分段後，會把「剩餘最多」的分段從中間切開接手後半段
   （動態分段），讓所有連線持續滿載直到檔案完成。
4. 進度定期寫入 ``<檔名>.part.json``，暫停 / 程式關閉 / 斷線後可從原處續傳。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from requests.adapters import HTTPAdapter

from ..models import Task
from ..utils import (
    cookies_to_jar,
    ensure_extension,
    filename_from_content_disposition,
    filename_from_url,
    sanitize_filename,
    unique_path,
)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
CHUNK = 64 * 1024
MIN_SPLIT = 512 * 1024          # 剩餘量至少 2 倍於此才會再切分
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 30
STATE_SAVE_INTERVAL = 2.0
REPORT_INTERVAL = 0.5


class DownloadError(Exception):
    pass


@dataclass
class ProbeResult:
    final_url: str
    total: int                 # -1 = 未知
    resumable: bool
    filename: str
    content_type: str
    etag: str = ""
    last_modified: str = ""


@dataclass
class Segment:
    start: int
    end: int          # 含
    pos: int          # 下一個待寫入的位元組
    active: bool = False

    @property
    def remaining(self) -> int:
        return self.end + 1 - self.pos

    @property
    def done(self) -> bool:
        return self.pos > self.end


class RateLimiter:
    """全域限速（token bucket），所有任務共用。"""

    def __init__(self, bytes_per_sec: int = 0):
        self._lock = threading.Lock()
        self.rate = bytes_per_sec
        self._allowance = 0.0
        self._last = time.monotonic()

    def set_rate(self, bytes_per_sec: int) -> None:
        with self._lock:
            self.rate = max(0, int(bytes_per_sec))
            self._allowance = 0.0

    def consume(self, n: int, stop: threading.Event | None = None) -> None:
        if self.rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self._allowance = min(self.rate, self._allowance + (now - self._last) * self.rate)
            self._last = now
            self._allowance -= n
            wait = -self._allowance / self.rate if self._allowance < 0 else 0
        if wait > 0:
            (stop.wait(wait) if stop else time.sleep(wait))


def make_session(task: Task, pool_size: int = 16) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = task.user_agent or DEFAULT_UA
    s.headers["Accept"] = "*/*"
    # 不要壓縮，否則 Content-Length / Range 會對不上
    s.headers["Accept-Encoding"] = "identity"
    if task.referer:
        s.headers["Referer"] = task.referer
    for k, v in (task.headers or {}).items():
        if k.lower() not in ("range", "cookie", "host", "content-length"):
            s.headers[k] = v
    if task.cookies:
        s.cookies = cookies_to_jar(task.cookies)
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=pool_size, max_retries=0)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def _is_permanent(e: Exception) -> bool:
    """4xx（逾時 408、過多請求 429 除外）視為重試也無用的錯誤。"""
    resp = getattr(e, "response", None)
    return (isinstance(e, requests.HTTPError) and resp is not None
            and 400 <= resp.status_code < 500 and resp.status_code not in (408, 429))


def _close_all(responses) -> None:
    for r in responses:
        try:
            r.close()
        except Exception:  # noqa: BLE001
            pass


_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.I)


def probe(session: requests.Session, url: str) -> ProbeResult:
    """探測檔案資訊（大小、檔名、是否可分段）。"""
    resp = session.get(url, headers={"Range": "bytes=0-"}, stream=True,
                       timeout=(CONNECT_TIMEOUT, READ_TIMEOUT), allow_redirects=True)
    try:
        if resp.status_code == 416:   # 空檔案或伺服器不接受 Range
            resp.close()
            resp = session.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        resp.raise_for_status()
        h = resp.headers
        total, resumable = -1, False
        m = _CONTENT_RANGE.match(h.get("Content-Range", ""))
        if resp.status_code == 206 and m:
            if m.group(3) != "*":
                total = int(m.group(3))
                resumable = total > 0
        elif "Content-Length" in h and not h.get("Content-Encoding"):
            total = int(h["Content-Length"])
        content_type = h.get("Content-Type", "")
        name = filename_from_content_disposition(h.get("Content-Disposition")) or filename_from_url(resp.url)
        name = ensure_extension(sanitize_filename(name), content_type)
        return ProbeResult(
            final_url=resp.url, total=total, resumable=resumable, filename=name,
            content_type=content_type, etag=h.get("ETag", ""), last_modified=h.get("Last-Modified", ""),
        )
    finally:
        resp.close()


class HttpDownloader:
    def __init__(self, task: Task, *, connections: int = 8, max_retries: int = 8,
                 limiter: RateLimiter | None = None,
                 on_update: Callable[[Task], None] | None = None,
                 name_reserver: Callable[[Path], Path] | None = None):
        self.task = task
        # 決定最終檔名：預設只檢查磁碟；DownloadManager 會傳入也避開其他任務已佔用名稱的版本
        self.reserve_name = name_reserver or unique_path
        self.connections = max(1, connections)
        self.max_retries = max_retries
        self.limiter = limiter or RateLimiter()
        self.on_update = on_update or (lambda t: None)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._responses: set[requests.Response] = set()
        self._errors: list[Exception] = []
        self._fatal: Exception | None = None
        self._etag = ""
        self._active = 0
        self._conn_limit = self.connections   # 伺服器限制連線數時會自動下修
        self._file = None
        self.segments: list[Segment] = []
        self._samples: deque[tuple[float, int]] = deque(maxlen=12)

    # ---------------------------------------------------------------- 控制
    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            responses = list(self._responses)
        if responses:
            # 連線卡住時 close() 會等到讀取逾時（約 30 秒）才返回：交給背景執行緒，避免卡住 GUI
            threading.Thread(target=_close_all, args=(responses,), daemon=True).start()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # ---------------------------------------------------------------- 主流程
    def run(self) -> bool:
        """執行下載（阻塞）。完成回傳 True，被暫停回傳 False，失敗拋出例外。"""
        task = self.task
        session = make_session(task, pool_size=self.connections + 2)
        try:
            info = probe(session, task.url)
            if self.stopped:
                return False
            Path(task.save_dir).mkdir(parents=True, exist_ok=True)
            if not task.filename:
                task.filename = self.reserve_name(Path(task.save_dir) / info.filename).name
            final = task.filepath
            part = final.with_name(final.name + ".part")
            state_file = final.with_name(final.name + ".part.json")
            if final.exists() and not part.exists():
                final = self.reserve_name(final)
                task.filename = final.name
                part = final.with_name(final.name + ".part")
                state_file = final.with_name(final.name + ".part.json")

            task.total = info.total if info.total > 0 else 0
            task.resumable = info.resumable

            if info.total > 0:
                free = shutil.disk_usage(task.save_dir).free
                existing = part.stat().st_size if part.exists() else 0
                if info.total - existing > free:
                    raise DownloadError("磁碟空間不足")

            if info.resumable:
                self._prepare_segments(info, part, state_file)
                self._run_segmented(session, info.final_url, part, state_file)
            else:
                self._run_single(session, info.final_url, part)

            if self.stopped:
                return False

            if final.exists():
                final = self.reserve_name(final)
                task.filename = final.name
            self._replace_with_retry(part, final)
            state_file.unlink(missing_ok=True)
            task.downloaded = task.total = final.stat().st_size
            return True
        finally:
            session.close()

    def _replace_with_retry(self, src: Path, dst: Path) -> None:
        for i in range(10):
            try:
                os.replace(src, dst)
                return
            except PermissionError:   # Windows：防毒軟體可能暫時鎖住檔案
                if i == 9:
                    raise
                time.sleep(0.5)

    # ---------------------------------------------------------------- 分段
    def _prepare_segments(self, info: ProbeResult, part: Path, state_file: Path) -> None:
        total = info.total
        state = None
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if (state and part.exists() and state.get("total") == total and part.stat().st_size == total
                and state.get("url", self.task.url) == self.task.url
                and (not info.etag or not state.get("etag") or state["etag"] == info.etag)):
            self.segments = [Segment(s, e, p) for s, e, p in state["segments"]]
            self._etag = state.get("etag", "")
            return
        # 全新下載：預先配置檔案
        with open(part, "wb") as f:
            f.truncate(total)
        n = max(1, min(self.connections, total // MIN_SPLIT))
        size = total // n
        self.segments = []
        for i in range(n):
            start = i * size
            end = total - 1 if i == n - 1 else (i + 1) * size - 1
            self.segments.append(Segment(start, end, start))
        self._etag = info.etag
        self._save_state(state_file)

    def _save_state(self, state_file: Path) -> None:
        with self._lock:
            if self._file:
                self._file.flush()
            data = {
                "url": self.task.url,
                "total": self.task.total,
                "etag": self._etag,
                "segments": [[s.start, s.end, s.pos] for s in self.segments if not s.done],
            }
        tmp = state_file.with_name(state_file.name + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, state_file)

    def _downloaded(self) -> int:
        with self._lock:
            return self.task.total - sum(s.remaining for s in self.segments if not s.done)

    def _next_segment(self) -> Segment | None:
        """取得一個待下載分段：優先拿無人負責的，否則把最大的分段切半接手。"""
        with self._lock:
            for s in self.segments:
                if not s.done and not s.active:
                    s.active = True
                    return s
            busy = [s for s in self.segments if s.active and not s.done]
            if not busy:
                return None
            best = max(busy, key=lambda s: s.remaining)
            if best.remaining < 2 * MIN_SPLIT:
                return None
            mid = best.pos + best.remaining // 2
            new = Segment(mid, best.end, mid, active=True)
            best.end = mid - 1
            self.segments.append(new)
            return new

    def _spawn_workers(self, session, url) -> None:
        while not self.stopped:
            with self._lock:
                if self._active >= self._conn_limit:
                    return
            seg = self._next_segment()
            if seg is None:
                return
            with self._lock:
                self._active += 1
            threading.Thread(target=self._worker, args=(session, url, seg), daemon=True).start()

    def _run_segmented(self, session, url, part: Path, state_file: Path) -> None:
        task = self.task
        self._file = open(part, "r+b")
        try:
            self._spawn_workers(session, url)
            last_save = time.monotonic()
            last_progress = self._downloaded()
            while True:
                self._wake.wait(REPORT_INTERVAL)
                self._wake.clear()
                if self.stopped:
                    break
                with self._lock:
                    all_done = all(s.done for s in self.segments)
                    active = self._active
                if all_done:
                    break
                if self._fatal is not None:
                    raise self._fatal
                downloaded = self._downloaded()
                with self._lock:
                    if downloaded > last_progress and self._errors:
                        # 有連線放棄、但其餘連線仍有進度：多半是伺服器限制同時連線數（如 503/429），
                        # 下修連線數上限並重新計算錯誤，避免把仍在進行的下載誤判為失敗
                        self._conn_limit = max(1, min(self._conn_limit, active))
                        self._errors.clear()
                    errors = list(self._errors)
                last_progress = max(last_progress, downloaded)
                if len(errors) >= max(3, self._conn_limit):
                    raise errors[-1]
                if active < self._conn_limit:
                    self._spawn_workers(session, url)
                    with self._lock:
                        active = self._active
                        # 上面檢查 all_done 之後，最後一條連線可能剛好完成並結束：重新確認，避免誤判為斷線
                        all_done = all(s.done for s in self.segments)
                    if all_done:
                        break
                    if active == 0:
                        raise errors[-1] if errors else DownloadError("所有連線皆已中斷")
                self._report()
                if time.monotonic() - last_save >= STATE_SAVE_INTERVAL:
                    self._save_state(state_file)
                    last_save = time.monotonic()
            # 等待所有連線結束
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with self._lock:
                    if self._active == 0:
                        break
                time.sleep(0.05)
        except BaseException:
            self.stop()     # 發生致命錯誤：中止其餘連線
            raise
        finally:
            with self._lock:
                f, self._file = self._file, None
            f.flush()
            f.close()
            if not all(s.done for s in self.segments):
                self._save_state(state_file)
            self._report()
        task.active_connections = 0

    def _worker(self, session: requests.Session, url: str, seg: Segment) -> None:
        retries = 0
        try:
            while seg is not None and not self.stopped:
                with self._lock:
                    if seg.done:
                        start = end = None
                    else:
                        start, end = seg.pos, seg.end
                if start is not None:
                    resp = None
                    try:
                        resp = session.get(url, headers={"Range": f"bytes={start}-{end}"}, stream=True,
                                           timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
                        with self._lock:
                            self._responses.add(resp)
                        if self.stopped:
                            break
                        if resp.status_code != 206:
                            resp.raise_for_status()
                            raise DownloadError(f"伺服器未回應分段內容（HTTP {resp.status_code}）")
                        for chunk in resp.iter_content(CHUNK):
                            if self.stopped:
                                break
                            with self._lock:
                                n = min(len(chunk), seg.end + 1 - seg.pos)
                                if n > 0 and self._file:
                                    self._file.seek(seg.pos)
                                    self._file.write(chunk if n == len(chunk) else chunk[:n])
                                    seg.pos += n
                            if n < len(chunk):
                                break       # 此分段已被其他連線切走後半，提早結束
                            retries = 0
                            self.limiter.consume(n, self._stop)
                        if not self.stopped and not seg.done:
                            raise DownloadError("連線提早中斷")
                    except Exception as e:  # noqa: BLE001
                        if self.stopped:
                            break
                        retries += 1
                        if retries > self.max_retries or _is_permanent(e):
                            raise
                        self._stop.wait(min(20.0, 0.5 * 2 ** retries))
                        continue
                    finally:
                        if resp is not None:
                            with self._lock:
                                self._responses.discard(resp)
                            resp.close()
                # 自己的分段完成 → 接手其他分段
                with self._lock:
                    seg.active = False
                seg = None if self.stopped else self._next_segment()
                retries = 0
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._errors.append(e)
                if _is_permanent(e):
                    self._fatal = e     # 權限 / 不存在等永久性錯誤，不再重試
        finally:
            with self._lock:
                if seg is not None:
                    seg.active = False
                self._active -= 1
            self._wake.set()

    # ---------------------------------------------------------------- 單連線
    def _run_single(self, session, url, part: Path) -> None:
        """伺服器不支援 Range：只能單連線下載，且無法續傳。"""
        task = self.task
        task.downloaded = 0
        resp = session.get(url, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        with self._lock:
            self._responses.add(resp)
        try:
            resp.raise_for_status()
            task.active_connections = 1
            last = 0.0
            with open(part, "wb") as f:
                for chunk in resp.iter_content(CHUNK):
                    if self.stopped:
                        break
                    f.write(chunk)
                    task.downloaded += len(chunk)
                    self.limiter.consume(len(chunk), self._stop)
                    now = time.monotonic()
                    if now - last >= REPORT_INTERVAL:
                        last = now
                        self._report(task.downloaded)
            if not self.stopped and task.total > 0 and task.downloaded < task.total:
                raise DownloadError("連線中斷，且伺服器不支援續傳")
        except requests.RequestException:
            if not self.stopped:
                raise
        finally:
            with self._lock:
                self._responses.discard(resp)
            resp.close()
            task.active_connections = 0
        if self.stopped:
            part.unlink(missing_ok=True)

    # ---------------------------------------------------------------- 進度
    def _report(self, downloaded: int | None = None) -> None:
        task = self.task
        if downloaded is None:
            downloaded = self._downloaded()
            with self._lock:
                task.active_connections = self._active
        task.downloaded = downloaded
        now = time.monotonic()
        self._samples.append((now, downloaded))
        t0, d0 = self._samples[0]
        task.speed = (downloaded - d0) / (now - t0) if now - t0 > 0.2 else 0.0
        self.on_update(task)
