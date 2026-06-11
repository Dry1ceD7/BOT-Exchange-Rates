#!/usr/bin/env python3
"""
tests/test_handlers.py
---------------------------------------------------------------------------
Headless tests for gui/handlers.py BatchHandler.

BatchHandler takes an injectable ``app`` (only needs an ``after`` method) and
an injectable ``event_bus``, so it is fully testable without a Tk root.
"""

import asyncio
import threading
import time

from core.workers.event_bus import EventBus
from gui.handlers import BatchHandler


class FakeApp:
    """Records after() calls instead of scheduling them on a Tk loop."""

    def __init__(self):
        self.after_calls = []
        self.marshal_calls = []

    def after(self, ms, func, *args):
        self.after_calls.append((ms, func, args))

    def _safe_marshal(self, func, *args):
        # Spy mirroring BOTExrateApp._safe_marshal: record the routing, then
        # dispatch via after(0, ...) exactly like the real helper.
        self.marshal_calls.append((func, args))
        self.after(0, func, *args)

    # Callback targets referenced by the handler (no-ops here).
    def _show_error(self, *a):
        pass

    def _update_progress(self, *a):
        pass

    def _show_batch_complete(self, *a):
        pass

    def _show_revert_success(self, *a):
        pass

    def _show_revert_error(self, *a):
        pass


def _drain_types(bus):
    return [e.get("type") for e in bus.drain()]


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestConcurrencyGuard:
    """A second concurrent start_batch must be rejected."""

    def test_second_concurrent_start_rejected(self):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        release = threading.Event()
        started = threading.Event()

        def slow_thread(file_queue, start_date, dry_run=False, stop_event=None):
            started.set()
            release.wait(timeout=3)
            with handler._batch_lock:
                handler._batch_active = False

        # Replace the real worker with a controllable stub.
        handler._batch_thread = slow_thread

        handler.start_batch(["a.xlsx"], "2025-01-01")
        assert started.wait(timeout=2), "first batch should start"

        # Second start while the first is active -> rejected.
        events_before = bus.drain()
        handler.start_batch(["b.xlsx"], "2025-01-02")
        types = _drain_types(bus)
        assert "warning" in types, f"expected reject warning, got {types}"
        assert handler._batch_active is True

        release.set()
        assert _wait_until(lambda: handler._batch_active is False)
        # The first start pushed a 'log' event.
        assert any(e.get("type") == "log" for e in events_before)

    def test_guard_released_after_completion_allows_restart(self):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        calls = []

        def fast_thread(file_queue, start_date, dry_run=False, stop_event=None):
            calls.append(list(file_queue))
            with handler._batch_lock:
                handler._batch_active = False

        handler._batch_thread = fast_thread
        handler.start_batch(["a.xlsx"], "2025-01-01")
        assert _wait_until(lambda: handler._batch_active is False)
        handler.start_batch(["b.xlsx"], "2025-01-02")
        assert _wait_until(lambda: handler._batch_active is False)
        assert calls == [["a.xlsx"], ["b.xlsx"]]


class TestBatchEvents:
    """start_batch pushes the correct log/dry-run events."""

    def test_start_batch_pushes_log(self):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)
        handler._batch_thread = lambda *a, **k: None  # no-op worker

        handler.start_batch(["a.xlsx", "b.xlsx"], "2025-01-01")
        msgs = [e.get("msg", "") for e in bus.drain()]
        assert any("Starting batch: 2 ledger" in m for m in msgs)

    def test_dry_run_warning_pushed(self):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)
        handler._batch_thread = lambda *a, **k: None

        handler.start_batch(["a.xlsx"], "2025-01-01", dry_run=True)
        msgs = [e.get("msg", "") for e in bus.drain()]
        assert any("SIMULATION" in m for m in msgs)
        assert any("DRY RUN" in m for m in msgs)

    def test_snapshot_isolates_caller_mutation(self):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)
        seen = []
        handler._batch_thread = (
            lambda fq, sd, dry_run=False, stop_event=None: seen.append(list(fq))
        )

        queue = ["a.xlsx"]
        handler.start_batch(queue, "2025-01-01")
        assert _wait_until(lambda: len(seen) == 1)
        queue.append("b.xlsx")  # mutate after start
        assert seen[0] == ["a.xlsx"]


class TestAuditLogSurfacing:
    """The GUI batch path must surface the engine-written audit CSV so the
    auditor-facing log is no longer CLI-only (CLI/GUI parity)."""

    def _run(self, handler, monkeypatch, audit_path):
        """Drive _run_batch with a fake engine that mimics process_batch."""
        import gui.handlers as handlers_mod

        class FakeClient:
            def __init__(self, *a, **k):
                pass

        class FakeEngine:
            def __init__(self, *a, **k):
                self.last_audit_path = audit_path

            async def process_batch(self, *a, **k):
                return (1, 0, [])

        # BOTClient reads tokens from the keychain/.env at construction;
        # CI has none, so stub it out alongside the engine.
        monkeypatch.setattr(handlers_mod, "BOTClient", FakeClient)
        monkeypatch.setattr(handlers_mod, "LedgerEngine", FakeEngine)
        asyncio.run(handler._run_batch(["a.xlsx"], "2025-01-01"))

    def test_audit_path_pushed_to_console(self, monkeypatch):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        self._run(handler, monkeypatch, "/tmp/data/logs/Audit_Log_X.csv")
        msgs = [e.get("msg", "") for e in bus.drain()]
        assert any(
            "Audit log:" in m and "Audit_Log_X.csv" in m for m in msgs
        ), msgs

    def test_no_audit_message_when_path_missing(self, monkeypatch):
        """A dry run (last_audit_path is None) must not announce an audit log."""
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        self._run(handler, monkeypatch, None)
        msgs = [e.get("msg", "") for e in bus.drain()]
        assert not any("Audit log:" in m for m in msgs), msgs


