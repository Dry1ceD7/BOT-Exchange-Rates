#!/usr/bin/env python3
"""
core/workers/event_bus.py
---------------------------------------------------------------------------
Thread-safe Producer-Consumer event queue for GUI ↔ Worker communication.
---------------------------------------------------------------------------
Workers push structured events; the CTk main loop drains and renders them.
"""

import threading
from typing import Any, Dict, List


class EventBus:
    """Thread-safe event bus using a simple list + lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self._queue: List[Dict[str, Any]] = []

    def push(self, event: Dict[str, Any]) -> None:
        """Push an event from any thread."""
        with self._lock:
            self._queue.append(event)

    def drain(self) -> List[Dict[str, Any]]:
        """Drain all pending events. Returns a list (may be empty)."""
        with self._lock:
            events = self._queue[:]
            self._queue.clear()
        return events
