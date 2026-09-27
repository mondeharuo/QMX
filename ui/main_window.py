from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
import subprocess
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QSettings, Qt, QUrl, QTimer
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPushButton, QProgressBar, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QMenu, QSlider, QGroupBox,
)

from core.database import Database
from core.scanner import FileEntry, ScanResult, scan
from core.workers import ProcessWorker, ScanWorker


def app_root() -> Path:
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parents[1]
    executable_dir = Path(sys.executable).resolve().parent
    # PyInstaller replaces dist/QMX during each build. Keep user data
    # outside that directory so rebuilding the executable cannot erase it.
    if executable_dir.parent.name.lower() == "dist":
        return executable_dir.parent.parent
    return executable_dir


APP_ROOT = app_root()
DB_PATH = APP_ROOT / "data" / "music_process.db"
LOG_PATH = APP_ROOT / "logs" / "app.log"

STATUS_TEXT = {"pending": "等待", "processing": "正在处理", "success": "已完成",
               "failed": "失败", "skipped": "已跳过", "unsupported": "不支持",
               "output_missing": "曾处理 / 输出丢失", "history_source_missing": "曾处理 / 源文件已删除",
               "history_files_missing": "曾处理 / 源与输出均丢失"}
STATUS_COLOR = {"pending": "#e8edf4", "processing": "#dbeafe", "success": "#dcfce7",
                "failed": "#fee2e2", "skipped": "#fef3c7", "unsupported": "#f1f5f9",
                "output_missing": "#fef3c7", "history_source_missing": "#fef3c7",
                "history_files_missing": "#fee2e2"}


def _sort_initial(name: str) -> str:
    try:
        from pypinyin import lazy_pinyin
        first = lazy_pinyin(name[:1], errors="ignore")
        value = first[0][:1].upper() if first and first[0] else ""
    except Exception:
        value = name[:1].upper()
    return value if value.isalpha() else "#"


def _name_sort_key(name: str) -> str:
    try:
        from pypinyin import lazy_pinyin
        return "".join(lazy_pinyin(name, errors="ignore")).casefold()
    except Exception:
        return name.casefold()


def find_qmdec() -> str:
    found = shutil.which("qmdec") or shutil.which("qmdec.exe")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    packages = local / "Packages"
    if packages.exists():
        for path in packages.glob("PythonSoftwareFoundation.Python*/LocalCache/local-packages/Python*/Scripts/qmdec.exe"):
            return str(path)
    return ""


