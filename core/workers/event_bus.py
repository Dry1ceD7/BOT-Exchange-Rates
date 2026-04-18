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
    """Thread-safe event bus using a simple list + lock.

    Args:
        maxlen: Maximum number of queued events. When exceeded, oldest
                events are silently dropped to prevent unbounded memory
                growth from a stuck or slow consumer.
    """

    MAX_QUEUE_WARNING = 500  # log warning at this threshold

    def __init__(self, maxlen: int = 2000):
        self._lock = threading.Lock()
        self._queue: List[Dict[str, Any]] = []
        self._maxlen = maxlen
        self._overflow_warned = False

    def push(self, event: Dict[str, Any]) -> None:
        """Push an event from any thread."""
        with self._lock:
            self._queue.append(event)
            qlen = len(self._queue)
            if qlen > self._maxlen:
                # Drop oldest events to stay within bounds
                excess = qlen - self._maxlen
                self._queue = self._queue[excess:]
                if not self._overflow_warned:
                    import logging
                    logging.getLogger(__name__).warning(
                        "EventBus overflow: dropped %d oldest events (maxlen=%d)",
                        excess, self._maxlen,
                    )
                    self._overflow_warned = True
            elif qlen >= self.MAX_QUEUE_WARNING and not self._overflow_warned:
                import logging
                logging.getLogger(__name__).warning(
                    "EventBus queue growing large: %d events pending", qlen,
                )


    def drain(self) -> List[Dict[str, Any]]:
        """Drain all pending events. Returns a list (may be empty)."""
        with self._lock:
            events = self._queue[:]
            self._queue.clear()
        return events
