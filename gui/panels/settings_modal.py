#!/usr/bin/env python3
"""
gui/panels/settings_modal.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.8) — Settings Modal Panel
---------------------------------------------------------------------------
Popup window for user preferences backed by core/config_manager.py.
Controls: Appearance (Dark/Light/System), Auto-Update toggle, API keys, API ping.

SFFB: Strict < 200 lines.
"""

import logging
import os
import threading
from typing import Optional

import customtkinter as ctk
import httpx

from core.config_manager import SettingsManager

logger = logging.getLogger(__name__)

COLOR_MODAL_BG = "#1E293B"
COLOR_MODAL_TEXT = "#F1F5F9"
COLOR_MODAL_ACCENT = "#3B82F6"
COLOR_MODAL_SUCCESS = "#22C55E"


class SettingsModal(ctk.CTkToplevel):
    """
    A modal settings window.

    Usage:
        modal = SettingsModal(parent_window)
        modal.grab_set()  # block interaction with parent
    """

    def __init__(self, master, config_dir: Optional[str] = None, **kwargs):
        super().__init__(master, **kwargs)

        self.title("Settings")
        self.geometry("420x540")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_MODAL_BG)

        self._mgr = SettingsManager(config_dir=config_dir)
        self._settings = self._mgr.load()

        self._build_ui()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = 420, 540
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

    def _build_ui(self):
        # Title
        ctk.CTkLabel(
            self, text="Application Settings",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=COLOR_MODAL_TEXT,
        ).pack(pady=(20, 16))

        # ── Appearance ───────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="APPEARANCE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#94A3B8",
        ).pack(anchor="w", padx=30)

        self._appearance_var = ctk.StringVar(
            value=self._settings.get("appearance", "system")
        )
        appearance_menu = ctk.CTkSegmentedButton(
            self,
            values=["system", "dark", "light"],
            variable=self._appearance_var,
            command=self._on_appearance_change,
            font=ctk.CTkFont(size=13),
        )
        appearance_menu.pack(padx=30, pady=(4, 16), fill="x")

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
            text_color=COLOR_MODAL_TEXT,
            progress_color=COLOR_MODAL_ACCENT,
        ).pack(anchor="w", padx=30, pady=(0, 16))

        # ── Manage API Keys ──────────────────────────────────────────
        ctk.CTkButton(
            self, text="Manage API Keys",
            fg_color="#475569", hover_color="#64748B",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_manage_keys,
        ).pack(padx=30, fill="x", pady=(0, 8))

        # ── API Connectivity Test ────────────────────────────────────
        self._btn_ping = ctk.CTkButton(
            self, text="Test API Connection",
            fg_color=COLOR_MODAL_ACCENT,
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_ping_api,
        )
        self._btn_ping.pack(padx=30, fill="x", pady=(0, 8))

        self._lbl_ping = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=COLOR_MODAL_SUCCESS,
        )
        self._lbl_ping.pack(pady=(0, 12))

        # ── Update Channel Toggle ────────────────────────────────────
        channel_frame = ctk.CTkFrame(self, fg_color="transparent")
        channel_frame.pack(padx=30, fill="x", pady=(0, 6))

        ctk.CTkLabel(
            channel_frame, text="Update Channel:",
            font=ctk.CTkFont(size=12), text_color="#94A3B8",
        ).pack(side="left", padx=(0, 8))

        self._channel_var = ctk.StringVar(value=self._settings.get("update_channel", "Stable"))
        self._channel_toggle = ctk.CTkSegmentedButton(
            channel_frame,
            values=["Stable", "Beta"],
            variable=self._channel_var,
            font=ctk.CTkFont(size=12, weight="bold"),
            selected_color="#2563EB",
            selected_hover_color="#1D4ED8",
            unselected_color="#334155",
            unselected_hover_color="#475569",
            corner_radius=6, height=32,
        )
        self._channel_toggle.pack(side="left", expand=True, fill="x")

        # ── Check for Updates ─────────────────────────────────────────
        self._btn_update = ctk.CTkButton(
            self, text="Check for Updates",
            fg_color="#475569", hover_color="#64748B",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_check_update,
        )
        self._btn_update.pack(padx=30, fill="x", pady=(0, 4))

        self._lbl_update = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=COLOR_MODAL_SUCCESS,
        )
        self._lbl_update.pack(pady=(0, 16))

        # ── Save & Close ─────────────────────────────────────────────
        ctk.CTkButton(
            self, text="Save and Close",
            fg_color=COLOR_MODAL_SUCCESS,
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8, height=42,
            command=self._save_and_close,
        ).pack(padx=30, fill="x", pady=(0, 20))

    def _on_appearance_change(self, value: str):
        ctk.set_appearance_mode(value)

    def _on_manage_keys(self):
        from core.paths import get_project_root
        from gui.panels.token_dialog import TokenRegistrationDialog

        env_path = os.path.join(get_project_root(), ".env")
        dialog = TokenRegistrationDialog(
            self,
            env_path=env_path,
            prefill_exg=os.environ.get("BOT_TOKEN_EXG", ""),
            prefill_hol=os.environ.get("BOT_TOKEN_HOL", ""),
        )
        self.wait_window(dialog)

    # ================================================================== #
    #  API CONNECTIVITY CHECK
    # ================================================================== #

    def _on_ping_api(self):
        from core.api_client import BOTClient

        self._lbl_ping.configure(text="Testing...", text_color="#94A3B8")
        self._btn_ping.configure(state="disabled")
        self.update_idletasks()

        def _ping_worker():
            try:
                with httpx.Client(timeout=8.0) as client:
                    api = BOTClient(client)
                    ok = api.ping()
                if ok:
                    self.after(0, self._lbl_ping.configure,
                               {"text": "✓ BOT API is reachable",
                                "text_color": COLOR_MODAL_SUCCESS})
                else:
                    self.after(0, self._lbl_ping.configure,
                               {"text": "✗ API returned unexpected response",
                                "text_color": "#F87171"})
            except Exception as e:
                self.after(0, self._lbl_ping.configure,
                           {"text": f"✗ {e}", "text_color": "#F87171"})
            finally:
                self.after(0, self._btn_ping.configure, {"state": "normal"})

        threading.Thread(target=_ping_worker, daemon=True).start()

    # ================================================================== #
    #  AUTO-UPDATE CHECK
    # ================================================================== #

    def _on_check_update(self):
        from core.auto_updater import check_for_update
        from core.version import __version__

        include_beta = self._channel_var.get() == "Beta"
        self._lbl_update.configure(
            text=f"Checking {'beta' if include_beta else 'stable'} channel...",
            text_color="#94A3B8",
        )
        self._btn_update.configure(state="disabled")
        self.update_idletasks()

        def _worker():
            result = check_for_update(
                current_version=__version__,
                include_prerelease=include_beta,
            )
            if result.get("update_available"):
                ver = result.get("latest_version", "?")
                url = result.get("download_url", "")
                is_beta = result.get("is_prerelease", False)
                self.after(0, self._show_update_available, ver, url, is_beta)
            elif result.get("error"):
                self.after(0, self._lbl_update.configure,
                           {"text": f"Check failed: {result['error']}",
                            "text_color": "#F87171"})
                self.after(0, self._btn_update.configure, {"state": "normal"})
            else:
                channel = "beta" if include_beta else "stable"
                self.after(0, self._lbl_update.configure,
                           {"text": f"✓ Up to date ({channel}: V{__version__})",
                            "text_color": COLOR_MODAL_SUCCESS})
                self.after(0, self._btn_update.configure, {"state": "normal"})

        threading.Thread(target=_worker, daemon=True).start()

    def _show_update_available(self, version: str, url: str, is_beta: bool = False):
        self._pending_ver = version
        beta_tag = " [BETA]" if is_beta else ""
        self._lbl_update.configure(
            text=f"Update available: V{version}{beta_tag}",
            text_color="#F59E0B",
        )
        self._btn_update.configure(
            text=f"Download V{version}{beta_tag}",
            fg_color="#F59E0B", hover_color="#D97706",
            state="normal",
            command=lambda: self._download_in_app(version),
        )

    def _download_in_app(self, version: str):
        """Download and apply the update to the server path."""
        from core.auto_updater import (
            apply_update,
            download_update,
            get_installer_asset_url,
        )

        self._btn_update.configure(state="disabled", text="Downloading...")
        self._lbl_update.configure(text="Fetching update...", text_color="#94A3B8")
        self.update_idletasks()

        def _worker():
            asset = get_installer_asset_url(version)
            if asset.get("error") or not asset.get("url"):
                self.after(0, self._lbl_update.configure,
                           {"text": f"Error: {asset.get('error', 'No installer found')}",
                            "text_color": "#F87171"})
                self.after(0, self._btn_update.configure, {"state": "normal", "text": "Retry"})
                return

            def _progress(downloaded, total):
                pct = int(downloaded / total * 100)
                self.after(0, self._lbl_update.configure,
                           {"text": f"Downloading... {pct}%", "text_color": "#94A3B8"})

            # Download to app's own directory (server path)
            result = download_update(
                url=asset["url"],
                filename=asset.get("filename"),
                progress_cb=_progress,
            )
            if result.get("error"):
                self.after(0, self._lbl_update.configure,
                           {"text": f"Download failed: {result['error']}",
                            "text_color": "#F87171"})
                self.after(0, self._btn_update.configure, {"state": "normal", "text": "Retry"})
                return

            # Apply in-place exe swap on server
            apply_result = apply_update(result["path"])
            if apply_result.get("success"):
                self.after(0, self._lbl_update.configure,
                           {"text": "✅ Update installed — restart to apply",
                            "text_color": COLOR_MODAL_SUCCESS})
                self.after(0, self._btn_update.configure,
                           {"state": "disabled", "text": "Updated ✓"})
            else:
                self.after(0, self._lbl_update.configure,
                           {"text": f"Update failed: {apply_result.get('error', '?')}",
                            "text_color": "#F87171"})
                self.after(0, self._btn_update.configure, {"state": "normal", "text": "Retry"})

        threading.Thread(target=_worker, daemon=True).start()

    def _save_and_close(self):
        self._settings["appearance"] = self._appearance_var.get()
        self._settings["auto_update"] = self._auto_update_var.get() == "on"
        self._settings["update_channel"] = self._channel_var.get()
        self._mgr.save(self._settings)
        self.destroy()
