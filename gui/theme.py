#!/usr/bin/env python3
"""
gui/theme.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) — Premium QSS Themes
---------------------------------------------------------------------------
Catppuccin-inspired Dark (Mocha) and Light (Latte) themes.
"""

from PySide6.QtWidgets import QApplication

# ────────────────────────────────────────────────────────────────────────
#  Color Palettes
# ────────────────────────────────────────────────────────────────────────
_DARK = {
    "base":      "#1E1E2E",
    "mantle":    "#181825",
    "crust":     "#11111B",
    "surface0":  "#313244",
    "surface1":  "#45475A",
    "surface2":  "#585B70",
    "text":      "#CDD6F4",
    "subtext":   "#A6ADC8",
    "blue":      "#89B4FA",
    "lavender":  "#B4BEFE",
    "green":     "#A6E3A1",
    "peach":     "#FAB387",
    "red":       "#F38BA8",
    "mauve":     "#CBA6F7",
    "teal":      "#94E2D5",
}

_LIGHT = {
    "base":      "#EFF1F5",
    "mantle":    "#E6E9EF",
    "crust":     "#DCE0E8",
    "surface0":  "#CCD0DA",
    "surface1":  "#BCC0CC",
    "surface2":  "#ACB0BE",
    "text":      "#4C4F69",
    "subtext":   "#6C6F85",
    "blue":      "#1E66F5",
    "lavender":  "#7287FD",
    "green":     "#40A02B",
    "peach":     "#FE640B",
    "red":       "#D20F39",
    "mauve":     "#8839EF",
    "teal":      "#179299",
}


