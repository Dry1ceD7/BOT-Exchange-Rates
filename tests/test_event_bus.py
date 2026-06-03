#!/usr/bin/env python3
"""Tests for core/workers/event_bus.py — bounded, throttled EventBus."""

from core.workers.event_bus import EventBus


class TestEventBus:
    """Push/drain semantics and overflow behavior."""

    def test_push_and_drain(self):
        bus = EventBus(maxlen=10)
        bus.push({"n": 1})
        bus.push({"n": 2})
        events = bus.drain()
        assert events == [{"n": 1}, {"n": 2}]
        # Drain empties the queue.
        assert bus.drain() == []

    def test_bounded_drop_oldest(self):
        """Fix #7: deque(maxlen) drops the OLDEST events when full."""
        bus = EventBus(maxlen=3)
        for i in range(6):
            bus.push({"n": i})
        events = bus.drain()
        assert len(events) == 3
        # Oldest (0,1,2) dropped; newest (3,4,5) retained.
        assert [e["n"] for e in events] == [3, 4, 5]

    def test_warning_not_latched(self):
        """Fix #7: overflow warning is throttled by time, not latched forever.

        With the throttle interval reset to 0, every overflow push must be
        able to emit a fresh warning (no permanent latch).
        """
        bus = EventBus(maxlen=1)
        bus.OVERFLOW_WARN_INTERVAL = 0.0  # allow every warning
        warnings = []

        import logging
        handler = logging.Handler()
        handler.emit = lambda record: warnings.append(record.getMessage())
        logger = logging.getLogger("core.workers.event_bus")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
        try:
            for i in range(5):
                bus.push({"n": i})
        finally:
            logger.removeHandler(handler)

        # More than one overflow warning proves the flag did not latch.
        overflow_warnings = [w for w in warnings if "overflow" in w]
        assert len(overflow_warnings) >= 2
