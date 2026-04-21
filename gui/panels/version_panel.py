#!/usr/bin/env python3
"""
gui/panels/version_panel.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Version Management Panel
---------------------------------------------------------------------------
Extracted from settings_modal.py. Handles:
  - Check for stable updates
  - Browse all versions (stable + beta)
  - Download + apply updates
  - Restart dialog

SFFB: Strict < 200 lines.
"""

import logging
import os
import threading

import customtkinter as ctk
import httpx

from core.secure_tokens import get_token
from gui.theme import get_theme

logger = logging.getLogger(__name__)

# GitHub API endpoints
_RELEASES_URL = (
    "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases"
)
_BOT_API_PING = (
    "https://gateway.api.bot.or.th"
    "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
    "?start_period=2025-01-01&end_period=2025-01-02&currency=USD"
)


class VersionPanel(ctk.CTkFrame):
    """Embeddable version/update panel for the settings modal."""

    def __init__(self, master, on_restart=None, on_error=None, **kwargs):
        t = get_theme()
        super().__init__(master, fg_color="transparent", **kwargs)
        self._t = t
        self._pending_installer = None
        self._destroyed = False
        self._busy_ping = False
        self._busy_update = False
        self._busy_browse = False
        self._on_restart = on_restart
        self._on_error = on_error
        self._busy_download = False
        self._build_ui()

    def destroy(self):
        """Mark as destroyed before actual teardown."""
        self._destroyed = True
        super().destroy()

    def _safe_after(self, ms, func, *args):
        """Thread-safe self.after() that silently ignores RuntimeError
        from callbacks arriving after the widget has been destroyed."""
        if self._destroyed:
            return
        try:
            self.after(ms, func, *args)
        except RuntimeError:
            logger.debug("Ignoring post-destroy callback: %s", func.__name__)

    def _build_ui(self):
        t = self._t

        # ── API Connectivity Test ────────────────────────────────────
        self._btn_ping = ctk.CTkButton(
            self, text="Test API Connection",
            fg_color=t["modal_accent"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_ping_api,
        )
        self._btn_ping.pack(fill="x", pady=(0, 4))

        self._lbl_ping = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_success"],
        )
        self._lbl_ping.pack(pady=(0, 8))

        # ── Check for Stable Updates ─────────────────────────────────
        self._btn_update = ctk.CTkButton(
            self, text="Check for Updates",
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_check_update,
        )
        self._btn_update.pack(fill="x", pady=(0, 4))

        self._lbl_update = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_success"],
        )
        self._lbl_update.pack(pady=(0, 8))

        # ── Version Browser ──────────────────────────────────────────
        self._btn_versions = ctk.CTkButton(
            self, text="Versions",
            fg_color=t["card_bg"], hover_color=t["btn_secondary"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_browse_versions,
        )
        self._btn_versions.pack(fill="x", pady=(0, 4))

        self._lbl_versions = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=t["modal_muted"],
        )
        self._lbl_versions.pack(pady=(0, 4))

        # Version option menu (hidden until versions loaded)
        self._version_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._selected_version = ctk.StringVar(value="")
        self._version_menu = ctk.CTkOptionMenu(
            self._version_frame,
            variable=self._selected_version,
            values=["Loading..."],
            font=ctk.CTkFont(size=12),
            fg_color=(t["divider"], t["card_bg"]),
            button_color=(t["text_muted"], t["btn_secondary"]),
            button_hover_color=(t["text_muted"], t["btn_secondary_hover"]),
            text_color=(t["text_primary"], t["modal_text"]),
            dropdown_fg_color=(t["section_bg"], t["modal_bg"]),
            dropdown_hover_color=(t["divider"], t["card_bg"]),
            dropdown_text_color=(t["text_primary"], t["modal_text"]),
            corner_radius=6, height=32,
            command=self._on_version_selected,
        )
        self._version_menu.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self._btn_dl_version = ctk.CTkButton(
            self._version_frame, text="Download",
            fg_color=t["warning"], hover_color=t["warning_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6, height=32, width=100,
            state="disabled",
            command=self._on_download_selected_version,
        )
        self._btn_dl_version.pack(side="right")

    # ── API Ping ─────────────────────────────────────────────────────
    def _on_ping_api(self):
        if self._busy_ping:
            return
        self._busy_ping = True
        t = self._t
        self._lbl_ping.configure(text="Testing...", text_color=t["modal_muted"])
        self._btn_ping.configure(state="disabled")
        self.update_idletasks()

        def _worker():
            try:
                token = get_token("BOT_TOKEN_EXG") or ""
                headers = {"accept": "application/json"}
                if token:
                    clean_token = token.removeprefix("Bearer ").strip()
                    headers["X-IBM-Client-Id"] = clean_token
                    headers["Authorization"] = f"Bearer {clean_token}"
                resp = httpx.get(_BOT_API_PING, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    self._safe_after(0, self._ping_done,
                               "✓ API connected & authenticated",
                               t["modal_success"])
                elif resp.status_code == 401:
                    msg = ("⚠ API reachable but token is invalid" if token
                           else "⚠ API reachable — no token configured")
                    self._safe_after(0, self._ping_done, msg, t["warning"])
                else:
                    self._safe_after(0, self._ping_done,
                               f"API returned HTTP {resp.status_code}",
                               t["error_text"])
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self._safe_after(0, self._ping_done, f"✗ {e}", t["error_text"])

        threading.Thread(target=_worker, daemon=True, name="APIPing").start()

    def _ping_done(self, text: str, color: str):
        self._busy_ping = False
        self._lbl_ping.configure(text=text, text_color=color)
        self._btn_ping.configure(state="normal")

    # ── Check for Updates ────────────────────────────────────────────
    def _on_check_update(self):
        if self._busy_update:
            return
        self._busy_update = True

        from core.auto_updater import check_for_update
        from core.version import __version__

        t = self._t
        self._lbl_update.configure(text="Checking...", text_color=t["modal_muted"])
        self._btn_update.configure(state="disabled")
        self.update_idletasks()

        def _worker():
            try:
                result = check_for_update(current_version=__version__)
                if result.get("update_available"):
                    ver = result.get("latest_version", "?")
                    self._safe_after(0, self._update_done,
                               f"Update available: V{ver}", t["warning"], ver)
                elif result.get("error"):
                    self._safe_after(0, self._update_done,
                               f"Check failed: {result['error']}",
                               t["error_text"], None)
                else:
                    self._safe_after(0, self._update_done,
                               f"✓ Up to date (V{__version__})",
                               t["modal_success"], None)
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self._safe_after(0, self._update_done,
                           f"Error: {e}", t["error_text"], None)

        threading.Thread(target=_worker, daemon=True, name="UpdateCheck").start()

    def _update_done(self, text: str, color: str, version):
        self._busy_update = False
        t = self._t
        self._lbl_update.configure(text=text, text_color=color)
        if version:
            self._btn_update.configure(
                text=f"Download V{version}",
                fg_color=t["warning"], hover_color=t["warning_hover"],
                state="normal",
                command=lambda: self._download_in_app(version),
            )
        else:
            self._btn_update.configure(
                text="Check for Updates",
                fg_color=t["btn_secondary"],
                hover_color=t["btn_secondary_hover"],
                state="normal",
                command=self._on_check_update,
            )

    # ── Version Browser ──────────────────────────────────────────────
    def _on_browse_versions(self):
        if self._busy_browse:
            return
        self._busy_browse = True
        t = self._t
        self._lbl_versions.configure(
            text="Fetching versions...", text_color=t["modal_muted"],
        )
        self._btn_versions.configure(state="disabled")
        self.update_idletasks()

        def _worker():
            try:
                resp = httpx.get(
                    _RELEASES_URL,
                    headers={"Accept": "application/vnd.github+json"},
                    timeout=10.0,
                    params={"per_page": 20},
                )
                resp.raise_for_status()
                releases = resp.json()

                versions = []
                for rel in releases:
                    tag = rel.get("tag_name", "").lstrip("vV")
                    is_pre = rel.get("prerelease", False)
                    label = f"v{tag} [BETA]" if is_pre else f"v{tag}"
                    versions.append((tag, label, is_pre))

                self._safe_after(0, self._show_versions, versions)
            except (httpx.RequestError, httpx.HTTPStatusError,
                    OSError, ValueError) as e:
                self._safe_after(0, self._versions_error, str(e))

        threading.Thread(target=_worker, daemon=True, name="VersionBrowse").start()

    def _show_versions(self, versions):
        self._busy_browse = False
        t = self._t
        self._btn_versions.configure(state="normal")
        if not versions:
            self._lbl_versions.configure(
                text="No releases found", text_color=t["error_text"],
            )
            return

        self._version_list = versions
        labels = [v[1] for v in versions]
        self._lbl_versions.configure(
            text=f"{len(versions)} versions available — select to download:",
            text_color=t["modal_muted"],
        )
        self._selected_version.set(labels[0])
        self._version_menu.configure(values=labels)
        self._version_frame.pack(fill="x", pady=(0, 8))
        self._btn_dl_version.configure(state="normal")

    def _versions_error(self, msg: str):
        self._busy_browse = False
        t = self._t
        self._btn_versions.configure(state="normal")
        self._lbl_versions.configure(
            text=f"Error: {msg}", text_color=t["error_text"],
        )

    def _on_version_selected(self, label: str):
        self._btn_dl_version.configure(state="normal")

    def _on_download_selected_version(self):
        label = self._selected_version.get()
        version = None
        for tag, lbl, _ in getattr(self, "_version_list", []):
            if lbl == label:
                version = tag
                break
        if version:
            self._download_in_app(version)

    # ── Download + Apply ─────────────────────────────────────────────
    def _download_in_app(self, version: str):
        """Download the update installer (does NOT run it yet)."""
        if self._busy_download:
            return
        self._busy_download = True

        from core.auto_updater import download_update, get_installer_asset_url

        t = self._t
        self._btn_update.configure(state="disabled")
        self._btn_dl_version.configure(state="disabled")
        self._lbl_update.configure(
            text=f"Downloading V{version}...", text_color=t["modal_muted"],
        )
        self.update_idletasks()

        def _worker():
            try:
                from core.auto_updater import _fetch_expected_checksum

                asset = get_installer_asset_url(version)
                if asset.get("error") or not asset.get("url"):
                    self._safe_after(0, self._dl_done,
                               f"Error: {asset.get('error', 'No installer')}",
                               t["error_text"], False)
                    return

                expected_sha256 = None
                if asset.get("sha256_url"):
                    expected_sha256 = _fetch_expected_checksum(
                        asset["sha256_url"]
                    )

                def _progress(downloaded, total):
                    pct = int(downloaded / total * 100)
                    self._safe_after(0, self._lbl_update.configure,
                               {"text": f"Downloading V{version}... {pct}%",
                                "text_color": t["modal_muted"]})

                result = download_update(
                    url=asset["url"],
                    filename=asset.get("filename"),
                    progress_cb=_progress,
                    expected_sha256=expected_sha256,
                )
                if result.get("error"):
                    self._safe_after(0, self._dl_done,
                               f"Download failed: {result['error']}",
                               t["error_text"], False)
                    return

                installer_path = result.get("path", "")
                self._safe_after(0, self._dl_done,
                           "✅ Downloaded — restart to install",
                           t["modal_success"], True, installer_path)
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self._safe_after(0, self._dl_done,
                           f"Error: {e}", t["error_text"], False)

        threading.Thread(target=_worker, daemon=True, name="UpdateDL").start()

    def _dl_done(self, text: str, color: str, success: bool,
                 installer_path: str = None):
        self._busy_download = False
        self._lbl_update.configure(text=text, text_color=color)
        if success:
            self._btn_update.configure(state="disabled", text="Updated ✓")
            self._btn_dl_version.configure(state="disabled")
            self._pending_installer = installer_path
            self._show_restart_dialog()
        else:
            self._btn_update.configure(state="normal")
            self._btn_dl_version.configure(state="normal")

    # ── Restart Dialog ───────────────────────────────────────────────
    def _show_restart_dialog(self):
        """Show a restart confirmation popup after successful update."""
        t = self._t
        dialog = ctk.CTkToplevel(self)
        dialog.title("Update Installed")
        dialog.geometry("340x180")
        dialog.resizable(False, False)
        dialog.configure(fg_color=t["modal_bg"])
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        dialog.update_idletasks()
        sx = (dialog.winfo_screenwidth() - 340) // 2
        sy = (dialog.winfo_screenheight() - 180) // 2
        dialog.geometry(f"340x180+{sx}+{sy}")

        ctk.CTkLabel(
            dialog, text="✅ Update Installed",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=t["modal_text"],
        ).pack(pady=(24, 8))

        ctk.CTkLabel(
            dialog,
            text="Restart the application to\napply the update.",
            font=ctk.CTkFont(size=13),
            text_color=t["modal_muted"],
        ).pack(pady=(0, 16))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24)

        ctk.CTkButton(
            btn_frame, text="Restart Later",
            fg_color=t["btn_secondary"], hover_color=t["btn_secondary_hover"],
            font=ctk.CTkFont(size=13), corner_radius=8,
            height=36, width=130,
            command=dialog.destroy,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="Restart Now",
            fg_color=t["modal_success"], hover_color=t["success"],
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=36, width=130,
            command=self._do_restart,
        ).pack(side="right")

    def _do_restart(self):
        """Apply pending installer (if any) then restart via callback or fallback."""
        import sys

        from core.auto_updater import apply_update

        installer = self._pending_installer

        # Close parent settings modal
        settings_modal = self.winfo_toplevel()
        try:
            settings_modal.grab_release()
        except RuntimeError:
            pass
        settings_modal.destroy()

        # Run the installer silently if we have a downloaded file
        if installer and os.path.isfile(installer):
            result = apply_update(installer)
            if not result.get("success"):
                logger.error("Installer failed: %s", result.get("error"))
                if self._on_error:
                    self._on_error(f"Install failed: {result.get('error')}")
                return
            # Installer succeeded — hard exit
            sys.exit(0)

        # Normal restart (no installer)
        if self._on_restart:
            self._on_restart()
        else:
            from core.auto_updater import restart_app
            restart_app()
