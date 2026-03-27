#!/usr/bin/env python3
"""
gui/app.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — PySide6 Main Window
---------------------------------------------------------------------------
Full-featured desktop UI with: file drag-and-drop, optional date override,
standalone ExRate sheet generation, batch processing, revert, Open Location,
settings, version browser, and light/dark theme toggle.
"""

import calendar
import logging
import os
import subprocess
import sys
from datetime import date
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.paths import get_project_root
from core.version import __version__
from core.workers.event_bus import EventBus
from gui.handlers import BatchWorker, RevertWorker, StandaloneExrateWorker
from gui.theme import COLORS_DARK, COLORS_LIGHT, apply_dark_theme, apply_light_theme

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".xlsx"}
ICON_PATH = os.path.join(get_project_root(), "assets", "icon.png")


class DropZoneWidget(QWidget):
    """Theme-aware dashed-border drop zone with placeholder text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumHeight(100)
        self._hover = False
        # Theme colors (can be updated externally)
        self.bg_color = "#181825"
        self.bg_hover = "#313244"
        self.border_color = "#585B70"
        self.border_hover = "#89B4FA"
        self.text_color = "#A6ADC8"
        self.text_hover = "#CDD6F4"
        self.sub_color = "#585B70"

    def set_colors(self, theme: dict):
        """Update colors from theme palette."""
        self.bg_color = theme["mantle"]
        self.bg_hover = theme["surface0"]
        self.border_color = theme["surface2"]
        self.border_hover = theme["blue"]
        self.text_color = theme["subtext"]
        self.text_hover = theme["text"]
        self.sub_color = theme["surface2"]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPen

        bg = QColor(self.bg_hover if self._hover else self.bg_color)
        painter.setBrush(bg)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 10, 10)

        pen = QPen(QColor(self.border_hover if self._hover else self.border_color))
        pen.setStyle(Qt.DashLine)
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(4, 4, -4, -4), 8, 8)

        painter.setPen(QColor(self.text_hover if self._hover else self.text_color))
        font = QFont("SF Pro Display", 14, QFont.Bold)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, -12, 0, 0), Qt.AlignCenter,
                         "Drop .xlsx files here")

        font2 = QFont("SF Pro Display", 11)
        painter.setFont(font2)
        painter.setPen(QColor(self.sub_color))
        painter.drawText(self.rect().adjusted(0, 16, 0, 0), Qt.AlignCenter,
                         "or click Add Files below")
        painter.end()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            self._hover = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hover = False
        self.update()

    def dropEvent(self, event: QDropEvent):
        self._hover = False
        self.update()
        main_win = self.window()
        if hasattr(main_win, "_handle_drop"):
            main_win._handle_drop(event)


class BOTExrateApp(QMainWindow):
    """Main application window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"BOT Exchange Rate Processor v{__version__}")
        self.setMinimumSize(QSize(980, 700))
        self.resize(1160, 800)
        self._dark_mode = True
        self._last_output_path: Optional[str] = None

        if os.path.isfile(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.bus = EventBus()
        self._worker: Optional[BatchWorker] = None
        self._exrate_worker: Optional[StandaloneExrateWorker] = None
        self._file_queue: List[str] = []
        self._pending_update_path: Optional[str] = None

        self._build_toolbar()
        self._build_ui()
        self._apply_theme_dark()
        self._connect_signals()
        self.statusBar().showMessage("Ready — drop .xlsx files to begin")

    # ────────────────────────────────────────────────────────────────────
    #  Toolbar
    # ────────────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setObjectName("MainToolbar")
        self.addToolBar(toolbar)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)

        self.btn_theme = QPushButton("Light Mode")
        self.btn_theme.setObjectName("ToolbarButton")
        self.btn_theme.clicked.connect(self._on_toggle_theme)
        toolbar.addWidget(self.btn_theme)

        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setObjectName("ToolbarButton")
        self.btn_settings.clicked.connect(self._on_settings)
        toolbar.addWidget(self.btn_settings)

        self.btn_about = QPushButton("About")
        self.btn_about.setObjectName("ToolbarButton")
        self.btn_about.clicked.connect(self._on_about)
        toolbar.addWidget(self.btn_about)

    # ────────────────────────────────────────────────────────────────────
    #  UI
    # ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # ── Header ───────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        if os.path.isfile(ICON_PATH):
            icon_lbl = QLabel()
            icon_lbl.setPixmap(QPixmap(ICON_PATH).scaled(
                36, 36, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            hdr.addWidget(icon_lbl)
        title = QLabel("BOT Exchange Rate Processor")
        title.setObjectName("AppHeader")
        hdr.addWidget(title)
        hdr.addStretch()
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("VersionBadge")
        hdr.addWidget(ver)
        root.addLayout(hdr)

        # ── Splitter ─────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)

        # ═══ LEFT PANEL ══════════════════════════════════════════════
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.setSpacing(8)

        # ── Drop Zone + Queue ────────────────────────────────────────
        q_grp = QGroupBox("Ledger Input")
        q_grp.setObjectName("SectionGroup")
        ql = QVBoxLayout(q_grp)
        ql.setSpacing(6)

        self.drop_zone = DropZoneWidget()
        ql.addWidget(self.drop_zone)

        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QAbstractItemView.DropOnly)
        self.file_list.setMinimumHeight(100)
        self.file_list.setObjectName("FileQueue")
        ql.addWidget(self.file_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.btn_add = QPushButton("Add Files")
        self.btn_add.setObjectName("QueueButton")
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setObjectName("QueueButton")
        self.btn_clear = QPushButton("Clear All")
        self.btn_clear.setObjectName("QueueButton")
        for b in (self.btn_add, self.btn_remove, self.btn_clear):
            btn_row.addWidget(b)
        ql.addLayout(btn_row)

        self.lbl_queue = QLabel("")
        self.lbl_queue.setObjectName("QueueStatus")
        self.lbl_queue.setAlignment(Qt.AlignCenter)
        ql.addWidget(self.lbl_queue)
        ll.addWidget(q_grp)

        # ── Start Date (optional) ────────────────────────────────────
        d_grp = QGroupBox("Start Date (optional)")
        d_grp.setObjectName("SectionGroup")
        dl = QVBoxLayout(d_grp)

        self.chk_custom_date = QCheckBox("Override auto-detected date")
        self.chk_custom_date.setChecked(False)
        self.chk_custom_date.toggled.connect(self._on_date_toggle)
        dl.addWidget(self.chk_custom_date)

        self._date_row = QWidget()
        dr = QHBoxLayout(self._date_row)
        dr.setContentsMargins(0, 0, 0, 0)
        dr.setSpacing(6)

        today = date.today()
        MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        dr.addWidget(QLabel("Day:"))
        self.combo_day = QComboBox()
        self.combo_day.setObjectName("DateCombo")
        self.combo_day.addItems([str(d) for d in range(1, 32)])
        self.combo_day.setCurrentIndex(today.day - 1)
        dr.addWidget(self.combo_day)

        dr.addWidget(QLabel("Month:"))
        self.combo_month = QComboBox()
        self.combo_month.setObjectName("DateCombo")
        self.combo_month.addItems(MONTHS)
        self.combo_month.setCurrentIndex(today.month - 1)
        self.combo_month.currentIndexChanged.connect(self._on_month_changed)
        dr.addWidget(self.combo_month)

        dr.addWidget(QLabel("Year:"))
        self.combo_year = QComboBox()
        self.combo_year.setObjectName("DateCombo")
        years = [str(y) for y in range(2018, today.year + 1)]
        self.combo_year.addItems(years)
        self.combo_year.setCurrentText(str(today.year))
        self.combo_year.currentIndexChanged.connect(self._on_month_changed)
        dr.addWidget(self.combo_year)

        dl.addWidget(self._date_row)
        self._date_row.setVisible(False)
        ll.addWidget(d_grp)

        # ── Actions ──────────────────────────────────────────────────
        act_grp = QGroupBox("Actions")
        act_grp.setObjectName("SectionGroup")
        al = QVBoxLayout(act_grp)

        self.btn_process = QPushButton("Process Ledger")
        self.btn_process.setObjectName("PrimaryAction")
        self.btn_process.setMinimumHeight(48)
        al.addWidget(self.btn_process)

        sec_row = QHBoxLayout()
        sec_row.setSpacing(8)

        self.btn_exrate = QPushButton("Generate ExRate Sheet")
        self.btn_exrate.setObjectName("ExrateAction")
        self.btn_exrate.setMinimumHeight(40)
        self.btn_exrate.setToolTip(
            "Download exchange rates from BOT API and create\n"
            "a standalone ExRate .xlsx file (no input file needed)")
        sec_row.addWidget(self.btn_exrate)

        self.btn_revert = QPushButton("Revert Previous Edit")
        self.btn_revert.setObjectName("SecondaryAction")
        self.btn_revert.setMinimumHeight(40)
        sec_row.addWidget(self.btn_revert)
        al.addLayout(sec_row)

        # ── Open Location Button (hidden until processing completes) ─
        self.btn_open_location = QPushButton("Open File Location")
        self.btn_open_location.setObjectName("LocationAction")
        self.btn_open_location.setMinimumHeight(36)
        self.btn_open_location.setVisible(False)
        self.btn_open_location.clicked.connect(self._on_open_location)
        al.addWidget(self.btn_open_location)

        ll.addWidget(act_grp)

        # Progress
        self.progress = QProgressBar()
        self.progress.setObjectName("BatchProgress")
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m files processed")
        self.progress.setValue(0)
        self.progress.setMaximum(1)
        ll.addWidget(self.progress)

        # ── EventBus polling timer for real-time log updates ─────────
        self._bus_timer = QTimer(self)
        self._bus_timer.setInterval(100)  # 100ms poll
        self._bus_timer.timeout.connect(self._poll_event_bus)
        ll.addStretch()

        # ═══ RIGHT PANEL ═════════════════════════════════════════════
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)

        c_grp = QGroupBox("Processing Log")
        c_grp.setObjectName("SectionGroup")
        cl = QVBoxLayout(c_grp)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setObjectName("LiveConsole")
        self.console.setFont(QFont("SF Mono", 11))
        cl.addWidget(self.console)

        cbr = QHBoxLayout()
        self.btn_clear_log = QPushButton("Clear Log")
        self.btn_clear_log.setObjectName("QueueButton")
        self.btn_clear_log.clicked.connect(lambda: self.console.clear())
        cbr.addStretch()
        cbr.addWidget(self.btn_clear_log)
        cl.addLayout(cbr)
        rl.addWidget(c_grp)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([460, 600])
        root.addWidget(splitter, 1)
        self.setStatusBar(QStatusBar())

    # ────────────────────────────────────────────────────────────────────
    #  Signals
    # ────────────────────────────────────────────────────────────────────
    def _connect_signals(self):
        self.btn_add.clicked.connect(self._on_add_files)
        self.btn_remove.clicked.connect(self._on_remove_selected)
        self.btn_clear.clicked.connect(self._on_clear_queue)
        self.btn_process.clicked.connect(self._on_process)
        self.btn_exrate.clicked.connect(self._on_exrate)
        self.btn_revert.clicked.connect(self._on_revert)

    # ────────────────────────────────────────────────────────────────────
    #  Theme
    # ────────────────────────────────────────────────────────────────────
    def _apply_theme_dark(self):
        apply_dark_theme(self)
        self.drop_zone.set_colors(COLORS_DARK)
        self._dark_mode = True
        self.btn_theme.setText("Light Mode")

    def _apply_theme_light(self):
        apply_light_theme(self)
        self.drop_zone.set_colors(COLORS_LIGHT)
        self._dark_mode = False
        self.btn_theme.setText("Dark Mode")

    def _on_toggle_theme(self):
        if self._dark_mode:
            self._apply_theme_light()
        else:
            self._apply_theme_dark()

    # ────────────────────────────────────────────────────────────────────
    #  Date helpers
    # ────────────────────────────────────────────────────────────────────
    def _on_date_toggle(self, checked: bool):
        self._date_row.setVisible(checked)

    def _on_month_changed(self):
        try:
            year = int(self.combo_year.currentText())
            month = self.combo_month.currentIndex() + 1
            max_day = calendar.monthrange(year, month)[1]
            cur = self.combo_day.currentIndex() + 1
            self.combo_day.blockSignals(True)
            self.combo_day.clear()
            self.combo_day.addItems([str(d) for d in range(1, max_day + 1)])
            self.combo_day.setCurrentIndex(min(cur, max_day) - 1)
            self.combo_day.blockSignals(False)
        except (ValueError, IndexError):
            pass

    def _get_start_date(self) -> Optional[str]:
        if not self.chk_custom_date.isChecked():
            return None
        year = int(self.combo_year.currentText())
        month = self.combo_month.currentIndex() + 1
        day = int(self.combo_day.currentText())
        max_day = calendar.monthrange(year, month)[1]
        day = min(day, max_day)
        return f"{year:04d}-{month:02d}-{day:02d}"

    # ────────────────────────────────────────────────────────────────────
    #  Drag & Drop
    # ────────────────────────────────────────────────────────────────────
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        self._handle_drop(event)

    def _handle_drop(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()
                if ext in ALLOWED_EXTENSIONS:
                    self._add_file(path)
                else:
                    self._log(f"Rejected: {os.path.basename(path)} — only .xlsx", "warn")

    # ────────────────────────────────────────────────────────────────────
    #  File Queue
    # ────────────────────────────────────────────────────────────────────
    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Ledger Files", "", "Excel Files (*.xlsx);;All Files (*)")
        for f in files:
            self._add_file(f)

    def _add_file(self, path: str):
        if path not in self._file_queue:
            self._file_queue.append(path)
            self.file_list.addItem(os.path.basename(path))
            self._log(f"Added: {os.path.basename(path)}")
            self._update_queue_label()

    def _on_remove_selected(self):
        for item in self.file_list.selectedItems():
            idx = self.file_list.row(item)
            self.file_list.takeItem(idx)
            removed = self._file_queue.pop(idx)
            self._log(f"Removed: {os.path.basename(removed)}")
        self._update_queue_label()

    def _on_clear_queue(self):
        self._file_queue.clear()
        self.file_list.clear()
        self._log("Queue cleared")
        self._update_queue_label()

    def _update_queue_label(self):
        n = len(self._file_queue)
        if n == 0:
            self.lbl_queue.setText("")
            self.statusBar().showMessage("Ready — drop .xlsx files to begin")
        else:
            w = "ledger" if n == 1 else "ledgers"
            self.lbl_queue.setText(f"Ready to process {n} {w}")
            self.statusBar().showMessage(f"{n} file(s) in queue")

    # ────────────────────────────────────────────────────────────────────
    #  Process Ledger
    # ────────────────────────────────────────────────────────────────────
    def _on_process(self):
        if not self._file_queue:
            QMessageBox.warning(self, "No Files", "Add .xlsx files to the queue first.")
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "Processing", "A batch is already running.")
            return

        start_date = self._get_start_date()
        self._log(f"Starting batch: {len(self._file_queue)} file(s), "
                  f"date: {start_date or 'auto-detect'}")

        self.progress.setMaximum(len(self._file_queue))
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m files processed")
        self.btn_process.setEnabled(False)
        self.btn_open_location.setVisible(False)

        # Reset the event bus and start polling
        self.bus = EventBus()
        self._bus_timer.start()

        self._worker = BatchWorker(
            file_queue=list(self._file_queue),
            start_date=start_date or "",
            event_bus=self.bus,
            parent=self,
        )
        self._worker.log.connect(self._log)
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.finished.connect(self._on_batch_finished)
        self._worker.error.connect(self._on_batch_error)
        self._worker.start()

    def _on_batch_progress(self, idx, total, fname, error):
        self.progress.setValue(idx)
        pct = int((idx / total) * 100) if total else 0
        self.progress.setFormat(f"{idx} / {total} files processed ({pct}%)")
        if error:
            self._log(f"[{idx}/{total}] {fname} — SKIPPED: {error}", "error")
            self.statusBar().showMessage(f"{fname}: {error}")
        else:
            self._log(f"[{idx}/{total}] {fname} — OK", "success")
            self.statusBar().showMessage(f"{fname} ({idx}/{total}) — {pct}%")

    def _on_batch_finished(self, success, failed, errors):
        self._bus_timer.stop()
        self._poll_event_bus()  # Flush remaining events
        self.btn_process.setEnabled(True)
        self.progress.setFormat(f"{success + failed} / {success + failed} files processed (100%)")
        self._log(f"Batch complete: {success} succeeded, {failed} failed",
                  "success" if failed == 0 else "warn")
        self.statusBar().showMessage(f"Complete — {success} OK, {failed} failed")

        # Show Open Location for the first processed file
        if self._file_queue:
            self._last_output_path = self._file_queue[0]
            self.btn_open_location.setVisible(True)

        if failed > 0:
            QMessageBox.warning(self, "Batch Complete",
                f"{success} succeeded, {failed} failed:\n\n" + "\n".join(errors))
        else:
            QMessageBox.information(self, "Batch Complete",
                f"All {success} file(s) processed successfully!")

    def _on_batch_error(self, msg):
        self._bus_timer.stop()
        self.btn_process.setEnabled(True)
        self._log(f"Error: {msg}", "error")
        QMessageBox.critical(self, "Processing Error", msg)

    def _poll_event_bus(self):
        """Drain new events from the EventBus into the Processing Log."""
        for ev in self.bus.drain():
            etype = ev.get("type", "log")
            msg = ev.get("msg", "")
            level_map = {"log": "info", "success": "success",
                         "error": "error", "warn": "warn"}
            self._log(msg, level_map.get(etype, "info"))

    # ────────────────────────────────────────────────────────────────────
    #  Generate ExRate Sheet (standalone — no input file needed)
    # ────────────────────────────────────────────────────────────────────
    def _on_exrate(self):
        if self._exrate_worker and self._exrate_worker.isRunning():
            QMessageBox.information(self, "Processing", "ExRate generation is running.")
            return

        # Open config dialog for currency/rate type selection
        from gui.panels.exrate_config_dialog import ExrateConfigDialog
        dlg = ExrateConfigDialog(parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        config = dlg.get_config()
        if not config:
            return
        output_dir, year, currencies, rate_types = config

        ccy_str = ", ".join(currencies[:5])
        if len(currencies) > 5:
            ccy_str += f" (+{len(currencies) - 5} more)"
        rt_str = ", ".join(rate_types.keys())
        self._log(f"Generating ExRate sheet: {year} | Currencies: {ccy_str} | Types: {rt_str}")
        self.btn_exrate.setEnabled(False)
        self.btn_open_location.setVisible(False)

        self._exrate_worker = StandaloneExrateWorker(
            output_dir=output_dir,
            year=year,
            currencies=currencies,
            rate_types=rate_types,
            event_bus=self.bus,
            parent=self,
        )
        self._exrate_worker.log.connect(self._log)
        self._exrate_worker.finished.connect(self._on_exrate_finished)
        self._exrate_worker.error.connect(self._on_exrate_error)
        self._exrate_worker.start()

    def _on_exrate_finished(self, output_path):
        self.btn_exrate.setEnabled(True)
        self._last_output_path = output_path
        self.btn_open_location.setVisible(True)
        self._log(f"ExRate sheet created: {os.path.basename(output_path)}", "success")
        self.statusBar().showMessage(f"ExRate saved: {output_path}")
        QMessageBox.information(self, "ExRate Complete",
            f"ExRate sheet generated successfully!\n\n{output_path}")

    def _on_exrate_error(self, msg):
        self.btn_exrate.setEnabled(True)
        self._log(f"ExRate error: {msg}", "error")
        QMessageBox.critical(self, "ExRate Error", msg)

    # ────────────────────────────────────────────────────────────────────
    #  Open Location (reveal in Finder/Explorer)
    # ────────────────────────────────────────────────────────────────────
    def _on_open_location(self):
        if not self._last_output_path:
            return
        path = os.path.abspath(self._last_output_path)
        if os.path.isfile(path):
            # Reveal file in file manager
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", path], check=False)
            elif sys.platform == "win32":
                subprocess.run(["explorer", "/select,", os.path.normpath(path)], check=False)
            else:
                subprocess.run(["xdg-open", os.path.dirname(path)], check=False)
        else:
            # File not found — try opening parent directory
            parent = os.path.dirname(path)
            if os.path.isdir(parent):
                if sys.platform == "darwin":
                    subprocess.run(["open", parent], check=False)
                elif sys.platform == "win32":
                    subprocess.run(["explorer", os.path.normpath(parent)], check=False)
                else:
                    subprocess.run(["xdg-open", parent], check=False)
            else:
                QMessageBox.warning(self, "Not Found",
                    f"Could not locate:\n{path}")

    # ────────────────────────────────────────────────────────────────────
    #  Revert
    # ────────────────────────────────────────────────────────────────────
    def _on_revert(self):
        if not self._file_queue:
            QMessageBox.warning(self, "No Files", "Add a file to the queue first.")
            return
        filepath = self._file_queue[-1]
        self._log(f"Reverting: {os.path.basename(filepath)}...")
        w = RevertWorker(filepath, event_bus=self.bus, parent=self)
        w.success.connect(self._on_revert_ok)
        w.error.connect(self._on_revert_err)
        w.start()

    def _on_revert_ok(self, filepath, backup):
        self._log(f"Reverted from: {backup}", "success")
        QMessageBox.information(self, "Revert Successful", f"Restored from:\n{backup}")

    def _on_revert_err(self, msg):
        self._log(f"Revert failed: {msg}", "error")
        QMessageBox.critical(self, "Revert Failed", msg)

    # ────────────────────────────────────────────────────────────────────
    #  Settings / About
    # ────────────────────────────────────────────────────────────────────
    def _on_settings(self):
        from gui.panels.settings_modal import SettingsModal
        dlg = SettingsModal(parent=self)
        dlg.update_pending.connect(self._on_update_pending)
        dlg.exec()

    def _on_update_pending(self, path: str):
        """An update has been downloaded. Show restart-now/later dialog."""
        self._pending_update_path = path
        self._show_update_ready_dialog()

    def _show_update_ready_dialog(self):
        """Prompt user: Restart Now or Later."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Update Ready")
        msg_box.setText("A new update has been downloaded and installed.")
        msg_box.setInformativeText(
            "Would you like to restart now to apply the update, "
            "or restart later?"
        )
        btn_now = msg_box.addButton("Restart Now", QMessageBox.AcceptRole)
        msg_box.addButton("Later", QMessageBox.RejectRole)
        msg_box.setDefaultButton(btn_now)
        msg_box.exec()

        if msg_box.clickedButton() == btn_now:
            from core.auto_updater import restart_app
            restart_app()
        else:
            self._log("Update ready — will apply on next restart.", "success")

    def closeEvent(self, event):
        """Handle app close — check for pending updates."""
        if self._pending_update_path:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Pending Update")
            msg_box.setText("A downloaded update is pending.")
            msg_box.setInformativeText(
                "What would you like to do?"
            )
            btn_restart = msg_box.addButton(
                "Update && Restart", QMessageBox.AcceptRole
            )
            btn_close = msg_box.addButton(
                "Close Without Update", QMessageBox.DestructiveRole
            )
            msg_box.addButton("Cancel", QMessageBox.RejectRole)
            msg_box.setDefaultButton(btn_restart)
            msg_box.exec()

            clicked = msg_box.clickedButton()
            if clicked == btn_restart:
                from core.auto_updater import restart_app
                event.accept()
                restart_app()
            elif clicked == btn_close:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def _on_about(self):
        QMessageBox.about(self, "About",
            f"<h3>BOT Exchange Rate Processor</h3>"
            f"<p>Version {__version__}</p>"
            f"<p>Enterprise Desktop Edition</p>"
            f"<p>Processes Bank of Thailand exchange rate data<br>"
            f"and injects XLOOKUP formulas into .xlsx ledgers.</p>"
            f'<p><a href="https://github.com/Dry1ceD7/BOT-Exchange-Rates">GitHub</a></p>')

    # ────────────────────────────────────────────────────────────────────
    #  Console
    # ────────────────────────────────────────────────────────────────────
    def _log(self, msg: str, level: str = "info"):
        # Use theme-independent colors (these are always contrasting)
        colors = {"info": "#6C7086", "warn": "#DF8E1D",
                  "error": "#D20F39", "success": "#40A02B"}
        if self._dark_mode:
            colors = {"info": "#CDD6F4", "warn": "#FAB387",
                      "error": "#F38BA8", "success": "#A6E3A1"}
        prefixes = {"info": "[LOG]", "warn": "[WRN]",
                    "error": "[ERR]", "success": "[ OK]"}
        c = colors.get(level, colors["info"])
        p = prefixes.get(level, "[LOG]")
        self.console.append(f'<span style="color:{c};">{p}  {msg}</span>')
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())
