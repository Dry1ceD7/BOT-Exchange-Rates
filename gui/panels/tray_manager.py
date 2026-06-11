#!/usr/bin/env python3
"""
gui/panels/tray_manager.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — System Tray Manager (v3.2.0)
---------------------------------------------------------------------------
Manages the system tray icon using pystray.  When the user closes the main
window, the app hides to the tray instead of exiting.  The window can be
restored by:
  1. Double-clicking the tray icon
  2. Launching the app again (single-instance mutex sends focus signal)
  3. Right-click → "Show Window"

Fully exit via right-click → "Exit".

Platform notes:
  - Windows: full tray support via pystray
  - macOS/Linux: graceful fallback — close simply quits as before
"""

import contextlib
import logging
import os
import platform
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Detect pystray availability ──────────────────────────────────────────
HAS_PYSTRAY = False
try:
    import pystray
    from PIL import Image

    HAS_PYSTRAY = True
except ImportError:
    logger.debug("pystray/Pillow not available — tray icon disabled")


def _load_tray_icon() -> "Image.Image | None":
    """Load the application icon for the system tray."""
    if not HAS_PYSTRAY:
        return None
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            # os.path.abspath avoids symlink resolution to keep the exact
            # legacy base dir; wrap in Path for the joins below.
            base_str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # noqa: PTH100, PTH120
            base = Path(base_str)
        # Try .ico first (best for Windows tray), then .png. A corrupt or
        # unreadable icon file falls through to the next candidate and
        # finally the generated square — one bad asset must not cost the
        # whole tray (and with it the only way back from close-to-tray).
        for name in ("icon.ico", "icon.png"):
            path = base / "assets" / name
            try:
                if path.exists():
                    return Image.open(path)
            except (OSError, ValueError) as e:
                logger.debug("Tray icon asset %s unusable: %s", name, e)
        # Fallback: generate a tiny coloured square
        img = Image.new("RGB", (64, 64), color=(59, 130, 246))
        return img
    except (OSError, ValueError) as e:
        logger.debug("Tray icon load failed: %s", e)
        return None


