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
        _failed_box=None,
        file_queue=file_queue if file_queue is not None else [],
        last_processed_path=None,
        btn_process=_btn(),
        btn_revert=_btn(),
        btn_export_exrate=_btn(),
        btn_reveal=_btn(),
        lbl_status=MagicMock(),
        progressbar=MagicMock(),
        batch_handler=MagicMock(),
        update_idletasks=MagicMock(),
        _tray=MagicMock(),
        restore_from_tray=MagicMock(),
    )
    app._render_failed_files = MagicMock()
    app._unlock_ui_after_batch = lambda: BOTExrateApp._unlock_ui_after_batch(app)
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
        return SimpleNamespace(
            _batch_running=batch_running,
            _scheduled_run_active=False,
            _revert_running=revert_running,
            btn_process=_btn(),
            btn_revert=_btn(),
            btn_export_exrate=_btn(),
            btn_reveal=_btn(),
            progressbar=MagicMock(),
            lbl_status=MagicMock(),
            batch_handler=MagicMock(),
        )

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
        from gui.app import BOTExrateApp

        monkeypatch.setattr(
            "gui.app.filedialog.askopenfilename", lambda **k: "ledger.xlsx",
        )
        app = self._app()
        app._revert_running = False

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
