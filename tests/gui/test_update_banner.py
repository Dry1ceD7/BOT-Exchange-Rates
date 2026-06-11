#!/usr/bin/env python3
"""
tests/gui/test_update_banner.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/update_banner.py (UpdateManager).

UpdateManager is a non-widget controller that holds a reference to the app
and creates CTkFrame banners on top of it.  All tests:

  - Use the session-scoped tk_root fixture as the parent for real CTk widgets.
  - Pass tk_root ITSELF as the mock 'app' with a few extra attributes attached
    as instance variables.  This is required because CTkFont (called inside
    _show_banner) traverses the widget hierarchy all the way to the Tk root;
    if the parent is a MagicMock, mock's child-attribute machinery stalls in
    an infinite loop.
  - _safe_after is intercepted directly in tests that need to verify scheduling;
    the real _safe_after contract is tested in TestMarkDestroyed.
  - NEVER call tk_root.mainloop() — the session-scoped root already ran one
    cycle; calling it again hangs on macOS/aarch64.

Covered ideas:
  1. check_for_updates() spawns a daemon thread; _safe_after(_show_banner)
     is called when update_available=True (intercepted directly).
  2. _show_banner() creates a CTkFrame (_banner) with "Update Now" + close.
  3. _start_download() shows a confirmation banner with install directory path.
  4. _change_install_dir() uses filedialog.askdirectory to pick a path.
  5. _do_download() / _update_progress() manage the download progress label.
  6. _show_ready_to_install() creates an "Install & Restart" button.
  7. _execute_installer() calls apply_update() and schedules _exit_for_restart
     after 500 ms.
  8. mark_destroyed() prevents further _safe_after scheduling.
"""

import contextlib
import threading
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(tk_root):
    """Return a fresh UpdateManager using tk_root as the app.

    tk_root IS a real CTk widget so CTkFont can traverse the widget hierarchy
    to the Tk root without hitting a MagicMock (which would stall in mock's
    child-attribute machinery).

    We add the attributes UpdateManager references to tk_root as instance
    variables (cleaned up in teardown implicitly when tk_root is reused across
    tests — since we only ADD attributes, not remove them, duplicate sets are
    harmless for a session-scoped root).
    """
    from gui.panels.update_banner import UpdateManager

    # card must be a real widget so CTkFrame.pack(before=card) works.
    card = ctk.CTkFrame(tk_root)
    card.pack()
    tk_root.card = card                        # UpdateManager: self.app.card
    tk_root.lbl_status = MagicMock()           # UpdateManager: _dismiss path

    mgr = UpdateManager(tk_root)
    return mgr


def _collect_button_texts(widget) -> list[str]:
    """Recursively gather cget('text') from all CTkButton children."""
    texts: list[str] = []
    _walk_widgets(widget, ctk.CTkButton, texts)
    return texts


def _collect_label_texts(widget) -> list[str]:
    """Recursively gather cget('text') from all CTkLabel children."""
    texts: list[str] = []
    _walk_widgets(widget, ctk.CTkLabel, texts)
    return texts


def _walk_widgets(widget, widget_type, collector: list) -> None:
    """Walk the CTk widget tree, collecting text from matching widget types."""
    if isinstance(widget, widget_type):
        with contextlib.suppress(Exception):
            collector.append(widget.cget("text"))
    for child in widget.winfo_children():
        _walk_widgets(child, widget_type, collector)


# ---------------------------------------------------------------------------
# 1. check_for_updates — background thread → _safe_after(_show_banner)
# ---------------------------------------------------------------------------

