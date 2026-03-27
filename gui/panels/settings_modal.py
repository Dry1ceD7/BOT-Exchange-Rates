#!/usr/bin/env python3
"""
gui/panels/settings_modal.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Settings Modal (PySide6)
---------------------------------------------------------------------------
QDialog for user preferences: API key management, API ping, version check,
and application info.
"""

import logging
import os
from typing import Optional

import httpx
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from core.config_manager import SettingsManager

logger = logging.getLogger(__name__)

_RELEASES_URL = "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases"
_BOT_API_PING = (
    "https://gateway.api.bot.or.th"
    "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
    "?start_period=2025-01-01&end_period=2025-01-02&currency=USD"
)


class PingWorker(QThread):
    """Background API ping worker."""
    done = Signal(str, str)  # text, color

    def run(self):
        try:
            token = os.environ.get("BOT_TOKEN_EXG", "")
            headers = {"accept": "application/json"}
            if token:
                clean = token.removeprefix("Bearer ").strip()
                headers["X-IBM-Client-Id"] = clean
                headers["Authorization"] = f"Bearer {clean}"
            resp = httpx.get(_BOT_API_PING, headers=headers, timeout=8.0)
            if resp.status_code == 200:
                self.done.emit("API connected & authenticated", "#A6E3A1")
            elif resp.status_code == 401:
                if token:
                    self.done.emit("API reachable but token is invalid", "#FAB387")
                else:
                    self.done.emit("API reachable — no token configured", "#FAB387")
            else:
                self.done.emit(f"API returned HTTP {resp.status_code}", "#F38BA8")
        except Exception as e:
            self.done.emit(f"Connection failed: {e}", "#F38BA8")


class UpdateCheckWorker(QThread):
    """Background update check worker."""
    done = Signal(str, str, str)  # text, color, version_or_empty

    def run(self):
        try:
            from core.auto_updater import check_for_update
            from core.version import __version__

            result = check_for_update(current_version=__version__)
            if result.get("update_available"):
                ver = result.get("latest_version", "?")
                self.done.emit(f"Update available: v{ver}", "#FAB387", ver)
            elif result.get("error"):
                self.done.emit(f"Check failed: {result['error']}", "#F38BA8", "")
            else:
                self.done.emit(f"Up to date (v{__version__})", "#A6E3A1", "")
        except Exception as e:
            self.done.emit(f"Error: {e}", "#F38BA8", "")


class DownloadUpdateWorker(QThread):
    """Background worker to download and apply an update."""
    progress = Signal(int, int)  # downloaded, total
    done = Signal(str, str)      # text, color
    restart_ready = Signal()     # emitted when restart is needed

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.version = version

    def run(self):
        try:
            from core.auto_updater import (
                apply_update,
                download_update,
                get_installer_asset_url,
            )

            self.done.emit("Finding installer...", "#A6ADC8")
            asset = get_installer_asset_url(self.version)
            if asset.get("error"):
                self.done.emit(f"Error: {asset['error']}", "#F38BA8")
                return

            url = asset["url"]
            fname = asset.get("filename", "update.exe")
            self.done.emit(f"Downloading {fname}...", "#89B4FA")

            def on_progress(downloaded, total):
                self.progress.emit(downloaded, total)

            dl = download_update(url, progress_cb=on_progress)
            if dl.get("error"):
                self.done.emit(f"Download failed: {dl['error']}", "#F38BA8")
                return

            self.done.emit("Installing update...", "#89B4FA")
            result = apply_update(dl["path"])
            if result.get("success"):
                self.done.emit("Update installed! Restarting...", "#A6E3A1")
                self.restart_ready.emit()
            else:
                self.done.emit(
                    f"Install failed: {result.get('error', 'Unknown')}",
                    "#F38BA8",
                )
        except Exception as e:
            self.done.emit(f"Error: {e}", "#F38BA8")


