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
    designed to be called from the main Tk thread (workers use `_safe_after`).
    """

    def __init__(self, app):
        self.app = app
        self._banner = None
        self._dl_label = None
        self._pending_version = None
        self._destroyed = False

    # ── Thread-safe after() wrapper ─────────────────────────────────
    def _safe_after(self, ms, fn, *args) -> None:
        """Schedule fn on the main thread only if app is still alive."""
        if self._destroyed:
            return
        try:
            self.app.after(ms, fn, *args)
        except RuntimeError:
            self._destroyed = True

    def mark_destroyed(self) -> None:
        """Called by the app during teardown to prevent further scheduling."""
        self._destroyed = True

    # ─── Entrypoint (called once on startup) ────────────────────────
    def check_for_updates(self) -> None:
        """Check for updates in a background thread."""
        from core.config_manager import SettingsManager
        from core.version import __version__ as APP_VERSION

        settings = SettingsManager().load()
        if not settings.get("auto_update", True):
            return
        silent_update = settings.get("silent_update", False)

        def _worker():
            from core.auto_updater import check_for_update
            result = check_for_update(current_version=APP_VERSION)
            if result.get("update_available"):
                ver = result.get("latest_version", "?")
                url = result.get("download_url", "")
                if silent_update:
                    # Enterprise mode: no approval banner; use default install path.
                    self._safe_after(0, self._do_download, ver)
                else:
                    self._safe_after(0, self._show_banner, ver, url)

        threading.Thread(target=_worker, daemon=True).start()

    # ─── Banner ─────────────────────────────────────────────────────
    def _show_banner(self, version: str, url: str) -> None:
        """Show a visible update banner at the TOP of the app (below header)."""
        t = get_theme()
        if self._banner:
            self._banner.destroy()

        self._banner = ctk.CTkFrame(
            self.app, fg_color=t["banner_warn"], corner_radius=0, height=40,
        )
        self._banner.pack(fill="x", before=self.app.card, pady=0)
        self._banner.pack_propagate(False)

        inner = ctk.CTkFrame(self._banner, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(
            inner,
            text=f"  Update available: V{version}",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=t["banner_warn_text"],
        ).pack(side="left", padx=(0, 12))

        self._pending_version = version

        ctk.CTkButton(
            inner, text="Update Now",
            width=100, height=28,
            fg_color=t["banner_dark"], hover_color=t["banner_dark_hover"],
            text_color=t["banner_text_light"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6,
            command=lambda: self._start_download(version),
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            inner, text="✕",
            width=28, height=28,
            fg_color="transparent", hover_color=t["banner_warn_hover"],
            text_color=t["banner_warn_text"],
            font=ctk.CTkFont(size=14, weight="bold"),
            corner_radius=4,
            command=lambda: self._banner.destroy(),
        ).pack(side="left")

    # ─── Download + Install ─────────────────────────────────────────
    def _start_download(self, version: str) -> None:
        """Show install location confirmation, then download and install."""
        t = get_theme()
        from core.auto_updater import _get_install_dir

        self._install_dir = _get_install_dir()

        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color=t["banner_confirm_bg"], height=52)

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
                text_color=t["banner_text_light"],
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                inner, text="Change",
                width=70, height=24,
                fg_color=t["banner_install"], hover_color=t["banner_install_hover"],
                text_color=t["banner_text_light"],
                font=ctk.CTkFont(size=11, weight="bold"),
                corner_radius=4,
                command=self._change_install_dir,
            ).pack(side="left", padx=(0, 6))

            ctk.CTkButton(
                inner, text="Install",
                width=70, height=24,
                fg_color=t["banner_apply"], hover_color=t["banner_apply_hover"],
                text_color=t["banner_text_light"],
                font=ctk.CTkFont(size=11, weight="bold"),
                corner_radius=4,
                command=lambda: self._do_download(version),
            ).pack(side="left", padx=(0, 6))

            ctk.CTkButton(
                inner, text="✕",
                width=24, height=24,
                fg_color="transparent", hover_color=t["banner_confirm_hover"],
                text_color=t["banner_text_light"],
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
        t = get_theme()
        from core.auto_updater import (
            download_update,
            get_installer_asset_url,
        )

        # Update banner to show downloading state
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color=t["banner_warn"], height=40)
            self._dl_label = ctk.CTkLabel(
                self._banner,
                text="  Downloading update...",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=t["banner_warn_text"],
            )
            self._dl_label.place(relx=0.5, rely=0.5, anchor="center")

        def _worker():
            from core.auto_updater import _fetch_expected_checksum

            asset = get_installer_asset_url(version)
            if asset.get("error") or not asset.get("url"):
                self._safe_after(0, self._show_error,
                               asset.get("error", "No installer found"))
                return

            # C-02: Fetch SHA-256 checksum for integrity verification
            expected_sha256 = None
            if asset.get("sha256_url"):
                expected_sha256 = _fetch_expected_checksum(
                    asset["sha256_url"]
                )

            def _progress(downloaded, total):
                pct = int(downloaded / total * 100)
                self._safe_after(0, self._update_progress, pct)

            result = download_update(
                url=asset["url"],
                filename=asset.get("filename"),
                progress_cb=_progress,
                expected_sha256=expected_sha256,
            )
            if result.get("error"):
                self._safe_after(0, self._show_error, result["error"])
                return

            # Instead of applying the update immediately and keeping the app alive,
            # we present a 'Ready to Install' banner. The user must click to apply,
            # which will execute the bat file and immediately exit the app.
            self._safe_after(0, self._show_ready_to_install, result["path"])

        threading.Thread(target=_worker, daemon=True).start()

    def _update_progress(self, pct: int) -> None:
        if self._dl_label:
            self._dl_label.configure(text=f"  Downloading update... {pct}%")

    # ─── Result banners ─────────────────────────────────────────────
    def _show_error(self, error: str) -> None:
        t = get_theme()
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color=t["banner_error"])
            ctk.CTkLabel(
                self._banner,
                text=f"  Update failed: {error}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=t["banner_text_light"],
            ).place(relx=0.5, rely=0.5, anchor="center")

    def _show_ready_to_install(self, installer_path: str) -> None:
        """Show banner indicating download is ready to apply."""
        t = get_theme()
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            self._banner.configure(fg_color=t["banner_success"], height=44)

            inner = ctk.CTkFrame(self._banner, fg_color="transparent")
            inner.place(relx=0.5, rely=0.5, anchor="center")

            ctk.CTkLabel(
                inner,
                text="  ✅ Update downloaded!",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=t["banner_text_light"],
            ).pack(side="left", padx=(0, 16))

            ctk.CTkButton(
                inner, text="Install & Restart",
                width=130, height=28,
                fg_color=t["banner_success_btn"], hover_color=t["banner_success_btn_h"],
                text_color=t["banner_success_text"],
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=6,
                command=lambda: self._execute_installer(installer_path),
            ).pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                inner, text="Later",
                width=70, height=28,
                fg_color="transparent", hover_color=t["banner_later_hover"],
                text_color=t["banner_text_light"],
                font=ctk.CTkFont(size=12, weight="bold"),
                corner_radius=6,
                border_width=1, border_color=t["banner_text_light"],
                command=self._dismiss,
            ).pack(side="left")

    def _execute_installer(self, installer_path: str) -> None:
        """Launch the background updater script and immediately EXIT."""
        t = get_theme()
        from core.auto_updater import apply_update

        # Update the UI first so user knows it's working
        if self._banner:
            for w in self._banner.winfo_children():
                w.destroy()
            ctk.CTkLabel(
                self._banner,
                text="  Applying update... Closing application.",
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=t["banner_text_light"],
            ).place(relx=0.5, rely=0.5, anchor="center")

        # Fire the detached process
        apply_update(installer_path, install_dir=self._install_dir)

        # IMMEDIATELY kill this instance so the .bat file can overwrite BOT-ExRate.exe
        self._safe_after(500, self._exit_for_restart)

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
