#!/usr/bin/env python3
"""
gui/panels/settings_modal.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.0) — Settings Modal Panel
---------------------------------------------------------------------------
Popup window for user preferences backed by core/config_manager.py.
Controls: Appearance (Dark/Light/System), Auto-Update toggle, API ping.

SFFB: Strict < 200 lines.
"""

import logging
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
        self.geometry("420x380")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_MODAL_BG)

        self._mgr = SettingsManager(config_dir=config_dir)
        self._settings = self._mgr.load()

        self._build_ui()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = 420, 380
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
        self._lbl_ping.pack(pady=(0, 16))

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

    def _on_ping_api(self):
        self._lbl_ping.configure(text="Pinging...", text_color="#94A3B8")
        self._btn_ping.configure(state="disabled")
        self.update_idletasks()

        def _worker():
            try:
                resp = httpx.get(
                    "https://apigw1.bot.or.th/bot/public/v2/Stat/DailyFXRateAvg/v1/",
                    timeout=8.0,
                )
                self.after(0, self._lbl_ping.configure,
                           {"text": f"API responded: HTTP {resp.status_code}",
                            "text_color": COLOR_MODAL_SUCCESS})
            except Exception as e:
                self.after(0, self._lbl_ping.configure,
                           {"text": f"Connection failed: {e}",
                            "text_color": "#F87171"})
            finally:
                self.after(0, self._btn_ping.configure, {"state": "normal"})

        threading.Thread(target=_worker, daemon=True).start()

    def _save_and_close(self):
        self._settings["appearance"] = self._appearance_var.get()
        self._settings["auto_update"] = self._auto_update_var.get() == "on"
        self._mgr.save(self._settings)
        self.destroy()