class TestCheckForUpdates:
    """check_for_updates() conditionally spawns a thread that calls _safe_after."""

    def test_no_banner_when_auto_update_disabled(self, tk_root):
        """When auto_update=False in settings, _show_banner must NOT be called."""
        mgr = _make_manager(tk_root)
        mgr._show_banner = MagicMock()

        with patch("core.config_manager.SettingsManager") as MockSM:
            MockSM.return_value.load.return_value = {"auto_update": False}
            mgr.check_for_updates()

        mgr._show_banner.assert_not_called()

    def test_safe_after_called_when_update_available(self, tk_root):
        """When update_available=True, _safe_after(0, _show_banner, ver, url)
        is invoked.  We intercept _safe_after so we never touch mainloop."""
        mgr = _make_manager(tk_root)

        scheduled: list[tuple] = []
        mgr._safe_after = lambda ms, fn, *a: scheduled.append((ms, fn, a))

        done = threading.Event()

        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                if self._target:
                    self._target()
                done.set()

        with (
            patch("core.config_manager.SettingsManager") as MockSM,
            patch("core.auto_updater.check_for_update") as mock_check,
            patch("gui.panels.update_banner.threading.Thread", _SyncThread),
            patch("gui.panels.update_banner.sys.platform", "win32"),
        ):
            MockSM.return_value.load.return_value = {"auto_update": True}
            mock_check.return_value = {
                "update_available": True,
                "latest_version": "9.9.9",
                "download_url": "https://example.com/update",
            }
            mgr.check_for_updates()

        done.wait(timeout=2)

        assert len(scheduled) == 1, f"Expected 1 _safe_after call, got {scheduled}"
        ms, fn, args = scheduled[0]
        assert ms == 0
        # Bound methods compare unequal with `is` on each access; compare by name.
        assert fn.__name__ == "_show_banner"
        assert args[0] == "9.9.9"

    def test_no_safe_after_when_no_update_available(self, tk_root):
        """When update_available=False, _safe_after is not called."""
        mgr = _make_manager(tk_root)

        scheduled: list[tuple] = []
        mgr._safe_after = lambda ms, fn, *a: scheduled.append((ms, fn, a))

        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        with (
            patch("core.config_manager.SettingsManager") as MockSM,
            patch("core.auto_updater.check_for_update") as mock_check,
            patch("gui.panels.update_banner.threading.Thread", _SyncThread),
            patch("gui.panels.update_banner.sys.platform", "win32"),
        ):
            MockSM.return_value.load.return_value = {"auto_update": True}
            mock_check.return_value = {"update_available": False}
            mgr.check_for_updates()

        assert scheduled == []


# ---------------------------------------------------------------------------
# 2. _show_banner — creates CTkFrame with Update Now and close buttons
# ---------------------------------------------------------------------------

