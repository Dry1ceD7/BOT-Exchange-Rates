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

import logging
import os
import platform
import sys
import threading

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
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        # Try .ico first (best for Windows tray), then .png
        for name in ("icon.ico", "icon.png"):
            path = os.path.join(base, "assets", name)
            if os.path.exists(path):
                return Image.open(path)
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
        self._icon: "pystray.Icon | None" = None
        self._tray_thread: threading.Thread | None = None
        self._is_hidden = False

    @property
    def supported(self) -> bool:
        """True if the current platform supports system tray."""
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
        """Called when the user clicks the window 'X' button."""
        self._app.withdraw()  # hide the window
        self._is_hidden = True
        logger.info("Window hidden to system tray")

    def _on_show(self, icon=None, item=None) -> None:
        """Restore the window from the tray."""
        # Schedule on the Tk main thread
        self._app.after(0, self._restore_window)

    def _restore_window(self) -> None:
        """Bring the window back and focus it."""
        self._app.deiconify()
        self._app.lift()
        self._app.focus_force()
        self._is_hidden = False
        logger.info("Window restored from system tray")

    def _on_exit(self, icon=None, item=None) -> None:
        """Fully quit the application."""
        logger.info("Exit requested from system tray")
        if self._icon:
            self._icon.stop()
        # Schedule destroy on the Tk main thread
        self._app.after(0, self._app.destroy)

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
            try:
                self._icon.stop()
            except (RuntimeError, OSError):
                pass
