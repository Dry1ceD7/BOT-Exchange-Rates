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

from gui.theme import get_theme

logger = logging.getLogger(__name__)


class CSVPanel(ctk.CTkFrame):
    """Embeddable CSV Import/Export panel for the settings modal."""

    def __init__(self, master, **kwargs):
        t = get_theme()
        super().__init__(master, fg_color="transparent", **kwargs)

        self._t = t
        self._destroyed = False
        self._build_ui()

    def destroy(self):
        self._destroyed = True
        super().destroy()

    def _safe_after(self, ms, func, *args):
        """Thread-safe self.after() — ignores RuntimeError post-destroy."""
        if self._destroyed:
            return
        try:
            self.after(ms, func, *args)
        except RuntimeError:
            pass

    def _build_ui(self):
        t = self._t

        # ── Import Offline Rates (CSV) ────────────────────────────────
        ctk.CTkButton(
            self, text="Import Offline Rates (CSV)",
            fg_color=t["accent_teal"], hover_color=t["accent_teal_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_import_csv,
        ).pack(fill="x", pady=(0, 4))

        self._lbl_csv = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
        )
        self._lbl_csv.pack(pady=(0, 4))

        # ── Export Cached Rates (CSV) ─────────────────────────────────
        ctk.CTkButton(
            self, text="Export Cached Rates (CSV)",
            fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_export_csv,
        ).pack(fill="x", pady=(0, 4))

        self._lbl_csv_export = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
        )
        self._lbl_csv_export.pack(pady=(0, 8))

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
        self._lbl_csv.configure(text="Importing...", text_color=t["modal_muted"])
        self.update_idletasks()

        def _worker():
            try:
                from core.csv_import import import_bot_csv
                from core.engine import _get_cache

                cache = _get_cache()
                count = import_bot_csv(csv_path, cache)
                self._safe_after(0, self._lbl_csv.configure,
                           {"text": f"✓ Imported {count} rate entries",
                            "text_color": t["modal_success"]})
            except (OSError, ValueError, KeyError) as e:
                self._safe_after(0, self._lbl_csv.configure,
                           {"text": f"✗ Import failed: {e}",
                            "text_color": t["error_text"]})

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
        self._lbl_csv_export.configure(
            text="Exporting...", text_color=t["modal_muted"],
        )
        self.update_idletasks()

        def _worker():
            try:
                from core.csv_export import export_rates_csv
                from core.engine import _get_cache

                cache = _get_cache()
                count = export_rates_csv(csv_path, cache)
                self._safe_after(0, self._lbl_csv_export.configure,
                           {"text": f"✓ Exported {count} rate rows",
                            "text_color": t["modal_success"]})
            except (OSError, ValueError) as e:
                self._safe_after(0, self._lbl_csv_export.configure,
                           {"text": f"✗ Export failed: {e}",
                            "text_color": t["error_text"]})

        threading.Thread(target=_worker, daemon=True, name="CSVExport").start()
