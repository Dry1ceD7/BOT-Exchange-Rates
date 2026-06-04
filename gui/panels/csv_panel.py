#!/usr/bin/env python3
"""
gui/panels/csv_panel.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — CSV Import/Export section.
---------------------------------------------------------------------------
Extracted from settings_modal.py to reduce God Object line count.
Standalone frame that can be embedded into the settings modal.

SFFB: Strict < 100 lines.
"""

import logging
import threading

import customtkinter as ctk

from core.constants import humanize_save_error
from core.i18n import tr
from gui.panels._base_panel import SafePanel
from gui.theme import get_theme

logger = logging.getLogger(__name__)


def _humanize_csv_error(action: str, exc: BaseException) -> str:
    """Map a CSV import/export failure to plain-language accountant guidance.

    The raw exception detail stays in the log (logger.error). The returned
    string is the only thing shown to the user, so it must be actionable for
    a non-technical Thai accounting user — never a raw errno/traceback.

    ``action`` is the user-facing verb, e.g. "Import" or "Export".
    """
    # File locked / open in Excel / permission denied — reuse the shared
    # save-error humanizer (it already covers WinError 32 / EACCES / EBUSY).
    locked = humanize_save_error("That file", exc)
    if locked is not None:
        return f"✗ {action} failed: That file is open in another program — close it and try again."
    if isinstance(exc, FileNotFoundError):
        return f"✗ {action} failed: The file could not be found. Check the location and try again."
    if isinstance(exc, OSError):
        return f"✗ {action} failed: The file could not be read or written. Check the file and try again."
    if isinstance(exc, ValueError | KeyError):
        return f"✗ {action} failed: This CSV format wasn't recognized. Check it is a BOT exchange-rate CSV."
    return f"✗ {action} failed: An unexpected error occurred. See the log for details."


class CSVPanel(SafePanel, ctk.CTkFrame):
    """Embeddable CSV Import/Export panel for the settings modal."""

    def __init__(self, master, **kwargs):
        t = get_theme()
        super().__init__(master, fg_color="transparent", **kwargs)

        self._t = t
        self._build_ui()

    def _build_ui(self):
        t = self._t

        # ── Import Offline Rates (CSV) ────────────────────────────────
        self._btn_import = ctk.CTkButton(
            self, text=tr("csv.btn_import"),
            fg_color=t["accent_teal"], hover_color=t["accent_teal_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_import_csv,
        )
        self._btn_import.pack(fill="x", pady=(0, 4))

        self._lbl_csv = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
        )
        self._lbl_csv.pack(pady=(0, 4))

        # ── Export Cached Rates (CSV) ─────────────────────────────────
        self._btn_export = ctk.CTkButton(
            self, text=tr("csv.btn_export"),
            fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_export_csv,
        )
        self._btn_export.pack(fill="x", pady=(0, 4))

        self._lbl_csv_export = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
        )
        self._lbl_csv_export.pack(pady=(0, 8))

    def _set_buttons_enabled(self, enabled: bool):
        """Enable/disable BOTH CSV buttons so neither op can re-fire mid-run.

        Disabling both (not just the clicked one) also blocks the
        "click Export while Import is still running" collision — two worker
        threads doing concurrent SQLite writes / a second file dialog.
        """
        state = "normal" if enabled else "disabled"
        self._btn_import.configure(state=state)
        self._btn_export.configure(state=state)

    def _on_import_csv(self):
        """Open a file dialog and import a BOT CSV into the local cache."""
        from tkinter import filedialog as fd

        csv_path = fd.askopenfilename(
            title="Select BOT Exchange Rate CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not csv_path:
            return

        t = self._t
        self._set_buttons_enabled(False)
        self._lbl_csv.configure(
            text=tr("csv.importing"), text_color=t["modal_muted"],
        )
        self.update_idletasks()

        def _worker():
            try:
                from core.csv_import import import_bot_csv
                from core.database import get_cache

                cache = get_cache()
                count = import_bot_csv(csv_path, cache)
                self._safe_after(0, lambda: self._lbl_csv.configure(
                    text=tr("csv.import_ok", count=count),
                    text_color=t["modal_success"]))
            except (OSError, ValueError, KeyError) as e:
                logger.error("CSV import failed for %s: %r", csv_path, e)
                msg = _humanize_csv_error("Import", e)
                self._safe_after(0, lambda: self._lbl_csv.configure(
                    text=msg, text_color=t["error_text"]))
            finally:
                self._safe_after(0, self._set_buttons_enabled, True)

        threading.Thread(target=_worker, daemon=True, name="CSVImport").start()

    def _on_export_csv(self):
        """Open a save-file dialog and export cached rates to CSV."""
        from tkinter import filedialog as fd

        csv_path = fd.asksaveasfilename(
            title="Export Cached Rates to CSV",
            defaultextension=".csv",
            initialfile="BOT_ExRate_Export.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not csv_path:
            return

        t = self._t
        self._set_buttons_enabled(False)
        self._lbl_csv_export.configure(
            text=tr("csv.exporting"), text_color=t["modal_muted"],
        )
        self.update_idletasks()

        def _worker():
            try:
                from core.csv_export import export_rates_csv
                from core.database import get_cache

                cache = get_cache()
                count = export_rates_csv(csv_path, cache)
                self._safe_after(0, lambda: self._lbl_csv_export.configure(
                    text=tr("csv.export_ok", count=count),
                    text_color=t["modal_success"]))
            except (OSError, ValueError) as e:
                logger.error("CSV export failed for %s: %r", csv_path, e)
                msg = _humanize_csv_error("Export", e)
                self._safe_after(0, lambda: self._lbl_csv_export.configure(
                    text=msg, text_color=t["error_text"]))
            finally:
                self._safe_after(0, self._set_buttons_enabled, True)

        threading.Thread(target=_worker, daemon=True, name="CSVExport").start()
