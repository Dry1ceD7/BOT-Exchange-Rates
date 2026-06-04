#!/usr/bin/env python3
"""
tests/gui/test_version_panel.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/version_panel.py (VersionPanel).

These tests exercise:
  1. Widget tree construction — btn_ping, btn_update, btn_versions, labels.
  2. _on_ping_api() disables the button and updates _lbl_ping text.
  3. _ping_done() restores btn_ping to normal and shows message/color.
  4. _on_check_update() threads; _update_done() restores state and labels.
  5. _on_browse_versions() threads; _show_versions() populates the menu.
  6. _versions_error() surfaces an error string and restores the button.
  7. SafePanel _destroyed prevents _safe_after callbacks post-destroy.
  8. Version selection triggers _on_version_selected; Download button enables.

All tests require a display; the tk_root fixture skips them on headless CI.
Network, keyring, and auto_updater are fully mocked — no real I/O.
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GET_TOKEN = "gui.panels.version_panel.get_token"
_HTTPX_GET = "gui.panels.version_panel.httpx.get"
_CHECK_FOR_UPDATE = "core.auto_updater.check_for_update"
_AUTO_UPDATER_MODULE = "gui.panels.version_panel"


def _make_panel(tk_root, **kwargs):
    """Create a VersionPanel under tk_root with all external deps mocked."""
    from gui.panels.version_panel import VersionPanel

    with patch(_GET_TOKEN, return_value=None):
        panel = VersionPanel(tk_root, **kwargs)
    return panel


# ---------------------------------------------------------------------------
# 1. Widget tree
# ---------------------------------------------------------------------------

class TestVersionPanelWidgetTree:
    """VersionPanel constructs the expected child widgets."""

    def test_panel_instantiates_without_error(self, tk_root):
        panel = _make_panel(tk_root)
        assert panel is not None
        panel.destroy()

    def test_btn_ping_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_btn_ping")
        panel.destroy()

    def test_btn_update_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_btn_update")
        panel.destroy()

    def test_btn_versions_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_btn_versions")
        panel.destroy()

    def test_lbl_ping_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_lbl_ping")
        panel.destroy()

    def test_lbl_update_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_lbl_update")
        panel.destroy()

    def test_lbl_versions_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_lbl_versions")
        panel.destroy()

    def test_version_menu_exists(self, tk_root):
        panel = _make_panel(tk_root)
        assert hasattr(panel, "_version_menu")
        panel.destroy()

    def test_btn_dl_version_starts_disabled(self, tk_root):
        panel = _make_panel(tk_root)
        assert panel._btn_dl_version.cget("state") == "disabled"
        panel.destroy()

    def test_all_main_buttons_are_ctk_button(self, tk_root):
        import customtkinter as ctk

        panel = _make_panel(tk_root)
        assert isinstance(panel._btn_ping, ctk.CTkButton)
        assert isinstance(panel._btn_update, ctk.CTkButton)
        assert isinstance(panel._btn_versions, ctk.CTkButton)
        panel.destroy()


# ---------------------------------------------------------------------------
# 2. _on_ping_api() — synchronous side-effects before thread launches
# ---------------------------------------------------------------------------

class TestVersionPanelPingApi:
    """_on_ping_api() disables the button and updates label; thread mocked."""

    def test_ping_sets_button_disabled(self, tk_root):
        panel = _make_panel(tk_root)
        # Patch threading.Thread so no real thread is spawned
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_ping_api()
        assert panel._btn_ping.cget("state") == "disabled"
        panel.destroy()

    def test_ping_sets_label_testing(self, tk_root):
        panel = _make_panel(tk_root)
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_ping_api()
        assert panel._lbl_ping.cget("text") == "Testing..."
        panel.destroy()

    def test_ping_sets_busy_flag(self, tk_root):
        panel = _make_panel(tk_root)
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_ping_api()
        assert panel._busy_ping is True
        panel.destroy()

    def test_ping_noop_when_already_busy(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_ping = True
        original_text = panel._lbl_ping.cget("text")
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            panel._on_ping_api()
            mock_thread.assert_not_called()
        assert panel._lbl_ping.cget("text") == original_text
        panel.destroy()


# ---------------------------------------------------------------------------
# 3. _ping_done() — restores button, shows message
# ---------------------------------------------------------------------------

class TestVersionPanelPingDone:
    """_ping_done() restores btn_ping and shows status text."""

    def test_ping_done_restores_button(self, tk_root):
        panel = _make_panel(tk_root)
        # Simulate in-flight state
        panel._busy_ping = True
        panel._btn_ping.configure(state="disabled")

        panel._ping_done("✓ API connected & authenticated", "#00aa00")

        assert panel._btn_ping.cget("state") == "normal"
        panel.destroy()

    def test_ping_done_updates_label_text(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_ping = True

        panel._ping_done("✓ API connected & authenticated", "#00aa00")

        assert panel._lbl_ping.cget("text") == "✓ API connected & authenticated"
        panel.destroy()

    def test_ping_done_clears_busy_flag(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_ping = True

        panel._ping_done("some message", "#ff0000")

        assert panel._busy_ping is False
        panel.destroy()

    def test_ping_done_error_message(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_ping = True

        panel._ping_done("✗ Connection refused", "#ff0000")

        assert "✗" in panel._lbl_ping.cget("text")
        panel.destroy()


# ---------------------------------------------------------------------------
# 4. _on_check_update() and _update_done()
# ---------------------------------------------------------------------------

class TestVersionPanelCheckUpdate:
    """_on_check_update() threads; _update_done() restores state."""

    def test_check_update_disables_button(self, tk_root):
        panel = _make_panel(tk_root)
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_check_update()
        assert panel._btn_update.cget("state") == "disabled"
        panel.destroy()

    def test_check_update_sets_checking_label(self, tk_root):
        panel = _make_panel(tk_root)
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_check_update()
        assert panel._lbl_update.cget("text") == "Checking..."
        panel.destroy()

    def test_check_update_noop_when_busy(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_update = True
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            panel._on_check_update()
            mock_thread.assert_not_called()
        panel.destroy()

    def test_update_done_no_update_restores_button(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_update = True
        panel._btn_update.configure(state="disabled")

        panel._update_done("✓ Up to date (V3.2.8)", "#00aa00", None)

        assert panel._btn_update.cget("state") == "normal"
        panel.destroy()

    def test_update_done_no_update_shows_label(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_update = True

        panel._update_done("✓ Up to date (V3.2.8)", "#00aa00", None)

        assert "Up to date" in panel._lbl_update.cget("text")
        panel.destroy()

    def test_update_done_with_version_changes_button_text(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_update = True
        panel._btn_update.configure(state="disabled")

        panel._update_done("Update available: V3.3.0", "#ffaa00", "3.3.0")

        # Button text should reflect downloadable version
        assert "3.3.0" in panel._btn_update.cget("text")
        panel.destroy()

    def test_update_done_clears_busy_flag(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_update = True

        panel._update_done("✓ Up to date (V3.2.8)", "#00aa00", None)

        assert panel._busy_update is False
        panel.destroy()


# ---------------------------------------------------------------------------
# 5. _on_browse_versions() and _show_versions()
# ---------------------------------------------------------------------------

class TestVersionPanelBrowseVersions:
    """_on_browse_versions() threads; _show_versions() populates the menu."""

    def test_browse_disables_button(self, tk_root):
        panel = _make_panel(tk_root)
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_browse_versions()
        assert panel._btn_versions.cget("state") == "disabled"
        panel.destroy()

    def test_browse_sets_fetching_label(self, tk_root):
        panel = _make_panel(tk_root)
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            panel._on_browse_versions()
        assert "Fetching" in panel._lbl_versions.cget("text")
        panel.destroy()

    def test_browse_noop_when_busy(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        with patch("gui.panels.version_panel.threading.Thread") as mock_thread:
            panel._on_browse_versions()
            mock_thread.assert_not_called()
        panel.destroy()

    def test_show_versions_populates_menu(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        panel._btn_versions.configure(state="disabled")

        versions = [
            ("3.2.8", "v3.2.8", False),
            ("3.2.7", "v3.2.7", False),
            ("3.3.0-beta", "v3.3.0-beta [BETA]", True),
        ]
        panel._show_versions(versions)

        labels = panel._version_menu.cget("values")
        assert "v3.2.8" in labels
        assert "v3.2.7" in labels
        panel.destroy()

    def test_show_versions_restores_button(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        panel._btn_versions.configure(state="disabled")

        panel._show_versions([("3.2.8", "v3.2.8", False)])

        assert panel._btn_versions.cget("state") == "normal"
        panel.destroy()

    def test_show_versions_enables_download_button(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        panel._btn_versions.configure(state="disabled")

        panel._show_versions([("3.2.8", "v3.2.8", False)])

        assert panel._btn_dl_version.cget("state") == "normal"
        panel.destroy()

    def test_show_versions_empty_shows_error(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True

        panel._show_versions([])

        assert "No releases found" in panel._lbl_versions.cget("text")
        panel.destroy()

    def test_show_versions_clears_busy_flag(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True

        panel._show_versions([("3.2.8", "v3.2.8", False)])

        assert panel._busy_browse is False
        panel.destroy()


# ---------------------------------------------------------------------------
# 6. _versions_error()
# ---------------------------------------------------------------------------

class TestVersionPanelVersionsError:
    """_versions_error() surfaces error text and restores the button."""

    def test_versions_error_sets_label(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True

        panel._versions_error("Connection timeout")

        assert "Connection timeout" in panel._lbl_versions.cget("text")
        panel.destroy()

    def test_versions_error_restores_button(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        panel._btn_versions.configure(state="disabled")

        panel._versions_error("Connection timeout")

        assert panel._btn_versions.cget("state") == "normal"
        panel.destroy()

    def test_versions_error_clears_busy_flag(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True

        panel._versions_error("Connection timeout")

        assert panel._busy_browse is False
        panel.destroy()


# ---------------------------------------------------------------------------
# 7. SafePanel mixin contract
# ---------------------------------------------------------------------------

class TestVersionPanelSafePanelMixin:
    """SafePanel mixin (_destroyed flag + _safe_after) behaves correctly."""

    def test_destroyed_flag_starts_false(self, tk_root):
        panel = _make_panel(tk_root)
        assert panel._destroyed is False
        panel.destroy()

    def test_destroyed_flag_flips_on_destroy(self, tk_root):
        panel = _make_panel(tk_root)
        panel.destroy()
        assert panel._destroyed is True

    def test_safe_after_noop_post_destroy(self, tk_root):
        """_safe_after must not raise and must not invoke callback post-destroy."""
        panel = _make_panel(tk_root)
        panel.destroy()

        called = []
        panel._safe_after(0, lambda: called.append(1))

        assert called == [], "_safe_after must be a no-op post-destroy"


# ---------------------------------------------------------------------------
# 8. Version selection and Download button
# ---------------------------------------------------------------------------

class TestVersionPanelVersionSelection:
    """_on_version_selected enables the Download button."""

    def test_on_version_selected_enables_download(self, tk_root):
        panel = _make_panel(tk_root)
        # Simulate the download button being in disabled state
        panel._btn_dl_version.configure(state="disabled")

        panel._on_version_selected("v3.2.8")

        assert panel._btn_dl_version.cget("state") == "normal"
        panel.destroy()

    def test_on_download_selected_version_calls_download(self, tk_root):
        """_on_download_selected_version triggers _download_in_app for a known version."""
        panel = _make_panel(tk_root)
        panel._version_list = [("3.2.8", "v3.2.8", False)]
        panel._selected_version.set("v3.2.8")

        with patch.object(panel, "_download_in_app") as mock_dl:
            panel._on_download_selected_version()

        mock_dl.assert_called_once_with("3.2.8")
        panel.destroy()

    def test_on_download_selected_version_noop_for_unknown_label(self, tk_root):
        """_on_download_selected_version does nothing if label not in version_list."""
        panel = _make_panel(tk_root)
        panel._version_list = [("3.2.8", "v3.2.8", False)]
        panel._selected_version.set("v99.99.99")

        with patch.object(panel, "_download_in_app") as mock_dl:
            panel._on_download_selected_version()

        mock_dl.assert_not_called()
        panel.destroy()

    def test_callbacks_passed_through_constructor(self, tk_root):
        """on_restart and on_error callbacks are stored on the panel."""
        restart_cb = MagicMock()
        error_cb = MagicMock()

        panel = _make_panel(tk_root, on_restart=restart_cb, on_error=error_cb)

        assert panel._on_restart is restart_cb
        assert panel._on_error is error_cb
        panel.destroy()
