#!/usr/bin/env python3
"""
core/workers/event_bus.py
---------------------------------------------------------------------------
Thread-safe Producer-Consumer event queue for GUI <-> Worker communication.
---------------------------------------------------------------------------
Workers push structured events; the CTk main loop drains and renders them.
"""

import logging
import threading
import time
from collections import deque
from typing import Any

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
    # Audit-critical event types that must never be silently evicted.
    PRIORITY_TYPES = ("error", "success")

    def __init__(self, maxlen: int = 2000):
        self._lock = threading.Lock()
        self._maxlen = maxlen
        # Unbounded deque; eviction is handled manually so priority events
        # (error/success) are retained even under overflow on slow hardware.
        self._queue: deque = deque()
        self._dropped_since_warn = 0
        self._last_warn_ts = 0.0

    def push(self, event: dict[str, Any]) -> None:
        """Push an event from any thread.

        On overflow, the oldest NON-priority event is evicted so audit-critical
        error/success lines survive. If the queue is full of priority events,
        a visible "N log lines dropped" marker is injected instead of silently
        discarding.
        """
        with self._lock:
            was_full = len(self._queue) >= self._maxlen
            if was_full:
                self._evict_one()
            self._queue.append(event)

            if was_full:
                self._dropped_since_warn += 1
                now = time.monotonic()
                # Throttle (don't latch): warn at most once per interval.
                if now - self._last_warn_ts >= self.OVERFLOW_WARN_INTERVAL:
                    logger.warning(
                        "EventBus overflow: dropped %d events "
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

    def _evict_one(self) -> None:
        """Evict one event to make room. Caller must hold the lock.

        Prefers dropping the oldest non-priority event. If every queued event
        is priority (error/success), drops the oldest priority event but leaves
        a visible "N log lines dropped" marker so the audit trail shows a gap
        rather than silently losing lines.
        """
        # Reuse an existing drop marker if present (so its count accumulates).
        existing_marker = None
        for ev in self._queue:
            if ev.get("_dropped"):
                existing_marker = ev
                break

        # Prefer evicting the oldest non-priority event that is NOT the marker.
        for i, ev in enumerate(self._queue):
            if ev.get("_dropped"):
                continue
            if ev.get("type") not in self.PRIORITY_TYPES:
                del self._queue[i]
                return

        # All remaining events are priority — drop the oldest priority event
        # and record the gap in a visible marker (audit trail shows a gap
        # rather than a silent loss).
        # popleft may remove the marker; guard by removing first priority event.
        for i, ev in enumerate(self._queue):
            if ev.get("type") in self.PRIORITY_TYPES:
                del self._queue[i]
                break
        if existing_marker is not None:
            existing_marker["_dropped"] += 1
            existing_marker["msg"] = (
                f"WARNING: {existing_marker['_dropped']} log lines dropped (queue full)"
            )
        else:
            self._queue.appendleft({
                "type": "warning",
                "msg": "WARNING: 1 log lines dropped (queue full)",
                "_dropped": 1,
            })

    def drain(self) -> list[dict[str, Any]]:
        """Drain all pending events. Returns a list (may be empty)."""
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
        return events
