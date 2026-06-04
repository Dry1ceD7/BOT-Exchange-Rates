#!/usr/bin/env python3
"""
tests/gui/test_handlers_cancel.py
---------------------------------------------------------------------------
Fix #3: batch cancellation wiring in gui/handlers.py BatchHandler.

start_batch must create a threading.Event and register it with the
ThreadRegistry under the BatchWorker key so ThreadRegistry.shutdown_all()
can SET it on app close — letting the worker stop at a safe boundary
(between files, after wb.close) instead of being killed mid-save.

BatchHandler takes an injectable ``app`` (only needs an ``after`` method)
and an injectable ``event_bus``/``registry``, so these are fully testable
without a Tk root and stay green on headless CI.
"""

import threading
import time

from core.workers.thread_registry import ThreadRegistry
from gui.handlers import BatchHandler


class FakeApp:
    """Records after() calls instead of scheduling them on a Tk loop."""

    def __init__(self):
        self.after_calls = []

    def after(self, ms, func, *args):
        self.after_calls.append((ms, func, args))

    def _show_error(self, *a):
        pass

    def _update_progress(self, *a):
        pass

    def _show_batch_complete(self, *a):
        pass


def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestStopEventRegistration:
    """start_batch registers a stop_event with the ThreadRegistry."""

    def test_stop_event_registered_under_batchworker(self):
        registry = ThreadRegistry()
        handler = BatchHandler(FakeApp(), registry=registry)

        captured = {}

        def capture_thread(file_queue, start_date, dry_run=False,
                           stop_event=None):
            captured["stop_event"] = stop_event

        handler._batch_thread = capture_thread
        handler.start_batch(["a.xlsx"], "2025-01-01")

        # A threading.Event reached the worker target...
        assert _wait_until(lambda: "stop_event" in captured)
        assert isinstance(captured["stop_event"], threading.Event)
        # ...and the SAME event is held by the registry under BatchWorker so
        # shutdown_all() can signal it.
        assert "BatchWorker" in registry._stop_events
        assert registry._stop_events["BatchWorker"] is captured["stop_event"]

    def test_shutdown_all_sets_the_batch_stop_event(self):
        registry = ThreadRegistry()
        handler = BatchHandler(FakeApp(), registry=registry)

        captured = {}
        release = threading.Event()

        def blocking_thread(file_queue, start_date, dry_run=False,
                            stop_event=None):
            captured["stop_event"] = stop_event
            # Hold the worker alive until shutdown_all has set the event.
            release.wait(timeout=3)
            with handler._batch_lock:
                handler._batch_active = False

        handler._batch_thread = blocking_thread
        handler.start_batch(["a.xlsx"], "2025-01-01")
        assert _wait_until(lambda: "stop_event" in captured)

        stop_event = captured["stop_event"]
        assert not stop_event.is_set()

        # shutdown_all signals every registered stop_event before joining.
        release.set()  # let the (now-signalled) worker exit promptly
        registry.shutdown_all(timeout=2)
        assert stop_event.is_set()


class TestStopEventThreadedToEngine:
    """The stop_event is forwarded into LedgerEngine.process_batch."""

    def test_run_batch_forwards_stop_event(self, monkeypatch):
        import asyncio

        handler = BatchHandler(FakeApp())

        forwarded = {}

        class _FakeEngine:
            def __init__(self, *a, **k):
                # Mirror the real LedgerEngine contract: handlers reads
                # engine.last_audit_path after process_batch returns.
                self.last_audit_path = None

            async def process_batch(self, file_queue, start_date=None,
                                    progress_cb=None, dry_run=False,
                                    stop_event=None):
                forwarded["stop_event"] = stop_event
                return 0, 0, []

        # Patch the engine + API the async executor instantiates.
        monkeypatch.setattr("gui.handlers.LedgerEngine", _FakeEngine)
        monkeypatch.setattr(
            "gui.handlers.BOTClient", lambda client: object(),
        )

        sentinel = threading.Event()
        asyncio.run(handler._run_batch(
            ["a.xlsx"], "2025-01-01", dry_run=True, stop_event=sentinel,
        ))
        assert forwarded["stop_event"] is sentinel
