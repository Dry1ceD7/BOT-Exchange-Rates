#!/usr/bin/env python3
"""
gui/panels/settings_modal.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Settings Modal Panel
---------------------------------------------------------------------------
Popup window for user preferences backed by core/config_manager.py.
Controls: Appearance, Auto-Update, API keys, API ping, Version browser.
"""

import logging
import os
import threading
from typing import Optional

import httpx

import customtkinter as ctk

from core.config_manager import SettingsManager

logger = logging.getLogger(__name__)

COLOR_MODAL_BG = ("#F5F7FA", "#1E293B")      # (light, dark)
COLOR_MODAL_TEXT = ("#1A202C", "#F1F5F9")
COLOR_MODAL_ACCENT = "#3B82F6"
COLOR_MODAL_SUCCESS = "#22C55E"
COLOR_MODAL_MUTED = ("#4A5568", "#94A3B8")

# GitHub API endpoints
_RELEASES_URL = (
    "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases"
)
_BOT_API_PING = (
    "https://gateway.api.bot.or.th"
    "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
    "?start_period=2025-01-01&end_period=2025-01-02&currency=USD"
)


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
        self.geometry("420x720")
        self.resizable(False, False)
        self.configure(fg_color=COLOR_MODAL_BG)

        self._mgr = SettingsManager(config_dir=config_dir)
        self._settings = self._mgr.load()

        self._build_ui()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = 420, 720
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
            text_color=COLOR_MODAL_MUTED,
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

        # ── Rate Type ─────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="RATE TYPE",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLOR_MODAL_MUTED,
        ).pack(anchor="w", padx=30)

        # Map display labels to API field names
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
        rate_menu = ctk.CTkSegmentedButton(
            self,
            values=["Buying TT", "Selling", "Buying Sight", "Mid Rate"],
            variable=self._rate_type_var,
            font=ctk.CTkFont(size=12),
        )
        rate_menu.pack(padx=30, pady=(4, 16), fill="x")

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
        self._btn_ping.pack(padx=30, fill="x", pady=(0, 4))

        self._lbl_ping = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=COLOR_MODAL_SUCCESS,
        )
        self._lbl_ping.pack(pady=(0, 8))

        # ── Import Offline Rates (CSV) ────────────────────────────────
        ctk.CTkButton(
            self, text="Import Offline Rates (CSV)",
            fg_color="#0F766E", hover_color="#115E59",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_import_csv,
        ).pack(padx=30, fill="x", pady=(0, 4))

        self._lbl_csv = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=COLOR_MODAL_MUTED,
        )
        self._lbl_csv.pack(pady=(0, 4))

        # ── Export Cached Rates (CSV) ─────────────────────────────────
        ctk.CTkButton(
            self, text="Export Cached Rates (CSV)",
            fg_color="#1D4ED8", hover_color="#1E40AF",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_export_csv,
        ).pack(padx=30, fill="x", pady=(0, 4))

        self._lbl_csv_export = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=COLOR_MODAL_MUTED,
        )
        self._lbl_csv_export.pack(pady=(0, 12))

        # ── Check for Stable Updates ─────────────────────────────────
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
        self._lbl_update.pack(pady=(0, 8))

        # ── Version Browser ──────────────────────────────────────────
        self._btn_versions = ctk.CTkButton(
            self, text="Versions",
            fg_color="#334155", hover_color="#475569",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=38,
            command=self._on_browse_versions,
        )
        self._btn_versions.pack(padx=30, fill="x", pady=(0, 4))

        self._lbl_versions = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=11),
            text_color=COLOR_MODAL_MUTED,
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
            fg_color=("#E2E8F0", "#334155"),
            button_color=("#CBD5E1", "#475569"),
            button_hover_color=("#94A3B8", "#64748B"),
            text_color=("#1A202C", "#F1F5F9"),
            dropdown_fg_color=("#FFFFFF", "#1E293B"),
            dropdown_hover_color=("#E2E8F0", "#334155"),
            dropdown_text_color=("#1A202C", "#F1F5F9"),
            corner_radius=6, height=32,
            command=self._on_version_selected,
        )
        self._version_menu.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self._btn_dl_version = ctk.CTkButton(
            self._version_frame, text="Download",
            fg_color="#F59E0B", hover_color="#D97706",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6, height=32, width=100,
            state="disabled",
            command=self._on_download_selected_version,
        )
        self._btn_dl_version.pack(side="right")

        # ── Save & Close ─────────────────────────────────────────────
        # Packed LAST so it stays at the very bottom of the modal.
        # The version_frame is shown/hidden dynamically above this.
        self._btn_save_close = ctk.CTkButton(
            self, text="Save and Close",
            fg_color=COLOR_MODAL_SUCCESS,
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=8, height=42,
            command=self._save_and_close,
        )
        self._btn_save_close.pack(padx=30, fill="x", pady=(12, 20), side="bottom")

    def _on_appearance_change(self, value: str):
        ctk.set_appearance_mode(value)
        # Delay: CTk batches mode changes — _apply_theme must read
        # the NEW mode, so wait for CTk's event loop to process it.
        parent = self.master
        if hasattr(parent, '_apply_theme'):
            self.after(150, parent._apply_theme)

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
    #  API CONNECTIVITY CHECK — simple HTTP ping (no BOTClient needed)
    # ================================================================== #

    def _on_ping_api(self):
        self._lbl_ping.configure(text="Testing...", text_color=COLOR_MODAL_MUTED)
        self._btn_ping.configure(state="disabled")
        self.update_idletasks()

        def _ping_worker():
            try:
                token = os.environ.get("BOT_TOKEN_EXG", "")
                headers = {"accept": "application/json"}
                if token:
                    # Match the real api_client.py header format:
                    # both X-IBM-Client-Id AND Authorization: Bearer
                    clean_token = token.removeprefix("Bearer ").strip()
                    headers["X-IBM-Client-Id"] = clean_token
                    headers["Authorization"] = f"Bearer {clean_token}"
                resp = httpx.get(_BOT_API_PING, headers=headers, timeout=8.0)
                if resp.status_code == 200:
                    self.after(0, self._ping_done,
                               "✓ API connected & authenticated",
                               COLOR_MODAL_SUCCESS)
                elif resp.status_code == 401:
                    if token:
                        self.after(0, self._ping_done,
                                   "⚠ API reachable but token is invalid",
                                   "#F59E0B")
                    else:
                        self.after(0, self._ping_done,
                                   "⚠ API reachable — no token configured",
                                   "#F59E0B")
                else:
                    self.after(0, self._ping_done,
                               f"API returned HTTP {resp.status_code}",
                               "#F87171")
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self.after(0, self._ping_done, f"✗ {e}", "#F87171")

        threading.Thread(target=_ping_worker, daemon=True).start()

    def _ping_done(self, text: str, color: str):
        self._lbl_ping.configure(text=text, text_color=color)
        self._btn_ping.configure(state="normal")

    # ================================================================== #
    #  CHECK FOR STABLE UPDATES
    # ================================================================== #

    def _on_check_update(self):
        from core.auto_updater import check_for_update
        from core.version import __version__

        self._lbl_update.configure(text="Checking...", text_color=COLOR_MODAL_MUTED)
        self._btn_update.configure(state="disabled")
        self.update_idletasks()

        def _worker():
            try:
                result = check_for_update(current_version=__version__)
                if result.get("update_available"):
                    ver = result.get("latest_version", "?")
                    self.after(0, self._update_done,
                               f"Update available: V{ver}", "#F59E0B",
                               ver)
                elif result.get("error"):
                    self.after(0, self._update_done,
                               f"Check failed: {result['error']}",
                               "#F87171", None)
                else:
                    self.after(0, self._update_done,
                               f"✓ Up to date (V{__version__})",
                               COLOR_MODAL_SUCCESS, None)
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self.after(0, self._update_done,
                           f"Error: {e}", "#F87171", None)

        threading.Thread(target=_worker, daemon=True).start()

    def _update_done(self, text: str, color: str, version):
        self._lbl_update.configure(text=text, text_color=color)
        if version:
            self._btn_update.configure(
                text=f"Download V{version}",
                fg_color="#F59E0B", hover_color="#D97706",
                state="normal",
                command=lambda: self._download_in_app(version),
            )
        else:
            self._btn_update.configure(
                text="Check for Updates",
                fg_color="#475569", hover_color="#64748B",
                state="normal",
                command=self._on_check_update,
            )

    # ================================================================== #
    #  VERSION BROWSER — lists all releases (stable + beta)
    # ================================================================== #

    def _on_browse_versions(self):
        self._lbl_versions.configure(
            text="Fetching versions...", text_color=COLOR_MODAL_MUTED,
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

                self.after(0, self._show_versions, versions)
            except (httpx.RequestError, httpx.HTTPStatusError, OSError, ValueError) as e:
                self.after(0, self._versions_error, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_versions(self, versions):
        self._btn_versions.configure(state="normal")
        if not versions:
            self._lbl_versions.configure(
                text="No releases found", text_color="#F87171",
            )
            return

        self._version_list = versions
        labels = [v[1] for v in versions]
        self._lbl_versions.configure(
            text=f"{len(versions)} versions available — select to download:",
            text_color=COLOR_MODAL_MUTED,
        )
        self._selected_version.set(labels[0])
        self._version_menu.configure(values=labels)
        self._version_frame.pack(padx=30, fill="x", pady=(0, 8))
        self._btn_dl_version.configure(state="normal")

    def _versions_error(self, msg: str):
        self._btn_versions.configure(state="normal")
        self._lbl_versions.configure(
            text=f"Error: {msg}", text_color="#F87171",
        )

    def _on_version_selected(self, label: str):
        self._btn_dl_version.configure(state="normal")

    def _on_download_selected_version(self):
        label = self._selected_version.get()
        # Find the actual version tag from label
        version = None
        for tag, lbl, _ in getattr(self, "_version_list", []):
            if lbl == label:
                version = tag
                break
        if version:
            self._download_in_app(version)

    # ================================================================== #
    #  DOWNLOAD + APPLY UPDATE (server-centric)
    # ================================================================== #

    def _download_in_app(self, version: str):
        """Download the update installer (does NOT run it yet)."""
        from core.auto_updater import (
            download_update,
            get_installer_asset_url,
        )

        self._btn_update.configure(state="disabled")
        self._btn_dl_version.configure(state="disabled")
        self._lbl_update.configure(
            text=f"Downloading V{version}...", text_color=COLOR_MODAL_MUTED,
        )
        self.update_idletasks()

        def _worker():
            try:
                asset = get_installer_asset_url(version)
                if asset.get("error") or not asset.get("url"):
                    self.after(0, self._dl_done,
                               f"Error: {asset.get('error', 'No installer')}",
                               "#F87171", False)
                    return

                def _progress(downloaded, total):
                    pct = int(downloaded / total * 100)
                    self.after(0, self._lbl_update.configure,
                               {"text": f"Downloading V{version}... {pct}%",
                                "text_color": COLOR_MODAL_MUTED})

                result = download_update(
                    url=asset["url"],
                    filename=asset.get("filename"),
                    progress_cb=_progress,
                )
                if result.get("error"):
                    self.after(0, self._dl_done,
                               f"Download failed: {result['error']}",
                               "#F87171", False)
                    return

                # Don't run the installer yet — just save the path.
                # The installer will run when user clicks "Restart Now".
                installer_path = result.get("path", "")
                self.after(0, self._dl_done,
                           "✅ Downloaded — restart to install",
                           COLOR_MODAL_SUCCESS, True, installer_path)
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self.after(0, self._dl_done, f"Error: {e}", "#F87171", False)

        threading.Thread(target=_worker, daemon=True).start()

    def _dl_done(self, text: str, color: str, success: bool,
                 installer_path: str = None):
        self._lbl_update.configure(text=text, text_color=color)
        if success:
            self._btn_update.configure(state="disabled", text="Updated ✓")
            self._btn_dl_version.configure(state="disabled")
            # Save installer path for deferred execution
            self._pending_installer = installer_path
            # Show restart confirmation dialog
            self._show_restart_dialog()
        else:
            self._btn_update.configure(state="normal")
            self._btn_dl_version.configure(state="normal")

    def _show_restart_dialog(self):
        """Show a restart confirmation popup after successful update."""
        dialog = ctk.CTkToplevel(self)
        dialog.title("Update Installed")
        dialog.geometry("340x180")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLOR_MODAL_BG)
        dialog.transient(self)
        dialog.grab_set()

        # Center on screen
        dialog.update_idletasks()
        sx = (dialog.winfo_screenwidth() - 340) // 2
        sy = (dialog.winfo_screenheight() - 180) // 2
        dialog.geometry(f"340x180+{sx}+{sy}")

        ctk.CTkLabel(
            dialog, text="✅ Update Installed",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLOR_MODAL_TEXT,
        ).pack(pady=(24, 8))

        ctk.CTkLabel(
            dialog,
            text="Restart the application to\napply the update.",
            font=ctk.CTkFont(size=13),
            text_color=COLOR_MODAL_MUTED,
        ).pack(pady=(0, 16))

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24)

        ctk.CTkButton(
            btn_frame, text="Restart Later",
            fg_color="#475569", hover_color="#64748B",
            font=ctk.CTkFont(size=13), corner_radius=8,
            height=36, width=130,
            command=dialog.destroy,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            btn_frame, text="Restart Now",
            fg_color=COLOR_MODAL_SUCCESS, hover_color="#16A34A",
            font=ctk.CTkFont(size=13, weight="bold"),
            corner_radius=8, height=36, width=130,
            command=self._do_restart,
        ).pack(side="right")

    def _do_restart(self):
        """Apply pending installer (if any) then restart via main app."""
        import sys

        from core.auto_updater import apply_update

        parent = self.master
        installer = getattr(self, "_pending_installer", None)

        # Close the settings modal first
        try:
            self.grab_release()
        except RuntimeError:
            pass
        self.destroy()

        # Run the installer silently if we have a downloaded file
        if installer and os.path.isfile(installer):
            result = apply_update(installer)
            if not result.get("success"):
                logger.error("Installer failed: %s", result.get("error"))
                # Still try to restart — user might need to update manually
                if hasattr(parent, '_show_download_error'):
                    parent.after(0, parent._show_download_error,
                                 f"Install failed: {result.get('error')}")
                return
            # If installer launched successfully, exit this app immediately
            # so the installer can overwrite the executable.
            parent.destroy()
            sys.exit(0)

        # Normal restart (no installer)
        if hasattr(parent, '_restart_app'):
            parent._restart_app()
        else:
            from core.auto_updater import restart_app
            restart_app()

    # ================================================================== #
    #  SAVE & CLOSE
    # ================================================================== #

    def _save_and_close(self):
        self._settings["appearance"] = self._appearance_var.get()
        self._settings["auto_update"] = self._auto_update_var.get() == "on"
        # v3.1.0: Save rate type
        selected_label = self._rate_type_var.get()
        self._settings["rate_type"] = self._rate_type_map.get(
            selected_label, "buying_transfer"
        )
        self._mgr.save(self._settings)
        self.destroy()

    # ================================================================== #
    #  CSV IMPORT
    # ================================================================== #

    def _on_import_csv(self):
        """Open a file dialog and import a BOT CSV into the local cache."""
        from tkinter import filedialog as fd

        csv_path = fd.askopenfilename(
            title="Select BOT Exchange Rate CSV",
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not csv_path:
            return

        self._lbl_csv.configure(
            text="Importing...", text_color=COLOR_MODAL_MUTED,
        )
        self.update_idletasks()

        def _worker():
            try:
                from core.csv_import import import_bot_csv
                from core.database import CacheDB

                cache = CacheDB()
                count = import_bot_csv(csv_path, cache)
                cache.close()
                self.after(
                    0, self._lbl_csv.configure,
                    {"text": f"✓ Imported {count} rate entries",
                     "text_color": COLOR_MODAL_SUCCESS},
                )
            except (OSError, ValueError, KeyError) as e:
                self.after(
                    0, self._lbl_csv.configure,
                    {"text": f"✗ Import failed: {e}",
                     "text_color": "#F87171"},
                )

        import threading
        threading.Thread(target=_worker, daemon=True).start()

    # ================================================================== #
    #  CSV EXPORT
    # ================================================================== #

    def _on_export_csv(self):
        """Open a save-file dialog and export cached rates to CSV."""
        from tkinter import filedialog as fd

        csv_path = fd.asksaveasfilename(
            title="Export Cached Rates to CSV",
            defaultextension=".csv",
            initialfile="BOT_ExRate_Export.csv",
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
        )
        if not csv_path:
            return

        self._lbl_csv_export.configure(
            text="Exporting...", text_color=COLOR_MODAL_MUTED,
        )
        self.update_idletasks()

        def _worker():
            try:
                from core.csv_export import export_rates_csv
                from core.database import CacheDB

                cache = CacheDB()
                count = export_rates_csv(csv_path, cache)
                cache.close()
                self.after(
                    0, self._lbl_csv_export.configure,
                    {"text": f"✓ Exported {count} rate rows",
                     "text_color": COLOR_MODAL_SUCCESS},
                )
            except (OSError, ValueError) as e:
                self.after(
                    0, self._lbl_csv_export.configure,
                    {"text": f"✗ Export failed: {e}",
                     "text_color": "#F87171"},
                )

        import threading
        threading.Thread(target=_worker, daemon=True).start()
