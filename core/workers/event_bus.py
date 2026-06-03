#!/usr/bin/env python3
"""
core/workers/event_bus.py
---------------------------------------------------------------------------
Thread-safe Producer-Consumer event queue for GUI ↔ Worker communication.
---------------------------------------------------------------------------
Workers push structured events; the CTk main loop drains and renders them.
"""

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class EventBus:
    """Thread-safe event bus backed by a bounded deque.

    Args:
        maxlen: Maximum number of queued events. When exceeded, the oldest
                events are dropped in O(1) (deque drop-oldest) to prevent
                unbounded memory growth from a stuck or slow consumer.
    """

    MAX_QUEUE_WARNING = 500  # log warning at this threshold
    OVERFLOW_WARN_INTERVAL = 30.0  # seconds between overflow warnings

    def __init__(self, maxlen: int = 2000):
        self._lock = threading.Lock()
        self._maxlen = maxlen
        self._queue: deque = deque(maxlen=maxlen)
        self._dropped_since_warn = 0
        self._last_warn_ts = 0.0

    def push(self, event: Dict[str, Any]) -> None:
        """Push an event from any thread."""
        with self._lock:
            was_full = len(self._queue) >= self._maxlen
            # deque(maxlen) drops the oldest item automatically when full.
            self._queue.append(event)

            if was_full:
                self._dropped_since_warn += 1
                now = time.monotonic()
                # Throttle (don't latch): warn at most once per interval.
                if now - self._last_warn_ts >= self.OVERFLOW_WARN_INTERVAL:
                    logger.warning(
                        "EventBus overflow: dropped %d oldest events "
                        "since last warning (maxlen=%d)",
                        self._dropped_since_warn, self._maxlen,
                    )
                    self._dropped_since_warn = 0
                    self._last_warn_ts = now
            elif len(self._queue) >= self.MAX_QUEUE_WARNING:
                now = time.monotonic()
                if now - self._last_warn_ts >= self.OVERFLOW_WARN_INTERVAL:
                    logger.warning(
                        "EventBus queue growing large: %d events pending",
                        len(self._queue),
                    )
                    self._last_warn_ts = now

    def drain(self) -> List[Dict[str, Any]]:
        """Drain all pending events. Returns a list (may be empty)."""
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
        return events
