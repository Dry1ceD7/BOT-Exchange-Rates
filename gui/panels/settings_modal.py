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
from datetime import datetime
from typing import Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

from core.config_manager import SettingsManager
from core.enterprise import load_job_history_stats, mask_secret
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
        self.geometry("460x860")
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
        w, h = 460, 860
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

        # ── Profile selector ───────────────────────────────────────────
        ctk.CTkLabel(
            self, text="PROFILE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)
        profiles = self._mgr.list_profiles()
        if "default" not in profiles:
            profiles.insert(0, "default")
        self._profile_var = ctk.StringVar(
            value=self._mgr.profile
        )
        ctk.CTkOptionMenu(
            self,
            values=profiles,
            variable=self._profile_var,
            font=ctk.CTkFont(size=12),
        ).pack(padx=30, pady=(4, 16), fill="x")

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

        self._silent_update_var = ctk.StringVar(
            value="on" if self._settings.get("silent_update", False) else "off"
        )
        ctk.CTkSwitch(
            self,
            text="  Silent update flow (enterprise)",
            variable=self._silent_update_var,
            onvalue="on",
            offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Role / Approval ───────────────────────────────────────────
        ctk.CTkLabel(
            self, text="GOVERNANCE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)
        self._usage_mode_var = ctk.StringVar(
            value=self._settings.get("usage_mode", "admin")
        )
        ctk.CTkSegmentedButton(
            self,
            values=["admin", "operator"],
            variable=self._usage_mode_var,
            font=ctk.CTkFont(size=12),
        ).pack(padx=30, pady=(4, 10), fill="x")

        self._operator_write_var = ctk.StringVar(
            value="on" if self._settings.get("operator_write_access", False) else "off"
        )
        ctk.CTkSwitch(
            self,
            text="  Allow operator write-back",
            variable=self._operator_write_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 8))

        self._approval_var = ctk.StringVar(
            value="on" if self._settings.get("require_approval_before_write", False) else "off"
        )
        ctk.CTkSwitch(
            self,
            text="  Require approval before write-back",
            variable=self._approval_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Data-source controls ───────────────────────────────────────
        ctk.CTkLabel(
            self, text="DATA SOURCES",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)
        self._fx_fallback_var = ctk.StringVar(
            value="on" if self._settings.get("enable_fx_fallback", True) else "off"
        )
        ctk.CTkSwitch(
            self,
            text="  Enable FX fallback source",
            variable=self._fx_fallback_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 8))

        overlay_row = ctk.CTkFrame(self, fg_color="transparent")
        overlay_row.pack(fill="x", padx=30, pady=(0, 16))
        self._holiday_overlay_var = ctk.StringVar(
            value=self._settings.get("holiday_overlay_path", "")
        )
        ctk.CTkEntry(
            overlay_row,
            textvariable=self._holiday_overlay_var,
            placeholder_text="Holiday overlay file (.csv/.json/.txt)",
            height=32,
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(
            overlay_row,
            text="Browse",
            width=80,
            height=32,
            command=self._choose_overlay_file,
        ).pack(side="left")

        # ── Notification controls ──────────────────────────────────────
        ctk.CTkLabel(
            self, text="NOTIFICATIONS & REPORTING",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30)
        self._notify_enabled_var = ctk.StringVar(
            value="on" if self._settings.get("notification_enabled", False) else "off"
        )
        ctk.CTkSwitch(
            self,
            text="  Enable webhook notifications",
            variable=self._notify_enabled_var,
            onvalue="on", offvalue="off",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_text"],
            progress_color=t["modal_accent"],
        ).pack(anchor="w", padx=30, pady=(0, 8))
        self._webhook_var = ctk.StringVar(
            value=self._settings.get("notification_webhook_url", "")
        )
        ctk.CTkEntry(
            self,
            textvariable=self._webhook_var,
            placeholder_text="https://your-webhook-url",
            height=32,
        ).pack(padx=30, pady=(0, 8), fill="x")
        stats = load_job_history_stats(
            limit=int(self._settings.get("job_history_limit", 30))
        )
        ctk.CTkLabel(
            self,
            text=(
                f"Recent runs: {stats.get('runs', 0)}  |  "
                f"Success: {stats.get('success_runs', 0)}  |  "
                f"Failed: {stats.get('failed_runs', 0)}"
            ),
            font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
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
        masked_exg = mask_secret(get_token("BOT_TOKEN_EXG") or "")
        masked_hol = mask_secret(get_token("BOT_TOKEN_HOL") or "")
        ctk.CTkLabel(
            self,
            text=f"EXG: {masked_exg or 'not set'}  |  HOL: {masked_hol or 'not set'}",
            font=ctk.CTkFont(size=10),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30, pady=(0, 8))
        self._token_rotation_var = ctk.StringVar(
            value=str(self._settings.get("token_rotation_days", 90))
        )
        ctk.CTkEntry(
            self,
            textvariable=self._token_rotation_var,
            placeholder_text="Token rotation reminder days (e.g. 90)",
            height=32,
        ).pack(padx=30, pady=(0, 8), fill="x")
        ctk.CTkLabel(
            self,
            text=f"Last rotated: {self._settings.get('token_last_rotated', 'unknown')}",
            font=ctk.CTkFont(size=10),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=30, pady=(0, 8))

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
        if self._settings.get("usage_mode", "admin") != "admin":
            messagebox.showwarning(
                "Admin Required",
                "Token management is restricted to Admin mode.",
            )
            return
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
        if getattr(dialog, "activated", False):
            self._settings["token_last_rotated"] = datetime.now().strftime("%Y-%m-%d")

    def _save_and_close(self):
        selected_profile = self._profile_var.get().strip() or "default"
        if selected_profile != self._mgr.profile:
            self._mgr.set_active_profile(selected_profile)
            self._mgr = SettingsManager(profile=selected_profile)
            self._settings = self._mgr.load()
        self._settings["appearance"] = self._appearance_var.get()
        self._settings["auto_update"] = self._auto_update_var.get() == "on"
        self._settings["silent_update"] = self._silent_update_var.get() == "on"
        selected_label = self._rate_type_var.get()
        self._settings["rate_type"] = self._rate_type_map.get(
            selected_label, "buying_transfer"
        )
        self._settings["usage_mode"] = self._usage_mode_var.get()
        self._settings["operator_write_access"] = self._operator_write_var.get() == "on"
        self._settings["require_approval_before_write"] = self._approval_var.get() == "on"
        self._settings["enable_fx_fallback"] = self._fx_fallback_var.get() == "on"
        self._settings["holiday_overlay_path"] = self._holiday_overlay_var.get().strip()
        self._settings["notification_enabled"] = self._notify_enabled_var.get() == "on"
        self._settings["notification_webhook_url"] = self._webhook_var.get().strip()
        try:
            self._settings["token_rotation_days"] = max(
                1, int(self._token_rotation_var.get().strip())
            )
        except (TypeError, ValueError):
            self._settings["token_rotation_days"] = 90
        self._mgr.save(self._settings)
        self.destroy()

    def _choose_overlay_file(self):
        path = filedialog.askopenfilename(
            title="Select holiday overlay file",
            filetypes=[
                ("Overlay files", "*.csv *.json *.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._holiday_overlay_var.set(path)
