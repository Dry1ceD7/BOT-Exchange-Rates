#!/usr/bin/env python3
"""
gui/app.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Enterprise Desktop Edition
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
"""

import logging
import os
import platform
import re
import subprocess
import threading
from datetime import date, datetime
from tkinter import filedialog, messagebox
from typing import List, Optional

import customtkinter as ctk

from core.auto_updater import check_for_update
from core.backup_manager import BackupManager
from core.config_manager import SettingsManager
from core.version import __version__ as APP_VERSION
from core.workers.event_bus import EventBus
from gui.handlers import BatchHandler
from gui.panels.live_console import LiveConsolePanel

# Load user settings and apply appearance
_settings_mgr = SettingsManager()
_user_settings = _settings_mgr.load()
ctk.set_appearance_mode(_user_settings.get("appearance", "system"))
logger = logging.getLogger(__name__)

# ── Theme System ─────────────────────────────────────────────────────────
# Returns the full color palette for the current appearance mode.
# Dark mode: navy/slate backgrounds with light text
# Light mode: white/gray backgrounds with dark text

def get_theme() -> dict:
    """Return the active color palette based on customtkinter appearance mode."""
    mode = ctk.get_appearance_mode()  # Returns "Dark", "Light", or "System"
    logger.debug("get_theme: ctk.get_appearance_mode() = %r", mode)

    # Determine if we should use dark palette
    if mode.lower() == "light":
        is_dark = False
    elif mode.lower() == "dark":
        is_dark = True
    else:
        # System mode — try to detect, default to dark on failure
        try:
            is_dark = ctk.AppearanceModeTracker.detect_appearance_mode() == 1
        except Exception:
            is_dark = True

    if is_dark:
        return {
            "bg":           "#0B1A33",
            "header_bg":    "#1A365D",
            "header_text":  "#FFFFFF",
            "header_sub":   "#94A3B8",
            "card_bg":      "#1E293B",
            "card_border":  "#334155",
            "divider":      "#334155",
            "section_bg":   "#0F172A",
            "text_primary": "#F1F5F9",
            "text_secondary": "#94A3B8",
            "text_muted":   "#64748B",
            "trust_blue":   "#3B82F6",
            "blue_hover":   "#2563EB",
            "success":      "#22C55E",
            "success_hover": "#16A34A",
            "warning":      "#F59E0B",
            "warning_hover": "#D97706",
            "revert_bg":    "#DC2626",
            "revert_hover": "#B91C1C",
            "error_text":   "#F87171",
            "process_text": "#60A5FA",
            "drop_border":  "#475569",
            "combo_bg":     "#1E293B",
            "combo_border": "#475569",
            "switch_track": "#475569",
            "switch_thumb": "#94A3B8",
        }
    else:
        return {
            "bg":           "#EEF2F7",
            "header_bg":    "#2D4A7A",
            "header_text":  "#FFFFFF",
            "header_sub":   "#CBD5E1",
            "card_bg":      "#FFFFFF",
            "card_border":  "#D1D9E6",
            "divider":      "#D1D9E6",
            "section_bg":   "#F5F7FA",
            "text_primary": "#1A202C",
            "text_secondary": "#4A5568",
            "text_muted":   "#A0AEC0",
            "trust_blue":   "#2B6CB0",
            "blue_hover":   "#2C5282",
            "success":      "#2F855A",
            "success_hover": "#276749",
            "warning":      "#C05621",
            "warning_hover": "#9C4221",
            "revert_bg":    "#C53030",
            "revert_hover": "#9B2C2C",
            "error_text":   "#C53030",
            "process_text": "#2B6CB0",
            "drop_border":  "#CBD5E1",
            "combo_bg":     "#F7FAFC",
            "combo_border": "#CBD5E1",
            "switch_track": "#CBD5E1",
            "switch_thumb": "#4A5568",
        }

# Legacy aliases for backward compatibility during transition
_t = get_theme()
COLOR_BG_DARK       = _t["bg"]
COLOR_HEADER_BG     = _t["header_bg"]
COLOR_HEADER_TEXT    = _t["header_text"]
COLOR_HEADER_SUB    = _t["header_sub"]
COLOR_CARD_BG       = _t["card_bg"]
COLOR_CARD_BORDER   = _t["card_border"]
COLOR_DIVIDER       = _t["divider"]
COLOR_SECTION_BG    = _t["section_bg"]
COLOR_TEXT_PRIMARY   = _t["text_primary"]
COLOR_TEXT_SECONDARY = _t["text_secondary"]
COLOR_TEXT_MUTED     = _t["text_muted"]
COLOR_TRUST_BLUE    = _t["trust_blue"]
COLOR_BLUE_HOVER    = _t["blue_hover"]
COLOR_SUCCESS       = _t["success"]
COLOR_SUCCESS_HOVER = _t["success_hover"]
COLOR_WARNING       = _t["warning"]
COLOR_WARNING_HOVER = _t["warning_hover"]
COLOR_REVERT_BG     = _t["revert_bg"]
COLOR_REVERT_HOVER  = _t["revert_hover"]
COLOR_ERROR_TEXT     = _t["error_text"]
COLOR_PROCESS_TEXT  = _t["process_text"]

# ── Attempt tkinterdnd2 ──────────────────────────────────────────────────
HAS_DND = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception as e:
    logger.debug("tkinterdnd2 not available: %s", e)


def parse_drop_data(raw: str, tk_root=None) -> List[str]:
    """Parse drag-and-drop payload. Uses native Tcl/Tk splitlist for
    cross-platform correctness ({} bracket stripping on macOS/Linux)."""
    if tk_root is not None:
        try:
            return list(tk_root.tk.splitlist(raw))
        except Exception as e:
            logger.debug("Tcl splitlist failed: %s", e)
    # Fallback: regex parser
    results = []
    for match in re.finditer(r'\{([^}]+)\}|(\S+)', raw):
        path = match.group(1) or match.group(2)
        if path:
            results.append(path.strip())
    return results


