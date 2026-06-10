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


class TestTrayNotify:
    """notify() forwards to the pystray icon and no-ops when unavailable."""

    def test_notify_noop_when_no_icon(self):
        # No icon running (the common macOS/Linux/headless case): must not raise.
        tray = _make_tray()
        assert tray._icon is None
        tray.notify("3 processed, 1 failed")  # no exception

    def test_notify_forwards_to_icon(self):
        tray = _make_tray()
        icon = MagicMock()
        tray._icon = icon
        tray.notify("3 processed, 1 failed", title="BOT ExRate")
        icon.notify.assert_called_once_with("3 processed, 1 failed", "BOT ExRate")

    def test_notify_default_title(self):
        tray = _make_tray()
        icon = MagicMock()
        tray._icon = icon
        tray.notify("done")
        args = icon.notify.call_args.args
        assert args[0] == "done"
        assert "BOT" in args[1]

    def test_notify_swallows_icon_error(self):
        # A backend that raises (some pystray backends don't support notify)
        # must not crash the completion callback.
        tray = _make_tray()
        icon = MagicMock()
        icon.notify.side_effect = NotImplementedError("backend has no balloons")
        tray._icon = icon
        tray.notify("done")  # no exception

    def test_notify_noop_when_icon_lacks_notify(self):
        tray = _make_tray()

        class _IconWithoutNotify:
            pass

        tray._icon = _IconWithoutNotify()
        tray.notify("done")  # no exception, no attribute error


class TestTrayCloseFallback:
    """F153: _on_close must not strand a hidden window when the tray icon
    never started (icon load failure / pystray thread not running)."""

    def test_on_close_quits_normally_when_no_icon(self):
        tray = _make_tray()
        assert tray._icon is None
        tray._on_close()
        # Normal quit path: close handler invoked, window never hidden.
        tray._app._on_app_close.assert_called_once()
        tray._app.withdraw.assert_not_called()
        assert tray._is_hidden is False

    def test_on_close_falls_back_to_destroy_without_close_handler(self):
        from gui.panels.tray_manager import TrayManager

        class _App:
            def __init__(self):
                self.destroyed = False
                self.withdrawn = False

            def destroy(self):
                self.destroyed = True

            def withdraw(self):
                self.withdrawn = True

        app = _App()
        tray = TrayManager(app)
        tray._on_close()
        assert app.destroyed is True
        assert app.withdrawn is False

    def test_on_close_hides_to_tray_when_icon_running(self):
        tray = _make_tray()
        tray._icon = MagicMock()
        tray._on_close()
        tray._app.withdraw.assert_called_once()
        assert tray._is_hidden is True
        tray._app._on_app_close.assert_not_called()


class TestTrayIconLoadFallthrough:
    """F153: a corrupt icon asset falls through to the generated square."""

    def test_corrupt_assets_fall_through_to_generated_square(
        self, tmp_path, monkeypatch,
    ):
        import sys

        import gui.panels.tray_manager as tm

        fake_image_mod = MagicMock()
        fake_image_mod.open.side_effect = OSError("corrupt icon file")
        fake_image_mod.new.return_value = "GENERATED_SQUARE"

        monkeypatch.setattr(tm, "HAS_PYSTRAY", True)
        monkeypatch.setattr(tm, "Image", fake_image_mod, raising=False)
        # Point the frozen base dir at tmp_path so both candidate assets exist.
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "icon.ico").write_bytes(b"junk")
        (assets / "icon.png").write_bytes(b"junk")

        result = tm._load_tray_icon()

        # Both corrupt files were attempted, then the square was generated.
        assert fake_image_mod.open.call_count == 2
        assert result == "GENERATED_SQUARE"

    def test_first_good_asset_wins(self, tmp_path, monkeypatch):
        import sys

        import gui.panels.tray_manager as tm

        fake_image_mod = MagicMock()
        fake_image_mod.open.side_effect = [OSError("bad .ico"), "PNG_ICON"]

        monkeypatch.setattr(tm, "HAS_PYSTRAY", True)
        monkeypatch.setattr(tm, "Image", fake_image_mod, raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "icon.ico").write_bytes(b"junk")
        (assets / "icon.png").write_bytes(b"junk")

        assert tm._load_tray_icon() == "PNG_ICON"


