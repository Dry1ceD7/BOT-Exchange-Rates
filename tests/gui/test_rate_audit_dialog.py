#!/usr/bin/env python3
"""GUI tests for the Rate Audit dialog: report construction, worker
marshalling, the _exrate_running lease lifecycle, and the guarded Revert.

The audit scan/correction logic itself is covered by tests/test_rate_audit*.py.
This module covers the UI side: the report Toplevel builds with and without
changes, the worker's callbacks route through app._safe_marshal, success and
raising worker paths release the _exrate_running lease, a picker cancel
releases it too, app._open_rate_audit refuses while busy, _poll_rate_audit_done
re-enables the buttons, and the report's Revert routes through the guarded
app entry. Tk-based tests require a display; tk_root skips on headless CI.
"""
import contextlib
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from core.i18n import tr
from core.rate_audit import RateAuditReport, RateChange
from gui.panels import rate_audit_dialog


def _report(*, with_changes: bool, applied: bool = True) -> RateAuditReport:
    r = RateAuditReport(
        file="/tmp/ledger.xlsx", scanned_rows=5, compared_cells=20,
        applied=applied, unverifiable=1,
    )
    r.backup_path = "/tmp/ledger.bak.xlsx"
    if with_changes:
        r.changes.append(RateChange(
            row=2, col=2, cell="B2", rate_date=date(2026, 5, 27),
            column_label="USD Buying TT Rate", currency="USD",
            rate_type="buying_transfer", old_value=Decimal("32.0000"),
            new_value=Decimal("32.4507"),
            reason="value 32.0000 != BOT buying_transfer 32.4507",
        ))
    return r


def _toplevels(root):
    return [w for w in root.winfo_children() if isinstance(w, ctk.CTkToplevel)]


def _find_button(root, text):
    """Depth-first walk for the CTkButton labelled ``text``."""
    stack = list(root.winfo_children())
    while stack:
        w = stack.pop()
        if isinstance(w, ctk.CTkButton):
            with contextlib.suppress(Exception):
                if w.cget("text") == text:
                    return w
        with contextlib.suppress(Exception):
            stack.extend(w.winfo_children())
    return None


def _destroy_toplevels(root):
    for w in _toplevels(root):
        with contextlib.suppress(Exception):
            w.destroy()


class TestReportDialog:
    def test_builds_with_changes(self, tk_root):
        rate_audit_dialog._show_report_dialog(
            tk_root, _report(with_changes=True), "/tmp/Audit_Log_x.csv"
        )
        tops = _toplevels(tk_root)
        assert tops, "report dialog Toplevel was not created"
        for w in tops:
            with contextlib.suppress(Exception):
                w.destroy()

    def test_builds_with_no_changes(self, tk_root):
        rate_audit_dialog._show_report_dialog(
            tk_root, _report(with_changes=False), None
        )
        tops = _toplevels(tk_root)
        assert tops, "report dialog Toplevel was not created"
        for w in tops:
            with contextlib.suppress(Exception):
                w.destroy()

    def test_set_status_is_safe_without_label(self, tk_root):
        # _set_status must no-op (not raise) when the app lacks lbl_status.
        from types import SimpleNamespace
        rate_audit_dialog._set_status(SimpleNamespace(), "hello")


class _MarshalApp:
    """Headless app stub recording _safe_marshal routing (no Tk needed)."""

    def __init__(self):
        self.marshal_calls = []
        self._exrate_running = True

    def _safe_marshal(self, func, *args):
        # Spy mirroring BOTExrateApp._safe_marshal: record, then run inline.
        self.marshal_calls.append((func, args))
        func(*args)


class _InlineThread:
    """threading.Thread stand-in that runs the target synchronously."""

    def __init__(self, target=None, **kw):
        self._target = target

    def start(self):
        self._target()


class _FakeAuditor:
    """StandaloneRateAuditor stub: one status tick, then a clean report."""

    def __init__(self, engine):
        pass

    async def run(self, filepath, apply=True, status_cb=None):
        if status_cb:
            status_cb("scanning ExRate sheet")
        return _report(with_changes=False)


def _stub_worker_deps(monkeypatch, auditor_factory):
    """Stub the network/engine stack so _launch_worker runs inline."""
    import sys
    import types

    fake_api = types.ModuleType("core.api_client")
    fake_api.CLIENT_TIMEOUT = 1.0
    fake_api.BOTClient = lambda *a, **kw: object()
    fake_engine = types.ModuleType("core.engine")
    fake_engine.LedgerEngine = lambda *a, **kw: object()
    monkeypatch.setitem(sys.modules, "core.api_client", fake_api)
    monkeypatch.setitem(sys.modules, "core.engine", fake_engine)

    import httpx

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    import core.rate_audit as rate_audit_mod

    monkeypatch.setattr(
        rate_audit_mod, "StandaloneRateAuditor", auditor_factory,
    )
    monkeypatch.setattr(
        rate_audit_dialog, "write_audit_csv", lambda r: "/tmp/audit.csv",
    )
    monkeypatch.setattr(rate_audit_dialog.threading, "Thread", _InlineThread)


