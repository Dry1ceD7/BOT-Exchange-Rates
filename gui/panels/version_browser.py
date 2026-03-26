#!/usr/bin/env python3
"""
gui/panels/version_browser.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Version Browser Dialog (PySide6)
---------------------------------------------------------------------------
Lists all GitHub releases (including beta) and allows opening them.
Inherits theme from the global QSS — no hardcoded colors.
"""

import logging
import webbrowser
from typing import List, Tuple

import httpx
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

_RELEASES_URL = "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases"


class FetchReleasesWorker(QThread):
    """Fetches releases from GitHub."""
    done = Signal(list)
    error = Signal(str)

    def run(self):
        try:
            resp = httpx.get(
                _RELEASES_URL,
                headers={"Accept": "application/vnd.github+json"},
                timeout=10.0,
                params={"per_page": 30},
            )
            resp.raise_for_status()
            releases = resp.json()
            result = []
            for rel in releases:
                tag = rel.get("tag_name", "").lstrip("vV")
                is_pre = rel.get("prerelease", False)
                label = f"v{tag}  [BETA]" if is_pre else f"v{tag}"
                html_url = rel.get("html_url", "")
                result.append((tag, label, html_url, is_pre))
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class VersionBrowserDialog(QDialog):
    """Dialog showing all GitHub releases with download links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Version Browser")
        self.setFixedSize(480, 520)
        self.setWindowModality(Qt.ApplicationModal)
        self._releases: List[Tuple] = []

        self._build_ui()
        self._fetch()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("Released Versions")
        title.setObjectName("AppHeader")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        self.lbl_status = QLabel("Fetching releases...")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)

        self.version_list = QListWidget()
        layout.addWidget(self.version_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_download = QPushButton("Open in GitHub")
        self.btn_download.setObjectName("PrimaryAction")
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._on_download)
        btn_row.addWidget(self.btn_download)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)

        layout.addLayout(btn_row)

    def _fetch(self):
        self._worker = FetchReleasesWorker(parent=self)
        self._worker.done.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, releases):
        self._releases = releases
        self.version_list.clear()
        if not releases:
            self.lbl_status.setText("No releases found")
            return
        for tag, label, url, is_pre in releases:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, url)
            self.version_list.addItem(item)

        self.lbl_status.setText(f"{len(releases)} versions available")
        self.btn_download.setEnabled(True)
        self.version_list.currentItemChanged.connect(
            lambda: self.btn_download.setEnabled(True))

    def _on_error(self, msg):
        self.lbl_status.setText(f"Error: {msg}")

    def _on_download(self):
        item = self.version_list.currentItem()
        if item:
            url = item.data(Qt.UserRole)
            if url:
                webbrowser.open(url)
