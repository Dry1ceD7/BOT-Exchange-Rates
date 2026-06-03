#!/usr/bin/env python3
"""Tests for core/workers/thread_registry.py — lifecycle + shutdown timeout."""

import threading
import time

from core.workers.thread_registry import ThreadRegistry


class TestThreadRegistry:
    """register/unregister/status and shutdown_all timeout behavior."""

    def test_register_and_status(self):
        reg = ThreadRegistry()
        evt = threading.Event()
        t = threading.Thread(target=evt.wait, daemon=True)
        t.start()
        reg.register(t, name="worker", stop_event=evt)

        assert "worker" in reg.status()
        assert reg.active_count == 1

        evt.set()
        reg.shutdown_all(timeout=2.0)
        assert reg.status() == {}

    def test_shutdown_joins_cooperative_threads(self):
        reg = ThreadRegistry()

        def loop(stop: threading.Event):
            while not stop.is_set():
                time.sleep(0.01)

        for i in range(3):
            evt = threading.Event()
            t = threading.Thread(target=loop, args=(evt,), daemon=True)
            t.start()
            reg.register(t, name=f"w{i}", stop_event=evt)

        hung = reg.shutdown_all(timeout=2.0)
        assert hung == []

    def test_shutdown_uses_total_deadline_not_per_thread(self):
        """Fix #8: timeout is a shared deadline, not divided per thread.

        With many cooperative threads that each take ~0.2s to notice the stop
        event, a per-thread budget of timeout/N would be far too small and
        produce false "hung" reports. A shared deadline must let them all exit.
        """
        reg = ThreadRegistry()
        n = 10

        def loop(stop: threading.Event):
            # Poll slowly so a tiny per-thread budget would expire.
            while not stop.is_set():
                time.sleep(0.2)

        for i in range(n):
            evt = threading.Event()
            t = threading.Thread(target=loop, args=(evt,), daemon=True)
            t.start()
            reg.register(t, name=f"slow{i}", stop_event=evt)

        # Total budget 3s is ample; per-thread (3/10=0.3s) would also be tight
        # but the shared deadline lets earlier joins free up time for later ones.
        hung = reg.shutdown_all(timeout=3.0)
        assert hung == []

    def test_unregister(self):
        reg = ThreadRegistry()
        t = threading.Thread(target=lambda: None)
        reg.register(t, name="gone")
        reg.unregister("gone")
        assert "gone" not in reg.status()