class TestWorkerMarshalsThroughSafeMarshal:
    """F75: the worker's status/done callbacks must route through
    app._safe_marshal (closing-flag check + RuntimeError AND TclError
    suppression), never a raw app.after guarded only by
    contextlib.suppress(RuntimeError)."""

    def test_success_path_routes_status_and_done_ok(self, monkeypatch):
        app = _MarshalApp()
        shown = []
        _stub_worker_deps(monkeypatch, _FakeAuditor)
        monkeypatch.setattr(
            rate_audit_dialog, "_show_report_dialog",
            lambda *a: shown.append(a),
        )

        rate_audit_dialog._launch_worker(app, "/tmp/ledger.xlsx")

        names = [getattr(f, "__name__", "") for f, _ in app.marshal_calls]
        assert "_set_status" in names, names  # the worker status tick
        assert "_done_ok" in names, names     # the completion callback
        assert app._exrate_running is False
        assert shown, "report dialog not reached via the marshalled _done_ok"

    def test_error_path_routes_done_err(self, monkeypatch):
        class _BoomAuditor:
            def __init__(self, engine):
                pass

            async def run(self, *a, **kw):
                raise ValueError("scan failed")

        app = _MarshalApp()
        _stub_worker_deps(monkeypatch, _BoomAuditor)

        rate_audit_dialog._launch_worker(app, "/tmp/ledger.xlsx")

        names = [getattr(f, "__name__", "") for f, _ in app.marshal_calls]
        assert "_done_err" in names, names
        assert app._exrate_running is False

    def test_no_raw_app_after_left_in_module(self):
        """The sweep must leave no raw app.after(...) marshal in the module."""
        import inspect

        source = inspect.getsource(rate_audit_dialog)
        assert "app.after(" not in source
        assert "app._safe_marshal(" in source


class TestRevertGuard:
    """F11: the report dialog's Revert must route through the app's guarded
    entry (_start_guarded_revert), never call batch_handler.start_revert
    directly — the _exrate_running lease was released before the dialog shows,
    so only the guarded entry re-checks the busy flags."""

    def test_revert_routes_through_guarded_entry(self, tk_root):
        tk_root._start_guarded_revert = MagicMock(return_value=True)
        report = _report(with_changes=True)
        try:
            rate_audit_dialog._show_report_dialog(tk_root, report, None)
            btn = _find_button(tk_root, tr("rateaudit.btn_revert"))
            assert btn is not None, "Revert button missing from report dialog"
            btn.cget("command")()
            tk_root._start_guarded_revert.assert_called_once_with(
                report.file, report.backup_path,
            )
        finally:
            _destroy_toplevels(tk_root)
            del tk_root._start_guarded_revert

    def test_revert_refusal_keeps_dialog_open_and_says_so(self, tk_root):
        # Guarded entry refuses (e.g. a scheduler-fired batch owns the file):
        # the dialog must stay open and surface the refusal, not silently die.
        tk_root._start_guarded_revert = MagicMock(return_value=False)
        report = _report(with_changes=True)
        try:
            rate_audit_dialog._show_report_dialog(tk_root, report, None)
            btn = _find_button(tk_root, tr("rateaudit.btn_revert"))
            assert btn is not None
            btn.cget("command")()
            tops = [w for w in _toplevels(tk_root) if w.winfo_exists()]
            assert tops, "dialog was destroyed despite the revert refusal"
            # The refusal text lands in the dialog's own status label.
            labels = []
            stack = list(tops[0].winfo_children())
            while stack:
                w = stack.pop()
                if isinstance(w, ctk.CTkLabel):
                    with contextlib.suppress(Exception):
                        labels.append(str(w.cget("text")))
                with contextlib.suppress(Exception):
                    stack.extend(w.winfo_children())
            assert tr("rateaudit.revert_busy") in labels
        finally:
            _destroy_toplevels(tk_root)
            del tk_root._start_guarded_revert


