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

import contextlib
import logging
import sys
import threading
import tkinter
import webbrowser
from pathlib import Path

import customtkinter as ctk
import httpx

from core.i18n import tr
from core.secure_tokens import get_token
from gui.panels._base_panel import SafePanel
from gui.theme import get_theme

logger = logging.getLogger(__name__)

# GitHub API endpoints
_RELEASES_URL = (
    "https://api.github.com/repos/Dry1ceD7/BOT-Exchange-Rates/releases"
)
_RELEASES_PAGE_URL = (
    "https://github.com/Dry1ceD7/BOT-Exchange-Rates/releases"
)
# Longest release-notes blob we render inline (keep the panel featherweight
# and avoid pasting a megabyte of changelog into a Tk textbox).
_MAX_NOTES_CHARS = 1200


def _can_install_in_place() -> bool:
    """True only when the in-app installer can actually apply an update.

    core.auto_updater.apply_update only works on a frozen Windows build (it
    runs the Inno Setup .exe). On macOS/Linux — or an unfrozen dev run — the
    install step always fails, so the in-app *install* flow is a dead end.
    Mirrors UpdateManager.check_for_updates' win32 gating so the panel never
    promises an install it cannot deliver.
    """
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def _truncate_notes(body: str | None) -> str:
    """Normalise GitHub release `body` markdown into short plain text."""
    if not body:
        return ""
    text = body.replace("\r\n", "\n").strip()
    if len(text) > _MAX_NOTES_CHARS:
        text = text[:_MAX_NOTES_CHARS].rstrip() + "\n…(truncated)"
    return text