class TrayManager:
    """
    Manages the lifecycle of a system tray icon.

    Usage:
        tray = TrayManager(app)  # app is the CTk root window
        tray.setup()             # installs WM_DELETE_WINDOW override
    """

    def __init__(self, app):
        self._app = app
        self._icon: pystray.Icon | None = None
        self._tray_thread: threading.Thread | None = None
        self._is_hidden = False
        # Human-readable summary of the most recent scheduled run, surfaced in
        # the tray menu so an operator who left the app minimised overnight can
        # see whether last night's run happened and how it went. None until the
        # first scheduled run completes.
        self._last_run_summary: str | None = None

    @property
    def supported(self) -> bool:
        """True if the current platform supports system tray.

        Windows only: pystray's macOS (AppKit) backend run off the main thread
        frequently shows no icon, so close-to-tray would strand a hidden
        window. On macOS/Linux the window close is a normal quit instead.
        """
        return HAS_PYSTRAY and platform.system() == "Windows"

    def setup(self) -> None:
        """Install the close-to-tray handler and start the tray icon."""
        if not self.supported:
            logger.info(
                "System tray not supported on %s — using normal close",
                platform.system(),
            )
            return

        # Override the window close button
        self._app.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start the tray icon in a background thread
        self._start_tray()
        logger.info("System tray manager initialised")

    def _start_tray(self) -> None:
        """Create and run the pystray icon in a daemon thread."""
        icon_image = _load_tray_icon()
        if icon_image is None:
            return

        menu = pystray.Menu(
            pystray.MenuItem(
                "Show Window",
                self._on_show,
                default=True,  # double-click action
            ),
            pystray.Menu.SEPARATOR,
            # Dynamic, click-through informational row. pystray re-evaluates the
            # text callable each time the menu opens, so this always reflects the
            # latest scheduled-run outcome without rebuilding the icon. enabled=
            # False renders it greyed-out (it's a status line, not an action).
            pystray.MenuItem(
                self._last_run_menu_text,
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._on_exit),
        )

        self._icon = pystray.Icon(
            name="BOTExrate",
            icon=icon_image,
            title="BOT Exchange Rate Processor",
            menu=menu,
        )

        self._tray_thread = threading.Thread(
            target=self._icon.run, daemon=True
        )
        self._tray_thread.start()

    # ── Callbacks ────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """Called when the user clicks the window 'X' button.

        If the tray icon never started (icon load failed, pystray thread not
        running), hiding the window would strand it with no visible way back
        — fall back to a normal application quit instead.
        """
        if self._icon is None:
            logger.info("Tray icon unavailable — window close quits normally")
            close_handler = getattr(
                self._app, "_on_app_close", self._app.destroy
            )
            close_handler()
            return
        self._app.withdraw()  # hide the window
        self._is_hidden = True
        logger.info("Window hidden to system tray")

    def _on_show(self, icon=None, item=None) -> None:
        """Restore the window from the tray."""
        # Schedule on the Tk main thread. _safe_marshal no-ops once the app
        # is closing and swallows both RuntimeError AND TclError (TclError is
        # NOT a RuntimeError subclass), so a teardown race can never raise
        # unhandled inside the pystray thread.
        self._app._safe_marshal(self._restore_window)

    def _restore_window(self) -> None:
        """Bring the window back and focus it.

        If a modal dialog (Settings / ExRate) held a Tk grab when the window
        was hidden, deiconifying only the root leaves the grab in place: clicks
        land on the (screen-centered, now behind) modal and the main window
        looks frozen. So after restoring the root we detect any active grab and
        lift+focus THAT toplevel instead, surfacing the modal the user must act
        on rather than fighting its grab.
        """
        self._app.deiconify()
        self._app.lift()
        self._app.focus_force()

        # Bring a still-grabbing modal to the front so input is not trapped.
        grabbed = None
        with contextlib.suppress(Exception):  # grab_current absent / Tk torn down
            grabbed = self._app.grab_current()
        if grabbed is not None and grabbed is not self._app:
            with contextlib.suppress(Exception):
                grabbed.deiconify()
            with contextlib.suppress(Exception):
                grabbed.lift()
            with contextlib.suppress(Exception):
                grabbed.focus_force()
            # Re-assert the grab so it owns input cleanly on top, rather than a
            # stale grab fighting the freshly-lifted root.
            with contextlib.suppress(Exception):
                grabbed.grab_set()
            logger.info("Window restored; surfaced active modal grab")

        self._is_hidden = False
        logger.info("Window restored from system tray")

    def _on_exit(self, icon=None, item=None) -> None:
        """Fully quit the application."""
        logger.info("Exit requested from system tray")
        if self._icon:
            self._icon.stop()
        # Schedule the app-level close handler on the Tk main thread so workers
        # are torn down cleanly before destroy (falls back to destroy).
        # _safe_marshal guards the pystray thread against app-teardown races
        # (RuntimeError + TclError, no-op once _closing).
        close_handler = getattr(self._app, "_on_app_close", self._app.destroy)
        self._app._safe_marshal(close_handler)

    def restore_if_hidden(self) -> None:
        """
        Called when a second instance detects this one is already running.
        Restores the window if it was hidden.
        """
        if self._is_hidden:
            self._on_show()

    def cleanup(self) -> None:
        """Stop the tray icon (called during app shutdown)."""
        if self._icon:
            with contextlib.suppress(RuntimeError, OSError):
                self._icon.stop()

    # ── Scheduled-run feedback ───────────────────────────────────────────

    def _last_run_menu_text(self, _item=None) -> str:
        """Text for the dynamic 'Last run' tray menu row.

        Called by pystray each time the menu is opened (item.text accepts a
        callable), so it always reflects the latest summary.
        """
        if self._last_run_summary:
            return f"Last run: {self._last_run_summary}"
        return "Last run: none yet"

    def notify(self, message: str, title: str = "BOT Exchange Rate Processor") -> None:
        """Show a balloon/toast notification from the tray icon.

        On the supported Windows/pystray path this surfaces succeeded/failed
        counts to an operator whose window is minimised to the tray. Anywhere
        else (macOS/Linux, no pystray, icon not yet running) it is a graceful
        no-op so callers never need to platform-guard.
        """
        icon = self._icon
        if icon is None:
            return
        notify = getattr(icon, "notify", None)
        if not callable(notify):
            return
        with contextlib.suppress(Exception):
            notify(message, title)

    def set_last_run(self, summary: str) -> None:
        """Record the most recent scheduled-run summary for the tray menu.

        ``summary`` is a short human-readable string (e.g.
        "07 OK, 1 failed @ 23:00"). Stored only; the menu text callable reads
        it lazily on next open, so no icon rebuild is required.
        """
        self._last_run_summary = summary
