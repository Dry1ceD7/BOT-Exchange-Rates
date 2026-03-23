#!/usr/bin/env python3
"""
gui/panels/control_panel.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.7) — Control Panel
---------------------------------------------------------------------------
Drop zone, date selectors, and action buttons extracted from the monolithic
gui/app.py for SFFB compliance (< 200 lines).

This module provides the ControlPanel frame which can be embedded into the
main application layout.

SFFB: Strict < 200 lines.
"""

import logging
import os
from typing import Callable, List, Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

# Supported extensions
EXCEL_EXTENSIONS = (".xlsx", ".xls", ".xlsm", ".xlsb")

# Color tokens (shared with app.py)
COLOR_SECTION_BG = "#F8FAFC"
COLOR_TEXT_PRIMARY = "#1E293B"
COLOR_TEXT_SECONDARY = "#64748B"
COLOR_TEXT_MUTED = "#94A3B8"
COLOR_TRUST_BLUE = "#2563EB"
COLOR_BLUE_HOVER = "#1D4ED8"
COLOR_REVERT_BG = "#C2410C"
COLOR_REVERT_HOVER = "#9A3412"
COLOR_SUCCESS = "#16A34A"
COLOR_DIVIDER = "#E2E8F0"


class ControlPanel(ctk.CTkFrame):
    """
    Encapsulates the drop zone, process/revert buttons, and file queue.

    Args:
        master: Parent widget.
        on_process: Callback when "Process Batch" is clicked.
        on_revert: Callback when "Revert" is clicked.
        on_files_selected: Callback(files: List[str]) when files are queued.
    """

    def __init__(
        self,
        master,
        on_process: Optional[Callable] = None,
        on_revert: Optional[Callable] = None,
        on_files_selected: Optional[Callable] = None,
        **kwargs,
    ):
        super().__init__(master, fg_color="transparent", **kwargs)

        self._on_process = on_process
        self._on_revert = on_revert
        self._on_files_selected = on_files_selected
        self.file_queue: List[str] = []

        self._build_drop_zone()
        self._build_buttons()

    def _build_drop_zone(self):
        ctk.CTkLabel(
            self, text="LEDGER INPUT",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=COLOR_TEXT_SECONDARY,
        ).pack(pady=(14, 0))

        self._drop_zone = ctk.CTkFrame(
            self, fg_color=COLOR_SECTION_BG, corner_radius=12,
            border_width=2, border_color="#CBD5E1", height=80,
        )
        self._drop_zone.pack(pady=(8, 0), padx=30, fill="x")
        self._drop_zone.pack_propagate(False)

        inner = ctk.CTkFrame(self._drop_zone, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        self._dz_text = ctk.CTkLabel(
            inner, text="Click to select files",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLOR_TEXT_SECONDARY,
        )
        self._dz_text.pack()

        self._dz_sub = ctk.CTkLabel(
            inner, text="or drag and drop Excel ledgers",
            font=ctk.CTkFont(size=11), text_color=COLOR_TEXT_MUTED,
        )
        self._dz_sub.pack(pady=(2, 0))

        self._lbl_queue = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_SECONDARY,
        )
        self._lbl_queue.pack(pady=(4, 0))

    def _build_buttons(self):
        ctk.CTkFrame(
            self, fg_color=COLOR_DIVIDER, height=1,
        ).pack(fill="x", padx=30, pady=(12, 0))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=(16, 0))

        self.btn_process = ctk.CTkButton(
            btn_row, text="Process Batch", height=48, width=240,
            fg_color=COLOR_TRUST_BLUE, hover_color=COLOR_BLUE_HOVER,
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10, command=self._on_process, state="disabled",
        )
        self.btn_process.pack(side="left", padx=(0, 12))

        self.btn_revert = ctk.CTkButton(
            btn_row, text="Revert Previous Edit", height=48, width=200,
            fg_color=COLOR_REVERT_BG, hover_color=COLOR_REVERT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10, command=self._on_revert,
        )
        self.btn_revert.pack(side="left")

    def set_queue(self, files: List[str]) -> None:
        """Update the file queue and UI labels."""
        self.file_queue = files
        count = len(files)
        if count == 1:
            self._dz_text.configure(
                text=os.path.basename(files[0]),
                text_color=COLOR_TRUST_BLUE,
            )
        else:
            self._dz_text.configure(
                text=f"{count} ledgers loaded",
                text_color=COLOR_TRUST_BLUE,
            )
        self._dz_sub.configure(text="Click to change selection")
        self._lbl_queue.configure(
            text=f"Ready to process {count} ledger{'s' if count != 1 else ''}.",
            text_color=COLOR_SUCCESS,
        )
        self.btn_process.configure(state="normal")
        if self._on_files_selected:
            self._on_files_selected(files)