class VersionPanel(SafePanel, ctk.CTkFrame):
    """Embeddable version/update panel for the settings modal."""

    def __init__(self, master, on_restart=None, on_error=None,
                 is_batch_active=None, **kwargs):
        t = get_theme()
        super().__init__(master, fg_color="transparent", **kwargs)
        self._t = t
        self._pending_installer = None
        self._pending_sha256 = None
        self._busy_ping = False
        self._busy_update = False
        self._busy_browse = False
        self._on_restart = on_restart
        self._on_error = on_error
        # Optional callable -> bool, supplied by the host so the restart-to-
        # install flow can refuse while a batch is mid-flight. When omitted we
        # fall back to walking the widget hierarchy to the app's batch_handler
        # (see _is_batch_active), so the guard works even without explicit
        # wiring from the settings modal.
        self._is_batch_active_cb = is_batch_active
        self._busy_download = False
        # Maps a version label -> its release notes (plain text), populated by
        # _on_browse_versions so _on_version_selected can render them.
        self._version_notes: dict[str, str] = {}
        self._build_ui()

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

        # On platforms where we cannot install in place (macOS/Linux, or an
        # unfrozen dev build) the button opens the release page instead of
        # promising a download+install that can never complete.
        dl_text = "Download" if _can_install_in_place() else "Open Release Page"
        self._btn_dl_version = ctk.CTkButton(
            self._version_frame, text=dl_text,
            fg_color=t["warning"], hover_color=t["warning_hover"],
            font=ctk.CTkFont(size=12, weight="bold"),
            corner_radius=6, height=32, width=140,
            state="disabled",
            command=self._on_download_selected_version,
        )
        self._btn_dl_version.pack(side="right")

        # ── Release Notes ("What's New") ─────────────────────────────
        # Hidden until a version with a non-empty body is selected (browse) or
        # a stable update is found (check). Read-only, scrollable, truncated.
        self._notes_box = ctk.CTkTextbox(
            self, height=110, wrap="word",
            font=ctk.CTkFont(size=11),
            fg_color=(t["section_bg"], t["card_bg"]),
            text_color=(t["text_primary"], t["modal_text"]),
            border_width=1, border_color=t["divider"],
            corner_radius=6,
        )
        self._notes_box.configure(state="disabled")

        if not _can_install_in_place():
            self._lbl_platform = ctk.CTkLabel(
                self, text="Updates install automatically on Windows only — "
                           "on this OS, download from the release page.",
                font=ctk.CTkFont(size=11),
                text_color=t["modal_muted"], wraplength=320,
            )
            self._lbl_platform.pack(pady=(0, 4))

    def _set_notes(self, text: str) -> None:
        """Render plain-text release notes into the read-only textbox.

        An empty string hides the box; non-empty content shows it.
        """
        if not text:
            self._notes_box.pack_forget()
            return
        self._notes_box.configure(state="normal")
        self._notes_box.delete("1.0", "end")
        self._notes_box.insert("1.0", text)
        self._notes_box.configure(state="disabled")
        self._notes_box.pack(fill="x", pady=(0, 8))

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
            # Busy-reset must always run (mirrors the token-dialog worker):
            # ping_token swallows network errors itself, so any exception
            # here is a programming error that would otherwise wedge the
            # button forever.
            try:
                from core.api_client import ping_token

                # The BOT gateway scopes each key to ONE product (a valid
                # EXG key 403s on the holiday endpoint and vice versa), so a
                # green result must prove BOTH keys a real batch depends on:
                # EXG for the rates and HOL for the mandatory holiday fetch.
                # Probing only EXG produced the support-killing state of a
                # green test alongside a batch whose holiday fetch fails.
                results = []
                for label, key_name, product in (
                    ("Exchange-rate", "BOT_TOKEN_EXG", "exg"),
                    ("Holiday", "BOT_TOKEN_HOL", "hol"),
                ):
                    token = get_token(key_name)
                    if not token:
                        results.append(
                            (False, f"{label} key: not configured")
                        )
                        continue
                    ok, msg = ping_token(token, product=product)
                    results.append((ok, f"{label} key: {'OK' if ok else msg}"))

                if all(ok for ok, _ in results):
                    self._safe_after(
                        0, self._ping_done,
                        "OK: API connected & authenticated (both keys)",
                        t["modal_success"],
                    )
                else:
                    self._safe_after(
                        0, self._ping_done,
                        " | ".join(msg for _, msg in results),
                        t["error_text"],
                    )
            except Exception:  # noqa: BLE001 — busy-reset must always run
                logger.exception("API ping worker failed unexpectedly")
                self._safe_after(
                    0, self._ping_done,
                    "FAILED: API test failed unexpectedly — see app.log.",
                    t["error_text"],
                )

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
                    notes = _truncate_notes(self._fetch_release_notes(ver))
                    self._safe_after(0, self._update_done,
                               f"Update available: V{ver}", t["warning"],
                               ver, notes)
                elif result.get("error"):
                    self._safe_after(0, self._update_done,
                               f"Check failed: {result['error']}",
                               t["error_text"], None, "")
                else:
                    self._safe_after(0, self._update_done,
                               f"OK: Up to date (V{__version__})",
                               t["modal_success"], None, "")
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self._safe_after(0, self._update_done,
                           f"Error: {e}", t["error_text"], None, "")

        threading.Thread(target=_worker, daemon=True, name="UpdateCheck").start()

    def _fetch_release_notes(self, version: str) -> str:
        """Fetch the GitHub release `body` for a tag. Returns '' on any error.

        Runs inside a worker thread only; failures are non-fatal — release
        notes are a nicety, never a blocker for the update flow.
        """
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/Dry1ceD7/"
                f"BOT-Exchange-Rates/releases/tags/v{version}",
                headers={"Accept": "application/vnd.github+json"},
                timeout=8.0,
            )
            resp.raise_for_status()
            return resp.json().get("body", "") or ""
        except (httpx.RequestError, httpx.HTTPStatusError,
                OSError, ValueError) as e:
            logger.debug("Could not fetch release notes for %s: %s", version, e)
            return ""

    def _update_done(self, text: str, color: str, version, notes: str = ""):
        self._busy_update = False
        t = self._t
        self._lbl_update.configure(text=text, text_color=color)
        self._set_notes(notes)
        if version and _can_install_in_place():
            self._btn_update.configure(
                text=f"Download V{version}",
                fg_color=t["warning"], hover_color=t["warning_hover"],
                state="normal",
                command=lambda: self._download_in_app(version),
            )
        elif version:
            # No in-place install on this OS: send the user to the release
            # page rather than starting a download that can never install.
            self._btn_update.configure(
                text=f"Open V{version} Release Page",
                fg_color=t["warning"], hover_color=t["warning_hover"],
                state="normal",
                command=self._open_release_page,
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
                notes = {}
                for rel in releases:
                    tag = rel.get("tag_name", "").lstrip("vV")
                    is_pre = rel.get("prerelease", False)
                    label = f"v{tag} [BETA]" if is_pre else f"v{tag}"
                    versions.append((tag, label, is_pre))
                    # GitHub already returns the body here — keep it so the
                    # browser can show 'what's new' without a second request.
                    notes[label] = _truncate_notes(rel.get("body", ""))

                self._safe_after(0, self._show_versions, versions, notes)
            except (httpx.RequestError, httpx.HTTPStatusError,
                    OSError, ValueError) as e:
                self._safe_after(0, self._versions_error, str(e))

        threading.Thread(target=_worker, daemon=True, name="VersionBrowse").start()

    def _show_versions(self, versions, notes: dict | None = None):
        self._busy_browse = False
        t = self._t
        self._btn_versions.configure(state="normal")
        if not versions:
            self._lbl_versions.configure(
                text="No releases found", text_color=t["error_text"],
            )
            return

        self._version_list = versions
        self._version_notes = notes or {}
        labels = [v[1] for v in versions]
        action = "download" if _can_install_in_place() else "view"
        self._lbl_versions.configure(
            text=f"{len(versions)} versions available — select to {action}:",
            text_color=t["modal_muted"],
        )
        self._selected_version.set(labels[0])
        self._version_menu.configure(values=labels)
        self._version_frame.pack(fill="x", pady=(0, 8))
        self._btn_dl_version.configure(state="normal")
        # Render the notes for the initially-selected (latest) version.
        self._set_notes(self._version_notes.get(labels[0], ""))

    def _versions_error(self, msg: str):
        self._busy_browse = False
        t = self._t
        self._btn_versions.configure(state="normal")
        self._lbl_versions.configure(
            text=f"Error: {msg}", text_color=t["error_text"],
        )

    def _on_version_selected(self, label: str):
        self._btn_dl_version.configure(state="normal")
        # Show the selected version's release notes (empty -> hides the box).
        self._set_notes(self._version_notes.get(label, ""))

    def _open_release_page(self):
        """Open the GitHub releases page in the default browser.

        Used on platforms where the in-app installer cannot run, so the user
        gets the binary from the canonical source instead of a dead-end
        download-then-fail-to-install flow.
        """
        with contextlib.suppress(OSError, webbrowser.Error):
            webbrowser.open(_RELEASES_PAGE_URL)

    def _on_download_selected_version(self):
        # On non-installable platforms this button is the 'Open Release Page'
        # affordance — never start a download that cannot be installed.
        if not _can_install_in_place():
            self._open_release_page()
            return
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
        # Defense-in-depth: the install step (apply_update) only works on a
        # frozen Windows build. On any other platform, downloading the .exe is
        # wasted bandwidth that ends in 'In-place update only works for frozen
        # apps' — so redirect to the release page instead of pretending.
        if not _can_install_in_place():
            self._open_release_page()
            return
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
                from core.auto_updater import fetch_expected_checksum

                asset = get_installer_asset_url(version)
                if asset.get("error") or not asset.get("url"):
                    self._safe_after(0, self._dl_done,
                               f"Error: {asset.get('error', 'No installer')}",
                               t["error_text"], False)
                    return

                expected_sha256 = None
                if asset.get("sha256_url"):
                    expected_sha256 = fetch_expected_checksum(
                        asset["sha256_url"]
                    )

                def _progress(downloaded, total):
                    pct = int(downloaded / total * 100)
                    self._safe_after(0, lambda: self._lbl_update.configure(
                        text=f"Downloading V{version}... {pct}%",
                        text_color=t["modal_muted"]))

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
                           "Done: Downloaded — restart to install",
                           t["modal_success"], True, installer_path,
                           expected_sha256)
            except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
                self._safe_after(0, self._dl_done,
                           f"Error: {e}", t["error_text"], False)
            except Exception as e:
                # Broad fallback: any escaping exception would kill this bare
                # daemon thread with _busy_download stuck True and both
                # buttons disabled forever. _dl_done(success=False) resets
                # the flag and re-enables them.
                logger.exception("Update download worker failed")
                self._safe_after(0, self._dl_done,
                           f"Error: {e}", t["error_text"], False)

        threading.Thread(target=_worker, daemon=True, name="UpdateDL").start()

    def _dl_done(self, text: str, color: str, success: bool,
                 installer_path: str = None, expected_sha256: str = None):
        self._busy_download = False
        self._lbl_update.configure(text=text, text_color=color)
        if success:
            self._btn_update.configure(state="disabled", text="Updated")
            self._btn_dl_version.configure(state="disabled")
            self._pending_installer = installer_path
            self._pending_sha256 = expected_sha256
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
        # Cosmetic modality only — transient/grab_set on a not-yet-viewable
        # Toplevel raise TclError on X11; a failed grab must not abort the
        # restart confirmation (no busy-flag blast radius here).
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            dialog.transient(self.winfo_toplevel())
        with contextlib.suppress(RuntimeError, tkinter.TclError):
            dialog.grab_set()

        dialog.update_idletasks()
        sx = (dialog.winfo_screenwidth() - 340) // 2
        sy = (dialog.winfo_screenheight() - 180) // 2
        dialog.geometry(f"340x180+{sx}+{sy}")

        ctk.CTkLabel(
            dialog, text="Update Installed",
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

    def _find_app(self):
        """Walk the widget hierarchy to the main app window.

        The panel lives inside the settings modal, whose own ``master`` is the
        app (see settings_modal.py). The app is the object that owns the
        ``batch_handler`` and the ``_on_app_close`` clean-shutdown handler.
        Returns the first ancestor exposing ``batch_handler`` or
        ``_on_app_close``, else None.
        """
        node = self
        seen = 0
        # Bounded walk (defensive against any accidental cycle in master refs).
        while node is not None and seen < 12:
            if hasattr(node, "batch_handler") or hasattr(node, "_on_app_close"):
                return node
            node = getattr(node, "master", None)
            seen += 1
        return None

    def _is_batch_active(self) -> bool:
        """True when a batch is currently processing files.

        Prefers the host-supplied callback; otherwise reads the app's
        ``batch_handler._batch_active`` via the hierarchy walk. Any lookup
        failure is treated as "not active" so the updater never blocks itself
        on a missing wiring path.
        """
        if self._is_batch_active_cb is not None:
            with contextlib.suppress(Exception):
                return bool(self._is_batch_active_cb())
        app = self._find_app()
        handler = getattr(app, "batch_handler", None) if app else None
        return bool(getattr(handler, "_batch_active", False))

    def _do_restart(self):
        """Apply pending installer (if any) then restart cleanly.

        A batch left an in-place .xlsx half-written if we exited mid-save, so:
          1. Refuse while a batch is active (the user keeps the dialog and is
             told to wait) — never tear down workers under a live save.
          2. Otherwise route the teardown through the app's ``_on_app_close``,
             which calls ThreadRegistry.shutdown_all() and lets any in-flight
             save finish at its safe boundary BEFORE we run the installer and
             exit. Only fall back to a bare modal-destroy when no app-level
             close handler is reachable.
        """
        import sys

        from core.auto_updater import apply_update

        # ── Guard: never restart-to-install while a batch is running ──────
        if self._is_batch_active():
            msg = tr("version.err_batch_running")
            logger.warning("Restart-to-install refused: a batch is active")
            if self._on_error:
                self._on_error(msg)
            else:
                from tkinter import messagebox
                messagebox.showwarning(tr("version.restart_blocked_title"), msg)
            return

        installer = self._pending_installer

        # ── Clean teardown: let any in-progress worker save finish ────────
        # Prefer the app-level close handler (stops workers via
        # shutdown_all(timeout) at a safe between-files boundary, then destroys
        # the root). Falling back to a bare modal destroy only when no such
        # handler exists keeps the previous behavior for non-app hosts (tests).
        app = self._find_app()
        close_handler = getattr(app, "_on_app_close", None) if app else None
        if callable(close_handler):
            with contextlib.suppress(RuntimeError):
                close_handler()
        else:
            settings_modal = self.winfo_toplevel()
            with contextlib.suppress(RuntimeError):
                settings_modal.grab_release()
            settings_modal.destroy()

        # Run the installer silently if we have a downloaded file.
        # SECURITY: pass the expected SHA-256 so apply_update re-verifies the
        # file immediately before executing it (TOCTOU guard).
        if installer and Path(installer).is_file():
            result = apply_update(
                installer, expected_sha256=self._pending_sha256
            )
            if not result.get("success"):
                logger.error("Installer failed: %s", result.get("error"))
                err_msg = f"Install failed: {result.get('error')}"
                if self._on_error:
                    self._on_error(err_msg)
                else:
                    # Failure must never be silent: the modal is already torn
                    # down, so fall back to a native error popup directly.
                    from tkinter import messagebox
                    messagebox.showerror("Update Failed", err_msg)
                return
            # Installer succeeded — hard exit
            sys.exit(0)

        # Normal restart (no installer)
        if self._on_restart:
            self._on_restart()
        else:
            from core.auto_updater import restart_app
            restart_app()