class TestSafeMarshalRouting:
    """F67: every worker-thread -> Tk callback must route through the app's
    _safe_marshal helper (closing-flag check + RuntimeError AND TclError
    suppression), never through a raw self.app.after guarded only by
    'except RuntimeError'."""

    def _marshalled(self, app):
        return [func for func, _args in app.marshal_calls]

    def test_progress_and_completion_route_through_safe_marshal(
        self, monkeypatch,
    ):
        import gui.handlers as handlers_mod

        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        class FakeEngine:
            def __init__(self, *a, **k):
                self.last_audit_path = None

            async def process_batch(self, file_queue, start_date=None,
                                    progress_cb=None, dry_run=False,
                                    stop_event=None):
                progress_cb(1, 1, "a.xlsx", None)
                return (1, 0, [])

        monkeypatch.setattr(handlers_mod, "BOTClient", lambda c: object())
        monkeypatch.setattr(handlers_mod, "LedgerEngine", FakeEngine)
        asyncio.run(handler._run_batch(["a.xlsx"], "2025-01-01"))

        marshalled = self._marshalled(app)
        assert app._update_progress in marshalled
        assert app._show_batch_complete in marshalled

    def test_completion_payload_passed_intact(self, monkeypatch):
        """The (success, fail, errors, dry_run) payload survives the marshal."""
        import gui.handlers as handlers_mod

        app = FakeApp()
        handler = BatchHandler(app, event_bus=EventBus())
        errors = [("a.xlsx", "boom")]

        class FakeEngine:
            def __init__(self, *a, **k):
                self.last_audit_path = None

            async def process_batch(self, *a, **k):
                return (2, 1, errors)

        monkeypatch.setattr(handlers_mod, "BOTClient", lambda c: object())
        monkeypatch.setattr(handlers_mod, "LedgerEngine", FakeEngine)
        asyncio.run(handler._run_batch(["a.xlsx"], "2025-01-01", dry_run=True))

        payloads = [
            args for func, args in app.marshal_calls
            if func == app._show_batch_complete
        ]
        assert payloads == [(2, 1, errors, True)]

    def test_batch_error_routes_through_safe_marshal(self):
        app = FakeApp()
        handler = BatchHandler(app, event_bus=EventBus())

        async def boom(*a, **k):
            raise ValueError("kaput")

        handler._run_batch = boom
        handler._batch_thread(["a.xlsx"], "2025-01-01")

        assert app._show_error in self._marshalled(app)
        assert any(
            func == app._show_error and args == ("kaput",)
            for func, args in app.marshal_calls
        )

    def test_revert_success_routes_through_safe_marshal(
        self, tmp_path, monkeypatch,
    ):
        app = FakeApp()
        handler = BatchHandler(app, event_bus=EventBus())

        import core.backup_manager as bm

        class FakeBackupMgr:
            def restore_latest(self, filepath):
                return str(tmp_path / "ledger_20250101_120000.xlsx")

        monkeypatch.setattr(bm, "BackupManager", FakeBackupMgr)
        handler._revert_thread(str(tmp_path / "ledger.xlsx"))
        assert app._show_revert_success in self._marshalled(app)

    def test_revert_error_routes_through_safe_marshal(
        self, tmp_path, monkeypatch,
    ):
        app = FakeApp()
        handler = BatchHandler(app, event_bus=EventBus())

        import core.backup_manager as bm

        class FakeBackupMgr:
            def restore_latest(self, filepath):
                raise bm.BackupError("no backup found")

        monkeypatch.setattr(bm, "BackupManager", FakeBackupMgr)
        handler._revert_thread(str(tmp_path / "ledger.xlsx"))
        assert app._show_revert_error in self._marshalled(app)

    def test_no_raw_after_guarded_only_by_runtimeerror(self):
        """The module must not retain raw self.app.after(...) call sites —
        the sweep replaced them all with self.app._safe_marshal(...)."""
        import inspect

        import gui.handlers as handlers_mod

        source = inspect.getsource(handlers_mod)
        assert "self.app.after(" not in source
        assert "self.app._safe_marshal(" in source


class TestRevertEvents:
    """start_revert success/error push the correct events."""

    def test_revert_success_event(self, tmp_path, monkeypatch):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        import core.backup_manager as bm

        class FakeBackupMgr:
            def restore_latest(self, filepath):
                return str(tmp_path / "ledger_20250101_120000.xlsx")

        monkeypatch.setattr(bm, "BackupManager", FakeBackupMgr)

        handler._revert_thread(str(tmp_path / "ledger.xlsx"))
        types = _drain_types(bus)
        assert "success" in types
        # A completion callback was scheduled on the app.
        assert any(c[1] == app._show_revert_success for c in app.after_calls)

    def test_revert_error_event(self, tmp_path, monkeypatch):
        app = FakeApp()
        bus = EventBus()
        handler = BatchHandler(app, event_bus=bus)

        import core.backup_manager as bm

        class FakeBackupMgr:
            def restore_latest(self, filepath):
                raise bm.BackupError("no backup found")

        monkeypatch.setattr(bm, "BackupManager", FakeBackupMgr)

        handler._revert_thread(str(tmp_path / "ledger.xlsx"))
        events = bus.drain()
        types = [e.get("type") for e in events]
        assert "error" in types
        assert any("Revert failed" in e.get("msg", "") for e in events)
        assert any(c[1] == app._show_revert_error for c in app.after_calls)