class TestTrayMarshalsThroughSafeMarshal:
    """F152: _on_show/_on_exit fire on the pystray thread and must route their
    Tk work through app._safe_marshal (closing-flag check + RuntimeError AND
    TclError suppression — TclError is NOT a RuntimeError subclass), never a
    raw app.after guarded only by suppress(RuntimeError)."""

    def test_on_show_routes_through_safe_marshal(self):
        tray = _make_tray()
        tray._on_show()
        tray._app._safe_marshal.assert_called_once_with(tray._restore_window)
        tray._app.after.assert_not_called()

    def test_on_exit_routes_close_handler_through_safe_marshal(self):
        tray = _make_tray()
        tray._on_exit()
        tray._app._safe_marshal.assert_called_once_with(
            tray._app._on_app_close,
        )
        tray._app.after.assert_not_called()

    def test_on_exit_falls_back_to_destroy_without_close_handler(self):
        from gui.panels.tray_manager import TrayManager

        class _App:
            """App without _on_app_close — _on_exit must marshal destroy."""

            def __init__(self):
                self.marshalled = []

            def destroy(self):
                pass

            def _safe_marshal(self, func, *args):
                self.marshalled.append((func, args))

        app = _App()
        tray = TrayManager(app)
        tray._on_exit()
        assert app.marshalled == [(app.destroy, ())]

    def test_on_show_survives_marshal_noop_when_app_closing(self):
        """A closing app's _safe_marshal no-ops; _on_show must not raise."""
        tray = _make_tray()
        tray._app._safe_marshal.return_value = None
        tray._on_show()  # no exception — the pystray thread stays alive


class TestTrayRestoreModalGrab:
    """_restore_window surfaces an active modal grab instead of fighting it.

    Finding: a second-instance restore deiconified the root but left a modal's
    Tk grab in place, so clicks landed on the off-screen modal and the window
    looked frozen.
    """

    def test_restore_surfaces_active_modal_grab(self):
        tray = _make_tray()
        modal = MagicMock()
        # grab_current() returns the modal toplevel that still owns input.
        tray._app.grab_current.return_value = modal

        tray._restore_window()

        # The root is restored first...
        tray._app.deiconify.assert_called_once()
        # ...then the still-grabbing modal is surfaced and re-grabbed so input
        # is not trapped behind the lifted root.
        modal.lift.assert_called_once()
        modal.focus_force.assert_called_once()
        modal.grab_set.assert_called_once()
        assert tray._is_hidden is False

    def test_restore_no_grab_does_not_touch_modal(self):
        """When no modal holds a grab, only the root is restored."""
        tray = _make_tray()
        tray._app.grab_current.return_value = None

        tray._restore_window()

        tray._app.deiconify.assert_called_once()
        tray._app.focus_force.assert_called_once()
        assert tray._is_hidden is False

    def test_restore_ignores_grab_owned_by_root(self):
        """A grab held by the root itself is not treated as a separate modal."""
        tray = _make_tray()
        # grab_current returns the root → no separate modal to surface.
        tray._app.grab_current.return_value = tray._app

        tray._restore_window()

        # grab_set must NOT be re-asserted on the root by the modal-surface path.
        tray._app.grab_set.assert_not_called()
        assert tray._is_hidden is False

    def test_restore_survives_grab_current_error(self):
        """A Tk error from grab_current must not break the restore."""
        tray = _make_tray()
        tray._app.grab_current.side_effect = RuntimeError("no display")

        tray._restore_window()  # no exception

        tray._app.deiconify.assert_called_once()
        assert tray._is_hidden is False


class TestTrayLastRun:
    """set_last_run() records a retrievable summary for the tray menu row."""

    def test_last_run_text_defaults_to_none_yet(self):
        tray = _make_tray()
        assert tray._last_run_summary is None
        assert tray._last_run_menu_text() == "Last run: none yet"

    def test_set_last_run_updates_menu_text(self):
        tray = _make_tray()
        tray.set_last_run("7 OK, 1 failed (04 Jun 23:00)")
        assert tray._last_run_summary == "7 OK, 1 failed (04 Jun 23:00)"
        assert tray._last_run_menu_text() == (
            "Last run: 7 OK, 1 failed (04 Jun 23:00)"
        )
