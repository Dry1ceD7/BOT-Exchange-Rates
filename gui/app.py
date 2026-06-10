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
from core.i18n import plural, tr
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

        # Keep the idle title so the processing-state title can be reset to it
        # when a batch finishes (#11).
        self._base_title = f"BOT Exchange Rate Processor  |  V{APP_VERSION}"
        self.title(self._base_title)
        # A 960px-tall default overflows a 1366x768 legacy laptop (title bar +
        # taskbar push the footer/console off-screen). Cap the default height to
        # the usable screen height and give the window a hard floor so dragging
        # the bottom edge up can't collapse the card/console into a sliver (#6).
        self._win_width = 740
        self._win_height = self._fit_default_height(960)
        self.geometry(f"{self._win_width}x{self._win_height}")
        self.minsize(740, 640)
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
        # True while the standalone ExRate worker (exrate_dialog) is in flight.
        # The dialog only re-enables btn_export_exrate itself, so app.py owns the
        # symmetric lock on Process/Revert and polls the export button's state to
        # release them when the worker finishes (round-6 finding #1).
        self._exrate_running = False
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

        # Center window using the fitted height (#6) so the offset is computed
        # against the actual window size, never a hardcoded 960 that pushed the
        # window off the top of a short screen.
        self.update_idletasks()
        w, h = self._win_width, self._win_height
        sx = (self.winfo_screenwidth() - w) // 2
        sy = max(0, (self.winfo_screenheight() - h) // 2)
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
        # Keyboard-only operation: accelerators + a sensible tab order so the
        # main window is fully drivable without a mouse (#13).
        self._bind_accelerators()
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

        # Crash-recovery: if a previous batch was interrupted (app crash / power
        # loss), a resume manifest survives on disk. Offer to finish only the
        # unprocessed files instead of forcing a full re-selection. Deferred via
        # after() so the prompt appears after the window is fully drawn, and the
        # manifest read (one tiny JSON stat) stays off the build hot-path.
        self.after(400, self._offer_batch_resume)

    def _fit_default_height(self, desired: int) -> int:
        """Clamp the desired window height to what fits on the current screen (#6).

        Reserves ~80px for the title bar + taskbar and never returns less than
        the 640px minimum the window can usefully render at, so the footer and
        live console stay on-screen on a 1366x768 legacy laptop.
        """
        try:
            screen_h = self.winfo_screenheight()
        except (RuntimeError, tkinter.TclError):
            return desired
        usable = max(640, screen_h - 80)
        return min(desired, usable)

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
            inner, text=tr("main.header_title"),
            font=ctk.CTkFont(size=22, weight="bold"), text_color=t["header_text"]
        )
        self.lbl_header_title.pack()

        sub_row = ctk.CTkFrame(inner, fg_color="transparent")
        sub_row.pack(pady=(2, 0))
        self.lbl_header_sub = ctk.CTkLabel(
            sub_row, text=tr("main.header_sub"),
            font=ctk.CTkFont(size=11), text_color=t["header_sub"]
        )
        self.lbl_header_sub.pack(side="left")

        # Settings button — visible, proper button styling
        self._btn_settings = ctk.CTkButton(
            sub_row, text=tr("main.settings_btn"), width=90, height=26,
            fg_color=t["settings_btn"], hover_color=t["settings_btn_hover"],
            text_color=t["settings_btn_text"],
            font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6,
            border_width=1, border_color=t["settings_btn_border"],
            command=self._open_settings,
        )
        self._btn_settings.pack(side="left", padx=(12, 0))

        # Help / About — version, license note, keyboard shortcuts, and a link
        # to the logs folder, so the app is no longer reference-less (#9).
        self._btn_help = ctk.CTkButton(
            sub_row, text=tr("main.help_btn"), width=70, height=26,
            fg_color=t["settings_btn"], hover_color=t["settings_btn_hover"],
            text_color=t["settings_btn_text"],
            font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6,
            border_width=1, border_color=t["settings_btn_border"],
            command=self._open_help,
        )
        self._btn_help.pack(side="left", padx=(6, 0))

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
            self.card, text=tr("main.date_section"),
            font=ctk.CTkFont(size=12, weight="bold"), text_color=t["text_secondary"]
        )
        self.lbl_date_section.pack(pady=(20, 0))

        # ── V2.4: Auto-Detect Toggle (primary) ───────────────────────────
        auto_row = ctk.CTkFrame(self.card, fg_color="transparent")
        auto_row.pack(pady=(8, 0))

        self.auto_detect_var = ctk.StringVar(value="on")
        self.toggle_auto = ctk.CTkSwitch(
            auto_row, text=tr("main.auto_detect_toggle"),
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
            text=tr("main.auto_hint"),
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
            toggle_row, text=tr("main.use_today_toggle"),
            variable=self.use_today_var, onvalue="on", offvalue="off",
            command=self._on_toggle_changed,
            font=ctk.CTkFont(size=12), text_color=t["text_secondary"],
            progress_color=t["success"], button_color=t["card_bg"],
            button_hover_color=t["switch_hover"], fg_color=t["switch_track"]
        )
        self.toggle_today.pack()

        self.lbl_toggle_hint = ctk.CTkLabel(
            self.manual_date_frame,
            text=tr("main.today_hint", date=date.today().strftime("%d %b %Y")),
            font=ctk.CTkFont(size=11), text_color=t["success"]
        )
        self.lbl_toggle_hint.pack(pady=(4, 4))

        # Date dropdowns
        date_row = ctk.CTkFrame(self.manual_date_frame, fg_color="transparent")
        date_row.pack()
        # Default the picker to the current BOT business date (Asia/Bangkok)
        # rather than a hardcoded, now-stale "2025" — a manual run with auto-
        # detect off should land on today, not a past year (#10).
        from core.constants import bot_today
        today = bot_today()
        current_year = today.year
        self._combo_widgets = []

        for label_key, width, values, default, attr in [
            ("main.label_year", 100, [str(y) for y in range(2020, current_year + 1)], str(today.year), "combo_year"),
            ("main.label_month", 80, [f"{m:02d}" for m in range(1, 13)], f"{today.month:02d}", "combo_month"),
            ("main.label_day", 80, [f"{d:02d}" for d in range(1, 32)], f"{today.day:02d}", "combo_day"),
        ]:
            grp = ctk.CTkFrame(date_row, fg_color="transparent")
            grp.pack(side="left", padx=8)
            ctk.CTkLabel(grp, text=tr(label_key).upper(),
                         font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=t["text_secondary"]).pack()
            combo = ctk.CTkComboBox(
                grp, values=values, width=width, height=36,
                # state="readonly" — the user can only PICK a listed value, never
                # type a typo that silently fails at process time (#1). The
                # strptime guard in _assemble_start_date stays as a backstop.
                state="readonly",
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
            self.card, text=tr("main.input_section"),
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

        dnd_hint = (
            tr("main.drop_hint_dnd") if self.dnd_enabled
            else tr("main.drop_hint_click")
        )
        self.dz_text = ctk.CTkLabel(
            dz_inner, text=dnd_hint,
            font=ctk.CTkFont(size=14, weight="bold"), text_color=t["text_secondary"]
        )
        self.dz_text.pack()
        self.dz_sub = ctk.CTkLabel(dz_inner, text=tr("main.drop_sub"),
                                    font=ctk.CTkFont(size=11), text_color=t["text_muted"])
        self.dz_sub.pack(pady=(2, 0))

        # First-launch / empty-state guidance: a numbered 1-2-3 walkthrough shown
        # ONLY while the queue is empty, so a brand-new operator knows the flow
        # (add files -> press Process -> review). Hidden the moment files load and
        # restored when the queue is reset after a run (#12).
        self.lbl_empty_state = ctk.CTkLabel(
            self.card, text=tr("main.empty_state_steps"),
            font=ctk.CTkFont(size=11), text_color=t["text_muted"],
            justify="center",
        )
        self.lbl_empty_state.pack(pady=(6, 0))

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

        # Explicit Clear-queue affordance — paired with the now-additive
        # drop/browse so an incrementally-built selection can be emptied without
        # restarting. Packed only while the queue is non-empty (#2).
        self.btn_clear_queue = ctk.CTkButton(
            self.card, text=tr("main.btn_clear_queue"),
            height=26, width=120,
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6,
            command=self._on_clear_queue,
        )

        # ── Divider ──────────────────────────────────────────────────────
        ctk.CTkFrame(self.card, fg_color=t["divider"], height=1).pack(fill="x", padx=50, pady=(12, 0))

        # ── 3. ACTION BUTTONS ────────────────────────────────────────────
        # Primary CTA row: Process Batch stands ALONE so it reads as the one
        # main action, with the Simulation toggle scoped DIRECTLY beneath it so
        # the operator can see the dry-run flag only governs Process Batch — not
        # the unrelated ExRate Sheet creator (#4, #5).
        primary_row = ctk.CTkFrame(self.card, fg_color="transparent")
        primary_row.pack(pady=(16, 0))

        self.btn_process = ctk.CTkButton(
            primary_row, text=tr("main.btn_process"),
            height=48, width=300,
            fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10, command=self._on_process_click, state="disabled"
        )
        self.btn_process.pack()

        # ── v3.2.0: Dry-Run Simulation Toggle (scoped to Process Batch) ──
        sim_row = ctk.CTkFrame(self.card, fg_color="transparent")
        sim_row.pack(pady=(6, 0))
        self.toggle_dryrun = ctk.CTkSwitch(
            sim_row, text=tr("main.dryrun_toggle"),
            variable=self._dry_run_var, onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=11), text_color=t["text_secondary"],
            progress_color=t["warning"], button_color=t["switch_thumb"],
            button_hover_color=t["switch_hover"], fg_color=t["switch_track"],
        )
        self.toggle_dryrun.pack()
        self.lbl_dryrun_hint = ctk.CTkLabel(
            sim_row, text=tr("main.dryrun_hint"),
            font=ctk.CTkFont(size=10), text_color=t["text_muted"],
        )
        self.lbl_dryrun_hint.pack(pady=(2, 0))

        # Secondary action row: file-recovery (Revert, Backups) and the
        # standalone ExRate Sheet creator live together BELOW the primary CTA,
        # so ExRate no longer reads as a sibling of the queue's Process action
        # and the row is no longer crammed edge-to-edge on a 740px window (#4).
        secondary_row = ctk.CTkFrame(self.card, fg_color="transparent")
        secondary_row.pack(pady=(10, 0))

        self.btn_revert = ctk.CTkButton(
            secondary_row, text=tr("main.btn_revert"),
            height=42, width=190,
            fg_color=t["revert_bg"], hover_color=t["revert_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10, command=self._on_revert_click
        )
        self.btn_revert.pack(side="left", padx=(0, 10))

        # Browse all timestamped backups (not just the latest) so the operator
        # can restore an EARLIER good copy — turns the backup store into a
        # user-facing undo history rather than a hidden, latest-only revert.
        self.btn_backups = ctk.CTkButton(
            secondary_row, text=tr("main.btn_backups"),
            height=42, width=150,
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10, command=self._open_backup_browser,
        )
        self.btn_backups.pack(side="left", padx=(0, 10))

        self.btn_export_exrate = ctk.CTkButton(
            secondary_row, text=tr("main.btn_exrate"),
            height=42, width=150,
            fg_color=t["accent_indigo"], hover_color=t["accent_indigo_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10, command=self._on_export_exrate
        )
        self.btn_export_exrate.pack(side="left")

        # Verify an existing workbook's ExRate rates against BOT and correct
        # any differing trading-day cells (file backed up first; revertable).
        self.btn_verify_rates = ctk.CTkButton(
            secondary_row, text="Verify Rates",
            height=42, width=150,
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10, command=self._open_rate_audit,
        )
        self.btn_verify_rates.pack(side="left", padx=(10, 0))

        # Revert starts disabled — there is nothing to revert until at least one
        # file with a backup exists. _refresh_revert_state re-evaluates it from
        # the available backups so the button is greyed when it would be a
        # dead-end rather than silently doing nothing (#6).
        self._refresh_revert_state()

        # ── 4. STATUS BOX ────────────────────────────────────────────────
        status_box = ctk.CTkFrame(
            self.card, fg_color=t["section_bg"], corner_radius=10,
            border_width=1, border_color=t["card_border"]
        )
        status_box.pack(pady=(16, 0), padx=50, fill="x", ipady=8)

        self.lbl_status = ctk.CTkLabel(
            status_box, text=tr("main.status_ready"),
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
            self.card, text=tr("main.btn_reveal"),
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
    #  KEYBOARD ACCELERATORS / TAB ORDER  (#13)
    # ================================================================== #
    def _bind_accelerators(self):
        """Wire keyboard shortcuts + a sensible tab order for the main window.

        Shortcuts (also documented in the Help dialog):
          * Ctrl/Cmd+Enter or F5 — Process Batch
          * Ctrl/Cmd+R           — Revert Previous Edit
          * Ctrl/Cmd+E           — Create ExRate Sheet
          * F1                   — Help / About
          * Enter on a focused button activates it

        Every handler routes through the existing click methods, which already
        enforce the batch/revert/ExRate busy guards, so a shortcut can never
        bypass a concurrency lock. Guarded so a headless/odd Tk never raises."""
        bindings = {
            "<Control-Return>": self._accel_process,
            "<Command-Return>": self._accel_process,
            "<F5>": self._accel_process,
            "<Control-r>": self._accel_revert,
            "<Command-r>": self._accel_revert,
            "<Control-e>": self._accel_exrate,
            "<Command-e>": self._accel_exrate,
            "<F1>": lambda e: self._open_help(),
        }
        for seq, fn in bindings.items():
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                self.bind_all(seq, fn)

        # Logical Tab order: the primary CTA first, then the secondary actions,
        # then Settings/Help. lift() reorders the focus traversal chain.
        order = [
            getattr(self, "btn_process", None),
            getattr(self, "btn_revert", None),
            getattr(self, "btn_backups", None),
            getattr(self, "btn_export_exrate", None),
            getattr(self, "_btn_settings", None),
            getattr(self, "_btn_help", None),
        ]
        for widget in order:
            if widget is not None:
                with contextlib.suppress(RuntimeError, tkinter.TclError):
                    widget.lift()

    def _accel_process(self, _event=None):
        """F5 / Ctrl+Enter — Process Batch, but only when it is actionable."""
        btn = getattr(self, "btn_process", None)
        if btn is not None and str(btn.cget("state")) != "disabled":
            self._on_process_click()
        return "break"

    def _accel_revert(self, _event=None):
        btn = getattr(self, "btn_revert", None)
        if btn is not None and str(btn.cget("state")) != "disabled":
            self._on_revert_click()
        return "break"

    def _accel_exrate(self, _event=None):
        btn = getattr(self, "btn_export_exrate", None)
        if btn is not None and str(btn.cget("state")) != "disabled":
            self._on_export_exrate()
        return "break"

    # ================================================================== #
    #  V2.4: AUTO-DETECT TOGGLE
    # ================================================================== #
    def _on_auto_detect_changed(self):
        """Toggle between auto-detect and manual date entry."""
        is_auto = self.auto_detect_var.get() == "on"
        if is_auto:
            self.manual_date_frame.pack_forget()
            self.lbl_auto_hint.configure(
                text=tr("main.auto_hint"),
                text_color=_get_colors()["trust_blue"]
            )
        else:
            self.lbl_auto_hint.configure(
                text=tr("main.manual_hint"),
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
                text=tr(
                    "main.today_hint",
                    date=date.today().strftime("%d %b %Y"),
                ),
                text_color=_get_colors()["success"]
            )
        else:
            self.lbl_toggle_hint.configure(
                text=tr("main.custom_date_hint"),
                text_color=_get_colors()["trust_blue"]
            )

    def _lock_date_dropdowns(self, locked: bool):
        # Unlocked combos stay "readonly" (pick-only), never "normal" — re-enabling
        # free-text entry would reintroduce the typo-at-process-time gap (#1).
        for combo in self._combo_widgets:
            combo.configure(state="disabled" if locked else "readonly")

    def _assemble_start_date(self) -> str | None:
        if self.use_today_var.get() == "on":
            return datetime.today().strftime("%Y-%m-%d")
        date_str = f"{self.combo_year.get()}-{self.combo_month.get()}-{self.combo_day.get()}"
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror(
                tr("main.invalid_date_title"),
                tr("main.invalid_date_body", date=date_str),
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
        # A FOLDER drop forces resolve_excel_files to do an os.listdir (a disk
        # walk) — doing that on the Tk thread freezes the UI for a big share.
        # Offload the listing to a worker and marshal the result back via
        # after() so the window stays responsive; a pure FILE drop is cheap and
        # stays synchronous so the existing drop→preflight→queue order holds (#8).
        if any(Path(p).is_dir() for p in paths):
            self.lbl_status.configure(
                text=tr("main.scanning_folder"),
                text_color=_get_colors()["process_text"],
            )

            def _resolve_in_worker():
                try:
                    result = resolve_excel_files(paths, collect_rejected=True)
                except OSError as e:
                    logger.debug("folder resolve failed: %s", e)
                    result = ([], [])
                # Hand the resolved lists back to the Tk thread. Guarded so the
                # os.listdir walk finishing after the root is destroyed can't
                # raise an unhandled RuntimeError/TclError in this daemon (#1).
                self._safe_marshal(lambda: self._finish_drop(*result))

            worker = threading.Thread(target=_resolve_in_worker, daemon=True)
            self._register_worker(worker, "FolderResolveWorker")
            worker.start()
            return
        excel_files, rejected = resolve_excel_files(paths, collect_rejected=True)
        self._finish_drop(excel_files, rejected)

    def _finish_drop(self, excel_files: list[str], rejected: list[str]):
        """Apply a resolved drop result on the Tk thread (#8).

        Shared terminal handler for both the synchronous file-drop path and the
        worker-resolved folder-drop path, so the warning/queue behaviour is
        identical regardless of how the listing was produced."""
        # A worker-marshalled call may land after the app started closing —
        # bail before touching any widget so we don't poke a torn-down root (#1).
        if getattr(self, "_closing", False):
            return
        # A folder scan may have finished after a batch started — re-check.
        if self._batch_running:
            self._flash_busy_status()
            return
        if rejected:
            names = ", ".join(Path(f).name for f in rejected)
            messagebox.showwarning(
                tr("main.format_warning_title"),
                tr("main.format_warning_body", names=names),
            )
        if excel_files:
            # Preflight only the NEWLY added files so re-dropping doesn't re-warn
            # about files already in the queue (#2).
            new_files = self._dedup_new(excel_files)
            if new_files:
                self._preflight_warn(new_files)
            # APPEND to the existing queue (dedup) rather than REPLACE it, so the
            # operator can build a batch incrementally across several drops (#2).
            self._set_queue(self.file_queue + new_files)
        elif rejected:
            messagebox.showwarning(
                tr("main.no_valid_files_title"),
                tr("main.no_valid_files_unsupported"),
            )
        else:
            messagebox.showwarning(
                tr("main.no_valid_files_title"),
                tr("main.no_valid_files_empty"),
            )

    def _dedup_new(self, candidates: list[str]) -> list[str]:
        """Return the subset of ``candidates`` not already in the queue (#2).

        De-dup is by normalized path so the same file dropped twice (or once via
        browse and once via drop) is queued only once. Order of first appearance
        is preserved."""
        existing = {os.path.normpath(p) for p in self.file_queue}
        out: list[str] = []
        seen = set(existing)
        for c in candidates:
            norm = os.path.normpath(c)
            if norm not in seen:
                seen.add(norm)
                out.append(c)
        return out

    def _on_clear_queue(self):
        """Clear the pending file selection (#2).

        Companion to the now-additive drop/browse: gives the operator an
        explicit way to empty an incrementally-built queue. Ignored mid-run so a
        batch's snapshot is never disturbed."""
        if self._batch_running:
            self._flash_busy_status()
            return
        self._reset_queue_after_run()

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
            # APPEND to the queue (dedup) so browse accumulates like drop (#2).
            new_files = self._dedup_new(list(paths))
            if new_files:
                self._preflight_warn(new_files)
            self._set_queue(self.file_queue + new_files)

    def _preflight_warn(self, files: list[str]):
        """Selection-time pre-flight feedback for the engine seam.

        Calls the side-effect-free ``LedgerEngine.preflight_file`` on each newly
        selected/dropped file and, if any are oversized or locked, surfaces a
        single warning immediately — instead of letting the operator discover
        the failure only mid-run after the API fetch + backup. This is advisory:
        the files still enter the queue and the run-time guard stays the
        authoritative check, so a transient lock that clears before processing
        is not falsely blocked.

        Unsupported-extension and empty-drop cases are already handled upstream
        in ``_on_drop`` (via ``resolve_excel_files``), so they are skipped here
        to avoid a duplicate warning. ``preflight_file`` does no I/O beyond a
        stat + a non-truncating write probe — safe to run on the UI thread.
        """
        from core.engine import LedgerEngine

        reasons: list[str] = []
        for path in files:
            try:
                report = LedgerEngine.preflight_file(path)
            except Exception as exc:  # never let a probe error block selection
                logger.debug("preflight_file failed for %s: %s", path, exc)
                continue
            # Skip the unsupported-extension case — _on_drop already warned.
            if report["ok"] or not report["supported"]:
                continue
            reason = report.get("reason")
            if reason:
                reasons.append(reason)

        if reasons:
            messagebox.showwarning(
                tr("main.preflight_warning_title"),
                tr(
                    "main.preflight_warning_body",
                    reasons="\n".join(f"  - {r}" for r in reasons),
                ),
            )

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
        self.dz_sub.configure(text=tr("main.drop_change"))
        self.lbl_queue.configure(
            text=tr("main.queue_ready", count=count, plural=plural(count)),
            text_color=_get_colors()["success"]
        )
        self.btn_process.configure(state="normal")
        self.btn_reveal.pack_forget()
        # Files are queued now — hide the 1-2-3 first-launch guidance (#12) and
        # surface the Clear-queue affordance (#2).
        self._hide_empty_state()
        self._show_clear_queue()

    def _hide_empty_state(self):
        """Hide the 1-2-3 first-launch guidance once files are queued (#12)."""
        lbl = getattr(self, "lbl_empty_state", None)
        if lbl is not None:
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                lbl.pack_forget()

    def _show_empty_state(self):
        """Re-show the 1-2-3 first-launch guidance in the idle/empty state (#12).

        Re-packed BEFORE the queue label so it sits just under the drop zone,
        matching its original build-time position."""
        lbl = getattr(self, "lbl_empty_state", None)
        if lbl is None:
            return
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            queue_lbl = getattr(self, "lbl_queue", None)
            if queue_lbl is not None:
                lbl.pack(before=queue_lbl, pady=(6, 0))
            else:
                lbl.pack(pady=(6, 0))

    def _show_clear_queue(self):
        """Show the Clear-queue button once files are queued (#2)."""
        btn = getattr(self, "btn_clear_queue", None)
        if btn is not None:
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                btn.pack(pady=(4, 0))

    def _hide_clear_queue(self):
        """Hide the Clear-queue button in the idle/empty state (#2)."""
        btn = getattr(self, "btn_clear_queue", None)
        if btn is not None:
            with contextlib.suppress(RuntimeError, tkinter.TclError):
                btn.pack_forget()

    def _flash_busy_status(self):
        """Tell the operator the UI is busy instead of silently swallowing the
        click/drop. Used by the drop zone / browse / queue guards (#1)."""
        if hasattr(self, "lbl_status"):
            self.lbl_status.configure(
                text=tr("main.status_busy"),
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
        # Reflect "busy" in the title/taskbar from the first moment of the run,
        # before the first progress event refines it to a count (#11).
        self._set_window_title(tr("main.title_processing_generic"))
        # Pulse the bar during the unavoidable first-file network wait (prescan +
        # two get_exchange_rates + holidays, each with tenacity retries) so the
        # window doesn't look frozen at a dead 0% (#7). The first _update_progress
        # call flips it back to determinate.
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start()

    def _unlock_ui_after_batch(self):
        """Re-enable the controls locked by _lock_ui_for_batch and clear the
        busy flag. Called from every batch terminal path (complete/error)."""
        self._batch_running = False
        self.btn_process.configure(state="normal")
        self.btn_export_exrate.configure(state="normal")
        # Restore the idle window title — the batch is no longer running (#11).
        self._set_window_title(None)
        # Revert/Backups are only useful once a backup exists — re-evaluate
        # rather than blindly re-enabling a dead-end button (#6).
        self._refresh_revert_state()

    def _refresh_revert_state(self):
        """Grey out Revert/Backups when there is nothing to restore (#6).

        A revert is a no-op until at least one timestamped backup exists, so a
        permanently-enabled button is a dead affordance: clicking it only warns
        "no backup found". We probe the backup store with a single early-exit
        glob (no sort, no recursion, no workbook load) so this stays cheap
        enough to call on the Tk thread on a 4GB legacy PC. The authoritative
        check still lives in _on_revert_click (it re-confirms per file)."""
        try:
            has_backup = next(
                Path(self.backup_mgr.backup_dir).glob("*.xlsx"), None
            ) is not None
        except Exception as e:  # advisory probe — never crash the UI thread
            logger.debug("revert-state probe failed: %s", e)
            # On any probe error, fail OPEN (enabled) so a recoverable file is
            # never made unreachable by a transient stat hiccup.
            has_backup = True
        state = "normal" if has_backup else "disabled"
        for btn in (getattr(self, "btn_revert", None), getattr(self, "btn_backups", None)):
            if btn is not None:
                btn.configure(state=state)

    def _on_process_click(self):
        if not self.file_queue or self._batch_running or self._revert_running:
            return
        # An ExRate worker already owns the cache/API — don't spin a second
        # engine over it (#1). The button is disabled while it runs, but guard
        # in case a stray event slips through during the release window.
        if self._exrate_running:
            self._flash_busy_status()
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
                    # May land after the app started closing — bail before
                    # touching any widget so we don't poke a torn-down root (#1).
                    if getattr(self, "_closing", False):
                        return
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

                # Guarded marshal-back: the prescan can finish after the root is
                # destroyed; a raw self.after() would then raise an unhandled
                # RuntimeError/TclError in this daemon thread (#1).
                self._safe_marshal(_update_ui_and_start)

            worker = threading.Thread(target=_prescan_and_batch, daemon=True)
            self._register_worker(worker, "PrescanBatchWorker")
            worker.start()
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

    def _offer_batch_resume(self):
        """Offer to resume an interrupted batch on launch (crash-recovery).

        Reads the engine's resume manifest (``data/batch_state.json``) written
        by a real run and updated per completed file. If unprocessed files
        remain — i.e. the previous run did NOT finish cleanly and was NOT
        cancelled — ask the operator whether to finish only those files:
          * Yes  → load the unfinished files into the queue (after the usual
                   preflight) so a normal Process Batch re-runs only the
                   remainder. The manifest is left in place; the next run
                   rewrites it from the new selection.
          * No   → delete the manifest so the prompt does not reappear.
        Never runs while a batch is already in flight, and any error degrades to
        a no-op (a resume offer must never block startup)."""
        if self._batch_running:
            return
        try:
            from core.engine import BatchManifest
            manifest = BatchManifest()
            pending = manifest.pending_files()
        except Exception as exc:  # never let a resume probe break startup
            logger.debug("resume manifest probe failed: %s", exc)
            return
        if not pending:
            return
        count = len(pending)
        if not messagebox.askyesno(
            tr("main.resume_title"),
            tr("main.resume_body", count=count, plural=plural(count)),
        ):
            # Declined — drop the manifest so we don't ask again next launch.
            with contextlib.suppress(Exception):
                manifest.clear()
            return
        # Resume: preflight the unfinished files, then load them as the queue.
        with contextlib.suppress(Exception):
            self._preflight_warn(pending)
        self._set_queue(pending)
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            self.lbl_status.configure(
                text=tr("main.resume_loaded", count=count, plural=plural(count)),
                text_color=_get_colors()["process_text"],
            )

    def _settle_progressbar(self, value: float):
        """Stop any pulse animation and pin the bar to a determinate value (#7).

        Idempotent: stop() on an already-stopped bar is a no-op, so this is safe
        to call from the first _update_progress as well as the terminal paths."""
        try:
            self.progressbar.stop()
        except (RuntimeError, tkinter.TclError) as e:
            logger.debug("progressbar.stop() failed: %s", e)
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(value)

    def _set_window_title(self, suffix: str | None = None):
        """Reflect the active batch in the OS window title / taskbar (#11).

        ``suffix`` None restores the idle title; otherwise the title becomes
        ``"<suffix>  —  <base title>"`` so a minimised window's taskbar entry
        shows progress (e.g. "Processing 3 of 10")."""
        base = getattr(self, "_base_title", "BOT Exchange Rate Processor")
        title_fn = getattr(self, "title", None)
        if not callable(title_fn):
            return
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            title_fn(f"{suffix}  —  {base}" if suffix else base)

    def _update_progress(self, idx: int, total: int, fname: str, error):
        # First progress event arrives once the first file's API fetch is done —
        # leave the pulse behind and switch to a real determinate fraction (#7).
        self._settle_progressbar(idx / total)
        # Show how many files remain (not just the index) and reflect it in the
        # window title so a minimised/taskbar view shows live progress (#3, #11).
        remaining = max(0, total - idx)
        self._set_window_title(
            tr("main.title_processing", idx=idx, total=total)
        )
        if error:
            self.lbl_status.configure(
                text=tr(
                    "main.status_progress_skipped",
                    idx=idx, total=total, remaining=remaining, fname=fname,
                ),
                text_color=_get_colors()["warning"]
            )
        else:
            self.lbl_status.configure(
                text=tr(
                    "main.status_progress_ok",
                    idx=idx, total=total, remaining=remaining, fname=fname,
                ),
                text_color=_get_colors()["process_text"]
            )

    def _show_batch_complete(
        self, success: int, fail: int, errors: list[str], dry_run: bool = False,
    ):
        # Settle the pulse started in _lock_ui_for_batch and pin the bar full (#7).
        self._settle_progressbar(1)
        # Capture whether this was a scheduler-fired run BEFORE unlock clears
        # nothing (it doesn't touch the flag) — done here so the notification
        # path below fires exactly once and only for scheduled runs (#1).
        was_scheduled = self._scheduled_run_active
        self._scheduled_run_active = False
        self._unlock_ui_after_batch()
        if dry_run:
            # A simulation wrote nothing — say so plainly, keep the dry-run
            # warning colour, never offer a reveal of an unmodified file (#4).
            self.lbl_status.configure(
                text=tr(
                    "main.status_simulation",
                    count=success, plural=plural(success),
                ),
                text_color=_get_colors()["warning"],
            )
            self._render_failed_files(errors or [])
            self.btn_reveal.pack_forget()
            self.update_idletasks()
            return
        if fail == 0:
            self.lbl_status.configure(
                text=tr(
                    "main.status_complete_all",
                    count=success, plural=plural(success),
                ),
                text_color=_get_colors()["success"]
            )
            self._render_failed_files([])
        else:
            self.lbl_status.configure(
                text=tr(
                    "main.status_complete_partial",
                    success=success, fail=fail,
                ),
                text_color=_get_colors()["warning"]
            )
            # Surface WHICH files failed and WHY so the operator can act on
            # them — the bare count alone hides actionable detail (#2).
            self._render_failed_files(errors or [])
        # Only reveal a file that was ACTUALLY written. The reveal must never
        # point at the last queued file when that file failed/was skipped (#2),
        # so resolve the last SUCCEEDING path from the queue minus the failures.
        revealable = self._last_succeeded_path(errors)
        if revealable is not None:
            self.last_processed_path = revealable
            self.btn_reveal.pack(pady=(12, 14))
        else:
            self.last_processed_path = None
            self.btn_reveal.pack_forget()
        # Clear the processed queue so a stray second click can't silently
        # reprocess the same files (fresh backups + re-injected formulas). The
        # operator must make a new selection before Process Batch re-enables (#3).
        # Only for a MANUAL run — a scheduled fire uses its own file snapshot and
        # must not wipe the user's pending interactive selection.
        if success > 0 and not was_scheduled:
            self._reset_queue_after_run()
        # A scheduler-fired run may have completed while minimised to the tray;
        # surface the outcome so it is never invisible (#1).
        if was_scheduled:
            self._announce_scheduled_run(success, fail)
        # A real run wrote fresh backups — Revert/Backups are now meaningful (#6).
        if not dry_run and success > 0:
            self._refresh_revert_state()
        # Force UI refresh so the user sees the updated state immediately
        self.update_idletasks()

    def _last_succeeded_path(self, errors: list[str]) -> str | None:
        """Return the last file in the queue that did NOT fail this batch (#2).

        Engine failure entries are ``"<filename>: <reason>"`` strings, so the
        failed set is the basenames before the first ``": "``. We walk the queue
        in reverse and return the first path whose basename isn't in that set —
        the freshly-written file the reveal button should open. None when every
        queued file failed (or the queue is empty)."""
        if not self.file_queue:
            return None
        failed_names = {
            entry.split(":", 1)[0].strip() for entry in (errors or []) if entry
        }
        for path in reversed(self.file_queue):
            if Path(path).name not in failed_names:
                return path
        return None

    def _reset_queue_after_run(self):
        """Clear the queue + reset the drop zone to its idle prompt and disable
        Process Batch so a fresh selection is required before another run (#3)."""
        self.file_queue = []
        t = _get_colors()
        idle = (
            tr("main.drop_hint_dnd")
            if self.dnd_enabled else tr("main.drop_hint_click")
        )
        self.dz_text.configure(text=idle, text_color=t["text_secondary"])
        self.dz_sub.configure(text=tr("main.drop_sub"))
        self.lbl_queue.configure(text="", text_color=t["text_secondary"])
        self.btn_process.configure(state="disabled")
        # Back to the idle/empty state — restore the 1-2-3 guidance (#12) and
        # hide the now-pointless Clear-queue affordance (#2).
        self._show_empty_state()
        self._hide_clear_queue()

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
            text=tr("main.failed_files_header", count=len(errors)),
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
        # Settle the pulse (#7) before showing the error at a dead-zero bar.
        self._settle_progressbar(0)
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
        """Show an options dialog for creating a new ExRate sheet.

        Symmetric concurrency guard (#1): a batch or a revert already owns the
        cache/API, so refuse to spin up a second engine + asyncio loop. While the
        ExRate worker runs, lock Process/Revert (the dialog only toggles
        btn_export_exrate itself) and poll that button's state to release them
        once the worker re-enables it on completion."""
        if self._batch_running or self._revert_running or self._exrate_running:
            self._flash_busy_status()
            return
        self._exrate_running = True
        self.btn_process.configure(state="disabled")
        self.btn_revert.configure(state="disabled")
        from gui.panels.exrate_dialog import show_exrate_dialog
        show_exrate_dialog(self)
        # The dialog may be cancelled at the file picker (no worker spawned), in
        # which case btn_export_exrate is never disabled — poll handles both: it
        # releases the lock as soon as the export button is back to "normal".
        self._poll_exrate_done()

    def _poll_exrate_done(self):
        """Release the Process/Revert lock once the ExRate flow has finished (#1).

        The standalone ExRate path lives in exrate_dialog: the options dialog
        holds a grab while the user configures it, then a background worker
        disables btn_export_exrate for the duration of the API+save. We hold the
        sibling lock while EITHER is in progress:
          * the dialog still holds the grab (user configuring), OR
          * btn_export_exrate is disabled (worker running).
        When neither is true the flow is over (created OR cancelled), so the
        sibling buttons are released. Polls via after() — never blocks Tk."""
        if not self._exrate_running:
            return
        dialog_open = False
        try:
            dialog_open = self.grab_current() not in (None, self)
        except (RuntimeError, tkinter.TclError):
            dialog_open = False
        try:
            worker_busy = str(self.btn_export_exrate.cget("state")) == "disabled"
        except (RuntimeError, tkinter.TclError):
            worker_busy = False
        if dialog_open or worker_busy:
            # Still configuring or still writing — re-check shortly without
            # touching the network or blocking the Tk thread.
            self.after(150, self._poll_exrate_done)
            return
        self._exrate_running = False
        # Only re-enable Process if there is something queued to process; the
        # idle-state contract (#3) keeps it disabled until a selection exists.
        self.btn_process.configure(
            state="normal" if self.file_queue else "disabled"
        )
        # Revert/Backups only when a backup actually exists (#6).
        self._refresh_revert_state()

    # ================================================================== #
    #  RATE AUDIT — delegated to gui/panels/rate_audit_dialog.py
    # ================================================================== #
    def _open_rate_audit(self):
        """Verify an existing workbook's ExRate rates against BOT and correct
        any differing trading-day cell. Shares the ExRate concurrency guard so
        it can never collide with a batch, a revert, or an ExRate build; the
        worker (or a file-picker cancel) clears ``_exrate_running``."""
        if self._batch_running or self._revert_running or self._exrate_running:
            self._flash_busy_status()
            return
        self._exrate_running = True
        self.btn_process.configure(state="disabled")
        self.btn_revert.configure(state="disabled")
        from gui.panels.rate_audit_dialog import show_rate_audit_dialog
        show_rate_audit_dialog(self)
        self._poll_rate_audit_done()

    def _poll_rate_audit_done(self):
        """Release the Process/Revert lock once the rate-audit flow finishes.

        The worker clears ``_exrate_running`` on success/error and the file
        picker clears it on cancel; until then we re-check via after() without
        blocking Tk (mirrors _poll_exrate_done, keyed on the flag)."""
        if self._exrate_running:
            self.after(150, self._poll_rate_audit_done)
            return
        self.btn_process.configure(
            state="normal" if self.file_queue else "disabled"
        )
        self._refresh_revert_state()

    # ================================================================== #
    #  REVERT
    # ================================================================== #
    def _on_revert_click(self):
        """Pick a ledger and restore its latest backup — after confirmation.

        Guides the operator (#5): the picker defaults to the just-processed
        file's folder, .xlsm files the app fully supports are selectable (#8),
        and before anything is overwritten a confirmation names the file AND the
        backup timestamp it will be restored from. Refuses to run while a batch,
        another revert, or the ExRate worker holds the cache/file (#1, #3)."""
        if self._batch_running or self._revert_running or self._exrate_running:
            self._flash_busy_status()
            return
        # Default the picker to the folder/file of the most recently processed
        # ledger so the operator lands on the file they likely want to undo (#5).
        dialog_kwargs = {
            "title": "Select the file to revert",
            # .xlsm is accepted everywhere else in the app — don't hide the very
            # macro-enabled ledger the operator needs to restore (#8).
            "filetypes": [
                ("Excel workbooks", "*.xlsx *.xlsm"),
                ("All files", "*.*"),
            ],
        }
        last = self.last_processed_path
        if last and Path(last).exists():
            dialog_kwargs["initialdir"] = str(Path(last).parent)
            dialog_kwargs["initialfile"] = Path(last).name
        path = filedialog.askopenfilename(**dialog_kwargs)
        if not path:
            return

        # Look up the available backups FIRST so the confirmation can name the
        # exact backup (and timestamp) that will overwrite the live file (#5).
        try:
            backups = self.backup_mgr.list_backups(path)
        except (OSError, ValueError) as e:
            logger.debug("list_backups failed for %s: %s", path, e)
            backups = []
        if not backups:
            messagebox.showwarning(
                "No Backup Found",
                f"No backup exists for '{Path(path).name}'.\n\n"
                f"A file must have been processed at least once to have a "
                f"backup to restore from.",
            )
            return
        latest_backup = backups[0]
        ts = self.backup_mgr._parse_backup_timestamp(latest_backup)
        when = ts.strftime("%d %b %Y %H:%M") if ts is not None else "the latest backup"
        if not messagebox.askyesno(
            "Confirm Revert",
            f"Restore '{Path(path).name}' from backup dated {when}?\n\n"
            f"This OVERWRITES the current file with the backup. The current "
            f"version is snapshotted first (.pre-revert) so this is recoverable.",
        ):
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

    def _start_guarded_revert(
        self, filepath: str, backup_path: str | None = None
    ) -> bool:
        """Programmatic revert entry for sibling dialogs (Rate Audit report).

        The audit report dialog appears AFTER the audit worker released its
        _exrate_running lease, so its Revert button must re-acquire the busy
        guard itself: refuse while a batch, another revert, or an ExRate
        worker owns the cache/file (#1, #3), otherwise raise _revert_running
        and lock Process/Revert exactly like the manual flow above. The
        RevertWorker finishes through the same _show_revert_success/_error
        callbacks, which clear the flag and re-enable the UI. Returns True
        when the revert was dispatched, False when refused so the caller can
        surface the refusal to the operator."""
        if self._batch_running or self._revert_running or self._exrate_running:
            self._flash_busy_status()
            return False
        # Raise the busy flag BEFORE spawning the worker so a scheduler fire
        # racing in on the UI thread sees the revert in progress (#3).
        self._revert_running = True
        self.btn_revert.configure(state="disabled")
        self.btn_process.configure(state="disabled")
        self.lbl_status.configure(
            text=f"Restoring:  {Path(filepath).name}...",
            text_color=_get_colors()["warning"]
        )
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start()

        self.batch_handler.start_revert(filepath, backup_path=backup_path)
        return True

    def _show_revert_success(self, filepath: str, backup_name: str):
        self._revert_running = False
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(1)
        self.lbl_status.configure(
            text=f"Reverted successfully from backup:  {backup_name}",
            text_color=_get_colors()["success"]
        )
        self.btn_process.configure(state="normal")
        self.last_processed_path = filepath
        self.btn_reveal.pack(pady=(12, 14))
        # A backup still exists post-revert — keep Revert/Backups enabled (#6).
        self._refresh_revert_state()

    def _show_revert_error(self, msg: str):
        self._revert_running = False
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(0)
        self.lbl_status.configure(text=f"Error:  {msg}", text_color=_get_colors()["error_text"])
        self.btn_process.configure(state="normal")
        self._refresh_revert_state()

    def _open_backup_browser(self):
        """Open the Backup Browser so the operator can restore a SPECIFIC
        timestamped backup, not only the most recent one.

        Refuses while a batch, another revert, or the ExRate worker holds the
        cache/file (#1, #3): the browser's own restore reuses the revert busy
        flag and the same _show_revert_success/_error callbacks, so two threads
        must never touch one .xlsx."""
        if self._batch_running or self._revert_running or self._exrate_running:
            self._flash_busy_status()
            return
        from gui.panels.backup_browser import show_backup_browser
        show_backup_browser(self)

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

    # ================================================================== #
    #  HELP / ABOUT  (#9)
    # ================================================================== #
    def _logs_dir(self) -> str:
        """Absolute path to the data/logs folder the audit CSVs land in (#9)."""
        from core.paths import get_project_root
        return str(Path(get_project_root()) / "data" / "logs")

    def _open_folder(self, folder: str):
        """Open ``folder`` in the OS file manager (#9).

        Reuses the same platform-safe, fixed-argv launchers as _reveal_file. The
        folder is realpath-resolved and is_dir()-checked before launch so a
        non-existent/odd path is never handed to a subprocess."""
        try:
            real = os.path.realpath(folder)
            if not Path(real).is_dir():
                # data/logs may not exist until the first run wrote a log.
                Path(real).mkdir(parents=True, exist_ok=True)
            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(["open", real])  # noqa: S603, S607
            elif system == "Windows":
                subprocess.Popen(["explorer", os.path.normpath(real)])  # noqa: S603, S607
            else:
                subprocess.Popen(["xdg-open", real])  # noqa: S603, S607
        except OSError as e:
            logger.debug("open folder failed: %s", e)

    def _open_help(self):
        """Show a small Help / About dialog (#9, #13).

        Lists the version, a one-line license note, the keyboard shortcuts
        wired in _bind_accelerators, and a button that opens the logs/audit
        folder so generated CSVs are reachable. Built as a transient modal so
        Escape closes it like every other dialog in the app."""
        t = _get_colors()
        dialog = ctk.CTkToplevel(self)
        dialog.title(tr("main.help_title"))
        dialog.geometry("460x420")
        dialog.configure(fg_color=t["bg"])
        with contextlib.suppress(RuntimeError, tkinter.TclError, TypeError):
            dialog.transient(self)
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            dialog.grab_set()

        ctk.CTkLabel(
            dialog, text=tr("main.help_title"),
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=t["text_primary"],
        ).pack(pady=(18, 4), padx=20)
        ctk.CTkLabel(
            dialog, text=f"V{APP_VERSION}",
            font=ctk.CTkFont(size=12), text_color=t["text_secondary"],
        ).pack()
        ctk.CTkLabel(
            dialog, text=tr("main.help_license"),
            font=ctk.CTkFont(size=11), text_color=t["text_muted"],
            wraplength=400, justify="center",
        ).pack(pady=(6, 10), padx=20)

        ctk.CTkLabel(
            dialog, text=tr("main.help_shortcuts_header"),
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=t["text_secondary"], anchor="w",
        ).pack(fill="x", padx=24, pady=(4, 0))
        ctk.CTkLabel(
            dialog, text=tr("main.help_shortcuts_body"),
            font=ctk.CTkFont(size=11), text_color=t["text_primary"],
            justify="left", anchor="w",
        ).pack(fill="x", padx=28, pady=(0, 10))

        ctk.CTkButton(
            dialog, text=tr("main.help_open_logs"),
            height=36, width=200,
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=lambda: self._open_folder(self._logs_dir()),
        ).pack(pady=(4, 6))
        close_btn = ctk.CTkButton(
            dialog, text=tr("main.help_close"),
            height=36, width=200,
            fg_color=t["trust_blue"], hover_color=t["blue_hover"],
            font=ctk.CTkFont(size=12, weight="bold"), corner_radius=8,
            command=dialog.destroy,
        )
        close_btn.pack(pady=(0, 14))

        # Match every other modal: Escape cancels, Return confirms (#13).
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.bind("<Return>", lambda e: dialog.destroy())
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            close_btn.focus_set()
        return dialog

    def _apply_theme(self):
        """Re-read the theme and apply colors to ALL widgets."""
        from gui.theme_applicator import apply_theme_to_app
        apply_theme_to_app(self)


    # ================================================================== #
    #  V3.1.0: AUTO-SCHEDULER CALLBACKS
    # ================================================================== #
    def _on_scheduler_start(
        self,
        time_str: str,
        paths: list,
        skip_weekends: bool = False,
        skip_holidays: bool = False,
    ):
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
            skip_weekends=skip_weekends,
            skip_holidays=skip_holidays,
        )
        logger.info(
            "Scheduler started: %s, %d paths, skip_weekends=%s, skip_holidays=%s",
            time_str, len(paths), skip_weekends, skip_holidays,
        )

    def _begin_scheduled_batch(self, scheduled: list[str], start_str: str):
        """UI-thread entry point for a scheduler-fired batch (#3).

        Locks the same controls a manual run does and reflects the run in
        lbl_status so a desk-side user sees it and cannot collide with a manual
        Process/Revert/ExRate. Skip (log + return) when a batch is already
        running (manual or a prior scheduled fire), when a manual revert is in
        flight, OR when an ExRate build is running. The revert check is
        essential: start_revert spawns a RevertWorker that never sets
        _batch_running, so without consulting _revert_running the scheduler
        would spawn a BatchWorker that reads/writes the same .xlsx a
        RevertWorker is restoring a backup over — two threads on one workbook.
        The ExRate check is the same hazard: an ExRateWorker writing the master
        sheet must not race a scheduler-fired BatchWorker on the shared cache
        and progress widgets (#3).
        """
        if self._batch_running:
            logger.info("Scheduled batch skipped — a batch is already running")
            return
        if self._revert_running:
            logger.info("Scheduled batch skipped — a manual revert is in progress")
            return
        if self._exrate_running:
            logger.info("Scheduled batch skipped — an ExRate build is in progress")
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


    def _safe_marshal(self, func, *args):
        """Marshal a callback from a worker thread back onto the Tk thread.

        The BOTExrateApp root is a ctk.CTk and does NOT inherit SafePanel, so it
        has no _safe_after guard. Worker-thread os.listdir()/prescan walks can
        finish AFTER _on_app_close has flipped self._closing and destroyed the
        root, at which point a raw self.after(0, ...) raises an unhandled
        RuntimeError / TclError ("application has been destroyed") in the daemon
        thread. Mirror SafePanel._safe_after: no-op once closing, and swallow
        both exception types (TclError is NOT a RuntimeError subclass) so the
        worker thread stays alive and exits cleanly.
        """
        if getattr(self, "_closing", False):
            return
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            self.after(0, func, *args)

    def _register_worker(self, worker, name):
        """Register a transient GUI worker thread with the thread registry.

        So _on_app_close step 6 (registry.shutdown_all) can account for the
        thread before step 7 self.destroy(), mirroring the rate_ticker worker
        registration. Tolerant of a missing/None registry (getattr) so it never
        breaks a drop or batch start if the registry was not wired up.
        """
        registry = getattr(self, "thread_registry", None)
        if registry is not None:
            registry.register(worker, name=name)

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
