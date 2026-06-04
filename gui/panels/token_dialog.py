#!/usr/bin/env python3
"""
gui/panels/token_dialog.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — API Token Registration Dialog
---------------------------------------------------------------------------
License-key-style popup that collects BOT API tokens on first use.
Writes validated tokens to .env (legacy), OS keychain, and os.environ.

SFFB: Strict < 200 lines.
"""

import contextlib
import logging
import os
import threading
import webbrowser
from pathlib import Path

import customtkinter as ctk

from core.api_client import ping_token
from core.i18n import tr
from core.paths import get_project_root
from core.secure_tokens import _keyring_available, set_token
from gui.theme import MONO_FONT, get_theme

logger = logging.getLogger(__name__)

BOT_PORTAL_URL = "https://apiportal.bot.or.th/"
MIN_KEY_LENGTH = 8


def _sanitize_key(raw: str) -> tuple[str, str | None]:
    """Normalise a pasted API key and flag corruption.

    Returns ``(cleaned, error)``. ``cleaned`` has surrounding whitespace and a
    stray ``Bearer `` prefix removed so what gets stored matches what gets
    tested. ``error`` is a human message when the key contains internal
    whitespace/newlines (a wrapped paste), else ``None``. Keys pasted from an
    email or PDF often wrap mid-string; those slip past a plain ``.strip()``
    and only surface later as cryptic 401s.
    """
    cleaned = raw.strip()
    # Drop a stray "Bearer " prefix some users paste from auth headers.
    if cleaned[:7].lower() == "bearer ":
        cleaned = cleaned[7:].strip()
    if any(c.isspace() for c in cleaned):
        return cleaned, "Key contains spaces or line breaks — check your paste."
    return cleaned, None


