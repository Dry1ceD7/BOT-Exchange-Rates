#!/usr/bin/env python3
"""
gui/panels/settings_modal.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Settings Modal Panel
---------------------------------------------------------------------------
Popup window for user preferences backed by core/config_manager.py.
Composes: CSVPanel (csv_panel.py) + VersionPanel (version_panel.py).

SFFB: Strict < 200 lines.  (Previously 731 → now ~130)
"""

import logging
import platform
import subprocess
from pathlib import Path

import customtkinter as ctk

from core.config_manager import SettingsManager
from core.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGE_LABELS,
    SUPPORTED_LANGUAGES,
    set_language,
    tr,
)
from core.paths import get_project_root
from core.secure_tokens import get_token
from gui.theme import get_theme

logger = logging.getLogger(__name__)


def _open_folder(folder: str) -> bool:
    """Open ``folder`` in the OS file manager.

    Mirrors app.py:_reveal_file's platform-safe launch logic but targets a
    directory (no file selection). The path is realpath-resolved and checked
    to be a directory before handing a fixed argv to the OS launcher, so the
    subprocess call never receives untrusted/shell-interpolated input.

    Returns True on a successful launch, False otherwise (missing dir or
    OSError). Never raises — callers surface failure to the user.
    """
    # SEC: resolve symlinks for the security check, then verify it's a dir.
    resolved = Path(folder).resolve()
    if not resolved.is_dir():
        logger.warning("Open-folder target is not a directory: %s", folder)
        return False
    target = str(resolved)
    try:
        system = platform.system()
        # noqa S603/S607: target is resolve()-d and is_dir()-checked above;
        # each call uses the OS-standard file-manager launcher with fixed argv.
        if system == "Darwin":
            subprocess.Popen(["open", target])  # noqa: S603, S607
        elif system == "Windows":
            subprocess.Popen(["explorer", target])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", target])  # noqa: S603, S607
        return True
    except OSError as e:
        logger.debug("File manager open failed: %s", e)
        return False


