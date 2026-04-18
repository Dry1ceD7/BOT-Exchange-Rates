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
import os
from typing import Optional

import customtkinter as ctk

from core.config_manager import SettingsManager
from core.secure_tokens import get_token
from gui.theme import get_theme

logger = logging.getLogger(__name__)


class SettingsModal(ctk.CTkToplevel):
    """
    A modal settings window.

    Usage:
        modal = SettingsModal(parent_window)
        modal.grab_set()  # block interaction with parent
    """

    def __init__(self, master, config_dir: Optional[str] = None, **kwargs):
        super().__init__(master, **kwargs)

        t = get_theme()

        self.title("Settings")
        self.geometry("420x720")
        self.resizable(False, False)
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
        w, h = 420, 720
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

    def _build_ui(self):
        t = self._t

        # Title
        ctk.CTkLabel(
            self, text="Application Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=t["modal_text"],
        ).pack(pady=(20, 16))

        # ── Appearance ───────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="APPEARANCE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)

        self._appearance_var = ctk.StringVar(
            value=self._settings.get("appearance", "system")
        )
        ctk.CTkSegmentedButton(
            self,
            values=["system", "dark", "light"],
            variable=self._appearance_var,
            command=self._on_appearance_change,
            font=ctk.CTkFont(size=13),
        ).pack(padx=30, pady=(4, 16), fill="x")

        # ── Rate Type ─────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="RATE TYPE",
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
            self,
            values=["Buying TT", "Selling", "Buying Sight", "Mid Rate"],
            variable=self._rate_type_var,
            font=ctk.CTkFont(size=12),
        ).pack(padx=30, pady=(4, 16), fill="x")

        # ── Auto-Update ──────────────────────────────────────────────
        self._auto_update_var = ctk.StringVar(
            value="on" if self._settings.get("auto_update", True) else "off"
        )
        ctk.CTkSwitch(
            self,
            text="  Check for updates on startup",
            variable=self._auto_update_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Manage API Keys ──────────────────────────────────────────
        ctk.CTkButton(
            self, text="Manage API Keys",
            fg_color=t["btn_secondary"],
            hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_manage_keys,
        ).pack(padx=30, fill="x", pady=(0, 8))

        # ── CSV Panel (extracted) ─────────────────────────────────────
        from gui.panels.csv_panel import CSVPanel
        CSVPanel(self).pack(padx=30, fill="x")

        # ── Version Panel (extracted) ─────────────────────────────────
        from gui.panels.version_panel import VersionPanel

        # Provide callbacks so the panel doesn't traverse the widget tree
        app = self.master  # settings modal → main app
        VersionPanel(
            self,
            on_restart=getattr(app, "_restart_app", None),
            on_error=getattr(app, "_show_download_error", None),
        ).pack(padx=30, fill="x")

        # ── Save & Close ─────────────────────────────────────────────
        ctk.CTkButton(
            self, text="Save and Close",
            fg_color=t["modal_success"],
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8, height=42,
            command=self._save_and_close,
        ).pack(padx=30, fill="x", pady=(12, 20), side="bottom")

    def _on_appearance_change(self, value: str):
        ctk.set_appearance_mode(value)
        parent = self.master
        if hasattr(parent, "_apply_theme"):
            self.after(150, parent._apply_theme)

    def _on_manage_keys(self):
        from core.paths import get_project_root
        from gui.panels.token_dialog import TokenRegistrationDialog

        env_path = os.path.join(get_project_root(), ".env")
        dialog = TokenRegistrationDialog(
            self,
            env_path=env_path,
            prefill_exg=get_token("BOT_TOKEN_EXG") or "",
            prefill_hol=get_token("BOT_TOKEN_HOL") or "",
        )
        self.wait_window(dialog)

    def _save_and_close(self):
        self._settings["appearance"] = self._appearance_var.get()
        self._settings["auto_update"] = self._auto_update_var.get() == "on"
        selected_label = self._rate_type_var.get()
        self._settings["rate_type"] = self._rate_type_map.get(
            selected_label, "buying_transfer"
        )
        self._mgr.save(self._settings)
        self.destroy()