class TokenRegistrationDialog(ctk.CTkToplevel):
    """
    A registration-key-style modal for collecting BOT API tokens.

    Usage:
        dialog = TokenRegistrationDialog(root)
        root.wait_window(dialog)
        if dialog.activated:
            # tokens are now in keychain, os.environ, and .env
    """

    def __init__(
        self,
        master,
        env_path: str | None = None,
        prefill_exg: str = "",
        prefill_hol: str = "",
        **kwargs,
    ):
        super().__init__(master, **kwargs)

        self.activated = False
        self._env_path = env_path or str(Path(get_project_root()) / ".env")
        self._busy_test = False
        self._destroyed = False
        # Set once a keychain write has failed and we fell back to plaintext
        # .env; gates a one-time visible warning before the dialog dismisses.
        self._keychain_warned = False

        t = get_theme()
        self.title(tr("token.window_title"))
        self.geometry("520x560")
        self.resizable(False, False)
        self.configure(fg_color=t["modal_bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._prefill_exg = prefill_exg
        self._prefill_hol = prefill_hol
        self._show_keys = False

        self._build_ui()
        self._center()
        self.grab_set()

        # ── Keyboard accessibility ─────────────────────────────────
        # Enter submits (Activate), Escape cancels — first-run cancel handling
        # lives in main.py; here we only distinguish submit from cancel.
        self.bind("<Return>", lambda e: self._on_activate())
        self.bind("<Escape>", lambda e: self._on_close())
        # Focus the first entry so a keyboard user can type immediately.
        self._entry_exg.focus_set()
        self.focus_set()

    # ── Layout ───────────────────────────────────────────────────────────

    def _center(self):
        self.update_idletasks()
        w, h = 520, 560
        sx = (self.winfo_screenwidth() - w) // 2
        sy = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{sx}+{sy}")

    def _build_ui(self):
        t = get_theme()

        # Header
        ctk.CTkLabel(
            self, text=tr("token.heading"),
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=t["modal_text"],
        ).pack(pady=(28, 4))

        ctk.CTkLabel(
            self, text=tr("token.subheading"),
            font=ctk.CTkFont(size=13),
            text_color=t["modal_muted"],
        ).pack(pady=(0, 20))

        # Card frame
        card = ctk.CTkFrame(self, fg_color=t["card_bg"], corner_radius=12)
        card.pack(padx=30, fill="x")

        # ── Exchange Rate Key ────────────────────────────────────────
        ctk.CTkLabel(
            card, text=tr("token.label_exg"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=20, pady=(18, 4))

        self._entry_exg = ctk.CTkEntry(
            card, height=40, corner_radius=8,
            fg_color=t["modal_entry_bg"], border_color=t["modal_accent"],
            text_color=t["modal_text"], font=ctk.CTkFont(size=13, family=MONO_FONT),
            placeholder_text=tr("token.placeholder_exg"),
            show="•",
        )
        self._entry_exg.pack(padx=20, fill="x")
        if self._prefill_exg:
            self._entry_exg.insert(0, self._prefill_exg)

        # ── Holiday Key ──────────────────────────────────────────────
        ctk.CTkLabel(
            card, text=tr("token.label_hol"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=t["modal_muted"],
        ).pack(anchor="w", padx=20, pady=(14, 4))

        self._entry_hol = ctk.CTkEntry(
            card, height=40, corner_radius=8,
            fg_color=t["modal_entry_bg"], border_color=t["modal_accent"],
            text_color=t["modal_text"], font=ctk.CTkFont(size=13, family=MONO_FONT),
            placeholder_text=tr("token.placeholder_hol"),
            show="•",
        )
        self._entry_hol.pack(padx=20, fill="x")
        if self._prefill_hol:
            self._entry_hol.insert(0, self._prefill_hol)

        # ── Show Keys toggle ─────────────────────────────────────────
        self._chk_show = ctk.CTkCheckBox(
            card, text=tr("token.show_keys"),
            font=ctk.CTkFont(size=12), text_color=t["modal_muted"],
            command=self._toggle_visibility,
            checkbox_height=18, checkbox_width=18,
        )
        self._chk_show.pack(anchor="w", padx=20, pady=(10, 18))

        # ── Status Label ─────────────────────────────────────────────
        self._lbl_status = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12),
            text_color=t["error_text"],
        )
        self._lbl_status.pack(pady=(12, 4))

        # ── Test Keys Button ─────────────────────────────────────────
        # Lets a first-run user verify the entered keys reach + authenticate
        # against the BOT API before committing them via Activate.
        self._btn_test = ctk.CTkButton(
            self, text=tr("token.btn_test"),
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=10, height=38,
            command=self._on_test_keys,
        )
        self._btn_test.pack(padx=30, fill="x", pady=(0, 8))

        # ── Activate Button ──────────────────────────────────────────
        self._btn_activate = ctk.CTkButton(
            self, text=tr("token.btn_activate"),
            fg_color=t["modal_success"], hover_color=t["success_hover"],
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=10, height=44,
            command=self._on_activate,
        )
        self._btn_activate.pack(padx=30, fill="x", pady=(0, 8))

        # ── Portal Link ──────────────────────────────────────────────
        link = ctk.CTkLabel(
            self,
            text=tr("token.portal_link"),
            font=ctk.CTkFont(size=12, underline=True),
            text_color=t["modal_accent"], cursor="hand2",
        )
        link.pack(pady=(0, 20))
        link.bind("<Button-1>", lambda _: webbrowser.open(BOT_PORTAL_URL))

    # ── Actions ──────────────────────────────────────────────────────────

    def _toggle_visibility(self):
        self._show_keys = not self._show_keys
        char = "" if self._show_keys else "•"
        self._entry_exg.configure(show=char)
        self._entry_hol.configure(show=char)

    def _safe_after(self, delay_ms, callback, *args):
        """Schedule ``callback`` on the Tk thread, skipping if destroyed.

        The Test Keys worker runs on a background thread; by the time it
        finishes the user may have closed the dialog. Guard the after() call
        so a late result never touches a torn-down widget.
        """
        if self._destroyed:
            return
        with contextlib.suppress(Exception):
            self.after(delay_ms, lambda: callback(*args))

    def _on_test_keys(self):
        """Verify the entered keys against the BOT API off the Tk thread."""
        t = get_theme()
        if self._busy_test:
            return

        exg, exg_err = _sanitize_key(self._entry_exg.get())
        hol, hol_err = _sanitize_key(self._entry_hol.get())
        if not exg or not hol:
            self._lbl_status.configure(
                text=tr("token.err_enter_before_test"),
                text_color=t["error_text"],
            )
            return
        if exg_err or hol_err:
            self._lbl_status.configure(
                text=exg_err or hol_err, text_color=t["error_text"],
            )
            return

        self._busy_test = True
        self._btn_test.configure(state="disabled")
        self._lbl_status.configure(
            text=tr("token.testing"), text_color=t["modal_muted"],
        )
        self.update_idletasks()

        def _worker():
            # The exchange key is the one every batch run depends on; verify it
            # first so a bad EXG key is reported even if HOL happens to pass.
            exg_ok, exg_msg = ping_token(exg)
            if not exg_ok:
                self._safe_after(0, self._test_done, False, exg_msg)
                return
            hol_ok, hol_msg = ping_token(hol)
            if not hol_ok:
                # Distinguish which key failed for an actionable message.
                self._safe_after(
                    0, self._test_done, False,
                    f"Holiday key: {hol_msg}",
                )
                return
            self._safe_after(
                0, self._test_done, True,
                tr("token.test_ok"),
            )

        threading.Thread(target=_worker, daemon=True, name="TokenTest").start()

    def _test_done(self, ok: bool, message: str):
        t = get_theme()
        self._busy_test = False
        with contextlib.suppress(Exception):
            self._btn_test.configure(state="normal")
        self._lbl_status.configure(
            text=message,
            text_color=t["modal_success"] if ok else t["error_text"],
        )

    def _on_activate(self):
        t = get_theme()
        exg, exg_err = _sanitize_key(self._entry_exg.get())
        hol, hol_err = _sanitize_key(self._entry_hol.get())

        # Validate
        if not exg or not hol:
            self._lbl_status.configure(
                text=tr("token.err_both_required"), text_color=t["error_text"],
            )
            return
        if exg_err or hol_err:
            # Internal whitespace/newlines corrupt the key and only surface
            # later as cryptic 401s — reject up front with actionable feedback.
            self._lbl_status.configure(
                text=exg_err or hol_err, text_color=t["error_text"],
            )
            return
        if len(exg) < MIN_KEY_LENGTH or len(hol) < MIN_KEY_LENGTH:
            self._lbl_status.configure(
                text=tr("token.err_too_short"),
                text_color=t["error_text"],
            )
            return

        # SECURITY: prefer the OS keychain. Only write plaintext to .env when
        # no secure keychain backend is available, and lock the file to 0o600.
        keyring_present = _keyring_available()
        stored_in_keychain = False
        if keyring_present:
            exg_ok = set_token("BOT_TOKEN_EXG", exg)
            hol_ok = set_token("BOT_TOKEN_HOL", hol)
            stored_in_keychain = exg_ok and hol_ok

        # A keychain backend existed but the write failed (e.g. macOS user
        # clicked 'Deny', or a locked Windows Credential Manager). We still
        # fall back to .env so activation succeeds, but the user MUST be told
        # their keys landed in plaintext rather than the secure store they
        # expected — silently degrading defeats the keychain feature.
        keychain_fell_back = keyring_present and not stored_in_keychain

        if not stored_in_keychain:
            try:
                self._write_env(exg, hol)
            except OSError as e:
                self._lbl_status.configure(
                    text=f"Failed to save .env: {e}", text_color=t["error_text"],
                )
                logger.error("Failed to write .env: %s", e)
                return

        # Also inject into current process for immediate availability
        os.environ["BOT_TOKEN_EXG"] = exg
        os.environ["BOT_TOKEN_HOL"] = hol

        self.activated = True
        if stored_in_keychain:
            logger.info("API tokens activated and stored in OS keychain.")
        elif keychain_fell_back and not self._keychain_warned:
            # Surface the degraded-storage warning and keep the dialog open so
            # the user actually sees it. Tokens are already saved (.env +
            # os.environ) so activation has succeeded; a second press of the
            # relabelled button dismisses without re-storing.
            self._keychain_warned = True
            logger.warning(
                "Keychain write failed; tokens saved to plaintext .env instead."
            )
            self._lbl_status.configure(
                text=tr("token.keychain_fallback"),
                text_color=t["warning"],
            )
            with contextlib.suppress(Exception):
                self._btn_activate.configure(text=tr("token.btn_continue"))
            return
        elif not keychain_fell_back:
            logger.info("API tokens activated and saved to .env (no keychain available).")
        self._destroyed = True
        self.grab_release()
        self.destroy()

    def _write_env(self, exg: str, hol: str):
        """Write or update the .env file with the provided tokens."""
        lines = []
        env_file = Path(self._env_path)
        if env_file.exists():
            with env_file.open(encoding="utf-8") as f:
                lines = f.readlines()

        # Update existing keys or prepare to append
        keys_written = {"BOT_TOKEN_EXG": False, "BOT_TOKEN_HOL": False}
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("BOT_TOKEN_EXG="):
                new_lines.append(f"BOT_TOKEN_EXG={exg}\n")
                keys_written["BOT_TOKEN_EXG"] = True
            elif stripped.startswith("BOT_TOKEN_HOL="):
                new_lines.append(f"BOT_TOKEN_HOL={hol}\n")
                keys_written["BOT_TOKEN_HOL"] = True
            else:
                new_lines.append(line)

        if not keys_written["BOT_TOKEN_EXG"]:
            new_lines.append(f"BOT_TOKEN_EXG={exg}\n")
        if not keys_written["BOT_TOKEN_HOL"]:
            new_lines.append(f"BOT_TOKEN_HOL={hol}\n")

        with env_file.open("w", encoding="utf-8") as f:
            f.writelines(new_lines)

        # SECURITY: restrict the plaintext .env to the owner only.
        with contextlib.suppress(OSError):
            env_file.chmod(0o600)

    def _on_close(self):
        # Leave self.activated untouched: it is False for a genuine cancel and
        # only True once tokens were already stored (the keychain-fallback
        # warning path keeps the dialog open with activated=True). Closing the
        # window in that state must NOT discard the successful activation.
        self._destroyed = True
        self.grab_release()
        self.destroy()
