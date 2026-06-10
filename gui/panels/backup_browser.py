#!/usr/bin/env python3
"""
gui/panels/backup_browser.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Backup Browser (browse / restore-by-date)
---------------------------------------------------------------------------
A SafePanel-backed CTkToplevel that turns the otherwise-invisible backup
store into a user-facing undo history.

The app keeps 7 days of timestamped backups per file, but the only way to use
them was "Revert Previous Edit", which restores THE most recent backup. If
today's run was bad AND yesterday's also ran, the operator could not reach the
earlier good copy — and could not even see that backups existed.

This dialog lists every backup grouped by its original file, with the exact
timestamp and size of each, and lets the operator restore a SPECIFIC one after
a confirmation that names the source backup and the target file.

Featherweight by design: it shows METADATA ONLY (timestamp + size from a
stat()), never opening a single .xlsx. The restore itself reuses
BackupManager.restore_specific (integrity check + pre-revert snapshot), runs on
a short-lived daemon thread so the Tk loop never blocks, and marshals its
result back through the app's existing _show_revert_success / _show_revert_error
callbacks so the main card reflects the outcome exactly like a normal revert.
"""

import contextlib
import logging
import threading
import tkinter
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

from core.backup_manager import BackupError, BackupManager
from core.i18n import tr
from gui.panels._base_panel import SafePanel
from gui.theme import get_theme

logger = logging.getLogger(__name__)


def _human_size(num_bytes: int) -> str:
    """Render a byte count as a compact KB/MB string (no extra deps)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _format_timestamp(ts: datetime | None) -> str:
    """Human label for a backup's embedded timestamp."""
    if ts is None:
        return "unknown time"
    return ts.strftime("%d %b %Y  %H:%M:%S")