class SettingsModal(QDialog):
    """PySide6 settings dialog with API management and update features."""

    def __init__(self, config_dir: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setFixedSize(440, 560)
        self.setWindowModality(Qt.ApplicationModal)

        self._mgr = SettingsManager(config_dir=config_dir)
        self._settings = self._mgr.load()
        self._available_version = ""

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)

        # Title
        title = QLabel("Application Settings")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #89B4FA;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)

        # ── Auto-Update Toggle ────────────────────────────────────────
        self._chk_auto_update = QCheckBox("Check for updates on startup")
        self._chk_auto_update.setChecked(self._settings.get("auto_update", True))
        layout.addWidget(self._chk_auto_update)
        layout.addSpacing(8)

        # ── API Keys Group ────────────────────────────────────────────
        api_group = QGroupBox("API Keys")
        api_layout = QVBoxLayout(api_group)

        btn_manage = QPushButton("Manage API Keys")
        btn_manage.clicked.connect(self._on_manage_keys)
        api_layout.addWidget(btn_manage)

        self.btn_ping = QPushButton("Test API Connection")
        self.btn_ping.setObjectName("PingBtn")
        self.btn_ping.clicked.connect(self._on_ping)
        api_layout.addWidget(self.btn_ping)

        self.lbl_ping = QLabel("")
        self.lbl_ping.setObjectName("StatusLabel")
        self.lbl_ping.setAlignment(Qt.AlignCenter)
        api_layout.addWidget(self.lbl_ping)

        layout.addWidget(api_group)

        # ── Updates Group ─────────────────────────────────────────────
        update_group = QGroupBox("Updates")
        update_layout = QVBoxLayout(update_group)

        self.btn_check_update = QPushButton("Check for Updates")
        self.btn_check_update.clicked.connect(self._on_check_update)
        update_layout.addWidget(self.btn_check_update)

        self.lbl_update = QLabel("")
        self.lbl_update.setObjectName("StatusLabel")
        self.lbl_update.setAlignment(Qt.AlignCenter)
        update_layout.addWidget(self.lbl_update)

        self.btn_download_update = QPushButton("Download && Install Update")
        self.btn_download_update.setObjectName("PrimaryAction")
        self.btn_download_update.setVisible(False)
        self.btn_download_update.clicked.connect(self._on_download_update)
        update_layout.addWidget(self.btn_download_update)

        layout.addWidget(update_group)

        layout.addStretch()

        # ── Save & Close ──────────────────────────────────────────────
        btn_save = QPushButton("Save and Close")
        btn_save.setObjectName("SaveBtn")
        btn_save.setMinimumHeight(42)
        btn_save.clicked.connect(self._save_and_close)
        layout.addWidget(btn_save)

    # ── API Keys ──────────────────────────────────────────────────────
    def _on_manage_keys(self):
        from core.paths import get_project_root
        from gui.panels.token_dialog import TokenRegistrationDialog

        env_path = os.path.join(get_project_root(), ".env")
        dialog = TokenRegistrationDialog(
            env_path=env_path,
            prefill_exg=os.environ.get("BOT_TOKEN_EXG", ""),
            prefill_hol=os.environ.get("BOT_TOKEN_HOL", ""),
            parent=self,
        )
        dialog.exec()

    # ── API Ping ──────────────────────────────────────────────────────
    def _on_ping(self):
        self.lbl_ping.setText("Testing...")
        self.lbl_ping.setStyleSheet("color: #A6ADC8;")
        self.btn_ping.setEnabled(False)
        self._ping_worker = PingWorker(parent=self)
        self._ping_worker.done.connect(self._on_ping_done)
        self._ping_worker.start()

    def _on_ping_done(self, text: str, color: str):
        self.lbl_ping.setText(text)
        self.lbl_ping.setStyleSheet(f"color: {color};")
        self.btn_ping.setEnabled(True)

    # ── Update Check ──────────────────────────────────────────────────
    def _on_check_update(self):
        self.lbl_update.setText("Checking...")
        self.lbl_update.setStyleSheet("color: #A6ADC8;")
        self.btn_check_update.setEnabled(False)
        self.btn_download_update.setVisible(False)
        self._update_worker = UpdateCheckWorker(parent=self)
        self._update_worker.done.connect(self._on_update_done)
        self._update_worker.start()

    def _on_update_done(self, text: str, color: str, version: str):
        self.lbl_update.setText(text)
        self.lbl_update.setStyleSheet(f"color: {color};")
        self.btn_check_update.setEnabled(True)
        if version:
            self._available_version = version
            self.btn_download_update.setVisible(True)

    # ── Download & Install ────────────────────────────────────────────
    def _on_download_update(self):
        if not self._available_version:
            return
        self.btn_download_update.setEnabled(False)
        self.btn_check_update.setEnabled(False)
        self.lbl_update.setText("Preparing download...")
        self.lbl_update.setStyleSheet("color: #89B4FA;")

        self._dl_worker = DownloadUpdateWorker(
            version=self._available_version, parent=self,
        )
        self._dl_worker.done.connect(self._on_dl_status)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.restart_ready.connect(self._on_restart_ready)
        self._dl_worker.start()

    def _on_dl_status(self, text: str, color: str):
        self.lbl_update.setText(text)
        self.lbl_update.setStyleSheet(f"color: {color};")

    def _on_dl_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int(downloaded / total * 100)
            mb_dl = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.lbl_update.setText(
                f"Downloading... {mb_dl:.1f} / {mb_total:.1f} MB ({pct}%)"
            )

    def _on_restart_ready(self):
        """Triggered after update is installed — restart the app."""
        from core.auto_updater import restart_app
        restart_app()

    # ── Save & Close ──────────────────────────────────────────────────
    def _save_and_close(self):
        self._settings["auto_update"] = self._chk_auto_update.isChecked()
        self._mgr.save(self._settings)
        self.accept()

