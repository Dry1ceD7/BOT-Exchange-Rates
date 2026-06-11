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
_CAN_INSTALL = "gui.panels.version_panel._can_install_in_place"
_WEBBROWSER_OPEN = "gui.panels.version_panel.webbrowser.open"


def _make_panel(tk_root, *, can_install=True, **kwargs):
    """Create a VersionPanel under tk_root with all external deps mocked.

    `can_install` controls the platform-gating helper so a single test process
    can exercise both the Windows (install in place) and macOS/Linux
    (release-page redirect) branches without touching real sys.platform.
    """
    from gui.panels.version_panel import VersionPanel

    with (
        patch(_GET_TOKEN, return_value=None),
        patch(_CAN_INSTALL, return_value=can_install),
    ):
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

        panel._ping_done("OK: API connected & authenticated", "#00aa00")

        assert panel._btn_ping.cget("state") == "normal"
        panel.destroy()

    def test_ping_done_updates_label_text(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_ping = True

        panel._ping_done("OK: API connected & authenticated", "#00aa00")

        assert panel._lbl_ping.cget("text") == "OK: API connected & authenticated"
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

        panel._ping_done("FAILED: Connection refused", "#ff0000")

        assert "FAILED:" in panel._lbl_ping.cget("text")
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

        panel._update_done("OK: Up to date (V3.2.8)", "#00aa00", None)

        assert panel._btn_update.cget("state") == "normal"
        panel.destroy()

    def test_update_done_no_update_shows_label(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_update = True

        panel._update_done("OK: Up to date (V3.2.8)", "#00aa00", None)

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

        panel._update_done("OK: Up to date (V3.2.8)", "#00aa00", None)

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
        panel = _make_panel(tk_root, can_install=True)
        panel._version_list = [("3.2.8", "v3.2.8", False)]
        panel._selected_version.set("v3.2.8")

        # Patch the runtime gate too: the install branch only runs on Windows.
        with (
            patch(_CAN_INSTALL, return_value=True),
            patch.object(panel, "_download_in_app") as mock_dl,
        ):
            panel._on_download_selected_version()

        mock_dl.assert_called_once_with("3.2.8")
        panel.destroy()

    def test_on_download_selected_version_noop_for_unknown_label(self, tk_root):
        """_on_download_selected_version does nothing if label not in version_list."""
        panel = _make_panel(tk_root, can_install=True)
        panel._version_list = [("3.2.8", "v3.2.8", False)]
        panel._selected_version.set("v99.99.99")

        with (
            patch(_CAN_INSTALL, return_value=True),
            patch.object(panel, "_download_in_app") as mock_dl,
        ):
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


# ---------------------------------------------------------------------------
# 9. _do_restart install-failure surfacing (never silent)
# ---------------------------------------------------------------------------

class TestVersionPanelInstallFailure:
    """A failed installer must surface an error via callback OR messagebox."""

    def test_install_failure_calls_on_error(self, tk_root):
        error_cb = MagicMock()
        panel = _make_panel(tk_root, on_error=error_cb)
        panel._pending_installer = "/tmp/installer.exe"
        panel._pending_sha256 = "abc123"

        fake_toplevel = MagicMock()

        with (
            patch.object(panel, "winfo_toplevel", return_value=fake_toplevel),
            patch("pathlib.Path.is_file", return_value=True),
            patch("core.auto_updater.apply_update",
                  return_value={"success": False, "error": "checksum mismatch"}),
        ):
            panel._do_restart()

        error_cb.assert_called_once()
        assert "checksum mismatch" in error_cb.call_args[0][0]
        panel.destroy()

    def test_install_failure_falls_back_to_messagebox_when_no_callback(
        self, tk_root
    ):
        """When on_error is None, a native error popup must still fire."""
        panel = _make_panel(tk_root, on_error=None)
        panel._pending_installer = "/tmp/installer.exe"
        panel._pending_sha256 = "abc123"

        fake_toplevel = MagicMock()

        with (
            patch.object(panel, "winfo_toplevel", return_value=fake_toplevel),
            patch("pathlib.Path.is_file", return_value=True),
            patch("core.auto_updater.apply_update",
                  return_value={"success": False, "error": "boom"}),
            patch("tkinter.messagebox.showerror") as mock_box,
        ):
            panel._do_restart()

        mock_box.assert_called_once()
        # Message text carries the underlying error so it is never silent
        assert "boom" in mock_box.call_args[0][1]
        panel.destroy()


# ---------------------------------------------------------------------------
# 10. Platform gating — macOS/Linux must not start an impossible install
#     (finding: "In-app updater lets macOS/Linux users download an update
#      that can never install")
# ---------------------------------------------------------------------------

class TestVersionPanelPlatformGating:
    """On non-installable platforms the panel redirects to the release page."""

    def test_can_install_helper_true_only_on_frozen_win32(self):
        from gui.panels import version_panel as vp

        with patch.object(vp.sys, "platform", "win32"), \
             patch.object(vp.sys, "frozen", True, create=True):
            assert vp._can_install_in_place() is True

    def test_can_install_helper_false_on_macos(self):
        from gui.panels import version_panel as vp

        with patch.object(vp.sys, "platform", "darwin"), \
             patch.object(vp.sys, "frozen", True, create=True):
            assert vp._can_install_in_place() is False

    def test_can_install_helper_false_when_unfrozen(self):
        from gui.panels import version_panel as vp

        with patch.object(vp.sys, "platform", "win32"), \
             patch.object(vp.sys, "frozen", False, create=True):
            assert vp._can_install_in_place() is False

    def test_download_button_label_is_release_page_on_non_install(self, tk_root):
        panel = _make_panel(tk_root, can_install=False)
        assert "Release Page" in panel._btn_dl_version.cget("text")
        panel.destroy()

    def test_download_button_label_is_download_on_windows(self, tk_root):
        panel = _make_panel(tk_root, can_install=True)
        assert panel._btn_dl_version.cget("text") == "Download"
        panel.destroy()

    def test_platform_hint_label_present_on_non_install(self, tk_root):
        panel = _make_panel(tk_root, can_install=False)
        assert hasattr(panel, "_lbl_platform")
        assert "Windows only" in panel._lbl_platform.cget("text")
        panel.destroy()

    def test_no_platform_hint_label_on_windows(self, tk_root):
        panel = _make_panel(tk_root, can_install=True)
        assert not hasattr(panel, "_lbl_platform")
        panel.destroy()

    def test_download_selected_opens_release_page_on_non_install(self, tk_root):
        """Selecting + 'downloading' on macOS/Linux opens the page, never DLs."""
        panel = _make_panel(tk_root, can_install=False)
        panel._version_list = [("3.3.0", "v3.3.0", False)]
        panel._selected_version.set("v3.3.0")

        with (
            patch(_CAN_INSTALL, return_value=False),
            patch.object(panel, "_download_in_app") as mock_dl,
            patch(_WEBBROWSER_OPEN) as mock_open,
        ):
            panel._on_download_selected_version()

        mock_dl.assert_not_called()
        mock_open.assert_called_once()
        panel.destroy()

    def test_download_in_app_redirects_to_release_page_on_non_install(
        self, tk_root
    ):
        """Even a direct _download_in_app call must not start a real download."""
        panel = _make_panel(tk_root, can_install=False)

        with (
            patch(_CAN_INSTALL, return_value=False),
            patch(_WEBBROWSER_OPEN) as mock_open,
            patch("gui.panels.version_panel.threading.Thread") as mock_thread,
        ):
            panel._download_in_app("3.3.0")

        mock_open.assert_called_once()
        mock_thread.assert_not_called()
        assert panel._busy_download is False
        panel.destroy()

    def test_update_done_with_version_opens_page_on_non_install(self, tk_root):
        """A found update on macOS/Linux offers the release page, not Download."""
        panel = _make_panel(tk_root, can_install=False)
        panel._busy_update = True

        with patch(_CAN_INSTALL, return_value=False):
            panel._update_done("Update available: V3.3.0", "#ffaa00", "3.3.0")

        assert "Release Page" in panel._btn_update.cget("text")

        # The button command now routes to the release page, not a download.
        cmd = panel._btn_update.cget("command")
        with patch(_WEBBROWSER_OPEN) as mock_open:
            cmd()
        mock_open.assert_called_once()
        panel.destroy()

    def test_open_release_page_calls_webbrowser(self, tk_root):
        panel = _make_panel(tk_root, can_install=False)
        with patch(_WEBBROWSER_OPEN) as mock_open:
            panel._open_release_page()
        mock_open.assert_called_once()
        # Targets the canonical GitHub releases page.
        assert "releases" in mock_open.call_args[0][0]
        panel.destroy()


# ---------------------------------------------------------------------------
# 11. Release notes ("What's New") rendering
#     (finding: "Version browser and updater show no release notes")
# ---------------------------------------------------------------------------

class TestVersionPanelReleaseNotes:
    """The version browser and update check surface release notes."""

    def test_notes_box_starts_hidden(self, tk_root):
        panel = _make_panel(tk_root)
        # Not packed (no manager) until there are notes to show.
        assert panel._notes_box.winfo_manager() == ""
        panel.destroy()

    def test_set_notes_shows_text(self, tk_root):
        panel = _make_panel(tk_root)
        panel._set_notes("- Fixed the thing\n- Added another thing")
        assert "Fixed the thing" in panel._notes_box.get("1.0", "end")
        assert panel._notes_box.winfo_manager() == "pack"
        panel.destroy()

    def test_set_notes_empty_hides_box(self, tk_root):
        panel = _make_panel(tk_root)
        panel._set_notes("notes here")
        panel._set_notes("")
        assert panel._notes_box.winfo_manager() == ""
        panel.destroy()

    def test_notes_box_is_read_only(self, tk_root):
        panel = _make_panel(tk_root)
        panel._set_notes("read only please")
        # CTkTextbox does not expose state via cget; check the inner Text.
        assert panel._notes_box._textbox.cget("state") == "disabled"
        panel.destroy()

    def test_truncate_notes_caps_length(self):
        from gui.panels.version_panel import _MAX_NOTES_CHARS, _truncate_notes

        long_body = "x" * (_MAX_NOTES_CHARS + 500)
        out = _truncate_notes(long_body)
        assert len(out) <= _MAX_NOTES_CHARS + len("\n…(truncated)")
        assert "truncated" in out

    def test_truncate_notes_handles_none(self):
        from gui.panels.version_panel import _truncate_notes

        assert _truncate_notes(None) == ""

    def test_show_versions_stores_and_renders_notes(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        versions = [
            ("3.3.0", "v3.3.0", False),
            ("3.2.0", "v3.2.0", False),
        ]
        notes = {"v3.3.0": "New in 3.3.0", "v3.2.0": "Old news"}

        panel._show_versions(versions, notes)

        # Latest version's notes render immediately.
        assert "New in 3.3.0" in panel._notes_box.get("1.0", "end")
        panel.destroy()

    def test_version_selection_swaps_notes(self, tk_root):
        panel = _make_panel(tk_root)
        panel._busy_browse = True
        versions = [
            ("3.3.0", "v3.3.0", False),
            ("3.2.0", "v3.2.0", False),
        ]
        notes = {"v3.3.0": "New in 3.3.0", "v3.2.0": "Old news 3.2.0"}
        panel._show_versions(versions, notes)

        panel._on_version_selected("v3.2.0")

        assert "Old news 3.2.0" in panel._notes_box.get("1.0", "end")
        panel.destroy()

    def test_browse_worker_extracts_body_from_releases(self, tk_root):
        """_on_browse_versions parses GitHub `body` into the notes map."""
        panel = _make_panel(tk_root)

        fake_releases = [
            {"tag_name": "v3.3.0", "prerelease": False,
             "body": "Release notes for 3.3.0"},
            {"tag_name": "v3.2.0", "prerelease": False, "body": ""},
        ]
        fake_resp = MagicMock()
        fake_resp.json.return_value = fake_releases
        fake_resp.raise_for_status.return_value = None

        captured = {}

        def _capture(_ms, fn, *args):
            captured["fn"] = fn
            captured["args"] = args

        with (
            patch(_HTTPX_GET, return_value=fake_resp),
            patch.object(panel, "_safe_after", side_effect=_capture),
        ):
            # Run the worker body synchronously by capturing the thread target.
            with patch("gui.panels.version_panel.threading.Thread") as mt:
                panel._on_browse_versions()
                worker = mt.call_args.kwargs["target"]
            worker()

        # _show_versions(versions, notes) was scheduled with the parsed body.
        versions, notes = captured["args"]
        assert notes["v3.3.0"] == "Release notes for 3.3.0"
        assert any(tag == "3.3.0" for tag, _, _ in versions)
        panel.destroy()

    def test_update_done_renders_notes(self, tk_root):
        panel = _make_panel(tk_root, can_install=True)
        panel._busy_update = True

        panel._update_done(
            "Update available: V3.3.0", "#ffaa00", "3.3.0",
            "What's new: faster everything",
        )

        assert "faster everything" in panel._notes_box.get("1.0", "end")
        panel.destroy()

    def test_fetch_release_notes_returns_body(self, tk_root):
        panel = _make_panel(tk_root)
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"body": "the changelog"}
        fake_resp.raise_for_status.return_value = None

        with patch(_HTTPX_GET, return_value=fake_resp):
            out = panel._fetch_release_notes("3.3.0")

        assert out == "the changelog"
        panel.destroy()

    def test_fetch_release_notes_swallows_errors(self, tk_root):
        import httpx

        panel = _make_panel(tk_root)
        with patch(_HTTPX_GET, side_effect=httpx.ConnectError("boom")):
            out = panel._fetch_release_notes("3.3.0")
        assert out == ""
        panel.destroy()


# ---------------------------------------------------------------------------
# 12. Restart-to-install batch guard + clean teardown
#     (finding: "Restart-to-install during a batch hard-exits, abandoning
#      in-flight openpyxl saves")
# ---------------------------------------------------------------------------

class TestVersionPanelRestartBatchGuard:
    """_do_restart refuses during a batch and routes through clean shutdown."""

    def test_is_batch_active_prefers_callback(self, tk_root):
        panel = _make_panel(tk_root, is_batch_active=lambda: True)
        assert panel._is_batch_active() is True
        panel.destroy()

    def test_is_batch_active_callback_false(self, tk_root):
        panel = _make_panel(tk_root, is_batch_active=lambda: False)
        assert panel._is_batch_active() is False
        panel.destroy()

    def test_is_batch_active_false_when_no_app_and_no_callback(self, tk_root):
        # No callback, no app ancestor with batch_handler → safely not active.
        panel = _make_panel(tk_root)
        assert panel._is_batch_active() is False
        panel.destroy()

    def test_is_batch_active_reads_handler_via_hierarchy(self, tk_root):
        """With no callback, the panel finds batch_handler._batch_active."""
        panel = _make_panel(tk_root)
        fake_handler = MagicMock()
        fake_handler._batch_active = True
        fake_app = MagicMock(batch_handler=fake_handler)
        with patch.object(panel, "_find_app", return_value=fake_app):
            assert panel._is_batch_active() is True
        panel.destroy()

    def test_do_restart_refused_during_batch_calls_on_error(self, tk_root):
        """A live batch blocks the restart and surfaces a message; no install."""
        error_cb = MagicMock()
        panel = _make_panel(
            tk_root, on_error=error_cb, is_batch_active=lambda: True
        )
        panel._pending_installer = "/tmp/installer.exe"

        with patch("core.auto_updater.apply_update") as mock_apply:
            panel._do_restart()

        mock_apply.assert_not_called()
        error_cb.assert_called_once()
        panel.destroy()

    def test_do_restart_refused_during_batch_messagebox_fallback(self, tk_root):
        """When on_error is None, the refusal still surfaces a native popup."""
        panel = _make_panel(
            tk_root, on_error=None, is_batch_active=lambda: True
        )
        panel._pending_installer = "/tmp/installer.exe"

        with (
            patch("core.auto_updater.apply_update") as mock_apply,
            patch("tkinter.messagebox.showwarning") as mock_warn,
        ):
            panel._do_restart()

        mock_apply.assert_not_called()
        mock_warn.assert_called_once()
        panel.destroy()

    def test_do_restart_routes_through_app_close_handler(self, tk_root):
        """No batch → teardown goes through app._on_app_close before install.

        _on_app_close runs ThreadRegistry.shutdown_all(), letting an in-flight
        save finish at its safe boundary before we apply the installer + exit.
        """
        panel = _make_panel(tk_root, is_batch_active=lambda: False)
        panel._pending_installer = "/tmp/installer.exe"
        panel._pending_sha256 = "abc123"

        close_handler = MagicMock()
        fake_app = MagicMock(_on_app_close=close_handler)

        call_order: list[str] = []
        close_handler.side_effect = lambda: call_order.append("close")

        def _apply(_installer, expected_sha256=None):
            call_order.append("apply")
            return {"success": True}

        # Mirror the real sys.exit by raising SystemExit so flow stops at the
        # hard exit instead of falling through to the normal-restart branch.
        with (
            patch.object(panel, "_find_app", return_value=fake_app),
            patch("pathlib.Path.is_file", return_value=True),
            patch("core.auto_updater.apply_update", side_effect=_apply),
            patch("gui.panels.version_panel.sys.exit",
                  side_effect=SystemExit(0)) as mock_exit,
            pytest.raises(SystemExit),
        ):
            panel._do_restart()

        # Clean shutdown happened BEFORE the installer ran (saves can finish).
        assert call_order == ["close", "apply"]
        mock_exit.assert_called_once_with(0)
        panel.destroy()

    def test_do_restart_falls_back_to_modal_destroy_without_app(self, tk_root):
        """No reachable app close handler → previous bare-destroy behavior."""
        panel = _make_panel(tk_root, is_batch_active=lambda: False)
        panel._pending_installer = "/tmp/installer.exe"
        panel._pending_sha256 = "abc123"

        fake_modal = MagicMock()

        with (
            patch.object(panel, "_find_app", return_value=None),
            patch.object(panel, "winfo_toplevel", return_value=fake_modal),
            patch("pathlib.Path.is_file", return_value=True),
            patch("core.auto_updater.apply_update",
                  return_value={"success": True}),
            patch("gui.panels.version_panel.sys.exit",
                  side_effect=SystemExit(0)) as mock_exit,
            pytest.raises(SystemExit),
        ):
            panel._do_restart()

        fake_modal.destroy.assert_called_once()
        mock_exit.assert_called_once_with(0)
        panel.destroy()


# ---------------------------------------------------------------------------
# Test API Connection — both gateway products
# ---------------------------------------------------------------------------

class TestPingProbesBothProducts:
    """The ping must exercise BOTH per-product keys, not just EXG.

    The BOT gateway scopes each key to one API product (live-verified:
    EXG key on the holiday endpoint → 403). A green ping that only checks
    the EXG key coexists with a batch whose mandatory holiday fetch fails —
    the exact 'Test is green but the API does not work' support case.
    """

    def _run_ping_worker(self, panel, fake_get, tokens):
        captured = {}

        def _capture(_ms, fn, *args):
            captured["fn"] = fn
            captured["args"] = args

        with (
            patch("core.api_client.httpx.get", side_effect=fake_get),
            patch("gui.panels.version_panel.get_token",
                  side_effect=lambda name: tokens.get(name)),
            patch.object(panel, "_safe_after", side_effect=_capture),
        ):
            with patch("gui.panels.version_panel.threading.Thread") as mt:
                panel._on_ping_api()
                worker = mt.call_args.kwargs["target"]
            worker()
        return captured

    def test_worker_probes_both_endpoints(self, tk_root):
        from core.api_client import EXG_RATE_PATH, HOLIDAY_PATH

        panel = _make_panel(tk_root)
        urls = []

        def _fake_get(url, *, headers, timeout):
            urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        self._run_ping_worker(panel, _fake_get, {
            "BOT_TOKEN_EXG": "EXGKEY123456",
            "BOT_TOKEN_HOL": "HOLKEY123456",
        })
        assert any(EXG_RATE_PATH in u for u in urls)
        assert any(HOLIDAY_PATH in u for u in urls)
        panel.destroy()

    def test_hol_rejection_fails_ping_even_when_exg_ok(self, tk_root):
        from core.api_client import EXG_RATE_PATH

        panel = _make_panel(tk_root)

        def _fake_get(url, *, headers, timeout):
            resp = MagicMock()
            resp.status_code = 200 if EXG_RATE_PATH in url else 401
            return resp

        captured = self._run_ping_worker(panel, _fake_get, {
            "BOT_TOKEN_EXG": "EXGKEY123456",
            "BOT_TOKEN_HOL": "REVOKEDHOL99",
        })
        text = captured["args"][0]
        assert "holiday" in text.lower()
        assert "ok: api connected & authenticated" not in text.lower()
        panel.destroy()

    def test_missing_hol_key_is_named_not_green(self, tk_root):
        panel = _make_panel(tk_root)

        def _fake_get(url, *, headers, timeout):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        captured = self._run_ping_worker(panel, _fake_get, {
            "BOT_TOKEN_EXG": "EXGKEY123456",
            "BOT_TOKEN_HOL": None,
        })
        text = captured["args"][0]
        assert "holiday" in text.lower()
        assert "ok: api connected & authenticated" not in text.lower()
        panel.destroy()
