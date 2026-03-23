#!/usr/bin/env python3
"""
gui/panels/token_dialog.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.3) — API Token Registration Dialog
---------------------------------------------------------------------------
License-key-style popup that collects BOT API tokens on first use.
Writes validated tokens to .env and injects them into os.environ.

SFFB: Strict < 200 lines.
"""

import logging
import os
import webbrowser
from typing import Optional

import customtkinter as ctk

logger = logging.getLogger(__name__)

# ── Theme Constants ──────────────────────────────────────────────────────
COLOR_BG = "#0F172A"
COLOR_CARD = "#1E293B"
COLOR_TEXT = "#F1F5F9"
COLOR_MUTED = "#94A3B8"
COLOR_ACCENT = "#3B82F6"
COLOR_SUCCESS = "#22C55E"
COLOR_ERROR = "#F87171"
COLOR_ENTRY_BG = "#334155"

BOT_PORTAL_URL = "https://apiportal.bot.or.th/"
MIN_KEY_LENGTH = 8


class TokenRegistrationDialog(ctk.CTkToplevel):
    """
    A registration-key-style modal for collecting BOT API tokens.

    Usage:
        dialog = TokenRegistrationDialog(root)
        root.wait_window(dialog)
        if dialog.activated:
            # tokens are now in os.environ and .env
    """

    def __init__(
        self,
        master,
        env_path: Optional[str] = None,
        prefill_exg: str = "",
        prefill_hol: str = "",
        **kwargs,
    ):
        super().__init__(master, **kwargs)

        self.activated = False
        self._env_path = env_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ".env",
        )

        self.title("BOT Exchange Rate — API Registration")
        self.geometry("520x520")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._prefill_exg = prefill_exg
        self._prefill_hol = prefill_hol
        self._show_keys = False

        self._build_ui()
        self._center()
        self.grab_set()

    # ── Layout ───────────────────────────────────────────────────────────

    def _center(self):
        self.update_idletasks()
        w, h = 520, 520
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

    def _build_ui(self):
        # Header
        ctk.CTkLabel(
            self, text="API Registration",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLOR_TEXT,
        ).pack(pady=(28, 4))

        ctk.CTkLabel(
            self, text="Enter your Bank of Thailand API keys to activate",
            font=ctk.CTkFont(size=13),
            text_color=COLOR_MUTED,
        ).pack(pady=(0, 20))

        # Card frame
        card = ctk.CTkFrame(self, fg_color=COLOR_CARD, corner_radius=12)
        card.pack(padx=30, fill="x")

        # ── Exchange Rate Key ────────────────────────────────────────
        ctk.CTkLabel(
            card, text="EXCHANGE RATE API KEY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_MUTED,
        ).pack(anchor="w", padx=20, pady=(18, 4))

        self._entry_exg = ctk.CTkEntry(
            card, height=40, corner_radius=8,
            fg_color=COLOR_ENTRY_BG, border_color=COLOR_ACCENT,
            text_color=COLOR_TEXT, font=ctk.CTkFont(size=13, family="Courier"),
            placeholder_text="Paste your exchange rate API key here",
            show="•",
        )
        self._entry_exg.pack(padx=20, fill="x")
        if self._prefill_exg:
            self._entry_exg.insert(0, self._prefill_exg)

        # ── Holiday Key ──────────────────────────────────────────────
        ctk.CTkLabel(
            card, text="HOLIDAY API KEY",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_MUTED,
        ).pack(anchor="w", padx=20, pady=(14, 4))

        self._entry_hol = ctk.CTkEntry(
            card, height=40, corner_radius=8,
            fg_color=COLOR_ENTRY_BG, border_color=COLOR_ACCENT,
            text_color=COLOR_TEXT, font=ctk.CTkFont(size=13, family="Courier"),
            placeholder_text="Paste your holiday API key here",
            show="•",
        )
        self._entry_hol.pack(padx=20, fill="x")
        if self._prefill_hol:
            self._entry_hol.insert(0, self._prefill_hol)

        # ── Show Keys toggle ─────────────────────────────────────────
        self._chk_show = ctk.CTkCheckBox(
            card, text="Show keys",
            font=ctk.CTkFont(size=12), text_color=COLOR_MUTED,
            command=self._toggle_visibility,
            checkbox_height=18, checkbox_width=18,
        )
        self._chk_show.pack(anchor="w", padx=20, pady=(10, 18))

        # ── Status Label ─────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            text_color=COLOR_ERROR,
        )
        self._lbl_status.pack(pady=(12, 4))

        # ── Activate Button ──────────────────────────────────────────
        ctk.CTkButton(
            self, text="Activate",
            fg_color=COLOR_SUCCESS, hover_color="#16A34A",
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10, height=44,
            command=self._on_activate,
        ).pack(padx=30, fill="x", pady=(0, 8))

        # ── Portal Link ──────────────────────────────────────────────
        link = ctk.CTkLabel(
            self,
            text="Don't have keys? Register at apiportal.bot.or.th",
            font=ctk.CTkFont(size=12, underline=True),
            text_color=COLOR_ACCENT, cursor="hand2",
        )
        link.pack(pady=(0, 20))
        link.bind("<Button-1>", lambda _: webbrowser.open(BOT_PORTAL_URL))

    # ── Actions ──────────────────────────────────────────────────────────

    def _toggle_visibility(self):
        self._show_keys = not self._show_keys
        char = "" if self._show_keys else "•"
        self._entry_exg.configure(show=char)
        self._entry_hol.configure(show=char)

    def _on_activate(self):
        exg = self._entry_exg.get().strip()
        hol = self._entry_hol.get().strip()

        # Validate
        if not exg or not hol:
            self._lbl_status.configure(
                text="Both API keys are required.", text_color=COLOR_ERROR,
            )
            return
        if len(exg) < MIN_KEY_LENGTH or len(hol) < MIN_KEY_LENGTH:
            self._lbl_status.configure(
                text="API keys appear too short. Please check and try again.",
                text_color=COLOR_ERROR,
            )
            return

        # Write to .env
        try:
            self._write_env(exg, hol)
        except OSError as e:
            self._lbl_status.configure(
                text="Failed to save .env: %s" % e, text_color=COLOR_ERROR,
            )
            logger.error("Failed to write .env: %s", e)
            return

        # Inject into current process
        os.environ["BOT_TOKEN_EXG"] = exg
        os.environ["BOT_TOKEN_HOL"] = hol

        self.activated = True
        logger.info("API tokens activated and saved to .env")
        self.grab_release()
        self.destroy()

    def _write_env(self, exg: str, hol: str):
        """Write or update the .env file with the provided tokens."""
        lines = []
        if os.path.exists(self._env_path):
            with open(self._env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        # Update existing keys or prepare to append
        keys_written = {"BOT_TOKEN_EXG": False, "BOT_TOKEN_HOL": False}
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("BOT_TOKEN_EXG="):
                new_lines.append("BOT_TOKEN_EXG=%s\n" % exg)
                keys_written["BOT_TOKEN_EXG"] = True
            elif stripped.startswith("BOT_TOKEN_HOL="):
                new_lines.append("BOT_TOKEN_HOL=%s\n" % hol)
                keys_written["BOT_TOKEN_HOL"] = True
            else:
                new_lines.append(line)

        if not keys_written["BOT_TOKEN_EXG"]:
            new_lines.append("BOT_TOKEN_EXG=%s\n" % exg)
        if not keys_written["BOT_TOKEN_HOL"]:
            new_lines.append("BOT_TOKEN_HOL=%s\n" % hol)

        with open(self._env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)

    def _on_close(self):
        self.activated = False
        self.grab_release()
        self.destroy()
