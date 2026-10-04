from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..models import Kind, Status, Task
from ..utils import human_size, human_time
from . import icons

TASK_ROLE = Qt.ItemDataRole.UserRole + 1
SORT_ROLE = Qt.ItemDataRole.UserRole + 2

COL_NAME, COL_SIZE, COL_PROGRESS, COL_SPEED, COL_ETA, COL_STATUS, COL_ADDED = range(7)
HEADERS = ["檔案名稱", "大小", "進度", "速度", "剩餘時間", "狀態", "加入時間"]

CATEGORY_EXT = {
    "video": {"mp4", "mkv", "webm", "avi", "mov", "flv", "wmv", "m4v", "ts", "3gp", "mpg", "mpeg", "m3u8"},
    "music": {"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "wma", "ape"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "zst", "tgz"},
    "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "epub", "csv", "odt", "md"},
    "program": {"exe", "msi", "deb", "rpm", "appimage", "apk", "dmg", "sh", "run", "bat", "msix"},
    "image": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "avif", "heic"},
}

CATEGORIES = [
    ("all", "全部下載", "all"),
    ("active", "下載中", "downloading"),
    ("unfinished", "未完成", "unfinished"),
    ("completed", "已完成", "completed"),
    ("video", "影片", "video"),
    ("music", "音樂", "music"),
    ("archive", "壓縮檔", "archive"),
    ("document", "文件", "document"),
    ("program", "程式", "program"),
]

STATUS_COLORS = {
    Status.DOWNLOADING: "#2563eb",
    Status.PROCESSING: "#7c3aed",
    Status.COMPLETED: "#16a34a",
    Status.PAUSED: "#94a3b8",
    Status.QUEUED: "#64748b",
    Status.ERROR: "#dc2626",
}


def display_name(t: Task) -> str:
    if t.filename:
        return t.filename
    if t.title:
        return t.title
    return Path(urlsplit(t.url).path).name or t.url


def file_category(t: Task) -> str:
    ext = Path(display_name(t)).suffix.lower().lstrip(".")
    for cat, exts in CATEGORY_EXT.items():
        if ext in exts:
            return cat
    if t.kind == Kind.MEDIA:
        return "music" if (t.media_format or "").startswith("audio:") else "video"
    return "other"


def in_category(t: Task, cat: str) -> bool:
    if cat == "all":
        return True
    if cat == "active":
        return t.status in (Status.QUEUED, Status.DOWNLOADING, Status.PROCESSING)
    if cat == "unfinished":
        return t.status != Status.COMPLETED
    if cat == "completed":
        return t.status == Status.COMPLETED
    return file_category(t) == cat


def status_text(t: Task) -> str:
    if t.status == Status.DOWNLOADING:
        if t.active_connections > 1:
            return f"下載中 · {t.active_connections} 條連線"
        return "下載中"
    if t.status == Status.PROCESSING:
        return "合併影音中…"
    if t.status == Status.ERROR:
        return f"錯誤：{t.error}" if t.error else "錯誤"
    return Status.LABELS.get(t.status, t.status)


class TaskModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.tasks: list[Task] = []
        self.dark = False

    # ---------------------------------------------------------------- Qt API
    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.tasks)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        t = self.tasks[index.row()]
        col = index.column()
        if role == TASK_ROLE:
            return t
        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NAME:
                return display_name(t)
            if col == COL_SIZE:
                if t.total > 0:
                    return human_size(t.total)
                return human_size(t.downloaded) if t.downloaded else "-"
            if col == COL_PROGRESS:
                return None
            if col == COL_SPEED:
                return f"{human_size(t.speed)}/s" if t.status == Status.DOWNLOADING and t.speed > 0 else ""
            if col == COL_ETA:
                return human_time(t.eta) if t.status == Status.DOWNLOADING and t.eta >= 0 else ""
            if col == COL_STATUS:
                return status_text(t)
            if col == COL_ADDED:
                return time.strftime("%Y-%m-%d %H:%M", time.localtime(t.created_at))
        elif role == SORT_ROLE:
            return [display_name(t).lower(), t.total, t.progress, t.speed,
                    t.eta, t.status, t.created_at][col]
        elif role == Qt.ItemDataRole.DecorationRole and col == COL_NAME:
            cat = file_category(t)
            name = cat if cat in ("video", "music", "archive", "document", "program", "image") else "file"
            return icons.icon(name, "#94a3b8" if self.dark else "#64748b")
        elif role == Qt.ItemDataRole.ToolTipRole:
            if col == COL_NAME:
                return f"{display_name(t)}\n{t.url}\n儲存位置：{t.save_dir}"
            if col == COL_STATUS and t.error:
                return t.error
        elif role == Qt.ItemDataRole.ForegroundRole and col == COL_STATUS:
            if t.status in (Status.ERROR, Status.COMPLETED, Status.DOWNLOADING, Status.PROCESSING):
                return QColor(STATUS_COLORS[t.status]).lighter(130 if self.dark else 100)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (COL_SIZE, COL_SPEED, COL_ETA):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    # ---------------------------------------------------------------- 操作
    def row_of(self, task_id: str) -> int:
        for i, t in enumerate(self.tasks):
            if t.id == task_id:
                return i
        return -1

    def add_task(self, t: Task) -> None:
        if self.row_of(t.id) >= 0:
            return
        n = len(self.tasks)
        self.beginInsertRows(QModelIndex(), n, n)
        self.tasks.append(t)
        self.endInsertRows()

    def remove_task(self, task_id: str) -> None:
        r = self.row_of(task_id)
        if r >= 0:
            self.beginRemoveRows(QModelIndex(), r, r)
            self.tasks.pop(r)
            self.endRemoveRows()

    def refresh(self, ids: set[str]) -> None:
        for tid in ids:
            r = self.row_of(tid)
            if r >= 0:
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(HEADERS) - 1))


class TaskFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.category = "all"
        self.text = ""
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(False)

    def set_category(self, cat: str) -> None:
        self.category = cat
        self.invalidateFilter()

    def set_text(self, text: str) -> None:
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):  # noqa: N802
        t: Task = self.sourceModel().tasks[row]
        if not in_category(t, self.category):
            return False
        if self.text:
            return self.text in display_name(t).lower() or self.text in t.url.lower()
        return True

    def lessThan(self, left, right):  # noqa: N802
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class ProgressDelegate(QStyledItemDelegate):
    """進度欄：細圓角進度條 + 百分比。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dark = False

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, opt, painter, opt.widget)

        t: Task = index.data(TASK_ROLE)
        if t is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(8, 0, -8, 0)
        text_w = 52
        bar_h = 6
        bar = QRectF(r.left(), r.center().y() - bar_h / 2 + 1, max(10, r.width() - text_w - 8), bar_h)
        track = QColor("#334155" if self.dark else "#e2e8f0")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, bar_h / 2, bar_h / 2)

        p = t.progress
        color = QColor(STATUS_COLORS.get(t.status, "#2563eb"))
        if self.dark:
            color = color.lighter(120)
        if t.status == Status.DOWNLOADING and t.total <= 0:
            # 大小未知：顯示流動的不定進度
            phase = (time.monotonic() * 0.6) % 1.0
            seg = QRectF(bar.left() + (bar.width() * 0.75) * phase, bar.top(), bar.width() * 0.25, bar_h)
            painter.setBrush(color)
            painter.drawRoundedRect(seg, bar_h / 2, bar_h / 2)
            label = ""
        else:
            if p > 0:
                fill = QRectF(bar.left(), bar.top(), max(bar_h, bar.width() * p), bar_h)
                painter.setBrush(color)
                painter.drawRoundedRect(fill, bar_h / 2, bar_h / 2)
            label = f"{p * 100:.1f}%" if t.status != Status.COMPLETED else "100%"
        if label:
            text_rect = QRectF(bar.right() + 6, r.top(), text_w, r.height())
            fg = opt.palette.color(opt.palette.ColorRole.HighlightedText
                                   if opt.state & QStyle.StateFlag.State_Selected else opt.palette.ColorRole.Text)
            painter.setPen(QPen(fg))
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), label)
        painter.restore()
