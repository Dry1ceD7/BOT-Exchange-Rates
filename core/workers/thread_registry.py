#!/usr/bin/env python3
"""
core/workers/thread_registry.py
---------------------------------------------------------------------------
Centralized thread lifecycle registry for the BOT Exchange Rate Processor.
---------------------------------------------------------------------------
Tracks all daemon threads launched by the GUI layer. Provides:
  - register() for new threads
  - shutdown_all() for clean shutdown with timeout
  - status() for diagnostics

SFFB: Strict < 100 lines.
"""

import logging
import threading
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ThreadRegistry:
    """Tracks and manages the lifecycle of all daemon threads.

    Usage:
        registry = ThreadRegistry()
        registry.register(thread, name="RateTickerWorker")
        ...
        registry.shutdown_all(timeout=5)
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._threads: Dict[str, threading.Thread] = {}
        self._stop_events: Dict[str, threading.Event] = {}

    def register(
        self,
        thread: threading.Thread,
        name: Optional[str] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> str:
        """Register a thread for lifecycle management.

        Returns the thread name used as key.
        """
        key = name or thread.name
        with self._lock:
            self._threads[key] = thread
            if stop_event:
                self._stop_events[key] = stop_event
        logger.debug("Thread registered: %s (alive=%s)", key, thread.is_alive())
        return key

    def unregister(self, name: str) -> None:
        """Remove a thread from the registry."""
        with self._lock:
            self._threads.pop(name, None)
            self._stop_events.pop(name, None)

    def shutdown_all(self, timeout: float = 5.0) -> List[str]:
        """Signal all stop events, then join all threads.

        Returns list of thread names that did not exit within timeout.
        """
        with self._lock:
            events = dict(self._stop_events)
            threads = dict(self._threads)

        # 1. Signal all stop events
        for name, evt in events.items():
            logger.debug("Signaling stop: %s", name)
            evt.set()

        # 2. Join all threads
        hung = []
        per_thread_timeout = timeout / max(len(threads), 1)
        for name, thread in threads.items():
            if thread.is_alive():
                thread.join(timeout=per_thread_timeout)
                if thread.is_alive():
                    hung.append(name)
                    logger.warning("Thread did not exit: %s", name)
                else:
                    logger.debug("Thread exited: %s", name)

        # 3. Clear registry
        with self._lock:
            self._threads.clear()
            self._stop_events.clear()

        return hung

    def status(self) -> Dict[str, bool]:
        """Return {name: is_alive} for all registered threads."""
        with self._lock:
            return {name: t.is_alive() for name, t in self._threads.items()}

    @property
    def active_count(self) -> int:
        """Number of currently alive registered threads."""
        with self._lock:
            return sum(1 for t in self._threads.values() if t.is_alive())
