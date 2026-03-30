#!/usr/bin/env python3
"""
gui/panels/update_banner.py
---------------------------------------------------------------------------
Auto-updater UI — banner + download/install/restart logic.
Extracted from gui/app.py to reduce God Object line count.
---------------------------------------------------------------------------
"""

import logging
import os
import platform
import threading

import customtkinter as ctk

from gui.theme import get_theme

logger = logging.getLogger(__name__)


class UpdateManager:
    """Manages the auto-update lifecycle: check → banner → download → restart.

    Holds a reference to the parent app for UI callbacks. All methods are
    designed to be called from the main Tk thread (workers use `app.after`).
    """

    def __init__(self, app):
        self.app = app
        self._banner = None
        self._dl_label = None
        self._pending_version = None

    # ─── Entrypoint (called once on startup) ────────────────────────
    def check_for_updates(self) -> None:
        """Check for updates in a background thread."""
        from core.config_manager import SettingsManager
        from core.version import __version__ as APP_VERSION

        settings = SettingsManager().load()
        if not settings.get("auto_update", True):
            return

        def _worker():
            from core.auto_updater import check_for_update
            result = check_for_update(current_version=APP_VERSION)
            if result.get("update_available"):
                ver = result.get("latest_version", "?")
                url = result.get("download_url", "")
                self.app.after(0, self._show_banner, ver, url)

        threading.Thread(target=_worker, daemon=True).start()

    # ─── Banner ─────────────────────────────────────────────────────
    def _show_banner(self, version: str, url: str) -> None:
        """Show a visible update banner at the TOP of the app (below header)."""
        if self._banner:
            self._banner.destroy()

        self._banner = ctk.CTkFrame(
            self.app, fg_color="#F59E0B", corner_radius=0, height=40,
        )
        self._banner.pack(fill="x", before=self.app.card, pady=0)
        self._banner.pack_propagate(False)

        inner = ctk.CTkFrame(self._banner, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            inner,
            text=f"  Update available: V{version}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#1E293B",
        ).pack(side="left", padx=(0, 12))

        self._pending_version = version

        ctk.CTkButton(
            inner, text="Update Now",
            width=100, height=28,
            fg_color="#1E293B", hover_color="#0F172A",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6,
            command=lambda: self._start_download(version),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            inner, text="✕",
            width=28, height=28,
            fg_color="transparent", hover_color="#D97706",
            text_color="#1E293B",
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=4,
            command=lambda: self._banner.destroy(),
        ).pack(side="left")

    # ─── Download + Install ─────────────────────────────────────────
    def _start_download(self, version: str) -> None:
        """Download and install the update."""
        from core.auto_updater import (
            apply_update,
            download_update,
            get_installer_asset_url,
        )

        # Update banner to show downloading state
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._dl_label = ctk.CTkLabel(
                self._banner,
                text="  Downloading update...",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#1E293B",
            )
            self._dl_label.place(relx=0.5, rely=0.5, anchor="center")

        def _worker():
            asset = get_installer_asset_url(version)
            if asset.get("error") or not asset.get("url"):
                self.app.after(0, self._show_error,
                               asset.get("error", "No installer found"))
                return

            def _progress(downloaded, total):
                pct = int(downloaded / total * 100)
                self.app.after(0, self._update_progress, pct)

            result = download_update(
                url=asset["url"],
                filename=asset.get("filename"),
                progress_cb=_progress,
            )
            if result.get("error"):
                self.app.after(0, self._show_error, result["error"])
                return

            apply_result = apply_update(result["path"])
            if apply_result.get("success"):
                self.app.after(0, self._show_success)
            else:
                self.app.after(0, self._show_error,
                               apply_result.get("error", "Update failed"))

        threading.Thread(target=_worker, daemon=True).start()

    def _update_progress(self, pct: int) -> None:
        if self._dl_label:
            self._dl_label.configure(text=f"  Downloading update... {pct}%")

    # ─── Result banners ─────────────────────────────────────────────
    def _show_error(self, error: str) -> None:
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color="#DC2626")
            ctk.CTkLabel(
                self._banner,
                text=f"  Update failed: {error}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#FFFFFF",
            ).place(relx=0.5, rely=0.5, anchor="center")

    def _show_success(self) -> None:
        """Show success banner with Restart Now / Restart Later options."""
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color="#059669", height=44)

            inner = ctk.CTkFrame(self._banner, fg_color="transparent")
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
                command=self._dismiss,
            ).pack(side="left")

    def _dismiss(self) -> None:
        """Dismiss the update banner — update installed, will apply on next launch."""
        if self._banner:
            self._banner.destroy()
            self._banner = None
        t = get_theme()
        if hasattr(self.app, "lbl_status"):
            self.app.lbl_status.configure(
                text="Update installed — will apply on next restart.",
                text_color=t["success"],
            )

    def _restart_app(self) -> None:
        """Restart the application — launch new exe and exit current process."""
        import subprocess
        import sys

        logger.info("User requested restart after update")
        try:
            if getattr(sys, "frozen", False):
                exe_path = os.path.abspath(sys.executable)
                if platform.system() == "Windows":
                    DETACHED_PROCESS = 0x00000008
                    subprocess.Popen(
                        [exe_path],
                        creationflags=DETACHED_PROCESS,
                        close_fds=True,
                    )
                else:
                    subprocess.Popen([exe_path])
                self.app.after(500, self._exit_for_restart)
            else:
                self.app.destroy()
        except Exception as e:
            logger.error("Restart failed: %s", e)
            self._show_error(f"Restart failed: {e}")

    def _exit_for_restart(self) -> None:
        """Clean exit for restart — destroy window and exit process."""
        import sys
        try:
            self.app.destroy()
        except Exception:
            pass
        sys.exit(0)
