#!/usr/bin/env python3
"""
gui/panels/exrate_config_dialog.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — ExRate Sheet Configuration Dialog
---------------------------------------------------------------------------
Allows the user to choose currencies, rate types, and year for standalone
ExRate sheet generation. Inherits theme from the global QSS.
"""

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

# ── BOT API Available Currencies ────────────────────────────────────────
# Most commonly used currencies available from the BOT API
AVAILABLE_CURRENCIES = [
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "SGD",
    "HKD", "MYR", "KRW", "INR", "TWD", "CNY", "NZD",
    "DKK", "NOK", "SEK", "PHP", "IDR", "BND", "AED",
    "ZAR", "SAR", "KWD", "BHD", "QAR", "OMR", "PKR",
    "HUF", "CZK", "PLN", "BGN", "RON", "ILS", "EGP",
    "MMK", "KHR", "LAK", "VND",
]

# ── BOT API Available Rate Types ────────────────────────────────────────
AVAILABLE_RATE_TYPES = {
    "Buying TT":    "buying_transfer",
    "Buying Sight":  "buying_sight",
    "Selling":       "selling",
    "Mid Rate":      "mid_rate",
}


class ExrateConfigDialog(QDialog):
    """
    Configuration dialog for standalone ExRate sheet generation.
    Returns (output_dir, year, currencies, rate_types) on accept.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Generate ExRate Sheet")
        self.setFixedSize(520, 560)
        self.setWindowModality(Qt.ApplicationModal)

        self._output_dir: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # ── Title ────────────────────────────────────────────────────
        title = QLabel("Generate Standalone ExRate Sheet")
        title.setObjectName("AppHeader")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        desc = QLabel(
            "Select currencies and rate types to download from the BOT API.\n"
            "A new .xlsx file will be created with the selected data."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)

        # ── Year Selection ───────────────────────────────────────────
        year_grp = QGroupBox("Year")
        yl = QHBoxLayout(year_grp)
        yl.addWidget(QLabel("Generate data for year:"))
        self.combo_year = QComboBox()
        self.combo_year.setObjectName("DateCombo")
        today = date.today()
        years = [str(y) for y in range(2010, today.year + 2)]
        self.combo_year.addItems(years)
        self.combo_year.setCurrentText(str(today.year))
        yl.addWidget(self.combo_year)
        yl.addStretch()
        layout.addWidget(year_grp)

        # ── Currencies ───────────────────────────────────────────────
        ccy_grp = QGroupBox("Currencies")
        cl = QVBoxLayout(ccy_grp)

        # Quick select row
        qsr = QHBoxLayout()
        btn_select_all = QPushButton("Select All")
        btn_select_all.setObjectName("QueueButton")
        btn_select_all.clicked.connect(self._select_all_currencies)
        btn_deselect = QPushButton("Deselect All")
        btn_deselect.setObjectName("QueueButton")
        btn_deselect.clicked.connect(self._deselect_all_currencies)
        btn_common = QPushButton("Common Only")
        btn_common.setObjectName("QueueButton")
        btn_common.clicked.connect(self._select_common_currencies)
        qsr.addWidget(btn_select_all)
        qsr.addWidget(btn_deselect)
        qsr.addWidget(btn_common)
        qsr.addStretch()
        cl.addLayout(qsr)

        # Currency checkboxes in a grid-like flow
        self._ccy_checks: Dict[str, QCheckBox] = {}
        COMMON = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "SGD", "HKD", "CNY"}
        rows_layout = QVBoxLayout()
        row = QHBoxLayout()
        per_row = 5
        count = 0
        for ccy in AVAILABLE_CURRENCIES:
            cb = QCheckBox(ccy)
            cb.setChecked(ccy in COMMON)
            self._ccy_checks[ccy] = cb
            row.addWidget(cb)
            count += 1
            if count % per_row == 0:
                rows_layout.addLayout(row)
                row = QHBoxLayout()
        if count % per_row != 0:
            row.addStretch()
            rows_layout.addLayout(row)
        cl.addLayout(rows_layout)
        layout.addWidget(ccy_grp)

        # ── Rate Types ───────────────────────────────────────────────
        rt_grp = QGroupBox("Rate Types")
        rl = QVBoxLayout(rt_grp)
        self._rt_checks: Dict[str, QCheckBox] = {}
        DEFAULT_CHECKED = {"Buying TT", "Selling"}
        rt_row = QHBoxLayout()
        for label in AVAILABLE_RATE_TYPES:
            cb = QCheckBox(label)
            cb.setChecked(label in DEFAULT_CHECKED)
            self._rt_checks[label] = cb
            rt_row.addWidget(cb)
        rt_row.addStretch()
        rl.addLayout(rt_row)
        layout.addWidget(rt_grp)

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_generate = QPushButton("Choose Folder && Generate")
        self.btn_generate.setObjectName("PrimaryAction")
        self.btn_generate.setMinimumHeight(44)
        self.btn_generate.clicked.connect(self._on_generate)
        btn_row.addWidget(self.btn_generate)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    # ── Quick Select Helpers ─────────────────────────────────────────
    def _select_all_currencies(self):
        for cb in self._ccy_checks.values():
            cb.setChecked(True)

    def _deselect_all_currencies(self):
        for cb in self._ccy_checks.values():
            cb.setChecked(False)

    def _select_common_currencies(self):
        COMMON = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "SGD", "HKD", "CNY"}
        for ccy, cb in self._ccy_checks.items():
            cb.setChecked(ccy in COMMON)

    # ── Generate ─────────────────────────────────────────────────────
    def _on_generate(self):
        import os

        from PySide6.QtWidgets import QMessageBox

        currencies = [ccy for ccy, cb in self._ccy_checks.items() if cb.isChecked()]
        rate_types = {
            label: AVAILABLE_RATE_TYPES[label]
            for label, cb in self._rt_checks.items() if cb.isChecked()
        }

        if not currencies:
            QMessageBox.warning(self, "No Currencies",
                "Please select at least one currency.")
            return
        if not rate_types:
            QMessageBox.warning(self, "No Rate Types",
                "Please select at least one rate type.")
            return

        output_dir = QFileDialog.getExistingDirectory(
            self, "Select Output Folder",
            os.path.expanduser("~/Desktop"))
        if not output_dir:
            return

        self._output_dir = output_dir
        self._selected_currencies = currencies
        self._selected_rate_types = rate_types
        self._selected_year = int(self.combo_year.currentText())
        self.accept()

    # ── Public Accessors ─────────────────────────────────────────────
    def get_config(self) -> Optional[Tuple[str, int, List[str], Dict[str, str]]]:
        """Returns (output_dir, year, currencies, rate_types) or None."""
        if self._output_dir:
            return (
                self._output_dir,
                self._selected_year,
                self._selected_currencies,
                self._selected_rate_types,
            )
        return None
