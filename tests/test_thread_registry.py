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


class TestSameNameCollision:
    """round-11: register() used to key the dict by bare name, so a second
    same-name worker silently dropped tracking of the first live thread.
    Collisions with a LIVE different thread now get a unique -<n> suffix."""

    @staticmethod
    def _waiting_thread(stop):
        t = threading.Thread(target=stop.wait, args=(5,), daemon=True)
        t.start()
        return t

    def test_two_live_same_name_threads_both_tracked(self):
        reg = ThreadRegistry()
        stop = threading.Event()
        t1 = self._waiting_thread(stop)
        t2 = self._waiting_thread(stop)
        try:
            k1 = reg.register(t1, name="FolderResolveWorker")
            k2 = reg.register(t2, name="FolderResolveWorker")
            assert k1 != k2
            status = reg.status()
            assert status.get(k1) is True and status.get(k2) is True
        finally:
            stop.set()
        hung = reg.shutdown_all(timeout=5.0)
        assert hung == []

    def test_unregister_removes_only_own_key(self):
        """A finished worker's finally-unregister (by its OWN unique key)
        must not evict a newer same-name live thread — the reverse of the
        original bug."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t1 = self._waiting_thread(stop)
        t2 = self._waiting_thread(stop)
        try:
            k1 = reg.register(t1, name="Worker")
            k2 = reg.register(t2, name="Worker")
            reg.unregister(k1)
            status = reg.status()
            assert k1 not in status
            assert status.get(k2) is True
        finally:
            stop.set()
        reg.shutdown_all(timeout=5.0)

    def test_reregistering_same_thread_keeps_key(self):
        """Re-registering the SAME live thread object is idempotent — no
        suffix churn."""
        reg = ThreadRegistry()
        stop = threading.Event()
        t = self._waiting_thread(stop)
        try:
            k1 = reg.register(t, name="Ticker")
            k2 = reg.register(t, name="Ticker")
            assert k1 == k2 == "Ticker"
        finally:
            stop.set()
        reg.shutdown_all(timeout=5.0)

    def test_dead_thread_slot_is_reused_without_suffix(self):
        """A finished (not unregistered) thread's name is reclaimed — only
        LIVE threads force a suffix, so names don't grow unboundedly."""
        reg = ThreadRegistry()
        t1 = threading.Thread(target=lambda: None, daemon=True)
        t1.start()
        t1.join(5)
        reg.register(t1, name="OneShot")
        stop = threading.Event()
        t2 = self._waiting_thread(stop)
        try:
            k2 = reg.register(t2, name="OneShot")
            assert k2 == "OneShot"
        finally:
            stop.set()
        reg.shutdown_all(timeout=5.0)