def find_ffprobe() -> str:
    found = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if found:
        return found
    winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    if winget.exists():
        for folder in winget.glob("Gyan.FFmpeg.Shared_*"):
            matches = list(folder.glob("*/bin/ffprobe.exe"))
            if matches:
                return str(matches[0])
    return ""


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QMX 设置")
        self.setMinimumWidth(560)
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.source = QLineEdit(settings.value("source", r"G:\Music\VipSongsDownload"))
        self.output = QLineEdit(settings.value("output", r"G:\Music\Decoded"))
        self.ffprobe = QLineEdit(settings.value("ffprobe", find_ffprobe()))
        form.addRow("默认源目录", self._browse_row(self.source, directory=True))
        form.addRow("默认输出目录", self._browse_row(self.output, directory=True))
        form.addRow("ffprobe 路径", self._browse_row(self.ffprobe, directory=False))
        layout.addLayout(form)
        self.copy_lrc = QCheckBox("自动复制 LRC（保持文件内容不变）")
        self.copy_lrc.setChecked(settings.value("copy_lrc", True, type=bool))
        self.keep_structure = QCheckBox("保留源目录结构")
        self.keep_structure.setChecked(settings.value("keep_structure", True, type=bool))
        self.keep_filename = QCheckBox("保留原始歌曲 basename")
        self.keep_filename.setChecked(settings.value("keep_filename", True, type=bool))
        self.open_after = QCheckBox("处理完成后打开输出目录")
        self.open_after.setChecked(settings.value("open_after", False, type=bool))
        for check in (self.copy_lrc, self.keep_structure, self.keep_filename, self.open_after):
            layout.addWidget(check)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save = QPushButton("保存")
        cancel = QPushButton("取消")
        save.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _browse_row(self, edit: QLineEdit, directory: bool):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit)
        button = QPushButton("浏览…")
        if directory:
            button.clicked.connect(lambda: self._browse_dir(edit))
        else:
            button.clicked.connect(lambda: self._browse_file(edit))
        row.addWidget(button)
        return box

    def _browse_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "选择目录", edit.text())
        if path:
            edit.setText(path)

    def _browse_file(self, edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "选择 ffprobe.exe", edit.text(), "Programs (ffprobe.exe);;All files (*)")
        if path:
            edit.setText(path)

    def save_values(self):
        self.settings.setValue("source", self.source.text())
        self.settings.setValue("output", self.output.text())
        self.settings.setValue("ffprobe", self.ffprobe.text())
        self.settings.setValue("copy_lrc", self.copy_lrc.isChecked())
        self.settings.setValue("keep_structure", self.keep_structure.isChecked())
        self.settings.setValue("keep_filename", self.keep_filename.isChecked())
        self.settings.setValue("open_after", self.open_after.isChecked())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QMX — 本地音乐批处理")
        self.resize(1120, 760)
        self.settings = QSettings("QMX", "QMX")
        self.entries: list[FileEntry] = []
        self.row_by_rel: dict[str, int] = {}
        self.entry_by_rel: dict[str, FileEntry] = {}
        self.visible_entries: list[FileEntry] = []
        self.checked_rel: set[str] = set()
        self._updating_table = False
        self.scan_result: ScanResult | None = None
        self.worker = None
        self.close_after_task = False
        self.rescan_after_process = False
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(float(self.settings.value("player_volume", 0.65)))
        self._build_ui()
        self._load_settings()
        self._style()
        self.player.durationChanged.connect(self._duration_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.errorOccurred.connect(self._player_error)
        self._player_timer = QTimer(self)
        self._player_timer.setInterval(250)
        self._player_timer.timeout.connect(self._refresh_player)
        self._player_timer.start()
        QTimer.singleShot(250, self.start_scan)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(22, 18, 22, 18)
        main.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("QMX")
        title.setObjectName("appTitle")
        subtitle = QLabel("QQ Music 本地批处理")
        subtitle.setObjectName("subtitle")
        titles = QVBoxLayout()
        titles.addWidget(title)
        titles.addWidget(subtitle)
        title_row.addLayout(titles)
        title_row.addStretch(1)
        self.settings_button = QPushButton("⚙ 设置")
        self.settings_button.clicked.connect(self.open_settings)
        title_row.addWidget(self.settings_button)
        main.addLayout(title_row)

        paths = QFormLayout()
        self.source_edit = QLineEdit()
        self.output_edit = QLineEdit()
        paths.addRow("源文件夹", self._path_row(self.source_edit, True))
        paths.addRow("输出文件夹", self._path_row(self.output_edit, True))
        main.addLayout(paths)

        checks = QHBoxLayout()
        self.structure_check = QCheckBox("保留目录结构")
        self.lrc_check = QCheckBox("自动复制 LRC")
        self.skip_check = QCheckBox("跳过已处理文件")
        self.skip_check.setChecked(True)
        self.name_check = QCheckBox("保留原始文件名")
        for item in (self.structure_check, self.lrc_check, self.skip_check, self.name_check):
            checks.addWidget(item)
        checks.addStretch(1)
        main.addLayout(checks)

        action_row = QHBoxLayout()
        self.scan_button = QPushButton("扫描")
        self.scan_button.clicked.connect(self.start_scan)
        self.start_button = QPushButton("▶ 转换全部待处理")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_processing)
        action_row.addWidget(self.scan_button)
        action_row.addStretch(1)
        action_row.addWidget(self.start_button)
        main.addLayout(action_row)

        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("筛选"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("全部", "all")
        for label, value in (("待处理", "pending"), ("处理中", "processing"),
                             ("成功", "success"), ("失败", "failed"),
                             ("曾处理", "history"), ("源文件不存在", "source_missing"),
                             ("输出文件不存在", "output_missing")):
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(self._refresh_table_view)
        view_row.addWidget(self.filter_combo)
        view_row.addSpacing(12)
        view_row.addWidget(QLabel("排序"))
        self.sort_combo = QComboBox()
        for label, value in (("名称 A–Z", "name_asc"), ("名称 Z–A", "name_desc"),
                             ("首字母", "initial"), ("成功优先", "success_first"),
                             ("失败优先", "failed_first"), ("待处理优先", "pending_first"),
                             ("最近发现", "recent_seen"), ("最早发现", "oldest_seen"),
                             ("最近处理", "recent_processed"), ("最早处理", "oldest_processed")):
            self.sort_combo.addItem(label, value)
        self.sort_combo.currentIndexChanged.connect(self._refresh_table_view)
        view_row.addWidget(self.sort_combo)
        view_row.addStretch(1)
        main.addLayout(view_row)

        bulk_row = QHBoxLayout()
        self.select_all_button = QPushButton("全选当前列表")
        self.clear_selection_button = QPushButton("清空勾选")
        self.selection_count_label = QLabel("已勾选 0 项")
        self.convert_selected_button = QPushButton("转换所选 / 当前行")
        self.delete_source_selected_button = QPushButton("删除所选源文件")
        self.delete_output_selected_button = QPushButton("删除所选输出文件")
        self.select_all_button.clicked.connect(self._check_all_visible)
        self.clear_selection_button.clicked.connect(self._clear_checked)
        self.convert_selected_button.clicked.connect(self._convert_checked_or_selected)
        self.delete_source_selected_button.clicked.connect(lambda: self._delete_checked("source"))
        self.delete_output_selected_button.clicked.connect(lambda: self._delete_checked("output"))
        for button in (self.select_all_button, self.clear_selection_button):
            bulk_row.addWidget(button)
        bulk_row.addWidget(self.selection_count_label)
        for button in (self.convert_selected_button, self.delete_source_selected_button,
                       self.delete_output_selected_button):
            bulk_row.addWidget(button)
        bulk_row.addStretch(1)
        main.addLayout(bulk_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选", "文件名", "相对目录", "类型", "大小", "状态"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_context_menu)
        self.table.itemSelectionChanged.connect(self._show_details)
        self.table.itemChanged.connect(self._table_item_changed)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (3, 4, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        main.addWidget(self.table, 1)

        details = QGroupBox("歌曲详情")
        details_layout = QVBoxLayout(details)
        self.detail_label = QLabel("选择一首歌曲查看路径、处理时间和失败原因。")
        self.detail_label.setWordWrap(True)
        details_layout.addWidget(self.detail_label)
        self.playing_label = QLabel("当前播放：无")
        details_layout.addWidget(self.playing_label)
        player_row = QHBoxLayout()
        self.play_source_button = QPushButton("▶ 用 QQ 音乐播放源文件")
        self.play_output_button = QPushButton("▶ 播放输出文件")
        self.pause_audio_button = QPushButton("暂停/继续试听")
        self.stop_audio_button = QPushButton("停止试听")
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.player.setPosition)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setMaximumWidth(120)
        self.volume_slider.setValue(round(self.audio_output.volume() * 100) if hasattr(self, "audio_output") else 65)
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100) if hasattr(self, "audio_output") else None)
        self.play_source_button.clicked.connect(lambda: self._play_selected("source"))
        self.play_output_button.clicked.connect(lambda: self._play_selected("output"))
        self.pause_audio_button.clicked.connect(self._toggle_audio_pause)
        self.stop_audio_button.clicked.connect(self.player.stop if hasattr(self, "player") else lambda: None)
        for widget in (self.play_source_button, self.play_output_button, self.pause_audio_button, self.stop_audio_button,
                       QLabel("进度"), self.position_slider, QLabel("音量"), self.volume_slider):
            player_row.addWidget(widget)
        details_layout.addLayout(player_row)
        main.addWidget(details)

        self.counts = QLabel("总计 0   新增 0   完成 0   跳过 0   失败 0   不支持 0")
        main.addWidget(self.counts)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        main.addWidget(self.progress)
        self.current = QLabel("当前：等待扫描")
        self.current.setObjectName("currentLabel")
        main.addWidget(self.current)

        bottom = QHBoxLayout()
        self.pause_button = QPushButton("暂停")
        self.stop_button = QPushButton("停止")
        self.retry_button = QPushButton("重试失败项")
        self.open_button = QPushButton("打开输出文件夹")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button.clicked.connect(self.stop_processing)
        self.retry_button.clicked.connect(self.retry_failures)
        self.open_button.clicked.connect(self.open_output)
        for button in (self.pause_button, self.stop_button, self.retry_button):
            bottom.addWidget(button)
        bottom.addStretch(1)
        bottom.addWidget(self.open_button)
        main.addLayout(bottom)
        self._set_busy(False)
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(self.settings.value("filter", "all"))))
        self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(self.settings.value("sort", "name_asc"))))

    def _path_row(self, edit: QLineEdit, directory: bool):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit)
        button = QPushButton("选择")
        button.clicked.connect(lambda: self._choose_dir(edit) if directory else self._choose_file(edit))
        row.addWidget(button)
        return box

    def _choose_dir(self, edit: QLineEdit):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", edit.text())
        if path:
            edit.setText(path)

    def _choose_file(self, edit: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", edit.text())
        if path:
            edit.setText(path)

    def _load_settings(self):
        self.source_edit.setText(self.settings.value("source", r"G:\Music\VipSongsDownload"))
        self.output_edit.setText(self.settings.value("output", r"G:\Music\Decoded"))
        self.lrc_check.setChecked(self.settings.value("copy_lrc", True, type=bool))
        self.structure_check.setChecked(self.settings.value("keep_structure", True, type=bool))
        self.name_check.setChecked(self.settings.value("keep_filename", True, type=bool))
        if hasattr(self, "sort_combo"):
            self.sort_combo.setCurrentIndex(max(0, self.sort_combo.findData(self.settings.value("sort", "name_asc"))))
            self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(self.settings.value("filter", "all"))))

    def _save_settings(self):
        self.settings.setValue("source", self.source_edit.text())
        self.settings.setValue("output", self.output_edit.text())
        self.settings.setValue("copy_lrc", self.lrc_check.isChecked())
        self.settings.setValue("keep_structure", self.structure_check.isChecked())
        self.settings.setValue("keep_filename", self.name_check.isChecked())
        self.settings.setValue("sort", self.sort_combo.currentData())
        self.settings.setValue("filter", self.filter_combo.currentData())
        if hasattr(self, "audio_output"):
            self.settings.setValue("player_volume", self.audio_output.volume())

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.save_values()
            self._load_settings()
            if self.scan_result:
                self.start_scan()

    def _paths(self):
        source = Path(self.source_edit.text()).expanduser()
        output = Path(self.output_edit.text()).expanduser()
        ffprobe = self.settings.value("ffprobe", "") or find_ffprobe()
        qmdec = find_qmdec()
        return source, output, Path(ffprobe) if ffprobe else None, qmdec

    def start_scan(self):
        if self._running():
            return
        self._save_settings()
        source, output, ffprobe, _ = self._paths()
        if not source.is_dir():
            QMessageBox.warning(self, "找不到源目录", f"请选择有效的源文件夹：\n{source}")
            return
        source_resolved, output_resolved = source.resolve(), output.resolve()
        if output_resolved == source_resolved or source_resolved in output_resolved.parents:
            QMessageBox.critical(self, "输出目录不安全", "输出目录不能位于源目录内部，以确保源文件夹保持只读。")
            return
        if ffprobe is None or not ffprobe.is_file():
            QMessageBox.warning(self, "找不到 ffprobe", "请在设置中选择本机 ffprobe.exe。")
            return
        output.mkdir(parents=True, exist_ok=True)
        self.scan_button.setEnabled(False)
        self.current.setText("当前：正在递归扫描并校验已处理项目…")
        self.worker = ScanWorker(source, output, DB_PATH, str(ffprobe), self.lrc_check.isChecked(),
                                 self.structure_check.isChecked())
        self.worker.completed.connect(self._scan_complete)
        self.worker.failed.connect(self._scan_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _scan_complete(self, result: ScanResult):
        self.scan_result = result
        self.entries = result.entries
        self._populate_table()
        self._update_counts()
        self.current.setText(f"扫描完成；新增 LRC {result.lrc_copied} 个")
        self.progress.setValue(0)

    def _scan_failed(self, error: str):
        QMessageBox.critical(self, "扫描失败", error)
        self.current.setText("当前：扫描失败")

    def _worker_finished(self):
        self._set_busy(False)
        if self.close_after_task:
            QTimer.singleShot(0, self.close)
        elif self.rescan_after_process:
            self.rescan_after_process = False
            QTimer.singleShot(0, self.start_scan)

    def _populate_table(self):
        self.entry_by_rel = {entry.relative: entry for entry in self.entries}
        self.checked_rel.intersection_update(self.entry_by_rel)
        self._refresh_table_view()

    def _refresh_table_view(self, *_):
        if not hasattr(self, "table"):
            return
        filter_mode = self.filter_combo.currentData() if hasattr(self, "filter_combo") else "all"
        sort_mode = self.sort_combo.currentData() if hasattr(self, "sort_combo") else "name_asc"
        visible = list(self.entries)
        if filter_mode == "pending":
            visible = [e for e in visible if e.status == "pending"]
        elif filter_mode == "processing":
            visible = [e for e in visible if e.status == "processing"]
        elif filter_mode == "success":
            visible = [e for e in visible if e.status == "success"]
        elif filter_mode == "failed":
            visible = [e for e in visible if e.status == "failed"]
        elif filter_mode == "history":
            visible = [e for e in visible if e.history_status == "success" or e.processed_time]
        elif filter_mode == "source_missing":
            visible = [e for e in visible if not e.source_exists]
        elif filter_mode == "output_missing":
            visible = [e for e in visible if not e.output_exists and (e.history_status == "success" or e.status == "output_missing" or not e.source_exists)]

        status_rank = {"success": 0, "failed": 1, "pending": 2, "processing": 3,
                       "output_missing": 4, "history_source_missing": 5, "history_files_missing": 6}
        if sort_mode == "name_desc":
            visible.sort(key=lambda e: _name_sort_key(e.filename), reverse=True)
        elif sort_mode == "initial":
            visible.sort(key=lambda e: (_sort_initial(e.filename), _name_sort_key(e.filename)))
        elif sort_mode == "success_first":
            visible.sort(key=lambda e: (status_rank.get(e.status, 9), _name_sort_key(e.filename)))
        elif sort_mode == "failed_first":
            visible.sort(key=lambda e: (0 if e.status == "failed" else 1, _name_sort_key(e.filename)))
        elif sort_mode == "pending_first":
            visible.sort(key=lambda e: (0 if e.status == "pending" else 1, _name_sort_key(e.filename)))
        elif sort_mode == "recent_seen":
            visible.sort(key=lambda e: e.last_seen_time or e.first_seen_time or "", reverse=True)
        elif sort_mode == "oldest_seen":
            visible.sort(key=lambda e: e.first_seen_time or e.last_seen_time or "")
        elif sort_mode == "recent_processed":
            visible.sort(key=lambda e: e.processed_time or e.last_attempt_time or "", reverse=True)
        elif sort_mode == "oldest_processed":
            visible.sort(key=lambda e: e.processed_time or e.last_attempt_time or "")
        else:
            visible.sort(key=lambda e: _name_sort_key(e.filename))

        self.visible_entries = visible
        self.table.setRowCount(len(visible))
        self.row_by_rel.clear()
        self._updating_table = True
        try:
            for row, entry in enumerate(visible):
                self.row_by_rel[entry.relative] = row
                self._set_row(row, entry, entry.status)
        finally:
            self._updating_table = False
        self._show_details()
        self._update_bulk_buttons()

    def _set_row(self, row: int, entry: FileEntry, status: str):
        checked = QTableWidgetItem()
        checked.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        checked.setCheckState(Qt.CheckState.Checked if entry.relative in self.checked_rel else Qt.CheckState.Unchecked)
        checked.setBackground(QColor(STATUS_COLOR.get(status, "#ffffff")))
        self.table.setItem(row, 0, checked)
        values = [entry.filename, str(Path(entry.relative).parent) if str(Path(entry.relative).parent) != "." else "",
                  entry.extension, self._format_size(entry.size), STATUS_TEXT.get(status, status)]
        for col, value in enumerate(values, start=1):
            cell = QTableWidgetItem(value)
            cell.setBackground(QColor(STATUS_COLOR.get(status, "#ffffff")))
            if col == 1:
                cell.setToolTip(entry.error or f"{entry.relative}\n历史状态：{STATUS_TEXT.get(entry.history_status, entry.history_status or '无')}")
            self.table.setItem(row, col, cell)

    def _table_item_changed(self, item: QTableWidgetItem):
        if self._updating_table or item.column() != 0:
            return
        row = item.row()
        if not (0 <= row < len(self.visible_entries)):
            return
        relative = self.visible_entries[row].relative
        if item.checkState() == Qt.CheckState.Checked:
            self.checked_rel.add(relative)
        else:
            self.checked_rel.discard(relative)
        self._update_bulk_buttons()

    def _check_all_visible(self):
        self.checked_rel.update(entry.relative for entry in self.visible_entries)
        self._refresh_table_view()

    def _clear_checked(self):
        self.checked_rel.clear()
        self._refresh_table_view()

    def _checked_entries(self) -> list[FileEntry]:
        return [entry for entry in self.entries if entry.relative in self.checked_rel]

    def _update_bulk_buttons(self):
        if not hasattr(self, "convert_selected_button"):
            return
        busy = self._running()
        has_checked = bool(self.checked_rel)
        has_row = bool(self.table.selectionModel().selectedRows()) if hasattr(self, "table") else False
        self.selection_count_label.setText(f"已勾选 {len(self.checked_rel)} 项")
        can_convert = has_checked or has_row
        self.select_all_button.setEnabled(not busy and bool(self.visible_entries))
        self.clear_selection_button.setEnabled(not busy and has_checked)
        self.convert_selected_button.setEnabled(not busy and can_convert)
        self.delete_source_selected_button.setEnabled(not busy and has_checked)
        self.delete_output_selected_button.setEnabled(not busy and has_checked)

    def _selected_entry(self) -> FileEntry | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        return self.visible_entries[row] if 0 <= row < len(self.visible_entries) else None

    def _source_path(self, entry: FileEntry) -> Path:
        candidate = entry.source
        if candidate.is_absolute():
            return candidate
        return Path(self.source_edit.text()) / Path(entry.relative)

    def _output_path(self, entry: FileEntry) -> Path | None:
        if not entry.output_relative:
            return None
        return Path(self.output_edit.text()) / Path(entry.output_relative)

    def _show_details(self):
        if not hasattr(self, "detail_label"):
            return
        entry = self._selected_entry()
        if entry is None:
            self.detail_label.setText("选择一首歌曲查看路径、处理时间和失败原因。")
            self.play_source_button.setEnabled(False)
            self.play_output_button.setEnabled(False)
            self._update_bulk_buttons()
            return
        source = self._source_path(entry)
        output = self._output_path(entry)
        lines = [f"歌曲：{entry.filename}", f"状态：{STATUS_TEXT.get(entry.status, entry.status)}",
                 f"历史状态：{STATUS_TEXT.get(entry.history_status, entry.history_status or '无记录')}",
                 f"源文件：{source}（{'存在' if entry.source_exists else '不存在'}）",
                 f"大小：{self._format_size(entry.size)}"]
        if output:
            lines.append(f"输出文件：{output}（{'存在' if output.is_file() else '不存在'}）")
        if entry.processed_time:
            lines.append(f"上次成功处理：{entry.processed_time}")
        if entry.last_attempt_time:
            lines.append(f"最近尝试：{entry.last_attempt_time}")
        if entry.error:
            lines.append(f"原因：{entry.error}")
        self.detail_label.setText("\n".join(lines))
        self.play_source_button.setEnabled(source.is_file())
        self.play_output_button.setEnabled(bool(output and output.is_file()))
        self._update_bulk_buttons()

    def _row_context_menu(self, point):
        index = self.table.indexAt(point)
        if index.isValid():
            self.table.selectRow(index.row())
        entry = self._selected_entry()
        if entry is None:
            return
        menu = self._build_row_context_menu(entry)
        menu.exec(self.table.viewport().mapToGlobal(point))

    def _build_row_context_menu(self, entry: FileEntry) -> QMenu:
        menu = QMenu(self)
        source = self._source_path(entry)
        output = self._output_path(entry)
        actions = [
            ("用 QQ 音乐播放源文件", lambda: self._play_entry(entry, "source"), source.is_file()),
            ("播放输出文件", lambda: self._play_entry(entry, "output"), bool(output and output.is_file())),
            ("打开源文件位置", lambda: self._open_file_location(source), True),
            ("打开输出文件位置", lambda: self._open_file_location(output), output is not None),
        ]
        for label, callback, enabled in actions:
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
        menu.addSeparator()
        convert = menu.addAction("转换此文件…")
        convert.setEnabled(entry.status in ("pending", "failed", "output_missing") and source.is_file())
        convert.triggered.connect(lambda: self._launch_process([entry]))
        source_root_name = Path(self.source_edit.text()).name or "源目录"
        output_root_name = Path(self.output_edit.text()).name or "输出目录"
        delete_source = menu.addAction(f"删除源文件（{source_root_name}）…")
        delete_source.setEnabled(source.is_file())
        delete_source.triggered.connect(lambda: self._delete_selected_file(entry, "source"))
        delete_output = menu.addAction(f"删除输出文件（{output_root_name}）…")
        delete_output.setEnabled(bool(output and output.is_file()))
        delete_output.triggered.connect(lambda: self._delete_selected_file(entry, "output"))
        return menu

    @staticmethod
    def _send_to_recycle_bin(path: Path, owner) -> None:
        if os.name != "nt":
            raise OSError("移入回收站仅支持 Windows。")

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        # SHFileOperation expects a double-NUL-terminated list of paths. The
        # buffer adds the final NUL; this explicit NUL terminates the path.
        source = ctypes.create_unicode_buffer(str(path) + "\0")
        operation = SHFILEOPSTRUCTW()
        operation.hwnd = wintypes.HWND(int(owner.winId()))
        operation.wFunc = 3  # FO_DELETE
        operation.pFrom = ctypes.cast(source, wintypes.LPCWSTR)
        operation.fFlags = 0x0040 | 0x0010 | 0x0400 | 0x0004  # allow undo, quiet UI
        # Declare the native signature explicitly. Without argtypes, ctypes may
        # marshal the pointer-sized HWND/structure incorrectly on 64-bit Windows.
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell_file_operation = shell32.SHFileOperationW
        shell_file_operation.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
        shell_file_operation.restype = ctypes.c_int
        result = shell_file_operation(ctypes.byref(operation))
        if result != 0 or operation.fAnyOperationsAborted or path.exists():
            raise OSError(f"Windows could not move the file to the Recycle Bin (error {result}).")

    def _delete_selected_file(self, entry: FileEntry, side: str):
        if side == "source":
            path = self._source_path(entry)
            role = "源文件（VipSongsDownload）"
        else:
            path = self._output_path(entry)
            role = "输出文件（Decoded）"
        if path is None or not path.is_file():
            QMessageBox.information(self, "文件不存在", f"所选{role}已经不存在。")
            return

        answer = QMessageBox.warning(
            self, f"删除{role}",
            f"确认将以下{role}移入 Windows 回收站？\n\n{path}\n\n只处理上面显示的这一条路径。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._send_to_recycle_bin(path, self)
        except OSError as exc:
            QMessageBox.critical(self, "删除失败", f"{role}未删除：\n{exc}")
            return
        self._record_deleted(entry, side)
        self._refresh_table_view()
        self._update_counts()

    def _record_deleted(self, entry: FileEntry, side: str, db: Database | None = None):
        db = db or Database(DB_PATH)
        if side == "source":
            entry.source_exists = False
            db.set_presence(entry.relative, source_exists=False)
            if entry.history_status == "success" or entry.status == "success":
                entry.status = "history_source_missing" if entry.output_exists else "history_files_missing"
            else:
                entry.status = "history_source_missing"
        else:
            entry.output_exists = False
            db.set_presence(entry.relative, output_exists=False)
            if entry.history_status == "success" or entry.status == "success":
                entry.status = "output_missing"

    def _delete_checked(self, side: str):
        if self._running():
            return
        entries = self._checked_entries()
        if not entries:
            QMessageBox.information(self, "没有勾选文件", "请先勾选列表左侧的文件框。")
            return
        source_side = side == "source"
        root = Path(self.source_edit.text() if source_side else self.output_edit.text())
        role = f"源文件（{root.name or '源目录'}）" if source_side else f"输出文件（{root.name or '输出目录'}）"
        confirm = QMessageBox.warning(
            self, f"批量删除{role}",
            f"确认将勾选项目中存在的 {len(entries)} 个{role}移入 Windows 回收站？\n\n目录：{root}\n\n只处理所选的{role}，不会删除另一侧文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        db = Database(DB_PATH)
        succeeded = 0
        missing = 0
        failures: list[str] = []
        for entry in entries:
            path = self._source_path(entry) if source_side else self._output_path(entry)
            if path is None or not path.is_file():
                missing += 1
                if source_side:
                    entry.source_exists = False
                else:
                    entry.output_exists = False
                db.set_presence(entry.relative, **({"source_exists": False} if source_side else {"output_exists": False}))
                continue
            try:
                self._send_to_recycle_bin(path, self)
                self._record_deleted(entry, side, db)
                succeeded += 1
            except OSError as exc:
                failures.append(f"{path}: {exc}")
        self._refresh_table_view()
        self._update_counts()
        details = [f"移入回收站：{succeeded}", f"文件已不存在：{missing}", f"失败：{len(failures)}"]
        if failures:
            details.extend(["", "失败路径：", *failures[:8]])
            if len(failures) > 8:
                details.append(f"另有 {len(failures) - 8} 项失败未展开。")
        QMessageBox.information(self, "批量删除结果", "\n".join(details))

    def _convert_checked_or_selected(self):
        entries = self._checked_entries()
        if not entries:
            selected = self._selected_entry()
            entries = [selected] if selected else []
        if not entries:
            QMessageBox.information(self, "没有选择文件", "请勾选文件，或先单击列表中的一行。")
            return
        self._launch_process(entries)

    def _open_file_location(self, path: Path | None):
        if path is None:
            QMessageBox.information(self, "没有输出记录", "数据库中没有这首歌的输出路径记录。")
            return
        if path.is_file():
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", f"/select,{path}"], creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        elif path.parent.is_dir():
            QMessageBox.information(self, "文件不存在", f"文件已经不存在，将打开原目录：\n{path.parent}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        else:
            QMessageBox.information(self, "文件不存在", "文件及其原目录都已不存在。")

    def _play_selected(self, kind: str):
        entry = self._selected_entry()
        if entry:
            self._play_entry(entry, kind)

    def _play_entry(self, entry: FileEntry, kind: str):
        path = self._source_path(entry) if kind == "source" else self._output_path(entry)
        if path is None or not path.is_file():
            QMessageBox.information(self, "文件不存在", "所选音频文件不存在。")
            return
        if kind == "source" and os.name == "nt":
            # Windows registry on this machine maps .mflac/.mgg to QQMusic.exe
            # with /play "%1". Shell association keeps Tencent's own player in
            # charge of its protected local formats.
            try:
                self.player.stop()
                os.startfile(str(path))
                self.playing_label.setText(f"已交给 QQ 音乐播放：{entry.filename}")
            except OSError as exc:
                QMessageBox.warning(self, "无法启动 QQ 音乐", f"打开源文件失败：\n{exc}")
            return
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.playing_label.setText(f"当前播放：{entry.filename}（{'源文件' if kind == 'source' else '输出文件'}）")

    def _toggle_audio_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif not self.player.source().isEmpty():
            self.player.play()

    def _duration_changed(self, duration: int):
        self.position_slider.setRange(0, max(0, duration))

    def _position_changed(self, position: int):
        if not self.position_slider.isSliderDown():
            self.position_slider.setValue(position)

    def _refresh_player(self):
        if self.player.source().isEmpty():
            return
        self.position_slider.setValue(self.player.position())

    def _player_error(self, error, message: str):
        if message:
            self.playing_label.setText(f"试听失败：{message}")

    @staticmethod
    def _format_size(size: int) -> str:
        return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"

    def _update_counts(self):
        tally = {key: sum(e.status == key for e in self.entries) for key in STATUS_TEXT}
        supported = sum(e.status != "unsupported" for e in self.entries)
        historical = sum(1 for e in self.entries if e.history_status == "success" or e.status == "success")
        self.counts.setText(f"总计 {supported}   新增 {tally['pending']}   完成 {tally['success']}   曾处理 {historical}   跳过 {tally['skipped']}   失败 {tally['failed']}   不支持 {tally['unsupported']}")
        self.retry_button.setEnabled(tally["failed"] > 0)
        self.start_button.setEnabled(tally["pending"] > 0 and not self._running())
        self._update_bulk_buttons()

    def start_processing(self):
        pending = [e for e in self.entries if e.status == "pending"]
        self._launch_process(pending)

    def retry_failures(self):
        failed = [e for e in self.entries if e.status == "failed"]
        self._launch_process(failed)

    def _launch_process(self, entries: list[FileEntry]):
        if self._running() or not entries:
            return
        eligible = [entry for entry in entries
                    if entry.status in ("pending", "failed", "output_missing")
                    and self._source_path(entry).is_file()]
        if not eligible:
            QMessageBox.information(self, "没有可转换文件", "所选文件都已完成处理、源文件不存在或格式不受支持。")
            return
        source, output, ffprobe, qmdec = self._paths()
        if not qmdec:
            QMessageBox.critical(self, "找不到 qmdec", "当前 Python 环境中没有找到已安装的 qmdec CLI。")
            return
        if ffprobe is None or not ffprobe.is_file():
            QMessageBox.critical(self, "找不到 ffprobe", "请在设置中选择本机 ffprobe.exe。")
            return
        output.mkdir(parents=True, exist_ok=True)
        self.progress.setRange(0, len(eligible))
        self.progress.setValue(0)
        self.current.setText(f"当前：准备处理 {len(eligible)} 个文件")
        self.worker = ProcessWorker(eligible, source, output, DB_PATH, qmdec, str(ffprobe),
                                    self.structure_check.isChecked(), self.name_check.isChecked(), LOG_PATH)
        self.worker.file_started.connect(self._file_started)
        self.worker.file_finished.connect(self._file_finished)
        self.worker.completed.connect(self._process_complete)
        self.worker.finished.connect(self._worker_finished)
        self._set_busy(True)
        self.worker.start()

    def _file_started(self, rel: str, index: int, total: int):
        self.progress.setMaximum(total)
        self.progress.setValue(index - 1)
        self.current.setText(f"当前：{rel}  ({index}/{total})")
        row = self.row_by_rel.get(rel)
        if row is not None:
            entry = self.entry_by_rel.get(rel)
            if entry:
                entry.status = "processing"
                self._set_row(row, entry, "processing")

    def _file_finished(self, rel: str, status: str, error: str):
        row = self.row_by_rel.get(rel)
        entry = self.entry_by_rel.get(rel)
        if entry is not None:
            entry.status = status
            entry.error = error
            entry.history_status = status if status == "success" else entry.history_status
            output = self._output_path(entry)
            entry.output_exists = bool(output and output.is_file())
            if row is not None:
                self._set_row(row, entry, status)
        self._update_counts()
        self._show_details()
        self.progress.setValue(self.progress.value() + 1)

    def _process_complete(self, stopped: bool):
        self.current.setText("当前：已停止（当前文件已完成）" if stopped else "当前：处理完成")
        if not stopped and self.settings.value("open_after", False, type=bool):
            self.open_output()
        self.rescan_after_process = True

    def toggle_pause(self):
        if not isinstance(self.worker, ProcessWorker):
            return
        if self.worker.pause_event.is_set():
            self.worker.pause()
            self.pause_button.setText("继续")
            self.current.setText("当前：已暂停，正在完成当前文件…")
        else:
            self.worker.resume()
            self.pause_button.setText("暂停")

    def stop_processing(self):
        if isinstance(self.worker, ProcessWorker):
            self.worker.stop()
            self.current.setText("当前：停止请求已发送，将在当前文件完成后停止")

    def open_output(self):
        _, output, _, _ = self._paths()
        output.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output)))

    def _running(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _set_busy(self, busy: bool):
        self.scan_button.setEnabled(not busy)
        self.settings_button.setEnabled(not busy)
        self.start_button.setEnabled(not busy and any(entry.status == "pending" for entry in self.entries))
        self.pause_button.setEnabled(busy and isinstance(self.worker, ProcessWorker))
        self.stop_button.setEnabled(busy and isinstance(self.worker, ProcessWorker))
        if not busy:
            self.pause_button.setText("暂停")
            self._update_counts()
        self._update_bulk_buttons()

    def _style(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#f6f8fb; color:#172033; font-family:'Segoe UI'; font-size:10pt; }
            QLabel#appTitle { font-size:22pt; font-weight:700; color:#14213d; }
            QLabel#subtitle { color:#64748b; }
            QLabel#currentLabel { color:#475569; }
            QLineEdit, QTableWidget { background:white; border:1px solid #d8e0eb; border-radius:7px; padding:6px; }
            QPushButton { background:white; border:1px solid #cbd5e1; border-radius:7px; padding:8px 14px; }
            QPushButton:hover { background:#eff6ff; border-color:#93c5fd; }
            QPushButton:disabled { color:#94a3b8; }
            QPushButton#primary { background:#2563eb; color:white; border:0; font-weight:600; }
            QPushButton#primary:hover { background:#1d4ed8; }
            QHeaderView::section { background:#edf2f7; border:0; border-bottom:1px solid #d8e0eb; padding:8px; font-weight:600; }
            QProgressBar { background:#e2e8f0; border:0; border-radius:6px; text-align:center; height:16px; }
            QProgressBar::chunk { background:#3b82f6; border-radius:6px; }
        """)

    def closeEvent(self, event):
        if self._running():
            answer = QMessageBox.question(
                self, "任务仍在运行", "当前任务尚未完成。\n\n等待当前文件完成后退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.close_after_task = True
                if isinstance(self.worker, ProcessWorker):
                    self.worker.stop()
                event.ignore()
            else:
                event.ignore()
            return
        self._save_settings()
        event.accept()
