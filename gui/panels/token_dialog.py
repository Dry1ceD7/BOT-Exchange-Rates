#!/usr/bin/env python3
"""
gui/panels/token_dialog.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — API Token Registration Dialog (PySide6)
---------------------------------------------------------------------------
Modal QDialog that collects BOT API tokens on first use.
Writes validated tokens to .env and injects them into os.environ.
"""

import logging
import os
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from core.paths import get_project_root

logger = logging.getLogger(__name__)

BOT_PORTAL_URL = "https://apiportal.bot.or.th/"
MIN_KEY_LENGTH = 8

# ── Theme Colors (Catppuccin Mocha) ──────────────────────────────────────
QSS = """
QDialog {
    background-color: #1E1E2E;
}
QLabel {
    color: #CDD6F4;
}
QLabel#Title {
    font-size: 22px;
    font-weight: 700;
    color: #89B4FA;
}
QLabel#Subtitle {
    font-size: 13px;
    color: #A6ADC8;
}
QLabel#FieldLabel {
    font-size: 11px;
    font-weight: 600;
    color: #A6ADC8;
}
QLabel#Status {
    font-size: 12px;
    color: #F38BA8;
}
QLabel#PortalLink {
    font-size: 12px;
    color: #89B4FA;
}
QLineEdit {
    background-color: #313244;
    color: #CDD6F4;
    border: 1px solid #89B4FA;
    border-radius: 8px;
    padding: 8px 12px;
    font-family: "SF Mono", "Cascadia Code", monospace;
    font-size: 13px;
}
QLineEdit:focus {
    border-color: #B4BEFE;
}
QPushButton#Activate {
    background-color: #A6E3A1;
    color: #1E1E2E;
    font-size: 15px;
    font-weight: 700;
    border: none;
    border-radius: 10px;
    padding: 10px 20px;
}
QPushButton#Activate:hover {
    background-color: #94D18A;
}
QCheckBox {
    color: #A6ADC8;
    font-size: 12px;
}
"""


class TokenRegistrationDialog(QDialog):
    """
    PySide6 modal dialog for collecting BOT API tokens.

    Usage:
        dialog = TokenRegistrationDialog(env_path=".env")
        if dialog.exec() == QDialog.Accepted:
            # tokens are now in os.environ and .env
    """

    def __init__(
        self,
        env_path: Optional[str] = None,
        prefill_exg: str = "",
        prefill_hol: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._env_path = env_path or os.path.join(get_project_root(), ".env")

        self.setWindowTitle("BOT Exchange Rate — API Registration")
        self.setFixedSize(520, 480)
        self.setStyleSheet(QSS)
        self.setWindowModality(Qt.ApplicationModal)

        self._build_ui(prefill_exg, prefill_hol)

    def _build_ui(self, prefill_exg: str, prefill_hol: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 28, 30, 20)
        layout.setSpacing(8)

        # Title
        title = QLabel("API Registration")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Enter your Bank of Thailand API keys to activate")
        subtitle.setObjectName("Subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        layout.addSpacing(16)

        # Exchange Rate Key
        lbl_exg = QLabel("EXCHANGE RATE API KEY")
        lbl_exg.setObjectName("FieldLabel")
        layout.addWidget(lbl_exg)

        self._entry_exg = QLineEdit()
        self._entry_exg.setPlaceholderText("Paste your exchange rate API key here")
        self._entry_exg.setEchoMode(QLineEdit.Password)
        if prefill_exg:
            self._entry_exg.setText(prefill_exg)
        layout.addWidget(self._entry_exg)
        layout.addSpacing(10)

        # Holiday Key
        lbl_hol = QLabel("HOLIDAY API KEY")
        lbl_hol.setObjectName("FieldLabel")
        layout.addWidget(lbl_hol)

        self._entry_hol = QLineEdit()
        self._entry_hol.setPlaceholderText("Paste your holiday API key here")
        self._entry_hol.setEchoMode(QLineEdit.Password)
        if prefill_hol:
            self._entry_hol.setText(prefill_hol)
        layout.addWidget(self._entry_hol)
        layout.addSpacing(6)

        # Show keys checkbox
        self._chk_show = QCheckBox("Show keys")
        self._chk_show.toggled.connect(self._toggle_visibility)
        layout.addWidget(self._chk_show)
        layout.addSpacing(10)

        # Status label
        self._lbl_status = QLabel("")
        self._lbl_status.setObjectName("Status")
        self._lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._lbl_status)
        layout.addSpacing(6)

        # Activate button
        btn_activate = QPushButton("Activate")
        btn_activate.setObjectName("Activate")
        btn_activate.setMinimumHeight(44)
        btn_activate.clicked.connect(self._on_activate)
        layout.addWidget(btn_activate)
        layout.addSpacing(6)

        # Portal link
        link = QLabel("Don't have keys? Register at apiportal.bot.or.th")
        link.setObjectName("PortalLink")
        link.setAlignment(Qt.AlignCenter)
        link.setCursor(Qt.PointingHandCursor)
        link.mousePressEvent = lambda _: webbrowser.open(BOT_PORTAL_URL)
        layout.addWidget(link)

    def _toggle_visibility(self, checked: bool):
        mode = QLineEdit.Normal if checked else QLineEdit.Password
        self._entry_exg.setEchoMode(mode)
        self._entry_hol.setEchoMode(mode)

    def _on_activate(self):
        exg = self._entry_exg.text().strip()
        hol = self._entry_hol.text().strip()

        if not exg or not hol:
            self._lbl_status.setText("Both API keys are required.")
            return
        if len(exg) < MIN_KEY_LENGTH or len(hol) < MIN_KEY_LENGTH:
            self._lbl_status.setText("API keys appear too short.")
            return

        try:
            self._write_env(exg, hol)
        except OSError as e:
            self._lbl_status.setText(f"Failed to save .env: {e}")
            logger.error("Failed to write .env: %s", e)
            return

        os.environ["BOT_TOKEN_EXG"] = exg
        os.environ["BOT_TOKEN_HOL"] = hol

        logger.info("API tokens activated and saved to .env")
        self.accept()

    def _write_env(self, exg: str, hol: str):
        """Write or update .env with tokens."""
        lines = []
        if os.path.exists(self._env_path):
            with open(self._env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        keys_written = {"BOT_TOKEN_EXG": False, "BOT_TOKEN_HOL": False}
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("BOT_TOKEN_EXG="):
                new_lines.append(f"BOT_TOKEN_EXG={exg}\n")
                keys_written["BOT_TOKEN_EXG"] = True
            elif stripped.startswith("BOT_TOKEN_HOL="):
                new_lines.append(f"BOT_TOKEN_HOL={hol}\n")
                keys_written["BOT_TOKEN_HOL"] = True
            else:
                new_lines.append(line)

        if not keys_written["BOT_TOKEN_EXG"]:
            new_lines.append(f"BOT_TOKEN_EXG={exg}\n")
        if not keys_written["BOT_TOKEN_HOL"]:
            new_lines.append(f"BOT_TOKEN_HOL={hol}\n")

        with open(self._env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
