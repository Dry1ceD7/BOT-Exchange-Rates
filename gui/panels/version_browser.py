#!/usr/bin/env python3
"""
gui/panels/version_browser.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Version Browser Dialog (PySide6)
---------------------------------------------------------------------------
Lists all GitHub releases and allows downloading + installing them
directly within the app. No browser required.
"""

import logging
from typing import List, Optional

import httpx
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from core.version import __version__

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

                # Find .exe or .zip installer asset
                asset_url = None
                asset_name = None
                asset_size = 0
                for asset in rel.get("assets", []):
                    name = asset.get("name", "")
                    if name.lower().endswith((".exe", ".zip", ".dmg")):
                        asset_url = asset.get("browser_download_url")
                        asset_name = name
                        asset_size = asset.get("size", 0)
                        break

                result.append({
                    "tag": tag,
                    "label": label,
                    "is_pre": is_pre,
                    "asset_url": asset_url,
                    "asset_name": asset_name,
                    "asset_size": asset_size,
                })
            self.done.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class DownloadVersionWorker(QThread):
    """Downloads a specific version's installer asset."""
    progress = Signal(int, int)   # downloaded, total
    done = Signal(str)            # path to downloaded file
    error = Signal(str)
    status = Signal(str)          # status message

    def __init__(self, url: str, filename: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.filename = filename

    def run(self):
        try:
            from core.auto_updater import download_update
            self.status.emit(f"Downloading {self.filename}...")

            def on_progress(downloaded, total):
                self.progress.emit(downloaded, total)

            result = download_update(
                self.url,
                filename=self.filename,
                progress_cb=on_progress,
            )
            if result.get("error"):
                self.error.emit(result["error"])
            else:
                self.done.emit(result["path"])
        except Exception as e:
            self.error.emit(str(e))


class VersionBrowserDialog(QDialog):
    """Dialog showing all GitHub releases with in-app download/install."""

    # Signal emitted when a version has been downloaded and is ready
    # Parent (SettingsModal or MainWindow) should connect to this
    update_downloaded = Signal(str)  # path to downloaded file

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Version Browser")
        self.setFixedSize(500, 560)
        self.setWindowModality(Qt.ApplicationModal)
        self._releases: List[dict] = []
        self._downloaded_path: Optional[str] = None

        self._build_ui()
        self._fetch()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        title = QLabel("Version Manager")
        title.setObjectName("AppHeader")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        current = QLabel(f"Current: v{__version__}")
        current.setObjectName("VersionBadge")
        current.setAlignment(Qt.AlignCenter)
        layout.addWidget(current)

        self.lbl_status = QLabel("Fetching releases...")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)

        self.version_list = QListWidget()
        self.version_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.version_list)

        # Download progress
        self.progress = QProgressBar()
        self.progress.setObjectName("BatchProgress")
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Selected version info
        self.lbl_info = QLabel("")
        self.lbl_info.setAlignment(Qt.AlignCenter)
        self.lbl_info.setWordWrap(True)
        layout.addWidget(self.lbl_info)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_download = QPushButton("Download && Install")
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
        for rel in releases:
            item = QListWidgetItem(rel["label"])
            item.setData(Qt.UserRole, rel)
            self.version_list.addItem(item)

        self.lbl_status.setText(f"{len(releases)} versions available")

    def _on_error(self, msg):
        self.lbl_status.setText(f"Error: {msg}")

    def _on_selection_changed(self, current, _previous):
        if current is None:
            self.btn_download.setEnabled(False)
            self.lbl_info.setText("")
            return

        rel = current.data(Qt.UserRole)
        if rel and rel.get("asset_url"):
            size_mb = rel["asset_size"] / (1024 * 1024)
            self.lbl_info.setText(
                f"{rel['asset_name']} ({size_mb:.1f} MB)"
            )
            self.btn_download.setEnabled(True)
            self.btn_download.setText("Download && Install")
        else:
            self.lbl_info.setText("No installer available for this version")
            self.btn_download.setEnabled(False)

    def _on_download(self):
        item = self.version_list.currentItem()
        if not item:
            return
        rel = item.data(Qt.UserRole)
        if not rel or not rel.get("asset_url"):
            return

        self.btn_download.setEnabled(False)
        self.version_list.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress.setMaximum(rel["asset_size"] or 100)
        self.progress.setFormat("Downloading...")

        self._dl_worker = DownloadVersionWorker(
            url=rel["asset_url"],
            filename=rel["asset_name"],
            parent=self,
        )
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.status.connect(self._on_dl_status)
        self._dl_worker.done.connect(self._on_dl_done)
        self._dl_worker.error.connect(self._on_dl_error)
        self._dl_worker.start()

    def _on_dl_progress(self, downloaded, total):
        self.progress.setMaximum(total)
        self.progress.setValue(downloaded)
        if total > 0:
            pct = int(downloaded / total * 100)
            mb_dl = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.progress.setFormat(
                f"{mb_dl:.1f} / {mb_total:.1f} MB ({pct}%)"
            )

    def _on_dl_status(self, msg):
        self.lbl_info.setText(msg)

    def _on_dl_done(self, path):
        self._downloaded_path = path
        self.progress.setFormat("Download complete!")
        self.progress.setValue(self.progress.maximum())
        self.lbl_info.setText("Download complete! Installing...")

        # Apply update
        from core.auto_updater import apply_update
        result = apply_update(path)
        if result.get("success"):
            self.lbl_info.setText("Update installed successfully!")
            self.update_downloaded.emit(path)
            self.accept()
        else:
            err = result.get("error", "Unknown error")
            self.lbl_info.setText(f"Install failed: {err}")
            self.btn_download.setEnabled(True)
            self.version_list.setEnabled(True)

    def _on_dl_error(self, msg):
        self.progress.setFormat("Download failed")
        self.lbl_info.setText(f"Error: {msg}")
        self.btn_download.setEnabled(True)
        self.version_list.setEnabled(True)
        QMessageBox.warning(self, "Download Failed", msg)
