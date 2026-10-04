from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QSizePolicy, QSplitter, QStackedWidget, QTableView, QToolBar, QVBoxLayout, QWidget,
)

from ..models import Status, Task
from ..utils import human_size, open_path
from . import icons
from .dialogs import AddUrlDialog, DeleteDialog, SettingsDialog
from .task_model import (
    CATEGORIES, COL_ADDED, COL_ETA, COL_NAME, COL_PROGRESS, COL_SIZE, COL_SPEED, COL_STATUS, TASK_ROLE,
    ProgressDelegate, TaskFilter, TaskModel, in_category,
)

if TYPE_CHECKING:
    from .app import Controller


class MainWindow(QMainWindow):
    def __init__(self, ctl: "Controller"):
        super().__init__()
        self.ctl = ctl
        self.dark = ctl.dark
        self._dirty: set[str] = set()
        self._last_status: dict[str, str] = {}
        self._quitting = False
        self._tray_hint_shown = False

        self.setWindowTitle("Divebird 下載管理員")
        self.setWindowIcon(icons.app_icon())
        self.resize(1180, 680)
        self.setAcceptDrops(True)

        self.model = TaskModel(self)
        self.model.dark = self.dark
        for t in sorted(ctl.manager.tasks.values(), key=lambda t: t.created_at):
            self.model.add_task(t)
            self._last_status[t.id] = t.status
        self.proxy = TaskFilter(self)
        self.proxy.setSourceModel(self.model)

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

        b = ctl.bridge
        b.added.connect(self._on_added)
        b.removed.connect(self._on_removed)
        b.changed.connect(lambda t: self._dirty.add(t.id))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(400)
        self._tick()
        self.table.setFocus()

    # ---------------------------------------------------------------- 建構
    def _ico(self, name: str):
        return icons.themed(name, self.dark)

    def _build_toolbar(self):
        tb = QToolBar("工具列")
        tb.setObjectName("main-toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(tb)

        def act(icon_name, text, slot, shortcut=None):
            a = QAction(self._ico(icon_name), text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a

        self.act_add = act("link", "新增網址", self.add_url, "Ctrl+N")
        self.act_resume = act("play", "繼續", self.resume_selected)
        self.act_pause = act("pause", "暫停", self.pause_selected)
        tb.addSeparator()
        act("play-all", "全部開始", self.ctl.manager.start_all)
        act("pause-all", "全部暫停", self.ctl.manager.pause_all)
        tb.addSeparator()
        self.act_delete = act("trash", "刪除", self.delete_selected, QKeySequence.StandardKey.Delete)
        self.act_folder = act("folder", "開啟資料夾", self.open_folder)
        tb.addSeparator()
        act("settings", "設定", self.open_settings)
        quit_act = QAction("結束", self)
        quit_act.setShortcut(QKeySequence("Ctrl+Q"))
        quit_act.triggered.connect(self.ctl.quit)
        self.addAction(quit_act)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("搜尋檔名或網址")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.proxy.set_text)
        tb.addWidget(self.search)

    def _build_body(self):
        self.cats = QListWidget()
        self.cats.setObjectName("categories")
        self.cats.setIconSize(QSize(18, 18))
        for key, label, icon_name in CATEGORIES:
            it = QListWidgetItem(self._ico(icon_name), label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setData(Qt.ItemDataRole.UserRole + 1, label)
            self.cats.addItem(it)
        self.cats.setCurrentRow(0)
        self.cats.currentItemChanged.connect(
            lambda cur, _prev: cur and self.proxy.set_category(cur.data(Qt.ItemDataRole.UserRole)))

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setObjectName("tasks")
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_ADDED, Qt.SortOrder.DescendingOrder)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setIconSize(QSize(18, 18))
        self.delegate = ProgressDelegate(self.table)
        self.delegate.dark = self.dark
        self.table.setItemDelegateForColumn(COL_PROGRESS, self.delegate)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col, w in ((COL_SIZE, 90), (COL_PROGRESS, 190), (COL_SPEED, 100), (COL_ETA, 80),
                       (COL_STATUS, 210), (COL_ADDED, 130)):
            h.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self.table.setColumnWidth(col, w)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_actions())

        # 空白狀態提示
        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(2)
        big = QLabel()
        big.setPixmap(icons.app_icon().pixmap(72, 72))
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(big)
        t1 = QLabel("還沒有任何下載")
        t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = t1.font()
        f.setPointSizeF(f.pointSizeF() + 4)
        t1.setFont(f)
        ev.addWidget(t1)
        t2 = QLabel("在瀏覽器播放影片時，點擊影片右上角的「下載此影片」按鈕；\n"
                    "或按工具列的「新增網址」、直接把連結拖曳到這個視窗。")
        t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t2.setStyleSheet("color: palette(placeholder-text);")
        ev.addWidget(t2)
        ev.addStretch(3)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(empty)

        split = QSplitter()
        split.addWidget(self.cats)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([190, 990])
        split.setChildrenCollapsible(False)
        self.cats.setMinimumWidth(150)
        self.setCentralWidget(split)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.api_label = QLabel()
        self.speed_label = QLabel()
        sb.addWidget(self.api_label)
        sb.addPermanentWidget(self.speed_label)
        self.update_api_status()

    def update_api_status(self):
        if self.ctl.api_ok:
            self.api_label.setText(f"<span style='color:#16a34a'>●</span> 瀏覽器整合已啟用"
                                   f"（127.0.0.1:{self.ctl.settings.port}）")
        else:
            self.api_label.setText(f"<span style='color:#dc2626'>●</span> 瀏覽器整合未啟用："
                                   f"埠 {self.ctl.settings.port} 已被其他程式使用，請到設定更換")

    # ---------------------------------------------------------------- 事件
    def _on_added(self, t: Task):
        self.model.add_task(t)
        self._last_status[t.id] = t.status
        self.proxy.invalidate()

    def _on_removed(self, tid: str):
        self.model.remove_task(tid)
        self._last_status.pop(tid, None)

    def _tick(self):
        tasks = self.model.tasks
        # 不定進度條動畫
        for t in tasks:
            if t.status == Status.DOWNLOADING and t.total <= 0:
                self._dirty.add(t.id)
        refilter = False
        for t in tasks:
            if self._last_status.get(t.id) != t.status:
                self._last_status[t.id] = t.status
                self._dirty.add(t.id)
                refilter = True
        if self._dirty:
            self.model.refresh(self._dirty)
            self._dirty.clear()
        if refilter and self.proxy.category in ("active", "unfinished", "completed"):
            self.proxy.invalidate()

        for i in range(self.cats.count()):
            it = self.cats.item(i)
            key = it.data(Qt.ItemDataRole.UserRole)
            n = sum(1 for t in tasks if in_category(t, key))
            it.setText(f"{it.data(Qt.ItemDataRole.UserRole + 1)}　{n}" if n else it.data(Qt.ItemDataRole.UserRole + 1))

        self.stack.setCurrentIndex(1 if not tasks else 0)
        active = [t for t in tasks if t.status == Status.DOWNLOADING]
        speed = sum(t.speed for t in active)
        self.speed_label.setText(f"下載中 {len(active)} 個　·　總速度 {human_size(speed)}/s" if active else "閒置")
        self.ctl.update_tray_tooltip(len(active), speed)
        self._update_actions()

    def _update_actions(self):
        sel = self.selected_tasks()
        self.act_resume.setEnabled(any(t.status in (Status.PAUSED, Status.ERROR) for t in sel))
        self.act_pause.setEnabled(any(t.status in (Status.DOWNLOADING, Status.QUEUED) for t in sel))
        self.act_delete.setEnabled(bool(sel))

    def selected_tasks(self) -> list[Task]:
        rows = self.table.selectionModel().selectedRows()
        return [self.proxy.data(r, TASK_ROLE) for r in rows]

    # ---------------------------------------------------------------- 動作
    def add_url(self):
        clip = QGuiApplication.clipboard().text().strip()
        initial = clip if clip.lower().startswith(("http://", "https://")) and "\n" not in clip else ""
        dlg = AddUrlDialog(self, initial)
        if dlg.exec():
            urls = dlg.urls()
            for u in urls:
                self.ctl.request_url(u, ask=len(urls) == 1)

    def resume_selected(self):
        for t in self.selected_tasks():
            self.ctl.manager.start(t.id)

    def pause_selected(self):
        for t in self.selected_tasks():
            self.ctl.manager.pause(t.id)

    def delete_selected(self):
        sel = self.selected_tasks()
        if not sel:
            return
        dlg = DeleteDialog(len(sel), self)
        if dlg.exec():
            for t in sel:
                self.ctl.manager.remove(t.id, delete_files=dlg.delete_files.isChecked())

    def open_folder(self):
        sel = self.selected_tasks()
        if sel and sel[0].filename:
            t = sel[0]
            open_path(t.filepath if t.status == Status.COMPLETED else t.filepath.parent,
                      select=t.status == Status.COMPLETED)
        else:
            open_path(self.ctl.settings.download_dir)

    def open_settings(self):
        old_port = self.ctl.settings.port
        if SettingsDialog(self.ctl.settings, self).exec():
            self.ctl.manager.apply_settings()
            if self.ctl.settings.port != old_port:
                self.ctl.restart_server()
            self.update_api_status()

    def _double_clicked(self, index):
        t: Task = self.proxy.data(index, TASK_ROLE)
        if t.status == Status.COMPLETED:
            open_path(t.filepath)
        elif t.status in (Status.PAUSED, Status.ERROR):
            self.ctl.manager.start(t.id)

    def _context_menu(self, pos):
        sel = self.selected_tasks()
        if not sel:
            return
        t = sel[0]
        m = QMenu(self)
        if t.status == Status.COMPLETED:
            m.addAction(self._ico("open"), "開啟檔案", lambda: open_path(t.filepath))
            m.addAction(self._ico("folder"), "在資料夾中顯示", lambda: open_path(t.filepath, select=True))
            m.addSeparator()
        if any(x.status in (Status.PAUSED, Status.ERROR) for x in sel):
            m.addAction(self._ico("play"), "繼續", self.resume_selected)
        if any(x.status in (Status.DOWNLOADING, Status.QUEUED) for x in sel):
            m.addAction(self._ico("pause"), "暫停", self.pause_selected)
        m.addAction(self._ico("refresh"), "重新下載", lambda: [self.ctl.manager.redownload(x.id) for x in sel])
        m.addSeparator()
        m.addAction(self._ico("copy"), "複製網址",
                    lambda: QGuiApplication.clipboard().setText("\n".join(x.url for x in sel)))
        if t.page_url:
            m.addAction(self._ico("globe"), "複製來源網頁", lambda: QGuiApplication.clipboard().setText(t.page_url))
        m.addSeparator()
        m.addAction(self._ico("trash"), "刪除…", self.delete_selected)
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ---------------------------------------------------------------- 視窗
    def bring_to_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        md = e.mimeData()
        urls = [u.toString() for u in md.urls()] if md.hasUrls() else md.text().split()
        urls = [u for u in urls if u.lower().startswith(("http://", "https://"))]
        for u in urls:
            self.ctl.request_url(u, ask=len(urls) == 1)

    def closeEvent(self, e):  # noqa: N802
        if not self._quitting and self.ctl.settings.minimize_to_tray:
            e.ignore()
            if self.ctl.tray_available():
                self.hide()
                if not self._tray_hint_shown:
                    self._tray_hint_shown = True
                    self.ctl.notify("Divebird 仍在背景執行",
                                    "瀏覽器的下載會繼續交給 Divebird。要結束程式請在系統匣圖示按右鍵 → 結束。")
            else:
                # 沒有系統匣（例如 GNOME 預設）：改為最小化，讓擴充功能仍可交付下載
                self.showMinimized()
                if not self._tray_hint_shown:
                    self._tray_hint_shown = True
                    self.statusBar().showMessage("已最小化並在背景執行；按 Ctrl+Q 可結束 Divebird", 8000)
            return
        e.accept()
        self.ctl.quit()
