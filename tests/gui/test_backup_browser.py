#!/usr/bin/env python3
"""
tests/gui/test_backup_browser.py
---------------------------------------------------------------------------
Widget-level tests for gui/panels/backup_browser.py (BackupBrowser).

BackupBrowser is a CTkToplevel SafePanel that lists timestamped backups
grouped by original file and restores a SPECIFIC one (browse/restore-by-date).

Test strategy mirrors test_exrate_dialog.py:
  * Build the dialog against the session-scoped `tk_root` (auto-skips headless).
  * Patch CTkToplevel.grab_set / transient / update_idletasks to no-ops so the
    modal never blocks the test process or recalculates geometry.
  * Inject a BackupManager pointed at a tmp_path backup dir so NO real
    data/backups is read and NO workbook is ever opened (metadata only).
  * Never construct a real BOTClient — the browser does no network at all; the
    restore path is a single file copy run inline via a stubbed Thread.
"""

import contextlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backup_dir(tmp_path):
    """Create a BackupManager over a temp dir and seed a couple of backups.

    Returns (manager, backup_dir, source_filepath) where source_filepath is the
    most-recently backed-up original (its stem is used for restore targeting).
    """
    from core.backup_manager import BackupManager

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    mgr = BackupManager(backup_dir=str(backup_dir))

    src = tmp_path / "ledger.xlsx"
    src.write_bytes(b"ORIGINAL")
    mgr.create_backup(str(src))
    # A second backup of a different file so grouping is exercised.
    src2 = tmp_path / "invoice.xlsx"
    src2.write_bytes(b"INV")
    mgr.create_backup(str(src2))
    return mgr, backup_dir, src


def _open_browser(tk_root, mgr):
    """Instantiate BackupBrowser with patched modal calls; return the dialog."""
    import customtkinter as ctk

    from gui.panels.backup_browser import BackupBrowser

    with (
        patch.object(ctk.CTkToplevel, "grab_set", lambda self: None),
        patch.object(ctk.CTkToplevel, "transient", lambda self, *a: None),
        patch.object(ctk.CTkToplevel, "update_idletasks", lambda self: None),
    ):
        dialog = BackupBrowser(tk_root, backup_mgr=mgr)
        dialog.withdraw()
    return dialog


def _collect_radios(dialog):
    import customtkinter as ctk

    result = []

    def _walk(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkRadioButton):
                result.append(child)
            _walk(child)

    _walk(dialog)
    return result


def _find_button(dialog, fragment):
    import customtkinter as ctk

    result = []

    def _walk(w):
        for child in w.winfo_children():
            if isinstance(child, ctk.CTkButton) and (
                fragment.lower() in str(child.cget("text")).lower()
            ):
                result.append(child)
            _walk(child)

    _walk(dialog)
    return result[0] if result else None


# ---------------------------------------------------------------------------
# Creation + listing
# ---------------------------------------------------------------------------