class TestShowBanner:
    """_show_banner() creates a visible banner with the expected child buttons."""

    def test_banner_frame_created(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.2.3", "https://example.com")
        assert mgr._banner is not None
        assert isinstance(mgr._banner, ctk.CTkFrame)

    def test_banner_contains_update_now_button(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.2.3", "https://example.com")

        all_text = _collect_button_texts(mgr._banner)
        assert any("Update Now" in t for t in all_text), (
            f"'Update Now' button not found; found: {all_text}"
        )

    def test_banner_contains_close_button(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.2.3", "https://example.com")

        all_text = _collect_button_texts(mgr._banner)
        assert any(t.strip() == "X" for t in all_text), (
            f"Close (X) button not found; found: {all_text}"
        )

    def test_banner_stores_pending_version(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("2.0.0", "https://example.com")
        assert mgr._pending_version == "2.0.0"

    def test_second_show_banner_replaces_first(self, tk_root):
        """Calling _show_banner twice destroys the old banner first."""
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        first_banner = mgr._banner
        mgr._show_banner("2.0.0", "https://example.com")
        assert mgr._banner is not first_banner
        assert mgr._pending_version == "2.0.0"


# ---------------------------------------------------------------------------
# 3. _start_download — confirmation banner shows install dir path
# ---------------------------------------------------------------------------

class TestStartDownload:
    """_start_download() re-uses _banner to show install-dir confirmation."""

    def test_install_dir_text_shown_in_banner(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        with patch("core.auto_updater.get_install_dir", return_value="/opt/bot"):
            mgr._start_download("1.0.0")

        all_labels = _collect_label_texts(mgr._banner)
        assert any("/opt/bot" in t for t in all_labels), (
            f"Install dir not found in banner labels: {all_labels}"
        )

    def test_install_button_present_in_confirmation(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        with patch("core.auto_updater.get_install_dir", return_value="/opt/bot"):
            mgr._start_download("1.0.0")

        all_text = _collect_button_texts(mgr._banner)
        assert any("Install" in t for t in all_text), (
            f"'Install' button not found; found: {all_text}"
        )

    def test_change_button_present_in_confirmation(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        with patch("core.auto_updater.get_install_dir", return_value="/opt/bot"):
            mgr._start_download("1.0.0")

        all_text = _collect_button_texts(mgr._banner)
        assert any("Change" in t for t in all_text), (
            f"'Change' button not found; found: {all_text}"
        )

    def test_long_path_truncated_in_label(self, tk_root):
        """Paths > 45 chars are displayed with a leading '...' prefix."""
        long_path = "/very/long/path/" + "x" * 50
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        with patch("core.auto_updater.get_install_dir", return_value=long_path):
            mgr._start_download("1.0.0")

        all_labels = _collect_label_texts(mgr._banner)
        assert any("..." in t for t in all_labels), (
            f"Truncated path not found in labels: {all_labels}"
        )

    def test_no_op_when_banner_is_none(self, tk_root):
        """_start_download should not raise if _banner is None."""
        mgr = _make_manager(tk_root)
        mgr._banner = None
        with patch("core.auto_updater.get_install_dir", return_value="/opt/bot"):
            mgr._start_download("1.0.0")  # must not raise


# ---------------------------------------------------------------------------
# 4. _change_install_dir — filedialog + re-trigger _start_download
# ---------------------------------------------------------------------------

class TestChangeInstallDir:
    """_change_install_dir() opens a folder dialog and updates _install_dir."""

    def _setup_with_banner(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._pending_version = "1.0.0"
        with patch("core.auto_updater.get_install_dir", return_value="/old"):
            mgr._start_download("1.0.0")
        return mgr

    def test_filedialog_called_with_askdirectory(self, tk_root):
        mgr = self._setup_with_banner(tk_root)

        with (
            patch("tkinter.filedialog.askdirectory", return_value="/new/path") as mock_fd,
            patch("core.auto_updater.get_install_dir", return_value="/new/path"),
        ):
            mgr._change_install_dir()
            mock_fd.assert_called_once()

    def test_install_dir_updated_when_dialog_returns_path(self, tk_root):
        mgr = self._setup_with_banner(tk_root)

        with (
            patch("tkinter.filedialog.askdirectory", return_value="/new/path"),
            patch("core.auto_updater.get_install_dir", return_value="/new/path"),
        ):
            mgr._change_install_dir()

        assert mgr._install_dir == "/new/path"

    def test_install_dir_unchanged_when_dialog_cancelled(self, tk_root):
        """Returning '' from askdirectory (cancel) must not change _install_dir."""
        mgr = self._setup_with_banner(tk_root)

        with patch("tkinter.filedialog.askdirectory", return_value=""):
            mgr._change_install_dir()

        assert mgr._install_dir == "/old"


# ---------------------------------------------------------------------------
# 5. _do_download / _update_progress — progress label updates
# ---------------------------------------------------------------------------

class TestDoDownload:
    """_do_download() and _update_progress() manage the download progress UI."""

    def test_update_progress_sets_label_text(self, tk_root):
        """_update_progress(pct) updates _dl_label with the percentage."""
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._dl_label = ctk.CTkLabel(mgr._banner, text="  Downloading update...")
        mgr._update_progress(42)
        assert "42%" in mgr._dl_label.cget("text")

    def test_update_progress_100_percent(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._dl_label = ctk.CTkLabel(mgr._banner, text="  Downloading update...")
        mgr._update_progress(100)
        assert "100%" in mgr._dl_label.cget("text")

    def test_update_progress_noop_when_no_label(self, tk_root):
        """_update_progress must not raise when _dl_label is None."""
        mgr = _make_manager(tk_root)
        mgr._dl_label = None
        mgr._update_progress(99)  # must not raise

    def test_do_download_creates_dl_label(self, tk_root):
        """_do_download() creates _dl_label before spawning the worker thread."""
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        asset = {
            "url": "https://objects.githubusercontent.com/update.exe",
            "filename": "update.exe",
            "sha256_url": "https://objects.githubusercontent.com/update.sha256",
            "error": None,
        }

        class _NoopThread:
            def __init__(self, target=None, daemon=None, **kw):
                pass

            def start(self):
                pass

        with (
            patch("gui.panels.update_banner.threading.Thread", _NoopThread),
            patch("core.auto_updater.get_installer_asset_url", return_value=asset),
        ):
            mgr._do_download("1.0.0")

        assert mgr._dl_label is not None
        assert isinstance(mgr._dl_label, ctk.CTkLabel)

    def test_do_download_schedules_show_ready_on_success(self, tk_root):
        """After a successful download, _safe_after(_show_ready_to_install) is called."""
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        scheduled: list[tuple] = []
        mgr._safe_after = lambda ms, fn, *a: scheduled.append((fn,) + a)

        asset = {
            "url": "https://objects.githubusercontent.com/update.exe",
            "filename": "update.exe",
            "sha256_url": "https://objects.githubusercontent.com/update.sha256",
            "error": None,
        }

        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        with (
            patch("gui.panels.update_banner.threading.Thread", _SyncThread),
            patch("core.auto_updater.get_installer_asset_url", return_value=asset),
            patch("core.auto_updater.fetch_expected_checksum", return_value="a" * 64),
            patch(
                "core.auto_updater.download_update",
                return_value={"path": "/tmp/update.exe", "error": None},
            ),
        ):
            mgr._do_download("1.0.0")

        fn_names = [fn.__name__ for fn, *_ in scheduled]
        assert "_show_ready_to_install" in fn_names, (
            f"_show_ready_to_install not scheduled; got: {fn_names}"
        )

    def test_do_download_schedules_show_error_on_failure(self, tk_root):
        """When download fails, _safe_after(_show_error) is called."""
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")

        scheduled: list[tuple] = []
        mgr._safe_after = lambda ms, fn, *a: scheduled.append((fn,) + a)

        asset = {
            "url": "https://objects.githubusercontent.com/update.exe",
            "filename": "update.exe",
            "sha256_url": "https://objects.githubusercontent.com/update.sha256",
            "error": None,
        }

        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                if self._target:
                    self._target()

        with (
            patch("gui.panels.update_banner.threading.Thread", _SyncThread),
            patch("core.auto_updater.get_installer_asset_url", return_value=asset),
            patch("core.auto_updater.fetch_expected_checksum", return_value="a" * 64),
            patch(
                "core.auto_updater.download_update",
                return_value={"path": None, "error": "checksum mismatch"},
            ),
        ):
            mgr._do_download("1.0.0")

        fn_names = [fn.__name__ for fn, *_ in scheduled]
        assert "_show_error" in fn_names, (
            f"_show_error not scheduled; got: {fn_names}"
        )


# ---------------------------------------------------------------------------
# 6. _show_ready_to_install — "Install & Restart" button
# ---------------------------------------------------------------------------

class TestShowReadyToInstall:
    """_show_ready_to_install() renders the final installation prompt."""

    def test_install_restart_button_present(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._show_ready_to_install("/tmp/update.exe")

        all_text = _collect_button_texts(mgr._banner)
        assert any("Install" in t and "Restart" in t for t in all_text), (
            f"'Install & Restart' button not found; found: {all_text}"
        )

    def test_later_button_present(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._show_ready_to_install("/tmp/update.exe")

        all_text = _collect_button_texts(mgr._banner)
        assert any("Later" in t for t in all_text), (
            f"'Later' button not found; found: {all_text}"
        )

    def test_downloaded_label_present(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._show_ready_to_install("/tmp/update.exe")

        all_labels = _collect_label_texts(mgr._banner)
        assert any(
            "downloaded" in t.lower() or "Update downloaded" in t
            for t in all_labels
        ), f"Download-ready label not found; labels: {all_labels}"


# ---------------------------------------------------------------------------
# 7. _execute_installer — apply_update + _exit_for_restart scheduling
# ---------------------------------------------------------------------------

class TestExecuteInstaller:
    """_execute_installer() calls apply_update and schedules exit after 500 ms."""

    def test_apply_update_called_with_installer_path(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._install_dir = "/opt/bot"
        mgr._pending_sha256 = "a" * 64
        mgr._safe_after = MagicMock()  # prevent real scheduling

        with patch("core.auto_updater.apply_update") as mock_apply:
            mock_apply.return_value = {"success": True, "error": None}
            mgr._execute_installer("/tmp/update.exe")

        mock_apply.assert_called_once_with(
            "/tmp/update.exe",
            install_dir="/opt/bot",
            expected_sha256="a" * 64,
        )

    def test_exit_for_restart_scheduled_after_500ms(self, tk_root):
        """_safe_after(500, _exit_for_restart) is invoked after apply_update."""
        mgr = _make_manager(tk_root)
        mgr._show_banner("1.0.0", "https://example.com")
        mgr._install_dir = "/opt/bot"
        mgr._pending_sha256 = "a" * 64

        scheduled: list[tuple] = []
        mgr._safe_after = lambda ms, fn, *a: scheduled.append((ms, fn))

        with patch("core.auto_updater.apply_update") as mock_apply:
            mock_apply.return_value = {"success": True, "error": None}
            mgr._execute_installer("/tmp/update.exe")

        assert any(
            ms == 500 and fn.__name__ == "_exit_for_restart"
            for ms, fn in scheduled
        ), f"_exit_for_restart not scheduled at 500 ms; got: {scheduled}"


# ---------------------------------------------------------------------------
# 8. mark_destroyed — prevents further _safe_after calls
# ---------------------------------------------------------------------------

class TestMarkDestroyed:
    """mark_destroyed() sets _destroyed=True, causing _safe_after to no-op."""

    def test_destroyed_flag_starts_false(self, tk_root):
        mgr = _make_manager(tk_root)
        assert mgr._destroyed is False

    def test_mark_destroyed_sets_flag(self, tk_root):
        mgr = _make_manager(tk_root)
        mgr.mark_destroyed()
        assert mgr._destroyed is True

    def test_safe_after_noop_after_mark_destroyed(self, tk_root):
        """The real _safe_after must silently skip once _destroyed is True.

        We do NOT intercept _safe_after here — we exercise the real implementation.
        After mark_destroyed(), calling _safe_after must not call tk_root.after.
        """
        mgr = _make_manager(tk_root)
        mgr.mark_destroyed()

        called = []
        # If _safe_after fires, it would call tk_root.after(0, fn) and fn would
        # append to called via tk's pending callbacks.
        mgr._safe_after(0, lambda: called.append(1))
        tk_root.update_idletasks()

        assert called == [], "_safe_after must be a no-op after mark_destroyed()"

    def test_safe_after_delegates_to_app_after_before_destroyed(self, tk_root):
        """Before mark_destroyed(), the real _safe_after calls app.after()."""
        mgr = _make_manager(tk_root)
        assert mgr._destroyed is False

        # Temporarily replace app.after with a recorder to confirm the call.
        original_after = tk_root.after
        calls = []

        def _record_after(ms, fn, *a):
            calls.append((ms, fn))
            # Also schedule it for real so Tcl state stays consistent.
            return original_after(ms, fn, *a)

        tk_root.after = _record_after
        try:
            mgr._safe_after(0, lambda: None)
        finally:
            tk_root.after = original_after

        assert len(calls) == 1
        assert calls[0][0] == 0
