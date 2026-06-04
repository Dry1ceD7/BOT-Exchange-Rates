#!/usr/bin/env python3
"""
gui/app.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Enterprise Desktop Edition (v3.1.0)
---------------------------------------------------------------------------
Zero-emoji, typography-driven corporate UI.

Features:
 - Smart Date Toggle (CTkSwitch, defaults to today)
 - Universal Drop Zone (tkinterdnd2 with click-browse fallback)
 - File/Folder routing, batch queue with per-file progress
 - Live Processing Console (EventBus-driven)
 - Settings Modal (JSON-backed persistence)
 - Auto-Updater Engine (GitHub Releases API)
 - Revert button — restores corrupted files from automatic backups
 - Rate Ticker Widget — live USD/EUR rates in header (v3.1.0)
 - Auto-Scheduler Panel — background scheduled processing (v3.1.0)
"""

import contextlib
import logging
import os
import platform
import re
import subprocess
import threading
import tkinter
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core.backup_manager import BackupManager
from core.config_manager import SettingsManager
from core.version import __version__ as APP_VERSION
from core.workers.event_bus import EventBus
from gui.handlers import BatchHandler
from gui.panels.live_console import LiveConsolePanel
from gui.theme import get_theme

# Load user settings and apply appearance
_settings_mgr = SettingsManager()
_user_settings = _settings_mgr.load()
ctk.set_appearance_mode(_user_settings.get("appearance", "system"))
logger = logging.getLogger(__name__)

def _get_colors() -> dict:
    """Return the live theme palette. Always fresh — never stale."""
    return get_theme()

# ── Attempt tkinterdnd2 ──────────────────────────────────────────────────
HAS_DND = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError as e:
    logger.debug("tkinterdnd2 not available: %s", e)


def parse_drop_data(raw: str, tk_root=None) -> list[str]:
    """Parse drag-and-drop payload. Uses native Tcl/Tk splitlist for
    cross-platform correctness ({} bracket stripping on macOS/Linux)."""
    if tk_root is not None:
        try:
            return list(tk_root.tk.splitlist(raw))
        except (RuntimeError, ValueError) as e:
            logger.debug("Tcl splitlist failed: %s", e)
    # Fallback: only use the regex splitter when the payload is brace-delimited
    # ({}-wrapped paths, which Tk uses for paths containing spaces). For a plain
    # payload, treat the whole string as ONE path so paths with spaces survive.
    if "{" in raw:
        results = []
        for match in re.finditer(r'\{([^}]+)\}|(\S+)', raw):
            path = match.group(1) or match.group(2)
            if path:
                results.append(path.strip())
        return results
    raw = raw.strip()
    return [raw] if raw else []


# Supported Excel extensions (openpyxl handles .xlsx and .xlsm natively)
EXCEL_EXTENSIONS = (".xlsx", ".xlsm")


def resolve_excel_files(paths: list[str], collect_rejected: bool = False):
    """Resolve individual files and directories into a flat list of Excel files.

    When ``collect_rejected`` is True, returns ``(accepted, rejected)`` where
    ``rejected`` lists directly-dropped files with an unsupported spreadsheet
    extension (e.g. .xlsb, .xls) so the caller can warn the user. Otherwise
    returns just the accepted list (backward compatible).
    """
    # Spreadsheet-looking extensions we explicitly recognise as unsupported.
    UNSUPPORTED_EXTENSIONS = (".xlsb", ".xls", ".ods", ".csv")
    queue = []
    rejected = []
    for p in paths:
        if Path(p).is_file():
            base = Path(p).name
            if base.startswith("."):
                continue
            if p.lower().endswith(EXCEL_EXTENSIONS):
                queue.append(p)
            elif p.lower().endswith(UNSUPPORTED_EXTENSIONS):
                rejected.append(p)
        elif Path(p).is_dir():
            # Keep os.listdir + os.path.join: queued entries are full-path
            # strings handed to the engine; sorting bare names then joining is
            # the exact prior behavior the os.path.normpath dedup relies on.
            for fname in sorted(os.listdir(p)):  # noqa: PTH208
                if fname.startswith("."):
                    continue
                if fname.lower().endswith(EXCEL_EXTENSIONS):
                    queue.append(os.path.join(p, fname))  # noqa: PTH118
    seen = set()
    unique = []
    for f in queue:
        norm = os.path.normpath(f)
        if norm not in seen:
            seen.add(norm)
            unique.append(f)
    if collect_rejected:
        return unique, rejected
    return unique


class BOTExrateApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f"BOT Exchange Rate Processor  |  V{APP_VERSION}")
        self.geometry("740x960")
        self.resizable(False, True)
        self.configure(fg_color=_get_colors()["bg"])

        # ── Set window icon ──────────────────────────────────────────────
        self._set_app_icon()

        self.file_queue: list[str] = []
        self.last_processed_path: str | None = None
        # True while a batch (manual OR scheduler-fired) is running. Guards the
        # drop zone / browse / queue from re-enabling Process Batch mid-run and
        # lets the scheduler path lock the same controls a manual run does.
        self._batch_running = False
        # True only while the in-flight batch was started by the auto-scheduler
        # (not the manual button). Drives the tray notification + last-run
        # summary on completion so an overnight, minimised run is not invisible
        # (#1). Cleared on every batch terminal path.
        self._scheduled_run_active = False
        # True while a manual revert (BackupManager.restore_latest) is in flight.
        # start_revert spawns a RevertWorker that does NOT touch the batch guard,
        # and _on_revert_click never sets _batch_running, so the scheduler's
        # programmatic entry point (_begin_scheduled_batch) must consult this flag
        # to avoid two threads touching the same .xlsx (#3). Set in
        # _on_revert_click, cleared on every revert terminal path.
        self._revert_running = False
        # Holds the always-visible "Failed files" summary box, built lazily on
        # the first batch that reports failures (#2).
        self._failed_box = None
        self.backup_mgr = BackupManager()
        self.event_bus = EventBus()
        # Single registry that tracks worker threads (batch, revert, ...) so
        # an in-progress openpyxl save can finish/report before exit (#5).
        from core.workers.thread_registry import ThreadRegistry
        self.thread_registry = ThreadRegistry()
        self.batch_handler = BatchHandler(
            self, event_bus=self.event_bus, registry=self.thread_registry,
        )

        # Center window
        self.update_idletasks()
        w, h = 740, 960
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

        # DnD injection
        self.dnd_enabled = False
        if HAS_DND:
            try:
                TkinterDnD._require(self)
                self.dnd_enabled = True
            except (RuntimeError, OSError) as e:
                logger.debug("DnD init failed: %s", e)

        # Auto-updater manager (extracted module)
        from gui.panels.update_banner import UpdateManager
        self._updater = UpdateManager(self)

        # v3.2.0: Dry-run simulation mode flag
        self._dry_run_var = ctk.StringVar(value="off")

        self._build_header()
        self._build_footer()
        self._build_card()
        self._build_live_console()
        self._updater.check_for_updates()

        # Default close path: clean teardown of workers, then destroy. When the
        # tray is active it overrides this to hide-to-tray and the real exit
        # routes through the tray Exit item back into _on_app_close (#1).
        self._closing = False
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)

        # v3.2.0: System Tray — minimize to tray on close
        from gui.panels.tray_manager import TrayManager
        self._tray = TrayManager(self)
        self._tray.setup()

    def _set_app_icon(self):
        """Load and set the application window icon (works in source + frozen mode)."""
        import sys
        from tkinter import PhotoImage

        try:
            # Resolve assets directory
            if getattr(sys, "frozen", False):
                # Frozen (PyInstaller): assets bundled alongside exe
                base_dir = Path(sys.executable).parent
            else:
                # Source mode: project root.
                # noqa: PTH100,PTH120 — os.path.abspath avoids symlink
                # resolution to keep the exact legacy base dir; wrap in Path
                # for the joins below.
                base_dir = Path(os.path.dirname(os.path.abspath(__file__))).parent  # noqa: PTH100, PTH120

            ico_path = base_dir / "assets" / "icon.ico"
            png_path = base_dir / "assets" / "icon.png"

            # Windows: use .ico for taskbar + title bar
            if platform.system() == "Windows" and ico_path.exists():
                # Tk/Tcl expects a string path here.
                self.iconbitmap(str(ico_path))
                logger.info("Window icon set from: %s", ico_path)
            # All platforms: use .png via iconphoto for Tk title bar
            elif png_path.exists():
                try:
                    icon_image = PhotoImage(file=str(png_path))
                except tkinter.TclError:
                    # Fallback: use PIL to convert PNG → Tk-compatible format
                    try:
                        from PIL import Image, ImageTk
                        pil_img = Image.open(png_path).resize((64, 64))
                        icon_image = ImageTk.PhotoImage(pil_img)
                    except ImportError:
                        logger.debug("PIL not available for icon fallback")
                        return
                self.iconphoto(True, icon_image)
                # Keep a reference so it's not garbage-collected
                self._icon_ref = icon_image
                logger.info("Window icon set from: %s", png_path)
            else:
                logger.debug("No icon file found at %s or %s", ico_path, png_path)
        except (tkinter.TclError, OSError, ValueError) as e:
            logger.debug("Icon loading failed (non-critical): %s", e)

    # ================================================================== #
    #  HEADER
    # ================================================================== #
    def _build_header(self):
        t = _get_colors()
        self.hdr_frame = ctk.CTkFrame(
            self, fg_color=t["header_bg"], corner_radius=0,
            border_width=0,
        )
        self.hdr_frame.pack(fill="x")

        inner = ctk.CTkFrame(self.hdr_frame, fg_color="transparent")
        inner.pack(pady=(10, 8))

        self.lbl_header_title = ctk.CTkLabel(
            inner, text="Bank of Thailand  —  Ledger Processor",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=t["header_text"]
        )
        self.lbl_header_title.pack()

        sub_row = ctk.CTkFrame(inner, fg_color="transparent")
        sub_row.pack(pady=(2, 0))
        self.lbl_header_sub = ctk.CTkLabel(
            sub_row, text="Enterprise Desktop Edition",
            font=ctk.CTkFont(size=11), text_color=t["header_sub"]
        )
        self.lbl_header_sub.pack(side="left")

        # Settings button — visible, proper button styling
        self._btn_settings = ctk.CTkButton(
            sub_row, text="⚙  Settings", width=90, height=26,
            fg_color=t["settings_btn"], hover_color=t["settings_btn_hover"],
            text_color=t["settings_btn_text"],
            font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6,
            border_width=1, border_color=t["settings_btn_border"],
            command=self._open_settings,
        )
        self._btn_settings.pack(side="left", padx=(12, 0))

        # v3.2.1: Ticker integrated cleanly into header without separate background strip
        from core.database import CacheDB
        from gui.panels.rate_ticker import RateTicker
        try:
            self._cache_db = CacheDB()
            self.rate_ticker = RateTicker(
                inner, cache_db=self._cache_db,
            )
            # Center the ticker below the subtitle row
            self.rate_ticker.pack(pady=(2, 0))
            self.rate_ticker.start()
            # Register the ticker worker so the close handler can stop it (#5).
            if getattr(self.rate_ticker, "_worker", None) is not None:
                self.thread_registry.register(
                    self.rate_ticker._worker,
                    name="RateTickerWorker",
                    stop_event=getattr(self.rate_ticker, "_stop_event", None),
                )
        except (RuntimeError, OSError) as e:
            logger.debug("Rate ticker init failed (non-critical): %s", e)
            self._cache_db = None
            self.rate_ticker = None

    # ================================================================== #
    #  CARD
    # ================================================================== #
    def _build_card(self):
        t = _get_colors()
        self.card = ctk.CTkFrame(
            self, fg_color=t["card_bg"], corner_radius=16,
            border_width=1, border_color=t["card_border"]
        )
        self.card.pack(pady=22, padx=36, fill="both", expand=True)

        # ── 1. DATE SECTION ──────────────────────────────────────────────
        self.lbl_date_section = ctk.CTkLabel(
            self.card, text="RATE EXTRACTION DATE",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=t["text_secondary"]
        )
        self.lbl_date_section.pack(pady=(20, 0))

        # ── V2.4: Auto-Detect Toggle (primary) ───────────────────────────
        auto_row = ctk.CTkFrame(self.card, fg_color="transparent")
        auto_row.pack(pady=(8, 0))

        self.auto_detect_var = ctk.StringVar(value="on")
        self.toggle_auto = ctk.CTkSwitch(
            auto_row, text="  Auto-Detect Date Range from Ledger",
            variable=self.auto_detect_var, onvalue="on", offvalue="off",
            command=self._on_auto_detect_changed,
            font=ctk.CTkFont(size=13, weight="bold"), text_color=t["text_primary"],
            progress_color=t["trust_blue"],
            button_color=t["switch_thumb"],
            button_hover_color=t["switch_hover"],
            fg_color=t["switch_track"],
        )
        self.toggle_auto.pack()

        self.lbl_auto_hint = ctk.CTkLabel(
            self.card,
            text="Start date will be read from your Excel files automatically.",
            font=ctk.CTkFont(size=11), text_color=t["trust_blue"]
        )
        self.lbl_auto_hint.pack(pady=(4, 4))

        # ── Manual Override Section (hidden when auto-detect is ON) ──────
        self.manual_date_frame = ctk.CTkFrame(self.card, fg_color="transparent")
        # (starts hidden — auto-detect is ON by default)

        # "Use Today's Date" sub-toggle (inside manual section)
        toggle_row = ctk.CTkFrame(self.manual_date_frame, fg_color="transparent")
        toggle_row.pack(pady=(4, 0))

        self.use_today_var = ctk.StringVar(value="on")
        self.toggle_today = ctk.CTkSwitch(
            toggle_row, text="  Use Today's Date",
            variable=self.use_today_var, onvalue="on", offvalue="off",
            command=self._on_toggle_changed,
            font=ctk.CTkFont(size=12), text_color=t["text_secondary"],
            progress_color=t["success"], button_color=t["card_bg"],
            button_hover_color=t["switch_hover"], fg_color=t["switch_track"]
        )
        self.toggle_today.pack()

        self.lbl_toggle_hint = ctk.CTkLabel(
            self.manual_date_frame,
            text=f"Rates will be extracted up to: {date.today().strftime('%d %b %Y')}",
            font=ctk.CTkFont(size=11), text_color=t["success"]
        )
        self.lbl_toggle_hint.pack(pady=(4, 4))

        # Date dropdowns
        date_row = ctk.CTkFrame(self.manual_date_frame, fg_color="transparent")
        date_row.pack()
        current_year = date.today().year
        self._combo_widgets = []

        for label_text, width, values, default, attr in [
            ("Year",  100, [str(y) for y in range(2020, current_year + 1)], "2025", "combo_year"),
            ("Month",  80, [f"{m:02d}" for m in range(1, 13)],              "01",   "combo_month"),
            ("Day",    80, [f"{d:02d}" for d in range(1, 32)],              "01",   "combo_day"),
        ]:
            grp = ctk.CTkFrame(date_row, fg_color="transparent")
            grp.pack(side="left", padx=8)
            ctk.CTkLabel(grp, text=label_text.upper(),
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=t["text_secondary"]).pack()
            combo = ctk.CTkComboBox(
                grp, values=values, width=width, height=36,
                fg_color=t["combo_bg"], border_color=t["combo_border"],
                button_color=t["trust_blue"], button_hover_color=t["blue_hover"],
                dropdown_fg_color=t["card_bg"], text_color=t["text_primary"],
                font=ctk.CTkFont(size=13), justify="center"
            )
            combo.set(default)
            combo.pack(pady=(4, 0))
            setattr(self, attr, combo)
            self._combo_widgets.append(combo)

        self._lock_date_dropdowns(locked=True)

        # ── Divider ──────────────────────────────────────────────────────
        ctk.CTkFrame(self.card, fg_color=t["divider"], height=1).pack(fill="x", padx=50, pady=(16, 0))

        # ── 2. DROP ZONE ─────────────────────────────────────────────────
        self.lbl_input_section = ctk.CTkLabel(
            self.card, text="LEDGER INPUT",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=t["text_secondary"]
        )
        self.lbl_input_section.pack(pady=(14, 0))

        self.drop_zone = ctk.CTkFrame(
            self.card, fg_color=t["section_bg"], corner_radius=12,
            border_width=2, border_color=t["drop_border"], height=80
        )
        self.drop_zone.pack(pady=(8, 0), padx=50, fill="x")
        self.drop_zone.pack_propagate(False)

        dz_inner = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        dz_inner.place(relx=0.5, rely=0.5, anchor="center")

        dnd_hint = "Drop Excel files or folders here" if self.dnd_enabled else "Click to select files"
        self.dz_text = ctk.CTkLabel(
            dz_inner, text=dnd_hint,
            font=ctk.CTkFont(size=14, weight="bold"), text_color=t["text_secondary"]
        )
        self.dz_text.pack()
        self.dz_sub = ctk.CTkLabel(dz_inner, text="or click to browse",
                                    font=ctk.CTkFont(size=11), text_color=t["text_muted"])
        self.dz_sub.pack(pady=(2, 0))

        for widget in [self.drop_zone, dz_inner, self.dz_text, self.dz_sub]:
            widget.bind("<Button-1>", lambda e: self._browse_files())

        if self.dnd_enabled:
            try:
                self.drop_zone.drop_target_register(DND_FILES)
                self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)
                # Register DnD on child widgets too — they intercept events
                # from reaching the parent drop zone
                for child in [dz_inner, self.dz_text, self.dz_sub]:
                    try:
                        child.drop_target_register(DND_FILES)
                        child.dnd_bind("<<Drop>>", self._on_drop)
                    except (RuntimeError, OSError) as e:
                        logger.debug("DnD bind failed for child widget: %s", e)
            except (RuntimeError, OSError) as e:
                logger.warning("DnD registration failed: %s", e)
                self.dnd_enabled = False

        self.lbl_queue = ctk.CTkLabel(
            self.card, text="", font=ctk.CTkFont(size=12), text_color=t["text_secondary"]
        )
        self.lbl_queue.pack(pady=(4, 0))

        # ── Divider ──────────────────────────────────────────────────────
        ctk.CTkFrame(self.card, fg_color=t["divider"], height=1).pack(fill="x", padx=50, pady=(12, 0))

        # ── 3. ACTION BUTTONS ────────────────────────────────────────────
        btn_row = ctk.CTkFrame(self.card, fg_color="transparent")
        btn_row.pack(pady=(16, 0))

        self.btn_process = ctk.CTkButton(
            btn_row, text="Process Batch",
            height=48, width=240,
            fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10, command=self._on_process_click, state="disabled"
        )
        self.btn_process.pack(side="left", padx=(0, 12))

        self.btn_revert = ctk.CTkButton(
            btn_row, text="Revert Previous Edit",
            height=48, width=200,
            fg_color=t["revert_bg"], hover_color=t["revert_hover"],
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10, command=self._on_revert_click
        )
        self.btn_revert.pack(side="left", padx=(0, 12))

        # ── v3.2.0: Dry-Run Simulation Toggle ────────────────────────
        sim_row = ctk.CTkFrame(self.card, fg_color="transparent")
        sim_row.pack(pady=(8, 0))
        self.toggle_dryrun = ctk.CTkSwitch(
            sim_row, text="  Simulation Mode (Dry Run)",
            variable=self._dry_run_var, onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=11), text_color=t["text_secondary"],
            progress_color=t["warning"], button_color=t["switch_thumb"],
            button_hover_color=t["switch_hover"], fg_color=t["switch_track"],
        )
        self.toggle_dryrun.pack()
        self.lbl_dryrun_hint = ctk.CTkLabel(
            sim_row, text="Preview changes in the Processing Log without modifying files.",
            font=ctk.CTkFont(size=10), text_color=t["text_muted"],
        )
        self.lbl_dryrun_hint.pack(pady=(2, 0))

        self.btn_export_exrate = ctk.CTkButton(
            btn_row, text="ExRate Sheet",
            height=48, width=160,
            fg_color=t["accent_indigo"], hover_color=t["accent_indigo_hover"],
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10, command=self._on_export_exrate
        )
        self.btn_export_exrate.pack(side="left")

        # ── 4. STATUS BOX ────────────────────────────────────────────────
        status_box = ctk.CTkFrame(
            self.card, fg_color=t["section_bg"], corner_radius=10,
            border_width=1, border_color=t["card_border"]
        )
        status_box.pack(pady=(16, 0), padx=50, fill="x", ipady=8)

        self.lbl_status = ctk.CTkLabel(
            status_box, text="Status:  Ready  —  Backups enabled",
            font=ctk.CTkFont(size=13), text_color=t["text_secondary"]
        )
        self.lbl_status.pack(pady=(8, 4))

        self.progressbar = ctk.CTkProgressBar(
            status_box, width=440, height=8,
            progress_color=t["trust_blue"], corner_radius=4
        )
        self.progressbar.pack(pady=(0, 10))
        self.progressbar.set(0)

        # ── 5. REVEAL BUTTON (hidden by default) ────────────────────────
        self.btn_reveal = ctk.CTkButton(
            self.card, text="Show File in Folder",
            height=40, width=220,
            fg_color=t["warning"], hover_color=t["warning_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, command=self._reveal_file
        )

        # ── 6. SCHEDULER PANEL (v3.1.0) ──────────────────────────────
        from gui.panels.scheduler_panel import SchedulerPanel
        self.scheduler_panel = SchedulerPanel(
            self.card,
            on_start_scheduler=self._on_scheduler_start,
            on_stop_scheduler=self._on_scheduler_stop,
        )
        self.scheduler_panel.pack(pady=(16, 0), padx=50, fill="x")

    # ================================================================== #
    #  V2.4: AUTO-DETECT TOGGLE
    # ================================================================== #
    def _on_auto_detect_changed(self):
        """Toggle between auto-detect and manual date entry."""
        is_auto = self.auto_detect_var.get() == "on"
        if is_auto:
            self.manual_date_frame.pack_forget()
            self.lbl_auto_hint.configure(
                text="Start date will be read from your Excel files automatically.",
                text_color=_get_colors()["trust_blue"]
            )
        else:
            self.lbl_auto_hint.configure(
                text="Manual override — select a start date below.",
                text_color=_get_colors()["warning"]
            )
            # Show the manual section right after the auto-hint label
            self.manual_date_frame.pack(after=self.lbl_auto_hint, pady=(0, 4))
            self._on_toggle_changed()  # Sync dropdown state with "Use Today" sub-toggle

    # ================================================================== #
    #  SMART DATE TOGGLE (manual sub-toggle)
    # ================================================================== #
    def _on_toggle_changed(self):
        is_today = self.use_today_var.get() == "on"
        self._lock_date_dropdowns(locked=is_today)
        if is_today:
            self.lbl_toggle_hint.configure(
                text=f"Rates will be extracted up to: {date.today().strftime('%d %b %Y')}",
                text_color=_get_colors()["success"]
            )
        else:
            self.lbl_toggle_hint.configure(
                text="Select a custom historical start date below.",
                text_color=_get_colors()["trust_blue"]
            )

    def _lock_date_dropdowns(self, locked: bool):
        for combo in self._combo_widgets:
            combo.configure(state="disabled" if locked else "normal")

    def _assemble_start_date(self) -> str | None:
        if self.use_today_var.get() == "on":
            return datetime.today().strftime("%Y-%m-%d")
        date_str = f"{self.combo_year.get()}-{self.combo_month.get()}-{self.combo_day.get()}"
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror(
                "Invalid Date",
                f"The selected date '{date_str}' is not valid.\n\n"
                f"Please select a valid calendar date."
            )
            return None
        return date_str

    # ================================================================== #
    #  DROP / BROWSE
    # ================================================================== #
    def _on_drop(self, event):
        if self._batch_running:
            self._flash_busy_status()
            return
        paths = parse_drop_data(event.data, tk_root=self)
        excel_files, rejected = resolve_excel_files(paths, collect_rejected=True)
        if rejected:
            names = ", ".join(Path(f).name for f in rejected)
            messagebox.showwarning(
                "Format Warning",
                f"These files use an unsupported format:\n{names}\n\n"
                f"Please save them as .xlsx or .xlsm first."
            )
        if excel_files:
            self._set_queue(excel_files)
        elif rejected:
            messagebox.showwarning("No Valid Files",
                                   "No supported Excel files found.")
        else:
            messagebox.showwarning("No Valid Files",
                                   "No Excel files found in the dropped items.")

    def _browse_files(self):
        if self._batch_running:
            self._flash_busy_status()
            return
        paths = filedialog.askopenfilenames(
            title="Select Excel Ledgers",
            filetypes=[
                ("Excel workbooks", "*.xlsx *.xlsm"),
                ("All files", "*.*")
            ]
        )
        if paths:
            self._set_queue(list(paths))

    def _set_queue(self, files: list[str]):
        # A batch in flight owns the queue/selection — never mutate it or
        # re-enable Process Batch mid-run (#1). The drop/browse handlers already
        # short-circuit, but guard here too in case _set_queue is called direct.
        if self._batch_running:
            self._flash_busy_status()
            return
        self.file_queue = files
        self.last_processed_path = None
        count = len(files)
        if count == 1:
            self.dz_text.configure(text=Path(files[0]).name, text_color=_get_colors()["trust_blue"])
        else:
            self.dz_text.configure(text=f"{count} ledgers loaded", text_color=_get_colors()["trust_blue"])
        self.dz_sub.configure(text="Click to change selection")
        self.lbl_queue.configure(
            text=f"Ready to process {count} ledger{'s' if count != 1 else ''}.",
            text_color=_get_colors()["success"]
        )
        self.btn_process.configure(state="normal")
        self.btn_reveal.pack_forget()

    def _flash_busy_status(self):
        """Tell the operator the UI is busy instead of silently swallowing the
        click/drop. Used by the drop zone / browse / queue guards (#1)."""
        if hasattr(self, "lbl_status"):
            self.lbl_status.configure(
                text="Busy:  a batch is already running — please wait.",
                text_color=_get_colors()["warning"],
            )

    # ================================================================== #
    #  PROCESSING
    # ================================================================== #
    def _lock_ui_for_batch(self):
        """Disable every action a manual or scheduled batch must own while it
        runs and raise the busy flag (#1, #3). Shared by the manual click and
        the scheduler callback so a background run locks the UI identically."""
        self._batch_running = True
        self.btn_process.configure(state="disabled")
        self.btn_revert.configure(state="disabled")
        self.btn_export_exrate.configure(state="disabled")
        self.btn_reveal.pack_forget()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(0)

    def _unlock_ui_after_batch(self):
        """Re-enable the controls locked by _lock_ui_for_batch and clear the
        busy flag. Called from every batch terminal path (complete/error)."""
        self._batch_running = False
        self.btn_process.configure(state="normal")
        self.btn_revert.configure(state="normal")
        self.btn_export_exrate.configure(state="normal")

    def _on_process_click(self):
        if not self.file_queue or self._batch_running:
            return
        # Snapshot the selection at click time so a selection change during
        # the background prescan can't desync what actually gets processed (#2).
        queue_snapshot = list(self.file_queue)
        self._lock_ui_for_batch()
        total = len(queue_snapshot)

        is_auto = self.auto_detect_var.get() == "on"

        if is_auto:
            # ── V2.4: Smart Date Auto-Detection ──────────────────────
            self.lbl_status.configure(
                text=f"Scanning {total} ledger{'s' if total != 1 else ''} for date range...",
                text_color=_get_colors()["process_text"]
            )
            # Run prescan in background thread to prevent UI freeze
            def _prescan_and_batch():
                from core.engine import LedgerEngine
                oldest_date, was_detected = LedgerEngine.prescan_oldest_date(queue_snapshot)
                start_date_str = oldest_date.strftime("%Y-%m-%d")

                def _update_ui_and_start():
                    if was_detected:
                        self.lbl_auto_hint.configure(
                            text=f"Detected: {oldest_date.strftime('%d %b %Y')} → {date.today().strftime('%d %b %Y')}",
                            text_color=_get_colors()["success"]
                        )
                        self.lbl_status.configure(
                            text=(
                                f"Connecting to BOT API...  range: "
                                f"{oldest_date.strftime('%d %b %Y')} → today  (0 of {total})"
                            ),
                            text_color=_get_colors()["process_text"]
                        )
                    else:
                        self.lbl_auto_hint.configure(
                            text=f"No dates found — using fallback: {oldest_date.strftime('%d %b %Y')}",
                            text_color=_get_colors()["warning"]
                        )
                        self.lbl_status.configure(
                            text=f"Connecting to BOT API...  fallback range  (0 of {total})",
                            text_color=_get_colors()["warning"]
                        )
                    dry_run = self._dry_run_var.get() == "on"
                    self.batch_handler.start_batch(
                        queue_snapshot, start_date_str, dry_run=dry_run,
                    )

                self.after(0, _update_ui_and_start)

            threading.Thread(target=_prescan_and_batch, daemon=True).start()
        else:
            # ── Manual mode ──────────────────────────────────────────
            start_date_str = self._assemble_start_date()
            if start_date_str is None:
                self._unlock_ui_after_batch()
                return
            self.lbl_status.configure(
                text=f"Connecting to BOT API...  (0 of {total})",
                text_color=_get_colors()["process_text"]
            )
            dry_run = self._dry_run_var.get() == "on"
            self.batch_handler.start_batch(
                queue_snapshot, start_date_str, dry_run=dry_run,
            )

    def _update_progress(self, idx: int, total: int, fname: str, error):
        self.progressbar.set(idx / total)
        if error:
            self.lbl_status.configure(
                text=f"Warning:  {idx} of {total}  |  {fname} — skipped",
                text_color=_get_colors()["warning"]
            )
        else:
            self.lbl_status.configure(
                text=f"Processing:  {idx} of {total}  |  {fname}",
                text_color=_get_colors()["process_text"]
            )

    def _show_batch_complete(self, success: int, fail: int, errors: list[str]):
        self.progressbar.set(1)
        # Capture whether this was a scheduler-fired run BEFORE unlock clears
        # nothing (it doesn't touch the flag) — done here so the notification
        # path below fires exactly once and only for scheduled runs (#1).
        was_scheduled = self._scheduled_run_active
        self._scheduled_run_active = False
        self._unlock_ui_after_batch()
        if fail == 0:
            self.lbl_status.configure(
                text=f"Complete:  All {success} ledger{'s' if success != 1 else ''} processed successfully.",
                text_color=_get_colors()["success"]
            )
            self._render_failed_files([])
        else:
            self.lbl_status.configure(
                text=f"Complete:  {success} succeeded, {fail} failed.  See failed files below.",
                text_color=_get_colors()["warning"]
            )
            # Surface WHICH files failed and WHY so the operator can act on
            # them — the bare count alone hides actionable detail (#2).
            self._render_failed_files(errors or [])
        if self.file_queue:
            self.last_processed_path = self.file_queue[-1]
            self.btn_reveal.pack(pady=(12, 14))
        # A scheduler-fired run may have completed while minimised to the tray;
        # surface the outcome so it is never invisible (#1).
        if was_scheduled:
            self._announce_scheduled_run(success, fail)
        # Force UI refresh so the user sees the updated state immediately
        self.update_idletasks()

    def _announce_scheduled_run(self, success: int, fail: int) -> None:
        """Surface the outcome of a scheduler-fired batch (#1).

        Fires a tray balloon notification with succeeded/failed counts (Windows
        /pystray path; graceful no-op elsewhere), records a retrievable last-run
        summary in the tray menu, persists it to settings.json so the scheduler
        panel can show it across restarts, and auto-restores the window when the
        run had any failures so the operator is pulled back to the detail.
        """
        ts = datetime.now().strftime("%d %b %H:%M")
        summary = f"{success} OK, {fail} failed ({ts})"
        title = "BOT ExRate — Scheduled Run"
        message = (
            f"{success} ledger{'s' if success != 1 else ''} processed, "
            f"{fail} failed."
        )

        # Tray notification + retrievable last-run summary (guarded — the tray
        # is Windows-only and may be absent on this platform/build).
        tray = getattr(self, "_tray", None)
        if tray is not None:
            try:
                if hasattr(tray, "set_last_run"):
                    tray.set_last_run(summary)
                if hasattr(tray, "notify"):
                    tray.notify(message, title)
            except (RuntimeError, OSError) as e:
                logger.debug("Tray notification failed (non-critical): %s", e)

        # Persist a last-run record so the scheduler panel / a future session
        # can show "last run" even after the tray summary is gone.
        try:
            _settings_mgr.set(
                "scheduler_last_run",
                {"success": success, "fail": fail, "summary": summary},
            )
        except (OSError, ValueError, TypeError) as e:
            logger.debug("Persisting scheduler_last_run failed: %s", e)

        # Pull the operator back to the window on failure so the failed-files
        # box is seen rather than buried behind a minimised tray icon.
        if fail > 0:
            try:
                self.restore_from_tray()
            except (RuntimeError, tkinter.TclError) as e:
                logger.debug("Auto-restore on failure failed: %s", e)

    def _render_failed_files(self, errors: list[str]):
        """Render the failed-file reasons into an always-visible, scrollable
        box under the status line (#2). An empty list hides the box.

        Each entry in ``errors`` is already a ``"<filename>: <reason>"`` string
        from ``LedgerEngine.process_batch``; we surface them verbatim, one row
        per failure, so the operator sees what to fix without digging through
        the live console history.
        """
        # Tear down any prior box first so repeated batches don't stack frames.
        if self._failed_box is not None:
            try:
                self._failed_box.destroy()
            except (RuntimeError, tkinter.TclError) as e:
                logger.debug("failed-box teardown failed: %s", e)
            self._failed_box = None
        if not errors:
            return
        t = _get_colors()
        box = ctk.CTkFrame(
            self.card, fg_color=t["section_bg"], corner_radius=10,
            border_width=1, border_color=t["card_border"],
        )
        # Place the box directly after the status box / reveal button area.
        box.pack(pady=(8, 0), padx=50, fill="x")
        ctk.CTkLabel(
            box,
            text=f"Failed files ({len(errors)}) — fix and re-run:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=t["error_text"],
            anchor="w",
        ).pack(fill="x", padx=12, pady=(8, 2))
        scroll = ctk.CTkScrollableFrame(
            box, fg_color="transparent", height=92,
        )
        scroll.pack(fill="x", padx=8, pady=(0, 8))
        for entry in errors:
            ctk.CTkLabel(
                scroll, text=f"•  {entry}",
                font=ctk.CTkFont(size=11),
                text_color=t["text_secondary"],
                anchor="w", justify="left", wraplength=560,
            ).pack(fill="x", padx=4, pady=1)
        self._failed_box = box

    def _show_error(self, msg: str):
        self.progressbar.set(0)
        self.lbl_status.configure(text=f"Error:  {msg}", text_color=_get_colors()["error_text"])
        # A scheduler-fired run that errored out (e.g. network down overnight)
        # must still surface — otherwise the failure is invisible (#1).
        was_scheduled = self._scheduled_run_active
        self._scheduled_run_active = False
        self._unlock_ui_after_batch()
        if was_scheduled:
            tray = getattr(self, "_tray", None)
            summary = f"failed: {msg}"
            if tray is not None:
                with contextlib.suppress(RuntimeError, OSError):
                    if hasattr(tray, "set_last_run"):
                        tray.set_last_run(summary)
                    if hasattr(tray, "notify"):
                        tray.notify(
                            f"Scheduled run failed: {msg}",
                            "BOT ExRate — Scheduled Run",
                        )
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                self.restore_from_tray()
        self.update_idletasks()

    def _show_download_error(self, msg: str):
        """Surface an update-install failure to the user.

        Wired into VersionPanel(on_error=...) by the settings modal. A native
        error popup plus a status-label update guarantees the failure is never
        silent — the old getattr(app, '_show_download_error', None) resolved to
        None, which the panel swallowed on the failure path.
        """
        messagebox.showerror("Update Failed", msg)
        if hasattr(self, "lbl_status"):
            self.lbl_status.configure(
                text=f"Error:  {msg}", text_color=_get_colors()["error_text"]
            )

    # ================================================================== #
    #  EXRATE SHEET — delegated to gui/panels/exrate_dialog.py
    # ================================================================== #
    def _on_export_exrate(self):
        """Show an options dialog for creating a new ExRate sheet."""
        from gui.panels.exrate_dialog import show_exrate_dialog
        show_exrate_dialog(self)

    # ================================================================== #
    #  REVERT
    # ================================================================== #
    def _on_revert_click(self):
        """Opens a file dialog to select the file to revert, then restores it."""
        path = filedialog.askopenfilename(
            title="Select the file to revert",
            filetypes=[("Excel workbooks", "*.xlsx")]
        )
        if not path:
            return

        # Raise the busy flag BEFORE spawning the worker so a scheduler fire
        # racing in on the UI thread sees the revert in progress (#3).
        self._revert_running = True
        self.btn_revert.configure(state="disabled")
        self.btn_process.configure(state="disabled")
        self.lbl_status.configure(
            text=f"Restoring:  {Path(path).name}...",
            text_color=_get_colors()["warning"]
        )
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start()

        self.batch_handler.start_revert(path)


    def _show_revert_success(self, filepath: str, backup_name: str):
        self._revert_running = False
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(1)
        self.lbl_status.configure(
            text=f"Reverted successfully from backup:  {backup_name}",
            text_color=_get_colors()["success"]
        )
        self.btn_revert.configure(state="normal")
        self.btn_process.configure(state="normal")
        self.last_processed_path = filepath
        self.btn_reveal.pack(pady=(12, 14))

    def _show_revert_error(self, msg: str):
        self._revert_running = False
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(0)
        self.lbl_status.configure(text=f"Error:  {msg}", text_color=_get_colors()["error_text"])
        self.btn_revert.configure(state="normal")
        self.btn_process.configure(state="normal")

    # ================================================================== #
    #  FILE REVEAL
    # ================================================================== #
    def _reveal_file(self):
        fp = self.last_processed_path
        if not fp or not Path(fp).exists():
            return
        # SEC-04: Validate path before passing to subprocess. os.path.realpath
        # is kept deliberately (resolves symlinks for the security check).
        fp = os.path.realpath(fp)
        if not Path(fp).is_file():
            logger.warning("Reveal target is not a file: %s", fp)
            return
        try:
            system = platform.system()
            # noqa S603/S607: fp is realpath-resolved and is_file()-checked above;
            # each call uses the OS-standard file-manager launcher with a fixed argv.
            if system == "Darwin":
                subprocess.Popen(["open", "-R", fp])  # noqa: S603, S607
            elif system == "Windows":
                # os.path.normpath kept: shell needs the native path string.
                subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])  # noqa: S603, S607
            else:
                # Keep parent as str: handed to the xdg-open subprocess.
                parent = str(Path(fp).parent)
                if Path(parent).is_dir():
                    subprocess.Popen(["xdg-open", parent])  # noqa: S603, S607
        except OSError as e:
            logger.debug("File manager open failed: %s", e)
            self.lbl_status.configure(
                text="Could not open file manager.", text_color=_get_colors()["warning"]
            )


    # ================================================================== #
    #  V3.0: LIVE PROCESSING CONSOLE
    # ================================================================== #
    def _build_live_console(self):
        """Embed the LiveConsolePanel below the main card."""
        self.console = LiveConsolePanel(
            self, event_bus=self.event_bus, height=140,
        )
        self.console.pack(pady=(0, 16), padx=36, fill="x")
        self.console.start_polling()

    # ================================================================== #
    #  V3.0: SETTINGS MODAL + THEME
    # ================================================================== #
    def _open_settings(self):
        """Launch the settings modal window. Re-apply theme when closed."""
        from gui.panels.settings_modal import SettingsModal
        modal = SettingsModal(self)
        modal.grab_set()
        self.wait_window(modal)
        self._apply_theme()

    def _apply_theme(self):
        """Re-read the theme and apply colors to ALL widgets."""
        from gui.theme_applicator import apply_theme_to_app
        apply_theme_to_app(self)


    # ================================================================== #
    #  V3.1.0: AUTO-SCHEDULER CALLBACKS
    # ================================================================== #
    def _on_scheduler_start(self, time_str: str, paths: list):
        """Start or update the background scheduler."""
        from core.scheduler import AutoScheduler
        if not hasattr(self, "_auto_scheduler"):
            self._auto_scheduler = AutoScheduler()

        def _scheduler_callback(files):
            """Called by the scheduler when it's time to process."""
            if not files:
                return
            # Snapshot the scheduled files into a SEPARATE list so a scheduled
            # run never overwrites the user's interactive selection (#2). The
            # concurrency guard in start_batch rejects overlap with a manual run.
            scheduled = list(files)
            logger.info("Auto-scheduler firing with %d files", len(scheduled))
            # Use prescan to detect the oldest date in the ledgers,
            # matching the manual processing path instead of hardcoding today.
            from core.engine import LedgerEngine
            oldest, was_detected = LedgerEngine.prescan_oldest_date(scheduled)
            start_str = oldest.strftime("%Y-%m-%d")
            flag = "auto-detected" if was_detected else "fallback"
            logger.info("Scheduler start_date: %s (%s)", start_str, flag)
            # Marshal onto the UI thread: lock the SAME controls a manual run
            # locks and reflect the run in lbl_status, so a desk-side user can
            # see it and can't collide with Process/Revert/ExRate (#3).
            self.after(0, lambda: self._begin_scheduled_batch(scheduled, start_str))

        self._auto_scheduler.start(
            time_str=time_str,
            watch_paths=paths,
            callback=_scheduler_callback,
        )
        logger.info("Scheduler started: %s, %d paths", time_str, len(paths))

    def _begin_scheduled_batch(self, scheduled: list[str], start_str: str):
        """UI-thread entry point for a scheduler-fired batch (#3).

        Locks the same controls a manual run does and reflects the run in
        lbl_status so a desk-side user sees it and cannot collide with a manual
        Process/Revert/ExRate. Skip (log + return) when a batch is already
        running (manual or a prior scheduled fire) OR when a manual revert is in
        flight. The revert check is essential: start_revert spawns a RevertWorker
        that never sets _batch_running, so without consulting _revert_running the
        scheduler would spawn a BatchWorker that reads/writes the same .xlsx a
        RevertWorker is restoring a backup over — two threads on one workbook (#3).
        """
        if self._batch_running:
            logger.info("Scheduled batch skipped — a batch is already running")
            return
        if self._revert_running:
            logger.info("Scheduled batch skipped — a manual revert is in progress")
            return
        self._lock_ui_for_batch()
        # Mark this run as scheduler-fired so the completion path knows to raise
        # a tray notification + record a last-run summary (#1).
        self._scheduled_run_active = True
        total = len(scheduled)
        self.lbl_status.configure(
            text=(
                f"Scheduled run:  processing {total} "
                f"ledger{'s' if total != 1 else ''}...  (0 of {total})"
            ),
            text_color=_get_colors()["process_text"],
        )
        self.batch_handler.start_batch(scheduled, start_str)

    def _on_scheduler_stop(self):
        """Stop the background scheduler."""
        if hasattr(self, "_auto_scheduler"):
            self._auto_scheduler.stop()
            logger.info("Scheduler stopped")

    # ================================================================== #
    #  V3.2.0: COMPANY LICENSE FOOTER
    # ================================================================== #
    def _build_footer(self):
        """Build the company license footer bar at the bottom of the window."""
        t = get_theme()
        self.footer_frame = ctk.CTkFrame(
            self, fg_color=t["footer_bg"], corner_radius=0,
            border_width=0, height=26,
        )
        self.footer_frame.pack(fill="x", side="bottom")
        self.footer_frame.pack_propagate(False)

        self.lbl_footer = ctk.CTkLabel(
            self.footer_frame,
            text=(
                f"Property of Advanced ID Asia Engineering., Ltd (AAE)"
                f"  \u2502  V{APP_VERSION}"
            ),
            font=ctk.CTkFont(size=10),
            text_color=t["text_muted"],
        )
        self.lbl_footer.pack(expand=True)


    # ================================================================== #
    #  V3.2.x: CLEAN SHUTDOWN
    # ================================================================== #
    def _on_app_close(self):
        """App-level close handler: stop all workers BEFORE destroying the
        Tk root so no self.after() fires on a torn-down widget (#1, #5)."""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        logger.info("Application closing — tearing down workers")

        # 1. Stop the rate ticker (joins its worker, sets _destroyed).
        if getattr(self, "rate_ticker", None) is not None:
            try:
                self.rate_ticker.stop()
            except (RuntimeError, OSError) as e:
                logger.debug("rate_ticker.stop() failed: %s", e)

        # 2. Stop the live console polling loop.
        if getattr(self, "console", None) is not None:
            try:
                self.console.stop_polling()
            except (RuntimeError, AttributeError) as e:
                logger.debug("console.stop_polling() failed: %s", e)

        # 3. Stop the background scheduler.
        try:
            self._on_scheduler_stop()
        except (RuntimeError, AttributeError) as e:
            logger.debug("scheduler stop failed: %s", e)

        # 4. Mark the auto-updater destroyed (guarded).
        updater = getattr(self, "_updater", None)
        if updater is not None and hasattr(updater, "mark_destroyed"):
            try:
                updater.mark_destroyed()
            except (RuntimeError, OSError) as e:
                logger.debug("updater.mark_destroyed() failed: %s", e)

        # 5. Tear down the tray icon (guarded).
        tray = getattr(self, "_tray", None)
        if tray is not None and hasattr(tray, "cleanup"):
            try:
                tray.cleanup()
            except (RuntimeError, OSError) as e:
                logger.debug("tray.cleanup() failed: %s", e)

        # 6. Let any in-progress worker (e.g. openpyxl save) finish/report.
        registry = getattr(self, "thread_registry", None)
        if registry is not None:
            try:
                hung = registry.shutdown_all(timeout=5.0)
                if hung:
                    logger.warning("Workers did not exit cleanly: %s", hung)
            except RuntimeError as e:
                logger.debug("thread_registry.shutdown_all() failed: %s", e)

        # 6b. Close the rate-ticker cache DB. Done AFTER the ticker worker has
        # been joined (steps 1 + 6) so no thread is still reading the handle.
        cache_db = getattr(self, "_cache_db", None)
        if cache_db is not None:
            try:
                cache_db.close()
            except (RuntimeError, OSError) as e:
                logger.debug("_cache_db.close() failed: %s", e)

        # 7. Destroy the Tk root.
        try:
            self.destroy()
        except (RuntimeError, tkinter.TclError) as e:
            logger.debug("destroy() failed: %s", e)

    def restore_from_tray(self):
        """Called via IPC socket or tray double-click to restore the window."""
        if hasattr(self, "_tray"):
            self._tray.restore_if_hidden()
        else:
            self.deiconify()
            self.lift()
            self.focus_force()

if __name__ == "__main__":
    app = BOTExrateApp()
    app.mainloop()