class TestBackupBrowserCreation:
    def test_is_ctk_toplevel(self, tk_root, tmp_path):
        import customtkinter as ctk

        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        assert isinstance(dialog, ctk.CTkToplevel)
        dialog.destroy()

    def test_title(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        assert dialog.title() == "Backup Browser"
        dialog.destroy()

    def test_lists_one_radio_per_backup(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        # ledger + invoice = 2 backups -> 2 radio buttons.
        dialog = _open_browser(tk_root, mgr)
        radios = _collect_radios(dialog)
        assert len(radios) == 2
        dialog.destroy()

    def test_empty_store_shows_no_radios(self, tk_root, tmp_path):
        from core.backup_manager import BackupManager

        empty_dir = tmp_path / "empty_backups"
        empty_dir.mkdir()
        mgr = BackupManager(backup_dir=str(empty_dir))
        dialog = _open_browser(tk_root, mgr)
        assert _collect_radios(dialog) == []
        dialog.destroy()

    def test_restore_button_disabled_until_selection(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        btn = _find_button(dialog, "Restore Selected")
        assert btn is not None
        assert str(btn.cget("state")) == "disabled"
        dialog.destroy()

    def test_metadata_only_never_opens_workbook(self, tk_root, tmp_path):
        """The browser must list backups WITHOUT loading any .xlsx (#FW)."""
        import openpyxl

        mgr, _, _ = _make_backup_dir(tmp_path)
        with patch.object(
            openpyxl, "load_workbook",
            side_effect=AssertionError("workbook loaded — not featherweight"),
        ):
            dialog = _open_browser(tk_root, mgr)
            assert len(_collect_radios(dialog)) == 2
        dialog.destroy()


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class TestBackupBrowserSelection:
    def test_select_enables_restore(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        radios = _collect_radios(dialog)
        radios[0].invoke()  # select + fire command
        btn = _find_button(dialog, "Restore Selected")
        assert str(btn.cget("state")) == "normal"
        assert dialog._selected_path
        dialog.destroy()


# ---------------------------------------------------------------------------
# Escape closes
# ---------------------------------------------------------------------------


class TestBackupBrowserKeyBindings:
    def test_escape_binding_present(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        assert dialog.bind("<Escape>"), "Dialog must bind <Escape> to close"
        dialog.destroy()


# ---------------------------------------------------------------------------
# Restore path (busy-guard + confirm + dispatch)
# ---------------------------------------------------------------------------


def _select_first(dialog):
    radios = _collect_radios(dialog)
    radios[0].invoke()


class TestBackupBrowserRestore:
    def test_restore_blocked_while_batch_running(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        _select_first(dialog)
        tk_root._batch_running = True
        confirmed = []
        try:
            with patch(
                "tkinter.messagebox.askyesno",
                side_effect=lambda *a, **kw: confirmed.append(True) or True,
            ):
                dialog._on_restore()
            assert not confirmed, "Must not even prompt while a batch runs"
            # Dialog stays open so the operator can retry afterwards.
            assert dialog.winfo_exists() == 1
            assert "Busy" in str(dialog._status_label.cget("text"))
        finally:
            tk_root._batch_running = False
            with contextlib.suppress(Exception):
                dialog.destroy()

    def test_restore_blocked_while_revert_running(self, tk_root, tmp_path):
        mgr, _, _ = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        _select_first(dialog)
        tk_root._batch_running = False
        tk_root._revert_running = True
        try:
            with patch(
                "tkinter.messagebox.askyesno", return_value=True,
            ) as confirm:
                dialog._on_restore()
            confirm.assert_not_called()
            assert dialog.winfo_exists() == 1
        finally:
            tk_root._revert_running = False
            with contextlib.suppress(Exception):
                dialog.destroy()

    def test_cancel_confirm_does_not_restore(self, tk_root, tmp_path):
        mgr, _, src = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        # Make targeting deterministic: last_processed_path matches the stem.
        tk_root._batch_running = False
        tk_root._revert_running = False
        tk_root._exrate_running = False
        tk_root.last_processed_path = str(src)
        # Select the ledger backup specifically.
        for r in _collect_radios(dialog):
            if "ledger" not in str(r.cget("value")):
                continue
            r.invoke()
        started = []
        with (
            patch("tkinter.messagebox.askyesno", return_value=False),
            patch.object(mgr, "restore_specific",
                         side_effect=lambda *a: started.append(a)),
        ):
            dialog._on_restore()
        assert not started, "A declined confirmation must NOT restore"
        with contextlib.suppress(Exception):
            dialog.destroy()

    def test_confirmed_restore_dispatches_and_calls_success(
        self, tk_root, tmp_path,
    ):
        """A confirmed restore copies the chosen backup and routes the result
        through the app's revert-success callback (no real BOTClient/network)."""
        mgr, _, src = _make_backup_dir(tmp_path)
        # Corrupt the live file so we can prove the restore overwrote it.
        src.write_bytes(b"CORRUPTED")

        dialog = _open_browser(tk_root, mgr)
        tk_root._batch_running = False
        tk_root._revert_running = False
        tk_root._exrate_running = False
        tk_root.last_processed_path = str(src)
        tk_root.thread_registry = None
        # Stub the app's result callbacks + buttons the restore path touches.
        tk_root.btn_revert = MagicMock()
        tk_root.btn_process = MagicMock()
        success_calls = []
        flag_during_callback = []
        tk_root._show_revert_success = lambda fp, name: (
            flag_during_callback.append(tk_root._revert_running),
            success_calls.append((fp, name)),
        )
        tk_root._show_revert_error = lambda msg: success_calls.append(("ERR", msg))
        # Run app.after callbacks synchronously.
        tk_root.after = lambda _ms, fn=None, *a: fn(*a) if fn else None

        # Select the ledger backup (matches the src stem so no file picker).
        for r in _collect_radios(dialog):
            if "ledger" in str(r.cget("value")):
                r.invoke()
                break

        # Run the worker thread inline (forwarding target args).
        class _InlineThread:
            def __init__(self, target=None, args=(), **kw):
                self._target = target
                self._args = args

            def start(self):
                self._target(*self._args)

        with (
            patch("tkinter.messagebox.askyesno", return_value=True),
            patch("gui.panels.backup_browser.threading.Thread", _InlineThread),
        ):
            dialog._on_restore()

        # Live file restored to the pristine backup content.
        assert src.read_bytes() == b"ORIGINAL"
        # Success callback fired (not the error one).
        assert success_calls and success_calls[0][0] == str(src)
        assert "__bak__" in success_calls[0][1]
        # Busy flag was raised while the worker ran, so a racing scheduler
        # fire would have skipped. (The stub callback does not clear it like
        # the real _show_revert_success, so the F140 finally fail-safe does.)
        assert flag_during_callback == [True]
        assert tk_root._revert_running is False
        # Dialog closed before the worker ran.
        assert dialog.winfo_exists() == 0

    def test_busy_flag_raised_during_confirm_refuses_restore(
        self, tk_root, tmp_path,
    ):
        """F68 (TOCTOU): a scheduler batch starting WHILE the confirmation
        modal is open must make the restore refuse — the busy flags are
        re-checked after askyesno returns, before the worker is dispatched."""
        mgr, _, src = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        tk_root._batch_running = False
        tk_root._revert_running = False
        tk_root._exrate_running = False
        tk_root.last_processed_path = str(src)
        for r in _collect_radios(dialog):
            if "ledger" in str(r.cget("value")):
                r.invoke()
                break

        def _confirm_and_race(*_a, **_kw):
            # Simulate a scheduler fire starting a batch mid-modal-wait.
            tk_root._batch_running = True
            return True

        try:
            with (
                patch("tkinter.messagebox.askyesno",
                      side_effect=_confirm_and_race),
                patch.object(mgr, "restore_specific") as restore,
                patch("gui.panels.backup_browser.threading.Thread") as thread,
            ):
                dialog._on_restore()
            restore.assert_not_called()
            thread.assert_not_called()
            # The revert flag must NOT have been claimed on top of the batch.
            assert tk_root._revert_running is False
            # Dialog stays open with the busy message so the operator retries.
            assert dialog.winfo_exists() == 1
            assert "Busy" in str(dialog._status_label.cget("text"))
        finally:
            tk_root._batch_running = False
            with contextlib.suppress(Exception):
                dialog.destroy()

    def test_unexpected_worker_exception_clears_revert_flag(
        self, tk_root, tmp_path,
    ):
        """F140: an exception OUTSIDE (BackupError, OSError, ValueError) in the
        worker must not wedge app._revert_running — the finally fail-safe
        clears the flag, re-enables the buttons, and the error is surfaced
        through the existing _show_revert_error path."""
        mgr, _, src = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        tk_root._batch_running = False
        tk_root._revert_running = False
        tk_root._exrate_running = False
        tk_root.last_processed_path = str(src)
        tk_root.thread_registry = None
        tk_root.btn_revert = MagicMock()
        tk_root.btn_process = MagicMock()
        error_calls = []
        tk_root._show_revert_success = lambda fp, name: error_calls.append(
            ("OK", fp, name),
        )
        # Like the real callback, the stub does NOT clear the flag here so the
        # test proves the finally fail-safe is what recovers it.
        tk_root._show_revert_error = lambda msg: error_calls.append(msg)
        tk_root.after = lambda _ms, fn=None, *a: fn(*a) if fn else None
        # Exercise the preferred _safe_marshal route (exists on BOTExrateApp).
        tk_root._safe_marshal = lambda fn, *a: fn(*a)
        for r in _collect_radios(dialog):
            if "ledger" in str(r.cget("value")):
                r.invoke()
                break

        class _InlineThread:
            def __init__(self, target=None, args=(), **kw):
                self._target = target
                self._args = args

            def start(self):
                self._target(*self._args)

        try:
            with (
                patch("tkinter.messagebox.askyesno", return_value=True),
                patch("gui.panels.backup_browser.threading.Thread",
                      _InlineThread),
                patch.object(mgr, "restore_specific",
                             side_effect=TypeError("unexpected boom")),
            ):
                dialog._on_restore()
            # Flag recovered — Process/Revert are NOT dead until restart.
            assert tk_root._revert_running is False
            tk_root.btn_process.configure.assert_any_call(state="normal")
            tk_root.btn_revert.configure.assert_any_call(state="normal")
            # Surfaced via the existing error path, not swallowed.
            assert "unexpected boom" in error_calls
        finally:
            del tk_root._safe_marshal
            with contextlib.suppress(Exception):
                dialog.destroy()

    def test_confirm_dialog_shows_full_target_path(self, tk_root, tmp_path):
        """F139: stem-keyed targeting can propose an unrelated same-named
        file — the confirmation text must show the FULL target path so the
        operator can spot a wrong target before it is overwritten."""
        mgr, _, src = _make_backup_dir(tmp_path)
        dialog = _open_browser(tk_root, mgr)
        tk_root._batch_running = False
        tk_root._revert_running = False
        tk_root._exrate_running = False
        tk_root.last_processed_path = str(src)
        for r in _collect_radios(dialog):
            if "ledger" in str(r.cget("value")):
                r.invoke()
                break
        with patch(
            "tkinter.messagebox.askyesno", return_value=False,
        ) as confirm:
            dialog._on_restore()
        confirm.assert_called_once()
        message = confirm.call_args.args[1]
        assert str(src) in message, "Confirmation must show the full path"
        with contextlib.suppress(Exception):
            dialog.destroy()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestBackupBrowserFormatters:
    def test_human_size_bytes(self):
        from gui.panels.backup_browser import _human_size

        assert _human_size(512) == "512 B"

    def test_human_size_kb(self):
        from gui.panels.backup_browser import _human_size

        assert _human_size(2048) == "2.0 KB"

    def test_format_timestamp_none(self):
        from gui.panels.backup_browser import _format_timestamp

        assert _format_timestamp(None) == "unknown time"

    def test_format_timestamp_value(self):
        from gui.panels.backup_browser import _format_timestamp

        out = _format_timestamp(datetime(2026, 6, 4, 14, 32, 5))
        assert "2026" in out and "14:32" in out
