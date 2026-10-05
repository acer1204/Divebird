from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


class Status:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    PROCESSING = "processing"   # 影音合併 / 後處理
    COMPLETED = "completed"
    ERROR = "error"

    ACTIVE = (DOWNLOADING, PROCESSING)
    LABELS = {
        QUEUED: "排隊中",
        DOWNLOADING: "下載中",
        PAUSED: "已暫停",
        PROCESSING: "處理中",
        COMPLETED: "已完成",
        ERROR: "錯誤",
    }


class Kind:
    HTTP = "http"     # 一般檔案：多連線分段下載
    MEDIA = "media"   # 串流影音（HLS/DASH/影音網站）：交給 yt-dlp


@dataclass
class Task:
    url: str
    kind: str = Kind.HTTP
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    filename: str = ""
    save_dir: str = ""
    status: str = Status.QUEUED
    total: int = 0
    downloaded: int = 0
    speed: float = 0.0
    error: str = ""
    title: str = ""
    page_url: str = ""
    referer: str = ""
    user_agent: str = ""
    headers: dict = field(default_factory=dict)
    cookies: list = field(default_factory=list)
    media_format: str = ""          # yt-dlp format 選擇字串，空白 = 使用設定值
    source: str = ""                # 任務來源：空白 = 使用者或擴充功能；"mcp:<工具名稱>" = AI 透過 MCP 發起
    restrict_private: bool = False  # 不可連到內網或本機位址（AI 發起、未開放內網時）
    resumable: bool = False
    active_connections: int = 0
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    @property
    def filepath(self) -> Path:
        return Path(self.save_dir) / self.filename

    @property
    def progress(self) -> float:
        if self.status == Status.COMPLETED:
            return 1.0
        if self.total > 0:
            return min(1.0, self.downloaded / self.total)
        return 0.0

    @property
    def eta(self) -> float:
        if self.speed > 0 and self.total > 0:
            return max(0.0, (self.total - self.downloaded) / self.speed)
        return -1

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("speed", None)
        d.pop("active_connections", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})