# ---------------------------------------------------------------------------
# F169: worker lifecycle — the _exrate_running lease must ALWAYS be released
# ---------------------------------------------------------------------------
class TestWorkerLifecycle:
    """_launch_worker must clear _exrate_running on both the success and the
    raising path, and a file-picker cancel must release the lease the caller
    (app._open_rate_audit) acquired before the picker opened."""

    def test_success_path_clears_exrate_running(self, monkeypatch):
        app = _MarshalApp()
        _stub_worker_deps(monkeypatch, _FakeAuditor)
        monkeypatch.setattr(
            rate_audit_dialog, "_show_report_dialog", lambda *a: None,
        )
        assert app._exrate_running is True
        rate_audit_dialog._launch_worker(app, "/tmp/ledger.xlsx")
        assert app._exrate_running is False

    def test_raising_path_clears_exrate_running(self, monkeypatch):
        class _BoomAuditor:
            def __init__(self, engine):
                pass

            async def run(self, *a, **kw):
                raise ValueError("scan failed")

        app = _MarshalApp()
        _stub_worker_deps(monkeypatch, _BoomAuditor)
        assert app._exrate_running is True
        rate_audit_dialog._launch_worker(app, "/tmp/ledger.xlsx")
        assert app._exrate_running is False

    def test_picker_cancel_releases_guard_and_spawns_nothing(
        self, monkeypatch
    ):
        app = SimpleNamespace(_exrate_running=True)
        launched = []
        monkeypatch.setattr(
            rate_audit_dialog.filedialog, "askopenfilename",
            lambda **kw: "",
        )
        monkeypatch.setattr(
            rate_audit_dialog, "_launch_worker",
            lambda *a: launched.append(a),
        )
        rate_audit_dialog.show_rate_audit_dialog(app)
        assert app._exrate_running is False
        assert not launched, "worker spawned despite a cancelled picker"

    def test_picked_file_is_handed_to_the_worker(self, monkeypatch):
        app = SimpleNamespace(_exrate_running=True)
        launched = []
        monkeypatch.setattr(
            rate_audit_dialog.filedialog, "askopenfilename",
            lambda **kw: "/tmp/picked.xlsx",
        )
        monkeypatch.setattr(
            rate_audit_dialog, "_launch_worker",
            lambda *a: launched.append(a),
        )
        rate_audit_dialog.show_rate_audit_dialog(app)
        assert launched == [(app, "/tmp/picked.xlsx")]
        # The worker (stubbed here) owns releasing the lease from now on.
        assert app._exrate_running is True


# ---------------------------------------------------------------------------
# F169: app-side busy guard + poll loop (unbound methods on a stand-in self)
# ---------------------------------------------------------------------------
def _guard_app(**flags):
    app = SimpleNamespace(
        _batch_running=False,
        _revert_running=False,
        _exrate_running=False,
        _flash_busy_status=MagicMock(),
        btn_process=MagicMock(),
        btn_revert=MagicMock(),
        file_queue=[],
        after=MagicMock(),
        _refresh_revert_state=MagicMock(),
        _poll_rate_audit_done=MagicMock(),
    )
    for name, value in flags.items():
        setattr(app, name, value)
    return app


class TestOpenRateAuditBusyGuard:
    @pytest.mark.parametrize(
        "flag", ["_batch_running", "_revert_running", "_exrate_running"]
    )
    def test_refuses_while_busy(self, flag, monkeypatch):
        from gui.app import BOTExrateApp

        app = _guard_app(**{flag: True})
        opened = []
        monkeypatch.setattr(
            rate_audit_dialog, "show_rate_audit_dialog",
            lambda a: opened.append(a),
        )
        BOTExrateApp._open_rate_audit(app)
        app._flash_busy_status.assert_called_once()
        assert not opened, "dialog opened despite the busy guard"
        app.btn_process.configure.assert_not_called()
        # The lease is only taken when the audit actually starts.
        if flag != "_exrate_running":
            assert app._exrate_running is False

    def test_acquires_lease_and_locks_buttons_when_idle(self, monkeypatch):
        from gui.app import BOTExrateApp

        app = _guard_app()
        opened = []
        monkeypatch.setattr(
            rate_audit_dialog, "show_rate_audit_dialog",
            lambda a: opened.append(a),
        )
        BOTExrateApp._open_rate_audit(app)
        assert opened == [app]
        assert app._exrate_running is True
        app.btn_process.configure.assert_called_once_with(state="disabled")
        app.btn_revert.configure.assert_called_once_with(state="disabled")
        app._poll_rate_audit_done.assert_called_once()


class TestPollRateAuditDone:
    def test_keeps_polling_while_running(self):
        from gui.app import BOTExrateApp

        app = _guard_app(_exrate_running=True)
        BOTExrateApp._poll_rate_audit_done(app)
        app.after.assert_called_once()
        assert app.after.call_args[0][0] == 150
        app.btn_process.configure.assert_not_called()

    def test_reenables_process_button_when_done_with_queue(self):
        from gui.app import BOTExrateApp

        app = _guard_app(file_queue=["/tmp/a.xlsx"])
        BOTExrateApp._poll_rate_audit_done(app)
        app.after.assert_not_called()
        app.btn_process.configure.assert_called_once_with(state="normal")
        app._refresh_revert_state.assert_called_once()

    def test_keeps_process_disabled_when_queue_empty(self):
        from gui.app import BOTExrateApp

        app = _guard_app(file_queue=[])
        BOTExrateApp._poll_rate_audit_done(app)
        app.btn_process.configure.assert_called_once_with(state="disabled")
        app._refresh_revert_state.assert_called_once()
