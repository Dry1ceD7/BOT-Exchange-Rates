#!/usr/bin/env python3
"""
gui/panels/scheduler_panel.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Auto-Scheduler UI Panel
---------------------------------------------------------------------------
A collapsible panel embedded in the main GUI that controls the
background auto-processing scheduler. Provides:
  - Enable/Disable toggle switch
  - Time picker (hour + minute)
  - Watch folder list with Add/Remove
  - Status display

Persists all configuration via core/config_manager.SettingsManager.
"""

import logging
import os
from tkinter import filedialog
from typing import Callable, Optional

import customtkinter as ctk

from core.config_manager import SettingsManager

logger = logging.getLogger(__name__)

# Colors — follow the app's dark-mode first design
COLOR_PANEL_BG = ("#F0F4F8", "#162032")
COLOR_PANEL_BORDER = ("#D1D9E6", "#2D3E55")
COLOR_ACCENT = "#3B82F6"
COLOR_SUCCESS = "#22C55E"
COLOR_WARNING = "#F59E0B"
COLOR_TEXT = ("#1A202C", "#F1F5F9")
COLOR_TEXT_MUTED = ("#64748B", "#94A3B8")


class SchedulerPanel(ctk.CTkFrame):
    """
    Collapsible auto-scheduler control panel.

    When enabled, the scheduler will automatically process Excel files
    from the watched folders at the configured time each day.
    """

    def __init__(
        self,
        master,
        on_start_scheduler: Optional[Callable] = None,
        on_stop_scheduler: Optional[Callable] = None,
        **kwargs,
    ):
        super().__init__(
            master,
            fg_color=COLOR_PANEL_BG,
            corner_radius=10,
            border_width=1,
            border_color=COLOR_PANEL_BORDER,
            **kwargs,
        )

        self._on_start = on_start_scheduler
        self._on_stop = on_stop_scheduler
        self._mgr = SettingsManager()
        self._settings = self._mgr.load()

        self._build_ui()
        self._load_persisted_state()

    def _build_ui(self):
        # ── Header row with toggle ────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        ctk.CTkLabel(
            header, text="⏰ Auto-Processing",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(side="left")

        self._enable_var = ctk.StringVar(value="off")
        self._toggle = ctk.CTkSwitch(
            header,
            text="",
            variable=self._enable_var,
            onvalue="on", offvalue="off",
            width=46,
            progress_color=COLOR_SUCCESS,
            command=self._on_toggle,
        )
        self._toggle.pack(side="right")

        # ── Content (hidden when disabled) ────────────────────────────
        self._content = ctk.CTkFrame(self, fg_color="transparent")

        # Time picker row
        time_row = ctk.CTkFrame(self._content, fg_color="transparent")
        time_row.pack(fill="x", padx=12, pady=(4, 2))

        ctk.CTkLabel(
            time_row, text="Run at:",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MUTED,
        ).pack(side="left")

        self._hour_var = ctk.StringVar(value="23")
        self._hour_menu = ctk.CTkOptionMenu(
            time_row,
            variable=self._hour_var,
            values=[f"{h:02d}" for h in range(24)],
            width=60, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=("#E2E8F0", "#2D3E55"),
            button_color=(COLOR_ACCENT, COLOR_ACCENT),
            corner_radius=6,
            command=self._on_config_change,
        )
        self._hour_menu.pack(side="left", padx=(8, 2))

        ctk.CTkLabel(
            time_row, text=":",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(side="left")

        self._minute_var = ctk.StringVar(value="00")
        self._minute_menu = ctk.CTkOptionMenu(
            time_row,
            variable=self._minute_var,
            values=["00", "15", "30", "45"],
            width=60, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=("#E2E8F0", "#2D3E55"),
            button_color=(COLOR_ACCENT, COLOR_ACCENT),
            corner_radius=6,
            command=self._on_config_change,
        )
        self._minute_menu.pack(side="left", padx=2)

        # ── Watch paths section ───────────────────────────────────────
        paths_header = ctk.CTkFrame(self._content, fg_color="transparent")
        paths_header.pack(fill="x", padx=12, pady=(8, 2))

        ctk.CTkLabel(
            paths_header, text="Watch Folders:",
            font=ctk.CTkFont(size=12),
            text_color=COLOR_TEXT_MUTED,
        ).pack(side="left")

        self._btn_remove = ctk.CTkButton(
            paths_header, text="✕ Remove",
            width=80, height=26,
            font=ctk.CTkFont(size=11),
            fg_color="#DC2626", hover_color="#B91C1C",
            corner_radius=6,
            command=self._on_remove_path,
        )
        self._btn_remove.pack(side="right", padx=(4, 0))

        self._btn_add = ctk.CTkButton(
            paths_header, text="+ Add Folder",
            width=100, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=COLOR_ACCENT, hover_color="#2563EB",
            corner_radius=6,
            command=self._on_add_path,
        )
        self._btn_add.pack(side="right")

        # Path list (scrollable text box)
        self._path_list = ctk.CTkTextbox(
            self._content,
            height=60,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=("#FFFFFF", "#0F172A"),
            border_width=1,
            border_color=COLOR_PANEL_BORDER,
            corner_radius=6,
        )
        self._path_list.pack(fill="x", padx=12, pady=(0, 4))
        self._path_list.configure(state="disabled")

        # ── Status label ─────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            self._content, text="",
            font=ctk.CTkFont(size=11),
            text_color=COLOR_TEXT_MUTED,
        )
        self._lbl_status.pack(padx=12, pady=(0, 8))

        # Internal path storage
        self._paths: list = []

    def _load_persisted_state(self):
        """Load scheduler config from settings.json."""
        enabled = self._settings.get("scheduler_enabled", False)
        time_str = self._settings.get("scheduler_time", "23:00")
        paths = self._settings.get("scheduler_paths", [])

        # Parse time
        parts = time_str.split(":")
        if len(parts) == 2:
            self._hour_var.set(parts[0])
            self._minute_var.set(parts[1])

        # Load paths
        self._paths = [p for p in paths if os.path.isdir(p)]
        self._refresh_path_list()

        # Set toggle (triggers _on_toggle which shows/hides content)
        if enabled:
            self._enable_var.set("on")
            self._content.pack(fill="x", pady=(0, 4))
            self._update_status()
        else:
            self._enable_var.set("off")

    def _on_toggle(self):
        """Handle enable/disable toggle."""
        enabled = self._enable_var.get() == "on"
        if enabled:
            self._content.pack(fill="x", pady=(0, 4))
            self._update_status()
            if self._on_start:
                self._on_start(
                    f"{self._hour_var.get()}:{self._minute_var.get()}",
                    list(self._paths),
                )
        else:
            self._content.pack_forget()
            if self._on_stop:
                self._on_stop()
        self._save_config()

    def _on_config_change(self, _=None):
        """Handle time or path changes."""
        self._save_config()
        self._update_status()
        # If scheduler is running, update it live
        if self._enable_var.get() == "on" and self._on_start:
            self._on_start(
                f"{self._hour_var.get()}:{self._minute_var.get()}",
                list(self._paths),
            )

    def _on_add_path(self):
        """Add a folder to the watch list."""
        path = filedialog.askdirectory(
            title="Select folder to watch for Excel files",
        )
        if path and path not in self._paths:
            self._paths.append(path)
            self._refresh_path_list()
            self._on_config_change()

    def _on_remove_path(self):
        """Remove the last folder from the watch list."""
        if self._paths:
            self._paths.pop()
            self._refresh_path_list()
            self._on_config_change()

    def _refresh_path_list(self):
        """Refresh the path list textbox."""
        self._path_list.configure(state="normal")
        self._path_list.delete("1.0", "end")
        for p in self._paths:
            # Show abbreviated path for readability
            display = os.path.basename(p) or p
            self._path_list.insert("end", f"📁 {display}\n")
        self._path_list.configure(state="disabled")

    def _update_status(self):
        """Update the status label."""
        n = len(self._paths)
        time_str = f"{self._hour_var.get()}:{self._minute_var.get()}"
        if n == 0:
            self._lbl_status.configure(
                text="⚠ No folders selected — add at least one.",
                text_color=COLOR_WARNING,
            )
        else:
            self._lbl_status.configure(
                text=(
                    f"Next run: {time_str} — "
                    f"watching {n} folder{'s' if n != 1 else ''}"
                ),
                text_color=COLOR_SUCCESS,
            )

    def _save_config(self):
        """Persist current scheduler config to settings.json."""
        self._settings["scheduler_enabled"] = self._enable_var.get() == "on"
        self._settings["scheduler_time"] = (
            f"{self._hour_var.get()}:{self._minute_var.get()}"
        )
        self._settings["scheduler_paths"] = list(self._paths)
        self._mgr.save(self._settings)

    def get_config(self) -> dict:
        """Return current scheduler config for external use."""
        return {
            "enabled": self._enable_var.get() == "on",
            "time": f"{self._hour_var.get()}:{self._minute_var.get()}",
            "paths": list(self._paths),
        }
