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
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core.config_manager import SettingsManager
from core.i18n import plural, tr
from gui.theme import MONO_FONT, get_theme

logger = logging.getLogger(__name__)


class SchedulerPanel(ctk.CTkFrame):
    """
    Collapsible auto-scheduler control panel.

    When enabled, the scheduler will automatically process Excel files
    from the watched folders at the configured time each day.
    """

    def __init__(
        self,
        master,
        on_start_scheduler: Callable | None = None,
        on_stop_scheduler: Callable | None = None,
        **kwargs,
    ):
        t = get_theme()
        super().__init__(
            master,
            fg_color=t["sched_bg"],
            corner_radius=10,
            border_width=1,
            border_color=t["sched_border"],
            **kwargs,
        )

        self._on_start = on_start_scheduler
        self._on_stop = on_stop_scheduler
        self._mgr = SettingsManager()
        self._settings = self._mgr.load()

        self._build_ui()
        self._load_persisted_state()

    def _build_ui(self):
        t = get_theme()

        # ── Header row with toggle ────────────────────────────────────
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(10, 4))

        self._lbl_title = ctk.CTkLabel(
            header, text=tr("sched.title"),
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=t["text_primary"],
        )
        self._lbl_title.pack(side="left")

        self._enable_var = ctk.StringVar(value="off")
        self._toggle = ctk.CTkSwitch(
            header,
            text="",
            variable=self._enable_var,
            onvalue="on", offvalue="off",
            width=46,
            progress_color=t["success"],
            command=self._on_toggle,
        )
        self._toggle.pack(side="right")

        # ── Content (hidden when disabled) ────────────────────────────
        self._content = ctk.CTkFrame(self, fg_color="transparent")

        # Time picker row
        time_row = ctk.CTkFrame(self._content, fg_color="transparent")
        time_row.pack(fill="x", padx=12, pady=(4, 2))

        self._lbl_run_at = ctk.CTkLabel(
            time_row, text=tr("sched.run_at"),
            font=ctk.CTkFont(size=12),
            text_color=t["text_muted"],
        )
        self._lbl_run_at.pack(side="left")

        self._hour_var = ctk.StringVar(value="23")
        self._hour_menu = ctk.CTkOptionMenu(
            time_row,
            variable=self._hour_var,
            values=[f"{h:02d}" for h in range(24)],
            width=60, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=t["option_bg"],
            button_color=t["trust_blue"],
            corner_radius=6,
            command=self._on_config_change,
        )
        self._hour_menu.pack(side="left", padx=(8, 2))

        self._lbl_colon = ctk.CTkLabel(
            time_row, text=":",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=t["text_primary"],
        )
        self._lbl_colon.pack(side="left")

        self._minute_var = ctk.StringVar(value="00")
        self._minute_menu = ctk.CTkOptionMenu(
            time_row,
            variable=self._minute_var,
            # All 60 minutes (not just quarter hours) so any time — e.g. 08:05
            # — is selectable and any persisted off-grid minute round-trips
            # without silently snapping to a quarter the user never chose.
            values=[f"{m:02d}" for m in range(60)],
            width=60, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=t["option_bg"],
            button_color=t["trust_blue"],
            corner_radius=6,
            command=self._on_config_change,
        )
        self._minute_menu.pack(side="left", padx=2)

        # ── Skip options row ──────────────────────────────────────────
        skip_row = ctk.CTkFrame(self._content, fg_color="transparent")
        skip_row.pack(fill="x", padx=12, pady=(2, 0))

        self._skip_weekends_var = ctk.StringVar(value="off")
        self._chk_skip_weekends = ctk.CTkCheckBox(
            skip_row,
            text=tr("sched.skip_weekends"),
            variable=self._skip_weekends_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=11),
            text_color=t["text_muted"],
            checkmark_color=t["trust_blue"],
            hover_color=t["trust_blue"],
            command=self._on_config_change,
        )
        self._chk_skip_weekends.pack(side="left", padx=(0, 16))

        self._skip_holidays_var = ctk.StringVar(value="off")
        self._chk_skip_holidays = ctk.CTkCheckBox(
            skip_row,
            text=tr("sched.skip_holidays"),
            variable=self._skip_holidays_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=11),
            text_color=t["text_muted"],
            checkmark_color=t["trust_blue"],
            hover_color=t["trust_blue"],
            command=self._on_config_change,
        )
        self._chk_skip_holidays.pack(side="left")

        # ── Watch paths section ───────────────────────────────────────
        paths_header = ctk.CTkFrame(self._content, fg_color="transparent")
        paths_header.pack(fill="x", padx=12, pady=(8, 2))

        self._lbl_watch = ctk.CTkLabel(
            paths_header, text=tr("sched.watch_folders"),
            font=ctk.CTkFont(size=12),
            text_color=t["text_muted"],
        )
        self._lbl_watch.pack(side="left")

        self._btn_remove = ctk.CTkButton(
            paths_header, text=tr("sched.btn_remove"),
            width=80, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=t["revert_bg"], hover_color=t["revert_hover"],
            corner_radius=6,
            command=self._on_remove_path,
        )
        self._btn_remove.pack(side="right", padx=(4, 0))

        self._btn_add = ctk.CTkButton(
            paths_header, text=tr("sched.btn_add"),
            width=100, height=26,
            font=ctk.CTkFont(size=11),
            fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            corner_radius=6,
            command=self._on_add_path,
        )
        self._btn_add.pack(side="right")

        # Path list (scrollable text box)
        self._path_list = ctk.CTkTextbox(
            self._content,
            height=60,
            font=ctk.CTkFont(family=MONO_FONT, size=10),
            fg_color=t["path_list_bg"],
            border_width=1,
            border_color=t["sched_border"],
            corner_radius=6,
        )
        self._path_list.pack(fill="x", padx=12, pady=(0, 4))
        self._path_list.configure(state="disabled")

        # ── Status label ─────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            self._content, text="",
            font=ctk.CTkFont(size=11),
            text_color=t["text_muted"],
        )
        self._lbl_status.pack(padx=12, pady=(0, 8))

        # Internal path storage
        self._paths: list = []

    def _load_persisted_state(self):
        """Load scheduler config from settings.json."""
        enabled = self._settings.get("scheduler_enabled", False)
        time_str = self._settings.get("scheduler_time", "23:00")
        paths = self._settings.get("scheduler_paths", [])
        skip_weekends = self._settings.get("scheduler_skip_weekends", False)
        skip_holidays = self._settings.get("scheduler_skip_holidays", False)

        # Parse time, snapping any malformed/off-grid persisted component to a
        # value that exists in the dropdown so it round-trips. The minute menu
        # now offers all 60 values, so a normal HH:MM persists verbatim; this
        # only repairs corrupt data (e.g. "7:5", "08:37 " with stray text, or a
        # non-numeric) that would otherwise display a value the dropdown cannot
        # represent and silently jump the moment the user touches it.
        hour_opts = self._hour_menu.cget("values")
        minute_opts = self._minute_menu.cget("values")
        parts = time_str.split(":")
        if len(parts) == 2:
            self._hour_var.set(self._snap_to_options(parts[0], hour_opts, "23"))
            self._minute_var.set(
                self._snap_to_options(parts[1], minute_opts, "00")
            )

        # Load paths
        self._paths = [p for p in paths if Path(p).is_dir()]
        self._refresh_path_list()

        # Restore skip toggles.
        self._skip_weekends_var.set("on" if skip_weekends else "off")
        self._skip_holidays_var.set("on" if skip_holidays else "off")

        # Set toggle (triggers _on_toggle which shows/hides content).
        #
        # A persisted "on" state is only honored when at least one valid watch
        # folder survives the is_dir() filter above. Without a folder the
        # scheduler can never process anything, so we force the toggle back off
        # and re-persist (mirrors the zero-folder guard in _on_toggle) rather
        # than showing a "Next run" that silently fires over an empty list.
        if enabled and self._paths:
            self._enable_var.set("on")
            self._content.pack(fill="x", pady=(0, 4))
            self._update_status()
            # CRITICAL: actually arm the background scheduler on launch.
            # Restoring the toggle visual + status alone left the feature
            # silently dead across restarts — the AutoScheduler was never
            # started, so the scheduled batch never fired.
            if self._on_start:
                self._on_start(
                    f"{self._hour_var.get()}:{self._minute_var.get()}",
                    list(self._paths),
                    skip_weekends=self._skip_weekends_var.get() == "on",
                    skip_holidays=self._skip_holidays_var.get() == "on",
                )
        else:
            self._enable_var.set("off")
            if enabled and not self._paths:
                # Persisted-enabled but every watch folder is now missing:
                # repair the on-disk flag so we don't keep advertising a
                # scheduler that cannot run.
                self._content.pack(fill="x", pady=(0, 4))
                self._update_status()
                self._save_config()

    @staticmethod
    def _snap_to_options(raw: str, options, fallback: str) -> str:
        """Return a value guaranteed to exist in ``options``.

        A persisted time component is normalized to a zero-padded two-digit
        string and matched against the dropdown's option list. If the cleaned
        value is not a valid option (corrupt/legacy data), the fallback is
        returned so the dropdown never displays an unrepresentable value that
        would silently snap when the user opens it.
        """
        opts = list(options)
        cleaned = (raw or "").strip()
        if cleaned in opts:
            return cleaned
        try:
            padded = f"{int(cleaned):02d}"
        except (TypeError, ValueError):
            return fallback
        return padded if padded in opts else fallback

    def _on_toggle(self):
        """Handle enable/disable toggle."""
        enabled = self._enable_var.get() == "on"
        if enabled:
            # Refuse to arm with zero watch folders: the scheduler would
            # "start" and persist scheduler_enabled=True but could never
            # process anything (it scans an empty path list night after
            # night). Reveal the content so the warning is visible, snap the
            # switch back off, and do NOT persist an enabled state.
            self._content.pack(fill="x", pady=(0, 4))
            if not self._paths:
                self._enable_var.set("off")
                self._update_status()
                self._save_config()
                return
            self._update_status()
            if self._on_start:
                self._on_start(
                    f"{self._hour_var.get()}:{self._minute_var.get()}",
                    list(self._paths),
                    skip_weekends=self._skip_weekends_var.get() == "on",
                    skip_holidays=self._skip_holidays_var.get() == "on",
                )
        else:
            self._content.pack_forget()
            if self._on_stop:
                self._on_stop()
        self._save_config()

    def _on_config_change(self, _=None):
        """Handle time, path, or skip-option changes."""
        self._save_config()
        self._update_status()
        # If scheduler is running, update it live
        if self._enable_var.get() == "on" and self._on_start:
            self._on_start(
                f"{self._hour_var.get()}:{self._minute_var.get()}",
                list(self._paths),
                skip_weekends=self._skip_weekends_var.get() == "on",
                skip_holidays=self._skip_holidays_var.get() == "on",
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
        """Remove a selected folder from the watch list via a simple dialog."""
        if not self._paths:
            return
        # If only one path, just remove it directly
        if len(self._paths) == 1:
            self._paths.clear()
            self._refresh_path_list()
            self._on_config_change()
            return
        # Show a selection dialog for multiple paths. Snapshot the list so the
        # chosen index maps to a stable value, then remove BY VALUE (not by the
        # possibly-stale index) in case the list changed during the dialog.
        from gui.panels._path_chooser import choose_path_to_remove
        snapshot = list(self._paths)
        idx = choose_path_to_remove(self, snapshot)
        if idx is not None and 0 <= idx < len(snapshot):
            target = snapshot[idx]
            if target in self._paths:
                self._paths.remove(target)
                self._refresh_path_list()
                self._on_config_change()

    def _refresh_path_list(self):
        """Refresh the path list textbox."""
        self._path_list.configure(state="normal")
        self._path_list.delete("1.0", "end")
        for p in self._paths:
            # Show abbreviated path for readability.
            # noqa: PTH119 — os.path.basename returns "" for a trailing-sep
            # dir so the `or p` fallback shows the full path; Path.name would
            # return the last segment instead. Keep exact display behavior.
            display = os.path.basename(p) or p  # noqa: PTH119
            self._path_list.insert("end", f"📁 {display}\n")
        self._path_list.configure(state="disabled")

    def _update_status(self):
        """Update the status label."""
        t = get_theme()
        n = len(self._paths)
        time_str = f"{self._hour_var.get()}:{self._minute_var.get()}"
        if n == 0:
            self._lbl_status.configure(
                text=tr("sched.status_no_folders"),
                text_color=t["warning"],
            )
        else:
            self._lbl_status.configure(
                text=tr(
                    "sched.status_next_run",
                    time=time_str, count=n, plural=plural(n),
                ),
                text_color=t["success"],
            )

    def _save_config(self):
        """Persist current scheduler config to settings.json.

        Writes ONLY the three scheduler_* keys via SettingsManager.set(),
        which does a locked read-modify-write against the current on-disk
        state. The panel previously called mgr.save(self._settings) with a
        stale full-blob snapshot taken at construction — so if the Settings
        modal changed (e.g.) rate_type after this panel loaded, the next
        scheduler toggle/time/folder change silently reverted that edit. By
        touching only our own keys we never clobber settings owned elsewhere.
        """
        scheduler_keys = {
            "scheduler_enabled": self._enable_var.get() == "on",
            "scheduler_time": (
                f"{self._hour_var.get()}:{self._minute_var.get()}"
            ),
            "scheduler_paths": list(self._paths),
            "scheduler_skip_weekends": self._skip_weekends_var.get() == "on",
            "scheduler_skip_holidays": self._skip_holidays_var.get() == "on",
        }
        for key, value in scheduler_keys.items():
            self._mgr.set(key, value)
        # Keep our local snapshot consistent for any later reads in this panel.
        self._settings.update(scheduler_keys)

    def get_config(self) -> dict:
        """Return current scheduler config for external use."""
        return {
            "enabled": self._enable_var.get() == "on",
            "time": f"{self._hour_var.get()}:{self._minute_var.get()}",
            "paths": list(self._paths),
            "skip_weekends": self._skip_weekends_var.get() == "on",
            "skip_holidays": self._skip_holidays_var.get() == "on",
        }

    def apply_theme(self, t: dict) -> None:
        """Re-apply theme colors to EVERY interior widget.

        A theme switch (Light <-> Dark) previously recolored only the frame and
        title, leaving the time dropdowns, Watch-Folders label, +Add/Remove
        buttons, the folder-list box and the static labels stuck in the old
        palette — a visibly half-themed panel (e.g. a white folder list on a
        dark card). Reconfigure each widget with the same tokens used at build.
        """
        self.configure(
            fg_color=t["sched_bg"],
            border_color=t["sched_border"],
        )
        if hasattr(self, "_lbl_title"):
            self._lbl_title.configure(text_color=t["text_primary"])
        if hasattr(self, "_lbl_colon"):
            self._lbl_colon.configure(text_color=t["text_primary"])
        # Static muted sub-labels.
        for attr in ("_lbl_run_at", "_lbl_watch"):
            lbl = getattr(self, attr, None)
            if lbl is not None:
                lbl.configure(text_color=t["text_muted"])
        # Skip-option checkboxes.
        for attr in ("_chk_skip_weekends", "_chk_skip_holidays"):
            chk = getattr(self, attr, None)
            if chk is not None:
                chk.configure(
                    text_color=t["text_muted"],
                    checkmark_color=t["trust_blue"],
                    hover_color=t["trust_blue"],
                )
        # Time option menus.
        for menu in (
            getattr(self, "_hour_menu", None),
            getattr(self, "_minute_menu", None),
        ):
            if menu is not None:
                menu.configure(
                    fg_color=t["option_bg"],
                    button_color=t["trust_blue"],
                )
        # Action buttons.
        if hasattr(self, "_btn_add"):
            self._btn_add.configure(
                fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            )
        if hasattr(self, "_btn_remove"):
            self._btn_remove.configure(
                fg_color=t["revert_bg"], hover_color=t["revert_hover"],
            )
        # Folder-list textbox.
        if hasattr(self, "_path_list"):
            self._path_list.configure(
                fg_color=t["path_list_bg"],
                border_color=t["sched_border"],
            )
        # Recompute the status label so its success/warning color is correct
        # for the new palette (rather than freezing the old-theme color).
        if hasattr(self, "_lbl_status"):
            self._update_status()
