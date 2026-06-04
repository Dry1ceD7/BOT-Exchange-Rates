#!/usr/bin/env python3
"""
tests/gui/test_app.py
---------------------------------------------------------------------------
App-level tests for the scheduled-run feedback wiring on BOTExrateApp.

Constructing a real BOTExrateApp would spawn a second Tk interpreter alongside
the session-scoped tk_root (segfaults CTk on macOS/aarch64), so — mirroring
test_app_batch_state.py / test_app_shutdown.py — we invoke the unbound methods
against a minimal stand-in `self` carrying only the attributes each method
touches.

Covers the confirmed audit finding:

  [HIGH] Scheduled runs are invisible: no tray notification or results history
  when minimized. A scheduler-fired batch must, on completion, fire a tray
  balloon notification (Windows/pystray path; no-op elsewhere) with
  succeeded/failed counts, record a retrievable last-run summary, persist it,
  and pull the operator back to the window on failure. A manual run must do
  NONE of this.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.gui


def _btn():
    return MagicMock()


def _make_app(batch_running=False, scheduled=False, file_queue=None):
    """Minimal stand-in carrying the attrs the completion methods touch."""
    from gui.app import BOTExrateApp

    app = SimpleNamespace(
        _batch_running=batch_running,
        _scheduled_run_active=scheduled,
        _revert_running=False,
        _exrate_running=False,
        _failed_box=None,
        dnd_enabled=False,
        file_queue=file_queue if file_queue is not None else [],
        last_processed_path=None,
        btn_process=_btn(),
        btn_revert=_btn(),
        btn_export_exrate=_btn(),
        btn_reveal=_btn(),
        lbl_status=MagicMock(),
        lbl_queue=MagicMock(),
        dz_text=MagicMock(),
        dz_sub=MagicMock(),
        progressbar=MagicMock(),
        batch_handler=MagicMock(),
        update_idletasks=MagicMock(),
        _tray=MagicMock(),
        restore_from_tray=MagicMock(),
    )
    app._render_failed_files = MagicMock()
    app._unlock_ui_after_batch = lambda: BOTExrateApp._unlock_ui_after_batch(app)
    app._settle_progressbar = lambda value: BOTExrateApp._settle_progressbar(app, value)
    app._last_succeeded_path = (
        lambda errors: BOTExrateApp._last_succeeded_path(app, errors)
    )
    app._reset_queue_after_run = (
        lambda: BOTExrateApp._reset_queue_after_run(app)
    )
    app._announce_scheduled_run = (
        lambda success, fail: BOTExrateApp._announce_scheduled_run(
            app, success, fail
        )
    )
    return app


# ---------------------------------------------------------------------------
# _begin_scheduled_batch flags the run as scheduler-fired
# ---------------------------------------------------------------------------

class TestScheduledRunFlag:
    def test_begin_scheduled_batch_sets_scheduled_flag(self):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(
            _batch_running=False,
            _scheduled_run_active=False,
            _revert_running=False,
            btn_process=_btn(),
            btn_revert=_btn(),
            btn_export_exrate=_btn(),
            btn_reveal=_btn(),
            progressbar=MagicMock(),
            lbl_status=MagicMock(),
            batch_handler=MagicMock(),
        )
        app._lock_ui_for_batch = lambda: BOTExrateApp._lock_ui_for_batch(app)

        BOTExrateApp._begin_scheduled_batch(app, ["x.xlsx"], "2026-01-01")

        assert app._scheduled_run_active is True


# ---------------------------------------------------------------------------
# Scheduled batch must not collide with an in-flight manual revert (#3)
# ---------------------------------------------------------------------------

class TestScheduledBatchHonorsRevert:
    """start_revert spawns a RevertWorker that never touches _batch_running, so
    the scheduler's programmatic entry point must consult _revert_running to
    avoid two threads writing the same .xlsx (one processing, one restoring)."""

    def _app(self, *, batch_running=False, revert_running=False):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(
            _batch_running=batch_running,
            _scheduled_run_active=False,
            _revert_running=revert_running,
            _exrate_running=False,
            last_processed_path=None,
            backup_mgr=MagicMock(),
            btn_process=_btn(),
            btn_revert=_btn(),
            btn_export_exrate=_btn(),
            btn_reveal=_btn(),
            progressbar=MagicMock(),
            lbl_status=MagicMock(),
            batch_handler=MagicMock(),
        )
        app._flash_busy_status = lambda: BOTExrateApp._flash_busy_status(app)
        return app

    def test_scheduled_batch_skipped_while_revert_running(self):
        from gui.app import BOTExrateApp

        app = self._app(revert_running=True)
        app._lock_ui_for_batch = MagicMock()

        BOTExrateApp._begin_scheduled_batch(app, ["x.xlsx"], "2026-01-01")

        # No UI lock, no run flag, and crucially no batch dispatched onto the
        # workbook the RevertWorker is restoring.
        app._lock_ui_for_batch.assert_not_called()
        app.batch_handler.start_batch.assert_not_called()
        assert app._scheduled_run_active is False

    def test_scheduled_batch_proceeds_when_idle(self):
        from gui.app import BOTExrateApp

        app = self._app()
        app._lock_ui_for_batch = lambda: BOTExrateApp._lock_ui_for_batch(app)

        BOTExrateApp._begin_scheduled_batch(app, ["x.xlsx"], "2026-01-01")

        app.batch_handler.start_batch.assert_called_once()
        assert app._scheduled_run_active is True

    def test_on_revert_click_sets_revert_running(self, monkeypatch):
        from datetime import datetime

        from gui.app import BOTExrateApp

        monkeypatch.setattr(
            "gui.app.filedialog.askopenfilename", lambda **k: "ledger.xlsx",
        )
        # A backup exists and the operator confirms the overwrite (#5).
        monkeypatch.setattr(
            "gui.app.messagebox.askyesno", lambda *a, **k: True,
        )
        app = self._app()
        app._revert_running = False
        app.backup_mgr.list_backups.return_value = [
            "/data/backups/ledger__bak__20260101_120000.xlsx",
        ]
        app.backup_mgr._parse_backup_timestamp.return_value = datetime(
            2026, 1, 1, 12, 0, 0,
        )

        BOTExrateApp._on_revert_click(app)

        assert app._revert_running is True
        app.batch_handler.start_revert.assert_called_once_with("ledger.xlsx")

    def test_revert_terminal_paths_clear_flag(self):
        from gui.app import BOTExrateApp

        app = self._app(revert_running=True)
        app.last_processed_path = None
        BOTExrateApp._show_revert_success(app, "ledger.xlsx", "backup.xlsx")
        assert app._revert_running is False

        app._revert_running = True
        BOTExrateApp._show_revert_error(app, "boom")
        assert app._revert_running is False


# ---------------------------------------------------------------------------
# Completion path: scheduled run notifies; manual run does not
# ---------------------------------------------------------------------------

class TestScheduledRunNotification:
    def test_manual_run_does_not_notify(self, monkeypatch):
        from gui.app import BOTExrateApp

        announced = {"called": False}
        app = _make_app(batch_running=True, scheduled=False, file_queue=["a.xlsx"])
        app._announce_scheduled_run = (
            lambda success, fail: announced.__setitem__("called", True)
        )

        BOTExrateApp._show_batch_complete(app, success=3, fail=0, errors=[])

        assert announced["called"] is False
        assert app._scheduled_run_active is False

    def test_scheduled_run_announces(self):
        from gui.app import BOTExrateApp

        announced = {}
        app = _make_app(batch_running=True, scheduled=True, file_queue=["a.xlsx"])
        app._announce_scheduled_run = (
            lambda success, fail: announced.update(success=success, fail=fail)
        )

        BOTExrateApp._show_batch_complete(app, success=7, fail=1, errors=["b.xlsx: boom"])

        # The flag is cleared and the scheduled-run announcer fired with counts.
        assert app._scheduled_run_active is False
        assert announced == {"success": 7, "fail": 1}

    def test_announce_notifies_tray_and_records_summary(self, monkeypatch):
        from gui.app import BOTExrateApp

        recorded = {}
        monkeypatch.setattr(
            "gui.app._settings_mgr.set",
            lambda key, value: recorded.__setitem__(key, value),
        )
        app = _make_app(scheduled=True, file_queue=["a.xlsx"])

        BOTExrateApp._announce_scheduled_run(app, success=7, fail=1)

        # Tray balloon notification fired with both counts.
        app._tray.notify.assert_called_once()
        msg = app._tray.notify.call_args.args[0]
        assert "7" in msg
        assert "1 failed" in msg
        # A retrievable last-run summary recorded on the tray menu.
        app._tray.set_last_run.assert_called_once()
        assert "7 OK" in app._tray.set_last_run.call_args.args[0]
        # Last-run persisted to settings for the scheduler panel / next session.
        assert "scheduler_last_run" in recorded
        assert recorded["scheduler_last_run"]["success"] == 7
        assert recorded["scheduler_last_run"]["fail"] == 1

    def test_announce_restores_window_on_failure(self, monkeypatch):
        from gui.app import BOTExrateApp

        monkeypatch.setattr("gui.app._settings_mgr.set", lambda *a, **k: None)
        app = _make_app(scheduled=True, file_queue=["a.xlsx"])

        BOTExrateApp._announce_scheduled_run(app, success=2, fail=3)

        # A failed overnight run pulls the operator back to the window.
        app.restore_from_tray.assert_called_once()

    def test_announce_does_not_restore_on_full_success(self, monkeypatch):
        from gui.app import BOTExrateApp

        monkeypatch.setattr("gui.app._settings_mgr.set", lambda *a, **k: None)
        app = _make_app(scheduled=True, file_queue=["a.xlsx"])

        BOTExrateApp._announce_scheduled_run(app, success=5, fail=0)

        # All-clear runs leave the window minimised — no need to interrupt.
        app.restore_from_tray.assert_not_called()

    def test_announce_survives_missing_tray(self, monkeypatch):
        from gui.app import BOTExrateApp

        monkeypatch.setattr("gui.app._settings_mgr.set", lambda *a, **k: None)
        app = _make_app(scheduled=True, file_queue=["a.xlsx"])
        app._tray = None  # macOS/Linux/no-pystray build

        # Must not raise even though there is no tray to notify.
        BOTExrateApp._announce_scheduled_run(app, success=1, fail=0)


# ---------------------------------------------------------------------------
# Error terminal path: a scheduled run that errored must still surface
# ---------------------------------------------------------------------------

class TestScheduledRunError:
    def test_scheduled_error_notifies_and_restores(self, monkeypatch):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, scheduled=True)

        BOTExrateApp._show_error(app, "Network error — check your connection.")

        assert app._scheduled_run_active is False
        app._tray.notify.assert_called_once()
        assert "failed" in app._tray.notify.call_args.args[0].lower()
        app._tray.set_last_run.assert_called_once()
        app.restore_from_tray.assert_called_once()

    def test_manual_error_does_not_notify(self, monkeypatch):
        from gui.app import BOTExrateApp

        app = _make_app(batch_running=True, scheduled=False)

        BOTExrateApp._show_error(app, "boom")

        app._tray.notify.assert_not_called()
        app.restore_from_tray.assert_not_called()


# ---------------------------------------------------------------------------
# round-6 #1 — ExRate launch and batch/revert are mutually exclusive
# ---------------------------------------------------------------------------

class TestExrateConcurrencyGuard:
    def _exrate_app(self):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(
            _batch_running=False,
            _revert_running=False,
            _exrate_running=False,
            file_queue=[],
            btn_process=_btn(),
            btn_revert=_btn(),
            btn_export_exrate=_btn(),
            lbl_status=MagicMock(),
        )
        app._flash_busy_status = lambda: BOTExrateApp._flash_busy_status(app)
        return app

    def test_export_disables_process_and_revert(self, monkeypatch):
        import gui.panels.exrate_dialog as exd
        from gui.app import BOTExrateApp

        # Stub the dialog so no real Tk window is built, and the poller so the
        # test doesn't schedule a real after() loop.
        monkeypatch.setattr(exd, "show_exrate_dialog", lambda app: None)
        app = self._exrate_app()
        app._poll_exrate_done = MagicMock()

        BOTExrateApp._on_export_exrate(app)

        assert app._exrate_running is True
        app.btn_process.configure.assert_any_call(state="disabled")
        app.btn_revert.configure.assert_any_call(state="disabled")
        app._poll_exrate_done.assert_called_once()

    def test_export_refused_while_batch_running(self, monkeypatch):
        import gui.panels.exrate_dialog as exd
        from gui.app import BOTExrateApp

        opened = {"shown": False}
        monkeypatch.setattr(
            exd, "show_exrate_dialog",
            lambda app: opened.__setitem__("shown", True),
        )
        app = self._exrate_app()
        app._batch_running = True

        BOTExrateApp._on_export_exrate(app)

        # The dialog must never open and no second engine is set up.
        assert opened["shown"] is False
        assert app._exrate_running is False
        assert "Busy" in app.lbl_status.configure.call_args.kwargs["text"]

    def test_process_refused_while_exrate_running(self):
        from gui.app import BOTExrateApp

        app = self._exrate_app()
        app._exrate_running = True
        app.file_queue = ["a.xlsx"]
        app.batch_handler = MagicMock()
        app.auto_detect_var = MagicMock()
        app._lock_ui_for_batch = MagicMock()

        BOTExrateApp._on_process_click(app)

        # An ExRate run owns the cache/API — Process must not dispatch a batch.
        app._lock_ui_for_batch.assert_not_called()
        app.batch_handler.start_batch.assert_not_called()

    def test_poll_releases_when_dialog_closed_and_worker_idle(self):
        from gui.app import BOTExrateApp

        app = self._exrate_app()
        app._exrate_running = True
        app.file_queue = ["a.xlsx"]
        # No grab held (dialog gone) and export button back to normal.
        app.grab_current = lambda: None
        app.btn_export_exrate.cget = lambda key: "normal"
        app.after = MagicMock()

        BOTExrateApp._poll_exrate_done(app)

        assert app._exrate_running is False
        # Process re-enabled because a selection is queued; Revert re-enabled.
        app.btn_process.configure.assert_any_call(state="normal")
        app.btn_revert.configure.assert_any_call(state="normal")
        app.after.assert_not_called()

    def test_poll_keeps_lock_while_worker_busy(self):
        from gui.app import BOTExrateApp

        app = self._exrate_app()
        app._exrate_running = True
        app.grab_current = lambda: None
        # Export button still disabled => worker still running.
        app.btn_export_exrate.cget = lambda key: "disabled"
        app.after = MagicMock()
        # Bind the method so the re-arm reference (self._poll_exrate_done) resolves.
        app._poll_exrate_done = lambda: BOTExrateApp._poll_exrate_done(app)

        BOTExrateApp._poll_exrate_done(app)

        # Still busy — re-armed the poll, never released the sibling buttons.
        assert app._exrate_running is True
        app.after.assert_called_once()
        assert app.after.call_args.args[1] == app._poll_exrate_done


# ---------------------------------------------------------------------------
# round-6 #5 / #8 — revert confirmation, backup preview, .xlsm in picker
# ---------------------------------------------------------------------------

class TestRevertConfirmationAndFiletypes:
    def _revert_app(self):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(
            _batch_running=False,
            _revert_running=False,
            _exrate_running=False,
            last_processed_path=None,
            backup_mgr=MagicMock(),
            btn_process=_btn(),
            btn_revert=_btn(),
            progressbar=MagicMock(),
            lbl_status=MagicMock(),
            batch_handler=MagicMock(),
        )
        app._flash_busy_status = lambda: BOTExrateApp._flash_busy_status(app)
        return app

    def test_picker_includes_xlsm(self, monkeypatch):
        from gui.app import BOTExrateApp

        captured = {}

        def fake_picker(**kwargs):
            captured.update(kwargs)
            return ""  # cancel — we only inspect the filetypes

        monkeypatch.setattr("gui.app.filedialog.askopenfilename", fake_picker)
        app = self._revert_app()

        BOTExrateApp._on_revert_click(app)

        # The macro-enabled .xlsm extension the app supports is selectable (#8).
        excel_filter = captured["filetypes"][0][1]
        assert "*.xlsm" in excel_filter
        assert "*.xlsx" in excel_filter

    def test_confirmation_required_before_restore(self, monkeypatch):
        from datetime import datetime

        from gui.app import BOTExrateApp

        monkeypatch.setattr(
            "gui.app.filedialog.askopenfilename", lambda **k: "ledger.xlsx",
        )
        confirm = {"text": None}

        def fake_askyesno(title, text, **k):
            confirm["text"] = text
            return False  # operator declines

        monkeypatch.setattr("gui.app.messagebox.askyesno", fake_askyesno)
        app = self._revert_app()
        app.backup_mgr.list_backups.return_value = [
            "/data/backups/ledger__bak__20260604_143200.xlsx",
        ]
        app.backup_mgr._parse_backup_timestamp.return_value = datetime(
            2026, 6, 4, 14, 32, 0,
        )

        BOTExrateApp._on_revert_click(app)

        # The confirmation names the file AND the backup timestamp (#5)...
        assert "ledger.xlsx" in confirm["text"]
        assert "04 Jun 2026 14:32" in confirm["text"]
        # ...and declining means nothing is restored.
        app.batch_handler.start_revert.assert_not_called()
        assert app._revert_running is False

    def test_no_backup_warns_and_aborts(self, monkeypatch):
        from gui.app import BOTExrateApp

        monkeypatch.setattr(
            "gui.app.filedialog.askopenfilename", lambda **k: "ledger.xlsx",
        )
        warned = {"shown": False}
        monkeypatch.setattr(
            "gui.app.messagebox.showwarning",
            lambda *a, **k: warned.__setitem__("shown", True),
        )
        # askyesno must never be reached when there is no backup.
        monkeypatch.setattr(
            "gui.app.messagebox.askyesno",
            lambda *a, **k: pytest.fail("askyesno reached with no backup"),
        )
        app = self._revert_app()
        app.backup_mgr.list_backups.return_value = []

        BOTExrateApp._on_revert_click(app)

        assert warned["shown"] is True
        app.batch_handler.start_revert.assert_not_called()
        assert app._revert_running is False

    def test_revert_refused_while_exrate_running(self, monkeypatch):
        from gui.app import BOTExrateApp

        picker = MagicMock()
        monkeypatch.setattr("gui.app.filedialog.askopenfilename", picker)
        app = self._revert_app()
        app._exrate_running = True

        BOTExrateApp._on_revert_click(app)

        # The picker must never even open while ExRate owns the cache/API (#1).
        picker.assert_not_called()
        assert app._revert_running is False


# ---------------------------------------------------------------------------
# round-8 — _on_scheduler_start forwards skip_weekends / skip_holidays
# ---------------------------------------------------------------------------

class TestSchedulerStartForwardsFlags:
    """_on_scheduler_start must pass skip_weekends and skip_holidays to
    AutoScheduler.start(). Backward-compat: both flags default to False."""

    def _sched_app(self):
        """Minimal stand-in that mimics the scheduler-start surface."""
        return SimpleNamespace(
            _auto_scheduler=MagicMock(),
        )

    def test_skip_weekends_forwarded(self, monkeypatch):
        from gui.app import BOTExrateApp

        stub = MagicMock()
        monkeypatch.setattr("gui.app.AutoScheduler", lambda: stub, raising=False)

        app = SimpleNamespace()

        # Patch the import inside the method.
        import core.scheduler as sched_mod
        stub_instance = MagicMock()
        monkeypatch.setattr(sched_mod, "AutoScheduler", lambda: stub_instance)

        BOTExrateApp._on_scheduler_start(
            app, "23:00", ["/p"], skip_weekends=True, skip_holidays=False
        )

        stub_instance.start.assert_called_once()
        _, kwargs = stub_instance.start.call_args
        assert kwargs.get("skip_weekends") is True
        assert kwargs.get("skip_holidays") is False

    def test_skip_holidays_forwarded(self, monkeypatch):
        import core.scheduler as sched_mod
        from gui.app import BOTExrateApp
        stub_instance = MagicMock()
        monkeypatch.setattr(sched_mod, "AutoScheduler", lambda: stub_instance)

        app = SimpleNamespace()

        BOTExrateApp._on_scheduler_start(
            app, "23:00", ["/p"], skip_weekends=False, skip_holidays=True
        )

        stub_instance.start.assert_called_once()
        _, kwargs = stub_instance.start.call_args
        assert kwargs.get("skip_weekends") is False
        assert kwargs.get("skip_holidays") is True

    def test_both_flags_default_to_false(self, monkeypatch):
        """Calling without explicit flags must default to False (backward compat)."""
        import core.scheduler as sched_mod
        from gui.app import BOTExrateApp
        stub_instance = MagicMock()
        monkeypatch.setattr(sched_mod, "AutoScheduler", lambda: stub_instance)

        app = SimpleNamespace()

        BOTExrateApp._on_scheduler_start(app, "23:00", ["/p"])

        stub_instance.start.assert_called_once()
        _, kwargs = stub_instance.start.call_args
        assert kwargs.get("skip_weekends") is False
        assert kwargs.get("skip_holidays") is False

    def test_reuses_existing_auto_scheduler(self, monkeypatch):
        """If _auto_scheduler already exists it is reused, not replaced."""
        from gui.app import BOTExrateApp

        existing = MagicMock()
        app = SimpleNamespace(_auto_scheduler=existing)

        # AutoScheduler constructor must NOT be called when one already exists.
        constructed = {"called": False}

        import core.scheduler as sched_mod
        monkeypatch.setattr(
            sched_mod, "AutoScheduler",
            lambda: constructed.__setitem__("called", True) or MagicMock(),
        )

        BOTExrateApp._on_scheduler_start(
            app, "23:00", ["/p"], skip_weekends=True, skip_holidays=True
        )

        assert constructed["called"] is False
        existing.start.assert_called_once()
        _, kwargs = existing.start.call_args
        assert kwargs.get("skip_weekends") is True
        assert kwargs.get("skip_holidays") is True


# ---------------------------------------------------------------------------
# round-6 #6 — window has a hard minimum and a screen-fitted default height
# ---------------------------------------------------------------------------

class TestWindowSizing:
    def test_fit_default_height_clamps_to_short_screen(self):
        from gui.app import BOTExrateApp

        # A 768px laptop must not keep the full 960px default.
        app = SimpleNamespace(winfo_screenheight=lambda: 768)
        fitted = BOTExrateApp._fit_default_height(app, 960)
        assert fitted < 960
        assert fitted <= 768 - 80

    def test_fit_default_height_keeps_default_on_tall_screen(self):
        from gui.app import BOTExrateApp

        app = SimpleNamespace(winfo_screenheight=lambda: 1440)
        assert BOTExrateApp._fit_default_height(app, 960) == 960

    def test_fit_default_height_never_below_minimum(self):
        from gui.app import BOTExrateApp

        # A pathologically short screen still yields the usable 640px floor.
        app = SimpleNamespace(winfo_screenheight=lambda: 600)
        assert BOTExrateApp._fit_default_height(app, 960) == 640