class BackupBrowser(SafePanel, ctk.CTkToplevel):
    """Modal listing timestamped backups grouped by original file.

    Args:
        app:         The BOTExrateApp (used for busy guards + result callbacks).
        backup_mgr:  Optional BackupManager; one is created if omitted (tests
                     inject a temp-dir manager so no real data/backups is read).
    """

    def __init__(self, app, backup_mgr: BackupManager | None = None):
        super().__init__(app)
        self.app = app
        self.backup_mgr = backup_mgr or BackupManager()
        # Tracks the currently selected (filepath_stem, backup_record) so the
        # Restore button knows what to act on.
        self._selected_path: str | None = None
        self._selected_target_key: str | None = None
        self._restore_btn = None
        self._status_label = None

        t = get_theme()
        self.title(tr("backup.window_title"))
        self.geometry("560x540")
        self.minsize(480, 420)
        self.configure(fg_color=t["modal_bg"])
        self.transient(app.winfo_toplevel())
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            self.grab_set()

        self.update_idletasks()
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            sx = (self.winfo_screenwidth() - 560) // 2
            sy = (self.winfo_screenheight() - 540) // 2
            self.geometry(f"560x540+{sx}+{sy}")

        self._build(t)
        self.bind("<Escape>", lambda _e: self.destroy())

    # ------------------------------------------------------------------ #
    #  BUILD
    # ------------------------------------------------------------------ #
    def _build(self, t: dict):
        ctk.CTkLabel(
            self, text=tr("backup.heading"),
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=t["modal_text"],
        ).pack(pady=(16, 2))
        ctk.CTkLabel(
            self,
            text=tr("backup.subheading"),
            font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
        ).pack(pady=(0, 10))

        self._scroll = ctk.CTkScrollableFrame(
            self, fg_color=t["section_bg"], corner_radius=10,
        )
        self._scroll.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        self._radio_var = ctk.StringVar(value="")
        self._populate(t)

        self._status_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"], wraplength=520, justify="left",
        )
        self._status_label.pack(fill="x", padx=16, pady=(0, 4))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 14))

        ctk.CTkButton(
            btn_row, text=tr("backup.btn_close"),
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=12), corner_radius=6,
            height=34, width=110, command=self.destroy,
        ).pack(side="left")

        self._restore_btn = ctk.CTkButton(
            btn_row, text=tr("backup.btn_restore"),
            fg_color=t["revert_bg"], hover_color=t["revert_hover"],
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=6,
            height=34, width=160, command=self._on_restore, state="disabled",
        )
        self._restore_btn.pack(side="right")

    def _populate(self, t: dict):
        """Render the grouped backup list. Metadata only — never opens a file."""
        try:
            grouped = self.backup_mgr.list_grouped_backups()
        except (OSError, ValueError) as e:
            logger.debug("list_grouped_backups failed: %s", e)
            grouped = {}

        if not grouped:
            ctk.CTkLabel(
                self._scroll,
                text=tr("backup.none_found"),
                font=ctk.CTkFont(size=12),
                text_color=t["modal_muted"], justify="center",
            ).pack(pady=24)
            return

        # Map each radio value (the backup path) to its target file key so the
        # restore step can validate the chosen backup belongs to that file.
        self._value_to_key: dict[str, str] = {}
        for key, records in grouped.items():
            header = ctk.CTkFrame(self._scroll, fg_color="transparent")
            header.pack(fill="x", padx=6, pady=(10, 0))
            ctk.CTkLabel(
                header,
                text=f"{key}.xlsx  ({len(records)} backup"
                     f"{'s' if len(records) != 1 else ''})",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=t["modal_text"], anchor="w",
            ).pack(fill="x")
            for rec in records:
                label = (
                    f"{_format_timestamp(rec['timestamp'])}"
                    f"     {_human_size(rec['size_bytes'])}"
                )
                self._value_to_key[rec["path"]] = key
                ctk.CTkRadioButton(
                    self._scroll, text=label,
                    variable=self._radio_var, value=rec["path"],
                    font=ctk.CTkFont(size=11),
                    text_color=t["modal_muted"],
                    command=self._on_select,
                ).pack(anchor="w", padx=24, pady=2)

    # ------------------------------------------------------------------ #
    #  SELECTION + RESTORE
    # ------------------------------------------------------------------ #
    def _on_select(self):
        path = self._radio_var.get()
        if not path:
            return
        self._selected_path = path
        self._selected_target_key = getattr(self, "_value_to_key", {}).get(path)
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            self._restore_btn.configure(state="normal")

    def _set_status(self, text: str, *, error: bool = False):
        if self._status_label is None:
            return
        t = get_theme()
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            self._status_label.configure(
                text=text,
                text_color=t["error_text"] if error else t["modal_muted"],
            )

    def _target_filepath(self) -> str | None:
        """Resolve the live file a chosen backup should be restored over.

        Backups carry only the original file's stem, not its full path. We
        prefer the most-recently-processed file when its stem matches the
        selection; otherwise we ask the operator to point at the file via a
        native open dialog (so cross-folder originals still work)."""
        if self._selected_target_key is None:
            return None
        last = getattr(self.app, "last_processed_path", None)
        if last and Path(last).stem == self._selected_target_key:
            return last
        from tkinter import filedialog
        chosen = filedialog.askopenfilename(
            parent=self,
            title=f"Locate {self._selected_target_key}.xlsx to restore",
            initialfile=f"{self._selected_target_key}.xlsx",
            filetypes=[
                ("Excel workbooks", "*.xlsx *.xlsm"),
                ("All files", "*.*"),
            ],
        )
        return chosen or None

    def _app_busy(self) -> bool:
        """True while a batch / revert / ExRate worker owns the cache or file."""
        return (
            getattr(self.app, "_batch_running", False)
            or getattr(self.app, "_revert_running", False)
            or getattr(self.app, "_exrate_running", False)
        )

    def _refuse_busy(self):
        self._set_status(
            "Busy — a batch or revert is already running. Try again "
            "once it finishes.",
            error=True,
        )

    def _on_restore(self):
        """Confirm, then restore the selected backup over its live file."""
        if not self._selected_path:
            return
        # Never collide with an in-flight batch / revert / ExRate worker that
        # already owns the cache or the same .xlsx (#1, #3 of the wider audit).
        if self._app_busy():
            self._refuse_busy()
            return

        target = self._target_filepath()
        if not target:
            self._set_status("Restore cancelled — no target file chosen.")
            return

        ts = self.backup_mgr._parse_backup_timestamp(self._selected_path)
        when = _format_timestamp(ts)
        from tkinter import messagebox
        # Backups are keyed by stem only, so a same-named but unrelated file
        # could be auto-targeted — show the FULL target path so the operator
        # can spot a wrong target before overwriting it (F139).
        if not messagebox.askyesno(
            "Confirm Restore",
            f"Restore '{Path(target).name}' from the backup dated {when}?\n\n"
            f"Target file:\n{target}\n\n"
            f"This OVERWRITES the current file with that backup. The current "
            f"version is snapshotted first (.pre-revert) so this is "
            f"recoverable.",
            parent=self,
        ):
            return

        # Re-check the busy flags AFTER the modal waits: askopenfilename and
        # askyesno pump the Tk event loop for an unbounded time, so a scheduler
        # fire can start a batch between the first check and the confirmation
        # returning (F68 TOCTOU). Refuse rather than collide.
        if self._app_busy():
            self._refuse_busy()
            return

        # Raise the app's revert busy-flag so a scheduler fire racing in sees a
        # restore in progress and skips (mirrors _on_revert_click).
        self.app._revert_running = True
        with contextlib.suppress(RuntimeError, tkinter.TclError, AttributeError):
            self.app.btn_revert.configure(state="disabled")
            self.app.btn_process.configure(state="disabled")
        self._set_status(f"Restoring {Path(target).name}...")
        self.destroy()

        thread = threading.Thread(
            target=self._restore_thread,
            args=(target, self._selected_path),
            daemon=True,
            name="BackupRestoreWorker",
        )
        registry = getattr(self.app, "thread_registry", None)
        if registry is not None:
            with contextlib.suppress(RuntimeError):
                registry.register(thread, name="BackupRestoreWorker")
        thread.start()

    def _restore_thread(self, target: str, backup_path: str):
        """Background worker: a single fast file copy, then marshal the result
        back onto the Tk thread via the app's existing revert callbacks."""
        try:
            used = self.backup_mgr.restore_specific(target, backup_path)
            backup_name = Path(used).name
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                self.app.after(
                    0, self.app._show_revert_success, target, backup_name,
                )
        except (BackupError, OSError, ValueError) as e:
            logger.exception("Backup restore failed for %s", target)
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                self.app.after(0, self.app._show_revert_error, str(e))
        except Exception as e:  # noqa: BLE001 — fail-safe, see F140
            # Anything outside the expected tuple used to die silently in the
            # daemon thread, leaving app._revert_running True forever (Process
            # and Revert dead until restart). Log it and surface it through
            # the same error path as an expected failure.
            logger.exception(
                "Unexpected error restoring backup for %s", target,
            )
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                self.app.after(0, self.app._show_revert_error, str(e))
        finally:
            # Guarantee the busy-flag clear + UI re-enable on EVERY exit
            # path, marshalled onto the Tk thread (F140). Queued after the
            # success/error callback, so it normally no-ops (flag already
            # cleared) and only acts when neither callback got through.
            marshal = getattr(self.app, "_safe_marshal", None)
            if marshal is not None:
                marshal(self._finalize_restore)
            else:
                with contextlib.suppress(RuntimeError, tkinter.TclError):
                    self.app.after(0, self._finalize_restore)
            registry = getattr(self.app, "thread_registry", None)
            if registry is not None:
                with contextlib.suppress(RuntimeError):
                    registry.unregister("BackupRestoreWorker")

    def _finalize_restore(self):
        """Tk-thread fail-safe: clear the revert busy-flag and re-enable the
        Process/Revert buttons if _show_revert_success/_error never ran (F140).
        Idempotent — the normal callbacks clear the flag first, so this only
        acts when the worker exited without reaching either callback."""
        if not getattr(self.app, "_revert_running", False):
            return
        logger.warning(
            "Revert busy-flag still set after restore worker exit — "
            "clearing (fail-safe)",
        )
        self.app._revert_running = False
        with contextlib.suppress(RuntimeError, tkinter.TclError, AttributeError):
            self.app.btn_revert.configure(state="normal")
            self.app.btn_process.configure(state="normal")


def show_backup_browser(app, backup_mgr: BackupManager | None = None):
    """Open the Backup Browser dialog (entry point used by gui/app.py)."""
    return BackupBrowser(app, backup_mgr=backup_mgr)