class SettingsModal(ctk.CTkToplevel):
    """
    A modal settings window.

    Usage:
        modal = SettingsModal(parent_window)
        modal.grab_set()  # block interaction with parent
    """

    def __init__(self, master, config_dir: str | None = None, **kwargs):
        super().__init__(master, **kwargs)

        t = get_theme()

        self.title(tr("settings.title"))
        # The content now lives in a scrollable body (see _build_ui), so a short
        # window on a small legacy screen never clips the bottom Save button —
        # the body scrolls instead. Height is capped to the available screen so
        # the window itself always fits; width stays fixed for a tidy layout.
        self._width = 420
        self._height = min(720, max(480, self.winfo_screenheight() - 120))
        self.geometry(f"{self._width}x{self._height}")
        self.resizable(False, True)
        self.minsize(self._width, 460)
        self.configure(fg_color=t["modal_bg"])

        self._mgr = SettingsManager(config_dir=config_dir)
        self._settings = self._mgr.load()
        self._t = t

        self._build_ui()
        self._center()

        # ── Keyboard accessibility ─────────────────────────────────────
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Command-s>", lambda e: self._save_and_close())
        self.bind("<Control-s>", lambda e: self._save_and_close())
        self.focus_set()

    def _center(self):
        self.update_idletasks()
        w, h = self._width, self._height
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

    def _build_ui(self):
        t = self._t

        # Title (stays pinned at the top, outside the scroll region).
        ctk.CTkLabel(
            self, text=tr("settings.heading"),
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=t["modal_text"],
        ).pack(pady=(20, 12))

        # ── Save & Close (pinned to the bottom, OUTSIDE the scroll body) ──
        # Packed before the body so the body's expand never pushes it off the
        # window on a short legacy screen (the audit's clipped-controls bug).
        ctk.CTkButton(
            self, text=tr("settings.btn_save"),
            fg_color=t["modal_success"],
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8, height=42,
            command=self._save_and_close,
        ).pack(padx=30, fill="x", pady=(8, 16), side="bottom")

        # ── Scrollable content body ──────────────────────────────────
        # All controls live here so they scroll instead of clipping when the
        # window is shorter than the content (small screens / many options).
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Appearance ───────────────────────────────────────────────
        ctk.CTkLabel(
            self.body, text=tr("settings.section_appearance"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)

        # Show Title-Case labels (consistent with the Rate Type buttons below)
        # while persisting the lowercase CustomTkinter mode codes. The
        # label<->code maps mirror the rate-type pattern so casing is uniform
        # across every segmented button in the modal.
        self._appearance_map = {
            "System": "system",
            "Dark": "dark",
            "Light": "light",
        }
        self._appearance_reverse = {
            v: k for k, v in self._appearance_map.items()
        }
        current_mode = self._settings.get("appearance", "system")
        self._appearance_var = ctk.StringVar(
            value=self._appearance_reverse.get(current_mode, "System")
        )
        ctk.CTkSegmentedButton(
            self.body,
            values=["System", "Dark", "Light"],
            variable=self._appearance_var,
            command=self._on_appearance_change,
            font=ctk.CTkFont(size=13),
        ).pack(padx=30, pady=(4, 16), fill="x")

        # ── Language ─────────────────────────────────────────────────
        # A Thai accounting office needs the UI in Thai. The selector shows
        # human-readable language NAMES; the persisted value is the lowercase
        # code ('en'/'th'). Most surfaces re-read tr() when rebuilt, so a
        # restart-style note tells the user the change applies on reopen.
        ctk.CTkLabel(
            self.body, text=tr("settings.section_language"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)

        current_lang = self._settings.get("language", DEFAULT_LANGUAGE)
        if current_lang not in SUPPORTED_LANGUAGES:
            current_lang = DEFAULT_LANGUAGE
        # Map display name <-> code so the segmented button can show names.
        self._lang_label_to_code = {
            LANGUAGE_LABELS[code]: code for code in SUPPORTED_LANGUAGES
        }
        self._lang_code_to_label = {
            code: label for label, code in self._lang_label_to_code.items()
        }
        self._language_var = ctk.StringVar(
            value=self._lang_code_to_label[current_lang]
        )
        ctk.CTkSegmentedButton(
            self.body,
            values=[LANGUAGE_LABELS[c] for c in SUPPORTED_LANGUAGES],
            variable=self._language_var,
            font=ctk.CTkFont(size=13),
        ).pack(padx=30, pady=(4, 2), fill="x")
        ctk.CTkLabel(
            self.body, text=tr("settings.language_restart_note"),
            font=ctk.CTkFont(size=10),
            text_color=t["modal_muted"],
            anchor="w", justify="left", wraplength=340,
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Rate Type ─────────────────────────────────────────────────
        ctk.CTkLabel(
            self.body, text=tr("settings.section_rate_type"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)

        self._rate_type_map = {
            "Buying TT": "buying_transfer",
            "Selling": "selling",
            "Buying Sight": "buying_sight",
            "Mid Rate": "mid_rate",
        }
        self._rate_type_reverse = {v: k for k, v in self._rate_type_map.items()}

        current_api_field = self._settings.get("rate_type", "buying_transfer")
        current_label = self._rate_type_reverse.get(
            current_api_field, "Buying TT"
        )
        self._rate_type_var = ctk.StringVar(value=current_label)
        ctk.CTkSegmentedButton(
            self.body,
            values=["Buying TT", "Selling", "Buying Sight", "Mid Rate"],
            variable=self._rate_type_var,
            font=ctk.CTkFont(size=12),
        ).pack(padx=30, pady=(4, 16), fill="x")

        # ── Anomaly Threshold ─────────────────────────────────────────
        ctk.CTkLabel(
            self.body, text=tr("settings.section_anomaly"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)

        self._anomaly_threshold_var = ctk.StringVar(
            value=str(self._settings.get("anomaly_threshold_pct", 5.0))
        )
        self._anomaly_entry = ctk.CTkEntry(
            self.body,
            textvariable=self._anomaly_threshold_var,
            font=ctk.CTkFont(size=13),
        )
        self._anomaly_entry.pack(padx=30, pady=(4, 4), fill="x")

        # Inline validation error (hidden until a bad threshold is entered).
        self._anomaly_error = ctk.CTkLabel(
            self.body, text="",
            font=ctk.CTkFont(size=11),
            text_color=t["error_text"],
            anchor="w",
        )
        self._anomaly_error.pack(anchor="w", padx=30, pady=(0, 12))

        # ── Auto-Update ──────────────────────────────────────────────
        self._auto_update_var = ctk.StringVar(
            value="on" if self._settings.get("auto_update", True) else "off"
        )
        ctk.CTkSwitch(
            self.body,
            text=tr("settings.auto_update_toggle"),
            variable=self._auto_update_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Manage API Keys ──────────────────────────────────────────
        ctk.CTkButton(
            self.body, text=tr("settings.btn_manage_keys"),
            fg_color=t["btn_secondary"],
            hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_manage_keys,
        ).pack(padx=30, fill="x", pady=(0, 8))

        # ── Open Logs / Audit Folder ─────────────────────────────────
        ctk.CTkButton(
            self.body, text=tr("settings.btn_open_logs"),
            fg_color=t["btn_secondary"],
            hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_open_logs,
        ).pack(padx=30, fill="x", pady=(0, 8))

        # ── Export / Import Settings (multi-PC deployment) ───────────
        # An admin can export this PC's preferences (rate type, anomaly
        # threshold, language, appearance, scheduler config) and import them on
        # another machine. Secrets are NEVER included — API keys live in the OS
        # keyring, and config_manager strips any sensitive-looking key as a
        # belt-and-suspenders guard.
        ctk.CTkButton(
            self.body, text=tr("settings.btn_export_settings"),
            fg_color=t["btn_secondary"],
            hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_export_settings,
        ).pack(padx=30, fill="x", pady=(0, 8))

        ctk.CTkButton(
            self.body, text=tr("settings.btn_import_settings"),
            fg_color=t["btn_secondary"],
            hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_import_settings,
        ).pack(padx=30, fill="x", pady=(0, 4))

        # Inline status surface for the export/import buttons (success or
        # humanized failure — never a raw errno/traceback).
        self._settings_io_status = ctk.CTkLabel(
            self.body, text="",
            font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
            anchor="w", justify="left", wraplength=340,
        )
        self._settings_io_status.pack(anchor="w", padx=30, pady=(0, 8))

        # ── CSV Panel (extracted) ─────────────────────────────────────
        from gui.panels.csv_panel import CSVPanel
        CSVPanel(self.body).pack(padx=30, fill="x")

        # ── Version Panel (extracted) ─────────────────────────────────
        # on_restart: the previous wiring probed the app for a phantom
        # restart method that never existed, so the parameter was always None
        # and the "Restart Now" promise leaned on the panel's silent fallback.
        # Wire it honestly to the real relaunch entry point instead.
        from core.auto_updater import restart_app
        from gui.panels.version_panel import VersionPanel

        app = self.master  # settings modal → main app
        VersionPanel(
            self.body,
            on_restart=restart_app,
            on_error=getattr(app, "_show_download_error", None),
        ).pack(padx=30, fill="x")

    def _on_appearance_change(self, value: str):
        # The segmented button now hands back a Title-Case label ("Dark"); map
        # it to the lowercase mode code CustomTkinter expects.
        mode = self._appearance_map.get(value, value.lower())
        ctk.set_appearance_mode(mode)
        parent = self.master
        if hasattr(parent, "_apply_theme"):
            self.after(150, parent._apply_theme)

    def _on_open_logs(self):
        """Reveal data/logs (audit CSVs + rotated logs) in the OS file manager.

        Creates the directory if it does not exist yet so a fresh install with
        no logs still opens an (empty) folder rather than dead-ending. Surfaces
        a failed launch inline on the anomaly-error label, the only inline
        status surface this modal owns.
        """
        logs_dir = Path(get_project_root()) / "data" / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.debug("Could not create logs dir: %s", e)
        if not _open_folder(str(logs_dir)):
            self._anomaly_error.configure(
                text=tr("settings.open_logs_failed")
            )

    def _on_manage_keys(self):
        from gui.panels.token_dialog import TokenRegistrationDialog

        env_path = str(Path(get_project_root()) / ".env")
        dialog = TokenRegistrationDialog(
            self,
            env_path=env_path,
            prefill_exg=get_token("BOT_TOKEN_EXG") or "",
            prefill_hol=get_token("BOT_TOKEN_HOL") or "",
        )
        self.wait_window(dialog)

    def _on_export_settings(self):
        """Write this PC's settings (no secrets) to a chosen JSON file.

        A tiny, bounded synchronous write (the settings dict is a handful of
        keys), so no worker thread is needed. Failures are humanized — the raw
        errno stays in the log only.
        """
        from tkinter import filedialog as fd

        dest = fd.asksaveasfilename(
            title=tr("settings.export_dialog_title"),
            defaultextension=".json",
            initialfile="bot_exrate_settings.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not dest:
            return
        try:
            self._mgr.export_settings(dest)
        except OSError as e:
            logger.error("Settings export failed for %s: %r", dest, e)
            self._settings_io_status.configure(
                text=tr("settings.export_failed"),
                text_color=self._t["error_text"],
            )
            return
        self._settings_io_status.configure(
            text=tr("settings.export_ok"),
            text_color=self._t["modal_success"],
        )

    def _on_import_settings(self):
        """Load settings from a chosen JSON file and apply them in this modal.

        Only known keys are accepted and secrets are stripped (config_manager
        enforces both). After a successful import the modal's controls are
        refreshed from disk so the user immediately sees the imported values,
        and the active language/appearance are applied live.
        """
        from tkinter import filedialog as fd

        src = fd.askopenfilename(
            title=tr("settings.import_dialog_title"),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not src:
            return
        try:
            self._settings = self._mgr.import_settings(src)
        except (OSError, ValueError) as e:
            logger.error("Settings import failed for %s: %r", src, e)
            self._settings_io_status.configure(
                text=tr("settings.import_failed"),
                text_color=self._t["error_text"],
            )
            return
        self._apply_imported_settings()
        self._settings_io_status.configure(
            text=tr("settings.import_ok"),
            text_color=self._t["modal_success"],
        )

    def _apply_imported_settings(self):
        """Refresh the modal's controls from the freshly-imported settings.

        Updates the on-screen Vars so the user sees the imported values without
        reopening the window, and applies appearance + language live.
        """
        appearance = self._settings.get("appearance", "system")
        self._appearance_var.set(
            self._appearance_reverse.get(appearance, "System")
        )
        ctk.set_appearance_mode(appearance)
        parent = self.master
        if hasattr(parent, "_apply_theme"):
            self.after(150, parent._apply_theme)

        rate_field = self._settings.get("rate_type", "buying_transfer")
        self._rate_type_var.set(
            self._rate_type_reverse.get(rate_field, "Buying TT")
        )
        self._anomaly_threshold_var.set(
            str(self._settings.get("anomaly_threshold_pct", 5.0))
        )
        self._auto_update_var.set(
            "on" if self._settings.get("auto_update", True) else "off"
        )
        lang_code = self._settings.get("language", DEFAULT_LANGUAGE)
        if lang_code not in SUPPORTED_LANGUAGES:
            lang_code = DEFAULT_LANGUAGE
        self._language_var.set(self._lang_code_to_label[lang_code])
        set_language(lang_code)

    def _validate_anomaly_threshold(self) -> float | None:
        """Return a positive float threshold, or None if the entry is invalid.

        On invalid/non-positive input, shows an inline error and parks focus in
        the entry so a typo can't silently keep the old guardrail value while
        the modal pretends the save succeeded.
        """
        raw = self._anomaly_threshold_var.get().strip()
        try:
            threshold = float(raw)
        except (TypeError, ValueError):
            self._anomaly_error.configure(
                text=tr("settings.anomaly_invalid")
            )
            self._anomaly_entry.focus_set()
            return None
        if threshold <= 0:
            self._anomaly_error.configure(
                text=tr("settings.anomaly_nonpositive")
            )
            self._anomaly_entry.focus_set()
            return None
        self._anomaly_error.configure(text="")
        return threshold

    def _save_and_close(self):
        # Validate the anomaly threshold FIRST: on a bad/typo'd value, surface
        # an inline error and abort the save+close so the user can correct it.
        threshold = self._validate_anomaly_threshold()
        if threshold is None:
            return
        # Map the Title-Case appearance label back to the lowercase mode code.
        self._settings["appearance"] = self._appearance_map.get(
            self._appearance_var.get(), "system"
        )
        self._settings["auto_update"] = self._auto_update_var.get() == "on"
        selected_label = self._rate_type_var.get()
        self._settings["rate_type"] = self._rate_type_map.get(
            selected_label, "buying_transfer"
        )
        self._settings["anomaly_threshold_pct"] = threshold
        # Persist the chosen UI language (lowercase code) and refresh the
        # i18n cache so newly-built surfaces pick it up without a full restart.
        lang_code = self._lang_label_to_code.get(
            self._language_var.get(), DEFAULT_LANGUAGE
        )
        self._settings["language"] = lang_code
        set_language(lang_code)
        self._mgr.save(self._settings)
        self.destroy()
