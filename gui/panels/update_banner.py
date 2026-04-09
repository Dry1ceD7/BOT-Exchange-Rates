#!/usr/bin/env python3
"""
gui/panels/update_banner.py
---------------------------------------------------------------------------
Auto-updater UI — banner + download/install/restart logic.
Extracted from gui/app.py to reduce God Object line count.
---------------------------------------------------------------------------
"""

import logging
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
        """Show install location confirmation, then download and install."""
        from core.auto_updater import _get_install_dir

        self._install_dir = _get_install_dir()

        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color="#1E40AF", height=52)

            inner = ctk.CTkFrame(self._banner, fg_color="transparent")
            inner.place(relx=0.5, rely=0.5, anchor="center")

            # Show detected path
            short_path = self._install_dir or "Unknown"
            if len(short_path) > 45:
                short_path = "..." + short_path[-42:]

            ctk.CTkLabel(
                inner,
                text=f"  Install to: {short_path}",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color="#FFFFFF",
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                inner, text="Change",
                width=70, height=24,
                fg_color="#2563EB", hover_color="#3B82F6",
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=11, weight="bold"),
                corner_radius=4,
                command=self._change_install_dir,
            ).pack(side="left", padx=(0, 6))

            ctk.CTkButton(
                inner, text="Install",
                width=70, height=24,
                fg_color="#059669", hover_color="#10B981",
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=11, weight="bold"),
                corner_radius=4,
                command=lambda: self._do_download(version),
            ).pack(side="left", padx=(0, 6))

            ctk.CTkButton(
                inner, text="✕",
                width=24, height=24,
                fg_color="transparent", hover_color="#1E3A8A",
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=13, weight="bold"),
                corner_radius=4,
                command=lambda: self._banner.destroy(),
            ).pack(side="left")

    def _change_install_dir(self) -> None:
        """Open a folder dialog for the user to pick install directory."""
        from tkinter import filedialog

        new_dir = filedialog.askdirectory(
            title="Choose Installation Folder",
            initialdir=self._install_dir or "/",
        )
        if new_dir:
            self._install_dir = new_dir
            # Re-trigger the confirmation banner
            self._start_download(self._pending_version)

    def _do_download(self, version: str) -> None:
        """Execute the actual download + install with the confirmed path."""
        from core.auto_updater import (
            download_update,
            get_installer_asset_url,
        )

        # Update banner to show downloading state
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color="#F59E0B", height=40)
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

            # Instead of applying the update immediately and keeping the app alive,
            # we present a 'Ready to Install' banner. The user must click to apply,
            # which will execute the bat file and immediately exit the app.
            self.app.after(0, self._show_ready_to_install, result["path"])

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

    def _show_ready_to_install(self, installer_path: str) -> None:
        """Show banner indicating download is ready to apply."""
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color="#059669", height=44)

            inner = ctk.CTkFrame(self._banner, fg_color="transparent")
            inner.place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(
                inner,
                text="  ✅ Update downloaded!",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FFFFFF",
            ).pack(side="left", padx=(0, 16))

            ctk.CTkButton(
                inner, text="Install & Restart",
                width=130, height=28,
                fg_color="#FFFFFF", hover_color="#D1FAE5",
                text_color="#065F46",
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=6,
                command=lambda: self._execute_installer(installer_path),
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                inner, text="Later",
                width=70, height=28,
                fg_color="transparent", hover_color="#047857",
                text_color="#FFFFFF",
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=6,
                border_width=1, border_color="#FFFFFF",
                command=self._dismiss,
            ).pack(side="left")

    def _execute_installer(self, installer_path: str) -> None:
        """Launch the background updater script and immediately EXIT."""
        from core.auto_updater import apply_update

        # Update the UI first so user knows it's working
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            ctk.CTkLabel(
                self._banner,
                text="  Applying update... Closing application.",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color="#FFFFFF",
            ).place(relx=0.5, rely=0.5, anchor="center")

        # Fire the detached process
        apply_update(installer_path, install_dir=self._install_dir)

        # IMMEDIATELY kill this instance so the .bat file can overwrite BOT-ExRate.exe
        self.app.after(500, self._exit_for_restart)

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



    def _exit_for_restart(self) -> None:
        """Clean exit for restart — destroy window and exit process."""
        import sys
        try:
            self.app.destroy()
        except RuntimeError:
            pass
        sys.exit(0)
