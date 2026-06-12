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


class TestPriorityRetain:
    """Fix #9: audit-critical error/success events must not be silently lost."""

    def test_error_events_survive_overflow(self):
        """Non-priority log events get evicted before error/success."""
        bus = EventBus(maxlen=3)
        bus.push({"type": "error", "msg": "E0"})
        bus.push({"type": "success", "msg": "S0"})
        # Flood with low-priority logs that should be evicted, not the above.
        for i in range(10):
            bus.push({"type": "log", "msg": f"L{i}"})
        events = bus.drain()
        types = [e["type"] for e in events]
        # The priority events must still be present.
        assert "error" in types
        assert "success" in types
        assert len(events) == 3

    def test_all_priority_full_emits_drop_marker(self):
        """When the queue is full of priority events, a visible marker is left
        instead of silently dropping audit lines."""
        bus = EventBus(maxlen=2)
        bus.push({"type": "error", "msg": "E0"})
        bus.push({"type": "error", "msg": "E1"})
        # No room and everything is priority -> oldest dropped + marker.
        bus.push({"type": "error", "msg": "E2"})
        events = bus.drain()
        msgs = [e.get("msg", "") for e in events]
        assert any("dropped" in m for m in msgs), msgs
        # Newest priority event is still retained.
        assert any(m == "E2" for m in msgs)
        # round-11: the fresh marker pays for its own slot — len stays at
        # maxlen (the old code peaked at maxlen + 1 here).
        assert len(events) <= 2

    def test_all_priority_overflow_never_exceeds_maxlen(self):
        """round-11 regression: under a sustained all-priority flood the
        queue length must stay bounded at maxlen at every step (the fresh
        drop marker used to add a slot: len hit maxlen + 1)."""
        bus = EventBus(maxlen=3)
        for i in range(10):
            bus.push({"type": "error", "msg": f"E{i}"})
            assert len(bus._queue) <= 3, f"after push {i}"
        events = bus.drain()
        # No audit line vanished uncounted: marker count + survivors >= 10.
        marker = next(e for e in events if e.get("_dropped"))
        survivors = [e for e in events if not e.get("_dropped")]
        assert marker["_dropped"] + len(survivors) == 10
        # The marker says plainly that priority lines were among the drops.
        assert "including error/success lines" in marker["msg"]

    def test_drop_marker_counts_accumulate(self):
        bus = EventBus(maxlen=2)
        bus.push({"type": "error", "msg": "E0"})
        bus.push({"type": "error", "msg": "E1"})
        bus.push({"type": "error", "msg": "E2"})
        bus.push({"type": "error", "msg": "E3"})
        events = bus.drain()
        markers = [e for e in events if "dropped" in e.get("msg", "")]
        assert markers, "expected a drop marker"
        assert markers[0].get("_dropped", 0) >= 2