# Supported Excel extensions (openpyxl handles .xlsx and .xlsm natively)
EXCEL_EXTENSIONS = (".xlsx", ".xlsm")
OPENPYXL_NATIVE = (".xlsx", ".xlsm")


def resolve_excel_files(paths: List[str]) -> List[str]:
    """Resolve individual files and directories into a flat list of Excel files."""
    queue = []
    for p in paths:
        if os.path.isfile(p):
            if p.lower().endswith(EXCEL_EXTENSIONS) and not os.path.basename(p).startswith("."):
                queue.append(p)
        elif os.path.isdir(p):
            for fname in sorted(os.listdir(p)):
                if fname.startswith("."):
                    continue
                if fname.lower().endswith(EXCEL_EXTENSIONS):
                    queue.append(os.path.join(p, fname))
    seen = set()
    unique = []
    for f in queue:
        norm = os.path.normpath(f)
        if norm not in seen:
            seen.add(norm)
            unique.append(f)
    return unique


class BOTExrateApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(f"BOT Exchange Rate Processor  |  V{APP_VERSION}")
        self.geometry("740x920")
        self.resizable(False, True)
        self.configure(fg_color=COLOR_BG_DARK)

        # ── Set window icon ──────────────────────────────────────────────
        self._set_app_icon()

        self.file_queue: List[str] = []
        self.last_processed_path: Optional[str] = None
        self.backup_mgr = BackupManager()
        self.event_bus = EventBus()
        self.batch_handler = BatchHandler(self, event_bus=self.event_bus)

        # Center window
        self.update_idletasks()
        w, h = 740, 920
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

        # DnD injection
        self.dnd_enabled = False
        if HAS_DND:
            try:
                TkinterDnD._require(self)
                self.dnd_enabled = True
            except Exception as e:
                logger.debug("DnD init failed: %s", e)

        self._build_header()
        self._build_card()
        self._build_live_console()
        self._check_for_updates()

    def _set_app_icon(self):
        """Load and set the application window icon (works in source + frozen mode)."""
        import sys
        from tkinter import PhotoImage

        try:
            # Resolve assets directory
            if getattr(sys, "frozen", False):
                # Frozen (PyInstaller): assets bundled alongside exe
                base_dir = os.path.dirname(sys.executable)
            else:
                # Source mode: project root
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

            ico_path = os.path.join(base_dir, "assets", "icon.ico")
            png_path = os.path.join(base_dir, "assets", "icon.png")

            # Windows: use .ico for taskbar + title bar
            if platform.system() == "Windows" and os.path.exists(ico_path):
                self.iconbitmap(ico_path)
                logger.info("Window icon set from: %s", ico_path)
            # All platforms: use .png via iconphoto for Tk title bar
            elif os.path.exists(png_path):
                icon_image = PhotoImage(file=png_path)
                self.iconphoto(True, icon_image)
                # Keep a reference so it's not garbage-collected
                self._icon_ref = icon_image
                logger.info("Window icon set from: %s", png_path)
            else:
                logger.debug("No icon file found at %s or %s", ico_path, png_path)
        except Exception as e:
            logger.debug("Icon loading failed (non-critical): %s", e)

    # ================================================================== #
    #  HEADER
    # ================================================================== #
    def _build_header(self):
        self.hdr_frame = ctk.CTkFrame(self, fg_color=COLOR_HEADER_BG, corner_radius=0, height=80)
        self.hdr_frame.pack(fill="x")
        self.hdr_frame.pack_propagate(False)

        inner = ctk.CTkFrame(self.hdr_frame, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        self.lbl_header_title = ctk.CTkLabel(
            inner, text="Bank of Thailand  —  Ledger Processor",
            font=ctk.CTkFont(size=22, weight="bold"), text_color=COLOR_HEADER_TEXT
        )
        self.lbl_header_title.pack()

        sub_row = ctk.CTkFrame(inner, fg_color="transparent")
        sub_row.pack(pady=(2, 0))
        self.lbl_header_sub = ctk.CTkLabel(
            sub_row, text=f"Enterprise Desktop Edition  |  V{APP_VERSION}",
            font=ctk.CTkFont(size=11), text_color=COLOR_HEADER_SUB
        )
        self.lbl_header_sub.pack(side="left")

        # Settings button — visible, proper button styling
        self._btn_settings = ctk.CTkButton(
            sub_row, text="⚙  Settings", width=90, height=26,
            fg_color="#334155", hover_color="#475569",
            text_color="#E2E8F0",
            font=ctk.CTkFont(size=11, weight="bold"), corner_radius=6,
            border_width=1, border_color="#475569",
            command=self._open_settings,
        )
        self._btn_settings.pack(side="left", padx=(12, 0))

    # ================================================================== #
    #  CARD
    # ================================================================== #
    def _build_card(self):
        self.card = ctk.CTkFrame(
            self, fg_color=COLOR_CARD_BG, corner_radius=16,
            border_width=1, border_color=COLOR_CARD_BORDER
        )
        self.card.pack(pady=22, padx=36, fill="both", expand=True)

        # ── 1. DATE SECTION ──────────────────────────────────────────────
        self.lbl_date_section = ctk.CTkLabel(
            self.card, text="RATE EXTRACTION DATE",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=COLOR_TEXT_SECONDARY
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
            font=ctk.CTkFont(size=13, weight="bold"), text_color=COLOR_TEXT_PRIMARY,
            progress_color=COLOR_TRUST_BLUE,
            button_color="#64748B",
            button_hover_color="#475569",
            fg_color="#94A3B8",
        )
        self.toggle_auto.pack()

        self.lbl_auto_hint = ctk.CTkLabel(
            self.card,
            text="Start date will be read from your Excel files automatically.",
            font=ctk.CTkFont(size=11), text_color=COLOR_TRUST_BLUE
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
            font=ctk.CTkFont(size=12), text_color=COLOR_TEXT_SECONDARY,
            progress_color=COLOR_SUCCESS, button_color=COLOR_CARD_BG,
            button_hover_color="#F0FFF4", fg_color="#CBD5E1"
        )
        self.toggle_today.pack()

        self.lbl_toggle_hint = ctk.CTkLabel(
            self.manual_date_frame,
            text=f"Rates will be extracted up to: {date.today().strftime('%d %b %Y')}",
            font=ctk.CTkFont(size=11), text_color=COLOR_SUCCESS
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
                         text_color=COLOR_TEXT_SECONDARY).pack()
            combo = ctk.CTkComboBox(
                grp, values=values, width=width, height=36,
                fg_color=COLOR_SECTION_BG, border_color="#CBD5E1",
                button_color=COLOR_TRUST_BLUE, button_hover_color=COLOR_BLUE_HOVER,
                dropdown_fg_color=COLOR_CARD_BG, text_color=COLOR_TEXT_PRIMARY,
                font=ctk.CTkFont(size=13), justify="center"
            )
            combo.set(default)
            combo.pack(pady=(4, 0))
            setattr(self, attr, combo)
            self._combo_widgets.append(combo)

        self._lock_date_dropdowns(locked=True)

        # ── Divider ──────────────────────────────────────────────────────
        ctk.CTkFrame(self.card, fg_color=COLOR_DIVIDER, height=1).pack(fill="x", padx=50, pady=(16, 0))

        # ── 2. DROP ZONE ─────────────────────────────────────────────────
        self.lbl_input_section = ctk.CTkLabel(
            self.card, text="LEDGER INPUT",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=COLOR_TEXT_SECONDARY
        )
        self.lbl_input_section.pack(pady=(14, 0))

        self.drop_zone = ctk.CTkFrame(
            self.card, fg_color=COLOR_SECTION_BG, corner_radius=12,
            border_width=2, border_color="#CBD5E1", height=80
        )
        self.drop_zone.pack(pady=(8, 0), padx=50, fill="x")
        self.drop_zone.pack_propagate(False)

        dz_inner = ctk.CTkFrame(self.drop_zone, fg_color="transparent")
        dz_inner.place(relx=0.5, rely=0.5, anchor="center")

        dnd_hint = "Drop Excel files or folders here" if self.dnd_enabled else "Click to select files"
        self.dz_text = ctk.CTkLabel(
            dz_inner, text=dnd_hint,
            font=ctk.CTkFont(size=14, weight="bold"), text_color=COLOR_TEXT_SECONDARY
        )
        self.dz_text.pack()
        self.dz_sub = ctk.CTkLabel(dz_inner, text="or click to browse",
                                    font=ctk.CTkFont(size=11), text_color=COLOR_TEXT_MUTED)
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
                    except Exception as e:
                        logger.debug("DnD bind failed for child widget: %s", e)
            except Exception as e:
                logger.warning("DnD registration failed: %s", e)
                self.dnd_enabled = False

        self.lbl_queue = ctk.CTkLabel(
            self.card, text="", font=ctk.CTkFont(size=12), text_color=COLOR_TEXT_SECONDARY
        )
        self.lbl_queue.pack(pady=(4, 0))

        # ── Divider ──────────────────────────────────────────────────────
        ctk.CTkFrame(self.card, fg_color=COLOR_DIVIDER, height=1).pack(fill="x", padx=50, pady=(12, 0))

        # ── 3. ACTION BUTTONS ────────────────────────────────────────────
        btn_row = ctk.CTkFrame(self.card, fg_color="transparent")
        btn_row.pack(pady=(16, 0))

        self.btn_process = ctk.CTkButton(
            btn_row, text="Process Batch",
            height=48, width=240,
            fg_color=COLOR_TRUST_BLUE, hover_color=COLOR_BLUE_HOVER,
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10, command=self._on_process_click, state="disabled"
        )
        self.btn_process.pack(side="left", padx=(0, 12))

        self.btn_revert = ctk.CTkButton(
            btn_row, text="Revert Previous Edit",
            height=48, width=200,
            fg_color=COLOR_REVERT_BG, hover_color=COLOR_REVERT_HOVER,
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10, command=self._on_revert_click
        )
        self.btn_revert.pack(side="left", padx=(0, 12))

        self.btn_export_exrate = ctk.CTkButton(
            btn_row, text="ExRate Sheet",
            height=48, width=160,
            fg_color="#6366F1", hover_color="#4F46E5",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10, command=self._on_export_exrate
        )
        self.btn_export_exrate.pack(side="left")

        # ── 4. STATUS BOX ────────────────────────────────────────────────
        status_box = ctk.CTkFrame(
            self.card, fg_color=COLOR_SECTION_BG, corner_radius=10,
            border_width=1, border_color=COLOR_CARD_BORDER
        )
        status_box.pack(pady=(16, 0), padx=50, fill="x", ipady=8)

        self.lbl_status = ctk.CTkLabel(
            status_box, text="Status:  Ready  —  Backups enabled",
            font=ctk.CTkFont(size=13), text_color=COLOR_TEXT_SECONDARY
        )
        self.lbl_status.pack(pady=(8, 4))

        self.progressbar = ctk.CTkProgressBar(
            status_box, width=440, height=8,
            progress_color=COLOR_TRUST_BLUE, corner_radius=4
        )
        self.progressbar.pack(pady=(0, 10))
        self.progressbar.set(0)

        # ── 5. REVEAL BUTTON (hidden by default) ────────────────────────
        self.btn_reveal = ctk.CTkButton(
            self.card, text="Show File in Folder",
            height=40, width=220,
            fg_color=COLOR_WARNING, hover_color=COLOR_WARNING_HOVER,
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, command=self._reveal_file
        )

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
                text_color=COLOR_TRUST_BLUE
            )
        else:
            self.lbl_auto_hint.configure(
                text="Manual override — select a start date below.",
                text_color=COLOR_WARNING
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
                text_color=COLOR_SUCCESS
            )
        else:
            self.lbl_toggle_hint.configure(
                text="Select a custom historical start date below.",
                text_color=COLOR_TRUST_BLUE
            )

    def _lock_date_dropdowns(self, locked: bool):
        for combo in self._combo_widgets:
            combo.configure(state="disabled" if locked else "normal")

    def _assemble_start_date(self) -> Optional[str]:
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
        paths = parse_drop_data(event.data, tk_root=self)
        excel_files = resolve_excel_files(paths)
        if excel_files:
            # Warn about truly unsupported formats (.xlsb only)
            unsupported = [f for f in excel_files if f.lower().endswith('.xlsb')]
            if unsupported:
                names = ", ".join(os.path.basename(f) for f in unsupported)
                messagebox.showwarning(
                    "Format Warning",
                    f"These files use .xlsb format which is not supported:\n{names}\n\n"
                    f"Please save as .xlsx first."
                )
                # Remove unsupported files from queue
                excel_files = [f for f in excel_files if not f.lower().endswith('.xlsb')]
            if excel_files:
                self._set_queue(excel_files)
            else:
                messagebox.showwarning("No Valid Files",
                                       "No supported Excel files found.")
        else:
            messagebox.showwarning("No Valid Files",
                                   "No Excel files found in the dropped items.")

    def _browse_files(self):
        paths = filedialog.askopenfilenames(
            title="Select Excel Ledgers",
            filetypes=[
                ("Excel workbooks", "*.xlsx *.xlsm"),
                ("All files", "*.*")
            ]
        )
        if paths:
            self._set_queue(list(paths))

    def _set_queue(self, files: List[str]):
        self.file_queue = files
        self.last_processed_path = None
        count = len(files)
        if count == 1:
            self.dz_text.configure(text=os.path.basename(files[0]), text_color=COLOR_TRUST_BLUE)
        else:
            self.dz_text.configure(text=f"{count} ledgers loaded", text_color=COLOR_TRUST_BLUE)
        self.dz_sub.configure(text="Click to change selection")
        self.lbl_queue.configure(
            text=f"Ready to process {count} ledger{'s' if count != 1 else ''}.",
            text_color=COLOR_SUCCESS
        )
        self.btn_process.configure(state="normal")
        self.btn_reveal.pack_forget()

    # ================================================================== #
    #  PROCESSING
    # ================================================================== #
    def _on_process_click(self):
        if not self.file_queue:
            return
        self.btn_process.configure(state="disabled")
        self.btn_revert.configure(state="disabled")
        self.btn_reveal.pack_forget()
        total = len(self.file_queue)
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(0)

        is_auto = self.auto_detect_var.get() == "on"

        if is_auto:
            # ── V2.4: Smart Date Auto-Detection ──────────────────────
            self.lbl_status.configure(
                text=f"Scanning {total} ledger{'s' if total != 1 else ''} for date range...",
                text_color=COLOR_PROCESS_TEXT
            )
            # Run prescan in background thread to prevent UI freeze
            def _prescan_and_batch():
                from core.engine import LedgerEngine
                oldest_date, was_detected = LedgerEngine.prescan_oldest_date(self.file_queue)
                start_date_str = oldest_date.strftime("%Y-%m-%d")

                def _update_ui_and_start():
                    if was_detected:
                        self.lbl_auto_hint.configure(
                            text=f"Detected: {oldest_date.strftime('%d %b %Y')} → {date.today().strftime('%d %b %Y')}",
                            text_color=COLOR_SUCCESS
                        )
                        self.lbl_status.configure(
                            text=(
                                f"Connecting to BOT API...  range: "
                                f"{oldest_date.strftime('%d %b %Y')} → today  (0 of {total})"
                            ),
                            text_color=COLOR_PROCESS_TEXT
                        )
                    else:
                        self.lbl_auto_hint.configure(
                            text=f"No dates found — using fallback: {oldest_date.strftime('%d %b %Y')}",
                            text_color=COLOR_WARNING
                        )
                        self.lbl_status.configure(
                            text=f"Connecting to BOT API...  fallback range  (0 of {total})",
                            text_color=COLOR_WARNING
                        )
                    self.batch_handler.start_batch(self.file_queue, start_date_str)

                self.after(0, _update_ui_and_start)

            threading.Thread(target=_prescan_and_batch, daemon=True).start()
        else:
            # ── Manual mode ──────────────────────────────────────────
            start_date_str = self._assemble_start_date()
            if start_date_str is None:
                self.btn_process.configure(state="normal")
                self.btn_revert.configure(state="normal")
                return
            self.lbl_status.configure(
                text=f"Connecting to BOT API...  (0 of {total})",
                text_color=COLOR_PROCESS_TEXT
            )
            self.batch_handler.start_batch(self.file_queue, start_date_str)

    def _update_progress(self, idx: int, total: int, fname: str, error):
        self.progressbar.set(idx / total)
        if error:
            self.lbl_status.configure(
                text=f"Warning:  {idx} of {total}  |  {fname} — skipped",
                text_color=COLOR_WARNING
            )
        else:
            self.lbl_status.configure(
                text=f"Processing:  {idx} of {total}  |  {fname}",
                text_color=COLOR_PROCESS_TEXT
            )

    def _show_batch_complete(self, success: int, fail: int, errors: List[str]):
        self.progressbar.set(1)
        self.btn_process.configure(state="normal")
        self.btn_revert.configure(state="normal")
        if fail == 0:
            self.lbl_status.configure(
                text=f"Complete:  All {success} ledger{'s' if success != 1 else ''} processed successfully.",
                text_color=COLOR_SUCCESS
            )
        else:
            self.lbl_status.configure(
                text=f"Complete:  {success} succeeded, {fail} failed.",
                text_color=COLOR_WARNING
            )
        if self.file_queue:
            self.last_processed_path = self.file_queue[-1]
            self.btn_reveal.pack(pady=(12, 14))
        # Force UI refresh so the user sees the updated state immediately
        self.update_idletasks()

    def _show_error(self, msg: str):
        self.progressbar.set(0)
        self.lbl_status.configure(text=f"Error:  {msg}", text_color=COLOR_ERROR_TEXT)
        self.btn_process.configure(state="normal")
        self.btn_revert.configure(state="normal")
        self.update_idletasks()

    # ================================================================== #
    #  EXRATE SHEET — CREATE NEW WITH OPTIONS
    # ================================================================== #
    # Available currencies and rate types from the BOT API
    EXRATE_CURRENCIES = [
        "USD", "EUR", "GBP", "JPY", "CNY", "HKD", "SGD", "AUD", "CHF",
    ]
    EXRATE_RATE_TYPES = {
        "Buying TT":    "buying_transfer",
        "Buying Sight": "buying_sight",
        "Selling":      "selling",
        "Mid Rate":     "mid_rate",
    }

    def _on_export_exrate(self):
        """Show an options dialog for creating a new ExRate sheet."""
        t = get_theme()  # read current palette

        dialog = ctk.CTkToplevel(self)
        dialog.title("Create ExRate File")
        dialog.geometry("440x680")
        dialog.resizable(False, False)
        dialog.configure(fg_color=t["card_bg"])
        dialog.transient(self)
        dialog.grab_set()

        dialog.update_idletasks()
        sx = (dialog.winfo_screenwidth() - 440) // 2
        sy = (dialog.winfo_screenheight() - 680) // 2
        dialog.geometry(f"440x680+{sx}+{sy}")

        ctk.CTkLabel(
            dialog, text="ExRate Sheet Options",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=t["text_primary"],
        ).pack(pady=(16, 12))

        # ── Currencies ────────────────────────────────────────────
        ctk.CTkLabel(
            dialog, text="Currencies",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=t["text_secondary"],
        ).pack(anchor="w", padx=24, pady=(0, 4))

        cur_frame = ctk.CTkFrame(
            dialog, fg_color=t["section_bg"], corner_radius=8,
        )
        cur_frame.pack(fill="x", padx=24, pady=(0, 12))

        cur_vars = {}
        DEFAULTS_ON = {"USD", "EUR"}
        row_frame = None
        for i, ccy in enumerate(self.EXRATE_CURRENCIES):
            if i % 3 == 0:
                row_frame = ctk.CTkFrame(cur_frame, fg_color="transparent")
                row_frame.pack(fill="x", padx=8, pady=2)
            var = ctk.BooleanVar(value=ccy in DEFAULTS_ON)
            cur_vars[ccy] = var
            ctk.CTkCheckBox(
                row_frame, text=ccy, variable=var,
                font=ctk.CTkFont(size=13),
                text_color=t["text_primary"],
                fg_color="#6366F1", hover_color="#4F46E5",
                width=120,
            ).pack(side="left", padx=4, pady=2)

        # ── Rate Types ────────────────────────────────────────────
        ctk.CTkLabel(
            dialog, text="Rate Types",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=t["text_secondary"],
        ).pack(anchor="w", padx=24, pady=(0, 4))

        rate_frame = ctk.CTkFrame(
            dialog, fg_color=t["section_bg"], corner_radius=8,
        )
        rate_frame.pack(fill="x", padx=24, pady=(0, 16))

        rate_vars = {}
        RATE_DEFAULTS = {"Buying TT", "Selling"}
        row_frame2 = None
        for i, (label, _) in enumerate(self.EXRATE_RATE_TYPES.items()):
            if i % 2 == 0:
                row_frame2 = ctk.CTkFrame(rate_frame, fg_color="transparent")
                row_frame2.pack(fill="x", padx=8, pady=2)
            var = ctk.BooleanVar(value=label in RATE_DEFAULTS)
            rate_vars[label] = var
            ctk.CTkCheckBox(
                row_frame2, text=label, variable=var,
                font=ctk.CTkFont(size=13),
                text_color=t["text_primary"],
                fg_color="#6366F1", hover_color="#4F46E5",
                width=180,
            ).pack(side="left", padx=4, pady=2)

        # ── Date Range ─────────────────────────────────────────────
        ctk.CTkLabel(
            dialog, text="Date Range",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=t["text_secondary"],
        ).pack(anchor="w", padx=24, pady=(0, 4))

        date_range_frame = ctk.CTkFrame(
            dialog, fg_color=t["section_bg"], corner_radius=8,
        )
        date_range_frame.pack(fill="x", padx=24, pady=(0, 16))

        # Auto/Manual toggle
        date_mode_var = ctk.StringVar(value="auto")
        today = date.today()

        auto_label = ctk.CTkLabel(
            date_range_frame,
            text=f"  Auto: {today.year}-01-01 → {today.strftime('%Y-%m-%d')}",
            font=ctk.CTkFont(size=12),
            text_color=t["text_secondary"],
        )

        ctk.CTkSwitch(
            date_range_frame,
            text="  Select dates manually",
            variable=date_mode_var,
            onvalue="manual", offvalue="auto",
            font=ctk.CTkFont(size=13),
            text_color=t["text_primary"],
            progress_color="#6366F1",
            command=lambda: _toggle_date_mode(),
        ).pack(anchor="w", padx=12, pady=(8, 0))

        auto_label.pack(anchor="w", padx=12, pady=(2, 8))

        # Manual date inputs (initially hidden)
        manual_frame = ctk.CTkFrame(date_range_frame, fg_color="transparent")
        years = [str(y) for y in range(today.year - 5, today.year + 2)]
        months = [f"{m:02d}" for m in range(1, 13)]
        days = [f"{d:02d}" for d in range(1, 32)]

        # Start date row
        start_row = ctk.CTkFrame(manual_frame, fg_color="transparent")
        start_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(start_row, text="Start:", font=ctk.CTkFont(size=12),
                     text_color=t["text_secondary"], width=40).pack(side="left")
        start_year = ctk.CTkComboBox(
            start_row, values=years, width=80,
            font=ctk.CTkFont(size=12),
            fg_color=t["combo_bg"], border_color=t["combo_border"],
            text_color=t["text_primary"],
        )
        start_year.set(str(today.year))
        start_year.pack(side="left", padx=2)
        start_month = ctk.CTkComboBox(
            start_row, values=months, width=60,
            font=ctk.CTkFont(size=12),
            fg_color=t["combo_bg"], border_color=t["combo_border"],
            text_color=t["text_primary"],
        )
        start_month.set("01")
        start_month.pack(side="left", padx=2)
        start_day = ctk.CTkComboBox(
            start_row, values=days, width=60,
            font=ctk.CTkFont(size=12),
            fg_color=t["combo_bg"], border_color=t["combo_border"],
            text_color=t["text_primary"],
        )
        start_day.set("01")
        start_day.pack(side="left", padx=2)

        # End date row
        end_row = ctk.CTkFrame(manual_frame, fg_color="transparent")
        end_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(end_row, text="End:", font=ctk.CTkFont(size=12),
                     text_color=t["text_secondary"], width=40).pack(side="left")
        end_year = ctk.CTkComboBox(
            end_row, values=years, width=80,
            font=ctk.CTkFont(size=12),
            fg_color=t["combo_bg"], border_color=t["combo_border"],
            text_color=t["text_primary"],
        )
        end_year.set(str(today.year))
        end_year.pack(side="left", padx=2)
        end_month = ctk.CTkComboBox(
            end_row, values=months, width=60,
            font=ctk.CTkFont(size=12),
            fg_color=t["combo_bg"], border_color=t["combo_border"],
            text_color=t["text_primary"],
        )
        end_month.set(f"{today.month:02d}")
        end_month.pack(side="left", padx=2)
        end_day = ctk.CTkComboBox(
            end_row, values=days, width=60,
            font=ctk.CTkFont(size=12),
            fg_color=t["combo_bg"], border_color=t["combo_border"],
            text_color=t["text_primary"],
        )
        end_day.set(f"{today.day:02d}")
        end_day.pack(side="left", padx=2)

        def _toggle_date_mode():
            if date_mode_var.get() == "manual":
                auto_label.pack_forget()
                manual_frame.pack(fill="x", pady=(0, 8))
            else:
                manual_frame.pack_forget()
                auto_label.pack(anchor="w", padx=12, pady=(2, 8))

        # ── Create Button ─────────────────────────────────────────
        def _on_create():
            # Collect selections
            currencies = [c for c, v in cur_vars.items() if v.get()]
            rate_types = {
                lbl: self.EXRATE_RATE_TYPES[lbl]
                for lbl, v in rate_vars.items() if v.get()
            }
            if not currencies:
                ctk.CTkLabel(
                    dialog, text="Select at least one currency",
                    text_color="#EF4444",
                    font=ctk.CTkFont(size=12),
                ).pack(pady=(0, 4))
                return
            if not rate_types:
                ctk.CTkLabel(
                    dialog, text="Select at least one rate type",
                    text_color="#EF4444",
                    font=ctk.CTkFont(size=12),
                ).pack(pady=(0, 4))
                return

            # Get date range
            if date_mode_var.get() == "manual":
                try:
                    s_date = date(int(start_year.get()), int(start_month.get()),
                                  int(start_day.get()))
                    e_date = date(int(end_year.get()), int(end_month.get()),
                                  int(end_day.get()))
                except ValueError:
                    ctk.CTkLabel(
                        dialog, text="Invalid date entered",
                        text_color="#EF4444",
                        font=ctk.CTkFont(size=12),
                    ).pack(pady=(0, 4))
                    return
                date_range = (s_date, e_date)
            else:
                date_range = None  # auto = current year

            dialog.destroy()
            self._create_exrate_file(currencies, rate_types, date_range=date_range)

        ctk.CTkButton(
            dialog, text="Create ExRate File",
            fg_color="#6366F1", hover_color="#4F46E5",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=10, height=44,
            command=_on_create,
        ).pack(padx=24, fill="x", pady=(0, 12))

    def _create_exrate_file(self, currencies, rate_types, date_range=None):
        """Create a new standalone ExRate file — fully independent, pulls from BOT API."""
        dest = filedialog.asksaveasfilename(
            title="Save ExRate File",
            initialfile="ExRate.xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            defaultextension=".xlsx",
        )
        if not dest:
            return

        # Disable button and start progress bar
        self.btn_export_exrate.configure(state="disabled")
        self.lbl_status.configure(
            text="Creating ExRate file...", text_color=COLOR_TEXT_SECONDARY,
        )
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start()
        self.update_idletasks()

        def _status_cb(msg: str):
            self.after(0, self.lbl_status.configure,
                       {"text": msg, "text_color": COLOR_TEXT_SECONDARY})

        def _done(success: bool, message: str):
            """Main-thread callback to restore UI state."""
            self.progressbar.stop()
            self.progressbar.configure(mode="determinate")
            if success:
                self.progressbar.set(1)
                self.lbl_status.configure(
                    text=message, text_color=COLOR_SUCCESS,
                )
                self.last_processed_path = dest
                self.btn_reveal.pack(pady=(12, 14))
            else:
                self.progressbar.set(0)
                self.lbl_status.configure(
                    text=message, text_color=COLOR_ERROR_TEXT,
                )
            self.btn_export_exrate.configure(state="normal")

        def _worker():
            import asyncio

            import httpx
            from openpyxl import Workbook

            from core.api_client import CLIENT_TIMEOUT, BOTClient
            from core.engine import LedgerEngine

            try:
                # Create a blank workbook with an ExRate sheet
                wb = Workbook()
                ws = wb.active
                ws.title = "ExRate"
                wb.save(dest)
                wb.close()

                # Create a fresh engine with its own async client
                async def _run():
                    async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as client:
                        api = BOTClient(client)
                        engine = LedgerEngine(api, event_bus=self.event_bus)
                        return await engine.update_exrate_standalone(
                            dest,
                            progress_cb=_status_cb,
                            currencies=currencies,
                            rate_types=rate_types,
                            date_range=date_range,
                        )

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(_run())
                    self.after(0, _done, True,
                               f"✓ ExRate created: {os.path.basename(dest)}")
                except Exception as e:
                    logger.error("ExRate standalone failed: %s", e)
                    self.after(0, _done, False, f"Failed: {e}")
                finally:
                    loop.close()
            except Exception as e:
                logger.error("ExRate file creation failed: %s", e)
                self.after(0, _done, False, f"Failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()

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

        self.btn_revert.configure(state="disabled")
        self.btn_process.configure(state="disabled")
        self.lbl_status.configure(
            text=f"Restoring:  {os.path.basename(path)}...",
            text_color=COLOR_WARNING
        )
        self.progressbar.configure(mode="indeterminate")
        self.progressbar.start()

        self.batch_handler.start_revert(path)


    def _show_revert_success(self, filepath: str, backup_name: str):
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(1)
        self.lbl_status.configure(
            text=f"Reverted successfully from backup:  {backup_name}",
            text_color=COLOR_SUCCESS
        )
        self.btn_revert.configure(state="normal")
        self.btn_process.configure(state="normal")
        self.last_processed_path = filepath
        self.btn_reveal.pack(pady=(12, 14))

    def _show_revert_error(self, msg: str):
        self.progressbar.stop()
        self.progressbar.configure(mode="determinate")
        self.progressbar.set(0)
        self.lbl_status.configure(text=f"Error:  {msg}", text_color=COLOR_ERROR_TEXT)
        self.btn_revert.configure(state="normal")
        self.btn_process.configure(state="normal")

    # ================================================================== #
    #  FILE REVEAL
    # ================================================================== #
    def _reveal_file(self):
        fp = self.last_processed_path
        if not fp or not os.path.exists(fp):
            return
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.Popen(["open", "-R", fp])
            elif system == "Windows":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(fp)])
        except Exception as e:
            logger.debug("File manager open failed: %s", e)
            self.lbl_status.configure(
                text="Could not open file manager.", text_color=COLOR_WARNING
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
        t = get_theme()

        # ── Window background ─────────────────────────────────────────
        self.configure(fg_color=t["bg"])

        # ── Header ────────────────────────────────────────────────────
        if hasattr(self, "hdr_frame"):
            self.hdr_frame.configure(fg_color=t["header_bg"])
        if hasattr(self, "lbl_header_title"):
            self.lbl_header_title.configure(text_color=t["header_text"])
        if hasattr(self, "lbl_header_sub"):
            self.lbl_header_sub.configure(text_color=t["header_sub"])

        # ── Card ──────────────────────────────────────────────────────
        if hasattr(self, "card"):
            self.card.configure(
                fg_color=t["card_bg"],
                border_color=t["card_border"],
            )

        # ── Section title labels ──────────────────────────────────────
        for attr in ("lbl_date_section", "lbl_input_section"):
            widget = getattr(self, attr, None)
            if widget:
                widget.configure(text_color=t["text_secondary"])

        # ── Auto-detect hint ──────────────────────────────────────────
        if hasattr(self, "lbl_auto_hint"):
            # Keep its dynamic color (blue/warning) — just ensure it's
            # visible against the new background
            pass

        # ── Auto-detect toggle ────────────────────────────────────────
        if hasattr(self, "toggle_auto"):
            self.toggle_auto.configure(
                text_color=t["text_primary"],
                fg_color=t["switch_track"],
                button_color=t["switch_thumb"],
                button_hover_color=t["text_secondary"],
                progress_color=t["trust_blue"],
            )

        # ── Manual "Use Today" toggle ─────────────────────────────────
        if hasattr(self, "toggle_today"):
            self.toggle_today.configure(
                text_color=t["text_secondary"],
                fg_color=t["switch_track"],
                button_color=t["switch_thumb"],
            )

        # ── Date combo boxes ──────────────────────────────────────────
        if hasattr(self, "_combo_widgets"):
            for combo in self._combo_widgets:
                combo.configure(
                    fg_color=t["combo_bg"],
                    border_color=t["combo_border"],
                    text_color=t["text_primary"],
                    dropdown_fg_color=t["card_bg"],
                    button_color=t["trust_blue"],
                    button_hover_color=t["blue_hover"],
                )
        # Date combo labels (Year, Month, Day)
        if hasattr(self, "manual_date_frame"):
            for child in self.manual_date_frame.winfo_children():
                for sub in child.winfo_children():
                    for label in sub.winfo_children():
                        if isinstance(label, ctk.CTkLabel):
                            label.configure(text_color=t["text_secondary"])

        # ── Drop zone ────────────────────────────────────────────────
        if hasattr(self, "drop_zone"):
            self.drop_zone.configure(
                fg_color=t["section_bg"],
                border_color=t["drop_border"],
            )
        if hasattr(self, "dz_text"):
            self.dz_text.configure(text_color=t["text_secondary"])
        if hasattr(self, "dz_sub"):
            self.dz_sub.configure(text_color=t["text_muted"])

        # ── Queue label ───────────────────────────────────────────────
        if hasattr(self, "lbl_queue"):
            self.lbl_queue.configure(text_color=t["text_secondary"])

        # ── Status box ────────────────────────────────────────────────
        if hasattr(self, "lbl_status"):
            status_parent = self.lbl_status.master
            if status_parent:
                status_parent.configure(
                    fg_color=t["section_bg"],
                    border_color=t["card_border"],
                )

        # ── Progress bar ──────────────────────────────────────────────
        if hasattr(self, "progressbar"):
            self.progressbar.configure(progress_color=t["trust_blue"])

        # ── Buttons ───────────────────────────────────────────────────
        if hasattr(self, "btn_process"):
            self.btn_process.configure(
                fg_color=t["trust_blue"],
                hover_color=t["blue_hover"],
            )
        if hasattr(self, "btn_revert"):
            self.btn_revert.configure(
                fg_color=t["revert_bg"],
                hover_color=t["revert_hover"],
            )
        if hasattr(self, "btn_reveal"):
            self.btn_reveal.configure(
                fg_color=t["warning"],
                hover_color=t["warning_hover"],
            )

        # ── Dividers — recolor all 1px height frames in card ─────────
        if hasattr(self, "card"):
            for child in self.card.winfo_children():
                try:
                    if child.cget("height") == 1:
                        child.configure(fg_color=t["divider"])
                except Exception:
                    pass

        # ── Live console keeps its dark terminal aesthetic ────────────
        # (intentionally not themed — it stays dark in both modes)

        logger.debug("Theme applied: %s mode", ctk.get_appearance_mode())

    # ================================================================== #
    #  V3.0: AUTO-UPDATER (background, non-blocking)
    # ================================================================== #
    def _check_for_updates(self):
        """Check for updates in background thread on startup."""
        if not _user_settings.get("auto_update", True):
            return

        def _worker():
            result = check_for_update(current_version=APP_VERSION)
            if result.get("update_available"):
                ver = result.get("latest_version", "?")
                url = result.get("download_url", "")
                self.after(0, self._show_update_banner, ver, url)

        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_banner(self, version: str, url: str):
        """Show a visible update banner at the TOP of the app (below header)."""
        # Remove old banner if exists
        if hasattr(self, '_update_banner') and self._update_banner:
            self._update_banner.destroy()

        self._update_banner = ctk.CTkFrame(
            self, fg_color="#F59E0B", corner_radius=0, height=40,
        )
        # Insert right after header (before card)
        self._update_banner.pack(fill="x", before=self.card, pady=0)
        self._update_banner.pack_propagate(False)

        banner_inner = ctk.CTkFrame(self._update_banner, fg_color="transparent")
        banner_inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            banner_inner,
            text=f"  Update available: V{version}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#1E293B",
        ).pack(side="left", padx=(0, 12))

        self._pending_update_ver = version

        ctk.CTkButton(
            banner_inner, text="Update Now",
            width=100, height=28,
            fg_color="#1E293B", hover_color="#0F172A",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6,
            command=lambda: self._start_in_app_update(version),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            banner_inner, text="✕",
            width=28, height=28,
            fg_color="transparent", hover_color="#D97706",
            text_color="#1E293B",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=4,
            command=lambda: self._update_banner.destroy(),
        ).pack(side="left")

    def _start_in_app_update(self, version: str):
        """Download and install the update to the server path."""
        from core.auto_updater import (
            apply_update,
            download_update,
            get_installer_asset_url,
        )

        # Update banner to show downloading state
        if hasattr(self, '_update_banner') and self._update_banner:
            for w in self._update_banner.winfo_children():
                w.destroy()
            self._dl_label = ctk.CTkLabel(
                self._update_banner,
                text="  Downloading update...",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#1E293B",
            )
            self._dl_label.place(relx=0.5, rely=0.5, anchor="center")

        def _worker():
            # Get the .exe asset URL
            asset = get_installer_asset_url(version)
            if asset.get("error") or not asset.get("url"):
                self.after(0, self._show_download_error,
                           asset.get("error", "No installer found"))
                return

            def _progress(downloaded, total):
                pct = int(downloaded / total * 100)
                self.after(0, self._update_dl_progress, pct)

            # Download to the app's own directory (server path)
            result = download_update(
                url=asset["url"],
                filename=asset.get("filename"),
                progress_cb=_progress,
            )
            if result.get("error"):
                self.after(0, self._show_download_error, result["error"])
                return

            # Apply the update (in-place exe swap on server)
            apply_result = apply_update(result["path"])
            if apply_result.get("success"):
                self.after(0, self._show_update_success)
            else:
                self.after(0, self._show_download_error,
                           apply_result.get("error", "Update failed"))

        threading.Thread(target=_worker, daemon=True).start()

    def _update_dl_progress(self, pct: int):
        if hasattr(self, '_dl_label'):
            self._dl_label.configure(text=f"  Downloading update... {pct}%")

    def _show_download_error(self, error: str):
        if hasattr(self, '_update_banner') and self._update_banner:
            for w in self._update_banner.winfo_children():
                w.destroy()
            self._update_banner.configure(fg_color="#DC2626")
            ctk.CTkLabel(
                self._update_banner,
                text=f"  Update failed: {error}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#FFFFFF",
            ).place(relx=0.5, rely=0.5, anchor="center")

    def _show_update_success(self):
        """Show success banner with Restart Now / Restart Later options."""
        if hasattr(self, '_update_banner') and self._update_banner:
            for w in self._update_banner.winfo_children():
                w.destroy()
            self._update_banner.configure(fg_color="#059669", height=44)

            inner = ctk.CTkFrame(self._update_banner, fg_color="transparent")
            inner.place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(
                inner,
                text="  ✅ Update installed successfully!",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FFFFFF",
            ).pack(side="left", padx=(0, 16))

            ctk.CTkButton(
                inner, text="Restart Now",
                width=110, height=28,
                fg_color="#FFFFFF", hover_color="#D1FAE5",
                text_color="#065F46",
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=6,
                command=self._restart_app,
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                inner, text="Restart Later",
                width=110, height=28,
                fg_color="transparent", hover_color="#047857",
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=6,
                border_width=1, border_color="#FFFFFF",
                command=self._dismiss_update_banner,
            ).pack(side="left")

    def _dismiss_update_banner(self):
        """Dismiss the update banner — update is installed, will take effect on next launch."""
        if hasattr(self, '_update_banner') and self._update_banner:
            self._update_banner.destroy()
            self._update_banner = None
        # Show a subtle status message
        if hasattr(self, 'lbl_status'):
            self.lbl_status.configure(
                text="Update installed — will apply on next restart.",
                text_color=COLOR_SUCCESS,
            )

    def _restart_app(self):
        """Restart the application — launch new exe and exit current process."""
        import subprocess
        import sys

        logger.info("User requested restart after update")
        try:
            if getattr(sys, "frozen", False):
                # Frozen app: launch the updated exe as a detached process
                exe_path = os.path.abspath(sys.executable)
                if platform.system() == "Windows":
                    # Use DETACHED_PROCESS flag for clean separation
                    DETACHED_PROCESS = 0x00000008
                    subprocess.Popen(
                        [exe_path],
                        creationflags=DETACHED_PROCESS,
                        close_fds=True,
                    )
                else:
                    subprocess.Popen([exe_path])
                # Give the new process a moment to start, then exit
                self.after(500, self._exit_for_restart)
            else:
                # Dev mode: just close
                self.destroy()
        except Exception as e:
            logger.error("Restart failed: %s", e)
            self._show_download_error(f"Restart failed: {e}")

    def _exit_for_restart(self):
        """Clean exit for restart — destroy window and exit process."""
        import sys
        try:
            self.destroy()
        except Exception:
            pass
        sys.exit(0)


if __name__ == "__main__":
    app = BOTExrateApp()
    app.mainloop()
