#!/usr/bin/env python3
"""
tests/gui/test_tray_manager.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/tray_manager.py (TrayManager).

These tests exercise the platform-gating of `TrayManager.supported`:
  - macOS (Darwin) is NOT supported — pystray's AppKit backend run off the
    main thread frequently shows no icon, which would strand a hidden window
    on close-to-tray. macOS therefore falls back to a normal quit.
  - Linux is NOT supported.
  - Windows IS supported (only when pystray is available).

`supported` reads platform.system() + HAS_PYSTRAY only — it never touches Tk,
so these tests use a MagicMock app and need no display.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui


def _make_tray():
    """Construct a TrayManager with a mock app (no Tk required)."""
    from gui.panels.tray_manager import TrayManager

    return TrayManager(MagicMock())


class TestTraySupported:
    """`supported` is True only on Windows with pystray installed."""

    def test_not_supported_on_darwin(self):
        tray = _make_tray()
        with (
            patch("gui.panels.tray_manager.HAS_PYSTRAY", True),
            patch("gui.panels.tray_manager.platform.system",
                  return_value="Darwin"),
        ):
            assert tray.supported is False

    def test_not_supported_on_linux(self):
        tray = _make_tray()
        with (
            patch("gui.panels.tray_manager.HAS_PYSTRAY", True),
            patch("gui.panels.tray_manager.platform.system",
                  return_value="Linux"),
        ):
            assert tray.supported is False

    def test_supported_on_windows_with_pystray(self):
        tray = _make_tray()
        with (
            patch("gui.panels.tray_manager.HAS_PYSTRAY", True),
            patch("gui.panels.tray_manager.platform.system",
                  return_value="Windows"),
        ):
            assert tray.supported is True

    def test_not_supported_on_windows_without_pystray(self):
        tray = _make_tray()
        with (
            patch("gui.panels.tray_manager.HAS_PYSTRAY", False),
            patch("gui.panels.tray_manager.platform.system",
                  return_value="Windows"),
        ):
            assert tray.supported is False


class TestTraySetupSkipsWhenUnsupported:
    """setup() is a no-op (no protocol override) when unsupported."""

    def test_setup_noop_on_darwin(self):
        tray = _make_tray()
        with (
            patch("gui.panels.tray_manager.HAS_PYSTRAY", True),
            patch("gui.panels.tray_manager.platform.system",
                  return_value="Darwin"),
        ):
            tray.setup()
        # No close-to-tray handler installed on macOS — normal quit stands.
        tray._app.protocol.assert_not_called()