def _build_qss(C: dict) -> str:
    return f"""
/* ── Global ──────────────────────────────────────────── */
QMainWindow {{
    background-color: {C['base']};
}}
QWidget {{
    color: {C['text']};
    font-family: "SF Pro Display", "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}

/* ── Toolbar ────────────────────────────────────────── */
QToolBar#MainToolbar {{
    background-color: {C['mantle']};
    border-bottom: 1px solid {C['surface0']};
    spacing: 6px;
    padding: 4px 8px;
}}
QPushButton#ToolbarButton {{
    background-color: {C['surface0']};
    color: {C['subtext']};
    border: 1px solid {C['surface1']};
    border-radius: 5px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 500;
}}
QPushButton#ToolbarButton:hover {{
    background-color: {C['surface1']};
    color: {C['text']};
    border-color: {C['blue']};
}}

/* ── Header ──────────────────────────────────────────── */
QLabel#AppHeader {{
    font-size: 20px;
    font-weight: 700;
    color: {C['text']};
}}
QLabel#VersionBadge {{
    font-size: 11px;
    font-weight: 600;
    color: {C['subtext']};
    background-color: {C['surface0']};
    border-radius: 4px;
    padding: 3px 8px;
}}

/* ── Section Groups ──────────────────────────────────── */
QGroupBox#SectionGroup {{
    font-weight: 600;
    font-size: 13px;
    color: {C['lavender']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
}}
QGroupBox#SectionGroup::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background-color: {C['base']};
}}

/* ── File Queue ──────────────────────────────────────── */
QListWidget#FileQueue {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    padding: 4px;
    font-family: "SF Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
    color: {C['text']};
}}
QListWidget#FileQueue::item {{
    padding: 5px 8px;
    border-radius: 4px;
}}
QListWidget#FileQueue::item:selected {{
    background-color: {C['surface1']};
    color: {C['blue']};
}}
QListWidget#FileQueue::item:hover {{
    background-color: {C['surface0']};
}}

/* ── Queue Status ────────────────────────────────────── */
QLabel#QueueStatus {{
    font-size: 12px;
    font-weight: 600;
    color: {C['green']};
}}
QLabel#FieldLabel {{
    font-size: 11px;
    font-weight: 600;
    color: {C['subtext']};
}}

/* ── Checkbox ────────────────────────────────────────── */
QCheckBox {{
    color: {C['text']};
    font-size: 13px;
    spacing: 6px;
}}

/* ── Buttons ─────────────────────────────────────────── */
QPushButton {{
    background-color: {C['surface0']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {C['surface1']};
    border-color: {C['blue']};
}}
QPushButton:pressed {{
    background-color: {C['surface2']};
}}
QPushButton:disabled {{
    background-color: {C['crust']};
    color: {C['surface2']};
    border-color: {C['surface0']};
}}
QPushButton#QueueButton {{
    font-size: 12px;
    padding: 5px 12px;
}}

/* ── Primary Action ──────────────────────────────────── */
QPushButton#PrimaryAction {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['blue']}, stop:1 {C['lavender']}
    );
    color: {C['crust']};
    font-size: 15px;
    font-weight: 700;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
}}
QPushButton#PrimaryAction:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['lavender']}, stop:1 {C['mauve']}
    );
}}
QPushButton#PrimaryAction:disabled {{
    background: {C['surface1']};
    color: {C['surface2']};
}}

/* ── ExRate Action ───────────────────────────────────── */
QPushButton#ExrateAction {{
    background-color: {C['teal']};
    color: {C['crust']};
    font-weight: 600;
    border: none;
    border-radius: 6px;
}}
QPushButton#ExrateAction:hover {{
    background-color: {C['green']};
}}
QPushButton#ExrateAction:disabled {{
    background: {C['surface1']};
    color: {C['surface2']};
}}

/* ── Secondary Action ────────────────────────────────── */
QPushButton#SecondaryAction {{
    background-color: {C['surface0']};
    color: {C['peach']};
    border: 1px solid {C['peach']};
    border-radius: 6px;
}}
QPushButton#SecondaryAction:hover {{
    background-color: {C['surface1']};
}}

/* ── Date Combos ─────────────────────────────────────── */
QComboBox#DateCombo {{
    background-color: {C['mantle']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 13px;
    min-width: 55px;
}}
QComboBox#DateCombo::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox#DateCombo QAbstractItemView {{
    background-color: {C['mantle']};
    color: {C['text']};
    border: 1px solid {C['surface1']};
    selection-background-color: {C['surface1']};
    selection-color: {C['blue']};
}}

/* ── Progress Bar ────────────────────────────────────── */
QProgressBar#BatchProgress {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface1']};
    border-radius: 6px;
    text-align: center;
    color: {C['text']};
    font-weight: 600;
    min-height: 24px;
}}
QProgressBar#BatchProgress::chunk {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['green']}, stop:1 {C['teal']}
    );
    border-radius: 5px;
}}

/* ── Console ─────────────────────────────────────────── */
QTextEdit#LiveConsole {{
    background-color: {C['crust']};
    color: {C['text']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    padding: 8px;
    selection-background-color: {C['surface1']};
}}

/* ── Status Bar ──────────────────────────────────────── */
QStatusBar {{
    background-color: {C['mantle']};
    color: {C['subtext']};
    font-size: 12px;
    border-top: 1px solid {C['surface0']};
}}

/* ── Scroll Bars ─────────────────────────────────────── */
QScrollBar:vertical {{
    background: {C['mantle']};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {C['surface1']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {C['surface2']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {C['mantle']};
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {C['surface1']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {C['surface2']};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ── Splitter ────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {C['surface0']};
    border-radius: 1px;
}}
QSplitter::handle:hover {{
    background-color: {C['blue']};
}}

/* ── Location Action ──────────────────────────────────── */
QPushButton#LocationAction {{
    background-color: {C['green']};
    color: {C['crust']};
    font-weight: 600;
    border: none;
    border-radius: 6px;
}}
QPushButton#LocationAction:hover {{
    background-color: {C['teal']};
}}

/* ── Dialogs ─────────────────────────────────────────── */
QDialog {{
    background-color: {C['base']};
    color: {C['text']};
}}
QDialog QLabel {{
    color: {C['text']};
}}
QDialog QGroupBox {{
    font-weight: 600;
    font-size: 13px;
    color: {C['lavender']};
    border: 1px solid {C['surface1']};
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
}}
QDialog QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    background-color: {C['base']};
}}
QDialog QListWidget {{
    background-color: {C['mantle']};
    border: 1px solid {C['surface0']};
    border-radius: 6px;
    color: {C['text']};
    font-size: 13px;
    padding: 4px;
}}
QDialog QListWidget::item {{
    padding: 8px 10px;
    border-radius: 4px;
}}
QDialog QListWidget::item:selected {{
    background-color: {C['surface1']};
    color: {C['blue']};
}}
QDialog QListWidget::item:hover {{
    background-color: {C['surface0']};
}}

/* ── Message Boxes ───────────────────────────────────── */
QMessageBox {{
    background-color: {C['base']};
}}
QMessageBox QLabel {{
    color: {C['text']};
}}
"""


_DARK_QSS = _build_qss(_DARK)
_LIGHT_QSS = _build_qss(_LIGHT)

# Public exports for DropZone theme-awareness
COLORS_DARK = _DARK
COLORS_LIGHT = _LIGHT
# Legacy alias
COLORS = _DARK


def apply_dark_theme(window):
    """Apply Catppuccin Mocha dark theme."""
    app = QApplication.instance()
    if app:
        app.setStyleSheet(_DARK_QSS)


def apply_light_theme(window):
    """Apply Catppuccin Latte light theme."""
    app = QApplication.instance()
    if app:
        app.setStyleSheet(_LIGHT_QSS)


# Backward-compatible alias
def apply_theme(window):
    """Apply the default dark theme."""
    apply_dark_theme(window)
