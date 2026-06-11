#!/usr/bin/env python3
"""Tests for core/scheduler.py — Auto-Scheduler."""

import os
import time
from datetime import datetime
from unittest.mock import MagicMock

from core.scheduler import AutoScheduler


class TestAutoScheduler:
    """Test scheduler start, stop, and scan behavior."""

    def test_start_and_stop(self):
        scheduler = AutoScheduler()
        assert not scheduler.is_running

        scheduler.start(
            time_str="23:00",
            watch_paths=[],
            callback=lambda files: None,
        )
        assert scheduler.is_running

        scheduler.stop()
        assert not scheduler.is_running

    def test_start_captures_config(self):
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="14:30",
            watch_paths=["/tmp/test"],
            callback=lambda files: None,
        )
        assert scheduler._target_time == "14:30"
        assert scheduler._watch_paths == ["/tmp/test"]
        scheduler.stop()

    def test_scan_watch_paths(self, tmp_path):
        """Verify _scan_watch_paths finds only Excel files."""
        # Create test files
        (tmp_path / "ledger.xlsx").write_text("test")
        (tmp_path / "data.xlsm").write_text("test")
        (tmp_path / "readme.txt").write_text("test")
        (tmp_path / ".hidden.xlsx").write_text("test")  # hidden file

        scheduler = AutoScheduler()
        scheduler._watch_paths = [str(tmp_path)]

        files = scheduler._scan_watch_paths(scheduler._watch_paths)

        assert len(files) == 2
        names = [os.path.basename(f) for f in files]
        assert "ledger.xlsx" in names
        assert "data.xlsm" in names
        assert "readme.txt" not in names
        assert ".hidden.xlsx" not in names

    def test_scan_nonexistent_path(self):
        scheduler = AutoScheduler()
        scheduler._watch_paths = ["/nonexistent/path"]
        files = scheduler._scan_watch_paths(scheduler._watch_paths)
        assert files == []

    def test_callback_fires_at_time(self, tmp_path, monkeypatch):
        """Test that callback fires when current time matches.

        Time is frozen via a fake datetime so the captured target_time and
        the datetime.now() read inside _check_and_fire always agree (no
        wall-clock minute-rollover flakiness). Errors are NOT suppressed so a
        real failure surfaces instead of silently passing.
        """
        # Create a test file so scan finds something
        (tmp_path / "test.xlsx").write_text("test")

        frozen = datetime(2025, 1, 1, 23, 0, 0)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        monkeypatch.setattr("core.scheduler.datetime", _FrozenDatetime)

        callback = MagicMock()
        scheduler = AutoScheduler()
        scheduler._running = True
        scheduler._callback = callback
        scheduler._watch_paths = [str(tmp_path)]
        scheduler._last_run_date = None
        scheduler._target_time = frozen.strftime("%H:%M")

        scheduler._check_and_fire()

        # Callback should have been called with the discovered file
        callback.assert_called_once()
        scheduler.stop()

    def test_prevents_duplicate_run(self):
        """Test that scheduler doesn't fire twice on the same day."""
        scheduler = AutoScheduler()
        scheduler._running = True
        scheduler._last_run_date = "2025-01-01"

        # Even if time matches, should not fire
        callback = MagicMock()
        scheduler._callback = callback
        scheduler._target_time = "23:00"

        # This should be a no-op since last_run_date matches
        scheduler._last_run_date = time.strftime("%Y-%m-%d")
        scheduler._check_and_fire()
        callback.assert_not_called()
        scheduler.stop()

    def test_check_and_fire_marks_last_run_under_lock(
        self, tmp_path, monkeypatch
    ):
        """Fix: _check_and_fire snapshots shared state under the lock, writes
        _last_run_date back, and a second call on the same (frozen) day is a
        no-op — guarding against a double-fire race on the Timer thread.
        """
        (tmp_path / "test.xlsx").write_text("test")

        frozen = datetime(2025, 1, 1, 23, 0, 0)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        monkeypatch.setattr("core.scheduler.datetime", _FrozenDatetime)

        callback = MagicMock()
        scheduler = AutoScheduler()
        scheduler._running = True
        scheduler._callback = callback
        scheduler._watch_paths = [str(tmp_path)]
        scheduler._last_run_date = None
        scheduler._target_time = frozen.strftime("%H:%M")

        scheduler._check_and_fire()
        assert scheduler._last_run_date == "2025-01-01"
        callback.assert_called_once()

        # Same frozen day: duplicate-run guard must prevent a second fire.
        scheduler._check_and_fire()
        callback.assert_called_once()
        scheduler.stop()

    def test_check_and_fire_uses_watch_path_snapshot(
        self, tmp_path, monkeypatch
    ):
        """Fix: the callback receives the watch-path snapshot taken under the
        lock, so a concurrent start() cannot torn-read mid-scan.
        """
        (tmp_path / "test.xlsx").write_text("test")

        frozen = datetime(2025, 2, 2, 8, 30, 0)

        class _FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen

        monkeypatch.setattr("core.scheduler.datetime", _FrozenDatetime)

        received = []
        scheduler = AutoScheduler()
        scheduler._running = True
        scheduler._callback = received.extend
        scheduler._watch_paths = [str(tmp_path)]
        scheduler._last_run_date = None
        scheduler._target_time = frozen.strftime("%H:%M")

        scheduler._check_and_fire()

        assert len(received) == 1
        assert os.path.basename(received[0]) == "test.xlsx"
        scheduler.stop()

    def test_schedule_next_noop_after_stop(self):
        """Fix #6: a timer-thread call to _schedule_next after stop() must
        NOT install a new timer that survives stop().
        """
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="23:00",
            watch_paths=[],
            callback=lambda f: None,
        )
        scheduler.stop()
        assert not scheduler.is_running

        # Simulate the timer thread racing in after stop().
        scheduler._schedule_next()
        assert scheduler._timer is None

    def test_stop_cancels_timer(self):
        """After start, a live timer exists; stop() cancels and clears it."""
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="23:00",
            watch_paths=[],
            callback=lambda f: None,
        )
        assert scheduler._timer is not None
        scheduler.stop()
        assert scheduler._timer is None


# ---------------------------------------------------------------------------
# Finding: catch-up window + weekend/holiday skip (core/scheduler.py:183)
# ---------------------------------------------------------------------------


def _freeze(monkeypatch, dt: datetime) -> None:
    """Freeze core.scheduler.datetime.now() to a fixed datetime."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return dt

    monkeypatch.setattr("core.scheduler.datetime", _FrozenDatetime)


def _make_fired_scheduler(tmp_path, **kw):
    """A ready-to-fire scheduler with one ledger in the watch path."""
    (tmp_path / "test.xlsx").write_text("test")
    callback = MagicMock()
    scheduler = AutoScheduler()
    scheduler._running = True
    scheduler._callback = callback
    scheduler._watch_paths = [str(tmp_path)]
    scheduler._last_run_date = None
    for k, v in kw.items():
        setattr(scheduler, f"_{k}", v)
    return scheduler, callback


class TestSchedulerCatchUpWindow:
    """A slightly-late poll still triggers the day's run (no whole-day skip)."""

    def test_fires_when_poll_is_a_few_minutes_late(self, tmp_path, monkeypatch):
        # Target 23:00, but the poll only ran at 23:03 (PC was busy/asleep at
        # the exact minute). Old equality check would skip the day entirely.
        _freeze(monkeypatch, datetime(2025, 1, 1, 23, 3, 0))
        sched, cb = _make_fired_scheduler(tmp_path)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_called_once()
        assert sched._last_run_date == "2025-01-01"
        sched.stop()

    def test_does_not_fire_before_the_slot(self, tmp_path, monkeypatch):
        # 22:59 — one minute early. Must not fire yet.
        _freeze(monkeypatch, datetime(2025, 1, 1, 22, 59, 0))
        sched, cb = _make_fired_scheduler(tmp_path)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_not_called()
        assert sched._last_run_date is None
        sched.stop()

    def test_does_not_fire_after_catch_up_window_closes(
        self, tmp_path, monkeypatch
    ):
        # Slot 23:00, window 120 min -> closes 01:00. A poll at 06:00 next
        # morning must NOT fire the previous night's job.
        _freeze(monkeypatch, datetime(2025, 1, 2, 6, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_not_called()
        sched.stop()

    def test_fires_exactly_at_slot(self, tmp_path, monkeypatch):
        # Boundary: now == slot must fire (regression of the original behavior).
        _freeze(monkeypatch, datetime(2025, 1, 1, 8, 30, 0))
        sched, cb = _make_fired_scheduler(tmp_path)
        sched._target_time = "08:30"

        sched._check_and_fire()
        cb.assert_called_once()
        sched.stop()

    def test_malformed_target_time_never_fires(self, tmp_path, monkeypatch):
        _freeze(monkeypatch, datetime(2025, 1, 1, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path)
        sched._target_time = "not-a-time"

        sched._check_and_fire()
        cb.assert_not_called()
        sched.stop()

    def test_is_run_due_window_bound(self):
        sched = AutoScheduler()
        now = datetime(2025, 1, 1, 23, 30, 0)
        # 30 min after a 23:00 slot, inside the default 120-min window.
        assert sched._is_run_due(now, "23:00") is True
        # 30 min before the slot.
        assert sched._is_run_due(datetime(2025, 1, 1, 22, 30, 0), "23:00") is False


class TestSchedulerWeekendSkip:
    """skip_weekends marks the day done without firing the callback."""

    def test_weekend_slot_is_skipped(self, tmp_path, monkeypatch):
        # 2025-01-04 is a Saturday.
        _freeze(monkeypatch, datetime(2025, 1, 4, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_weekends=True)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_not_called()
        # The day is consumed so we don't retry across the catch-up window.
        assert sched._last_run_date == "2025-01-04"
        sched.stop()

    def test_sunday_slot_is_skipped(self, tmp_path, monkeypatch):
        # 2025-01-05 is a Sunday.
        _freeze(monkeypatch, datetime(2025, 1, 5, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_weekends=True)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_not_called()
        sched.stop()

    def test_weekday_still_fires_with_skip_weekends(self, tmp_path, monkeypatch):
        # 2025-01-06 is a Monday.
        _freeze(monkeypatch, datetime(2025, 1, 6, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_weekends=True)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_called_once()
        sched.stop()

    def test_weekend_fires_when_skip_disabled(self, tmp_path, monkeypatch):
        # Saturday, but skip_weekends defaults False -> still fires.
        _freeze(monkeypatch, datetime(2025, 1, 4, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path)
        sched._target_time = "23:00"

        sched._check_and_fire()
        cb.assert_called_once()
        sched.stop()

    def test_start_accepts_skip_flags(self):
        sched = AutoScheduler()
        sched.start(
            time_str="23:00",
            watch_paths=[],
            callback=lambda f: None,
            skip_weekends=True,
            skip_holidays=True,
        )
        assert sched._skip_weekends is True
        assert sched._skip_holidays is True
        sched.stop()


class TestSchedulerHolidaySkip:
    """skip_holidays consults the BOT holiday cache (read-only) and skips."""

    def test_holiday_slot_is_skipped(self, tmp_path, monkeypatch):
        _freeze(monkeypatch, datetime(2025, 12, 25, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_holidays=True)
        sched._target_time = "23:00"

        fake_cache = MagicMock()
        fake_cache.has_holidays_for_year.return_value = True
        fake_cache.get_holidays.return_value = [("2025-12-25", "Christmas")]
        monkeypatch.setattr("core.database.get_cache", lambda: fake_cache)

        sched._check_and_fire()
        cb.assert_not_called()
        assert sched._last_run_date == "2025-12-25"
        # Read-only: only the read accessors were touched.
        fake_cache.get_holidays.assert_called_once_with(2025)
        sched.stop()

    def test_non_holiday_still_fires(self, tmp_path, monkeypatch):
        _freeze(monkeypatch, datetime(2025, 6, 10, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_holidays=True)
        sched._target_time = "23:00"

        fake_cache = MagicMock()
        fake_cache.has_holidays_for_year.return_value = True
        fake_cache.get_holidays.return_value = [("2025-12-25", "Christmas")]
        monkeypatch.setattr("core.database.get_cache", lambda: fake_cache)

        sched._check_and_fire()
        cb.assert_called_once()
        sched.stop()

    def test_holiday_check_errors_are_ignored(self, tmp_path, monkeypatch):
        # A broken/empty cache must never block a scheduled run.
        _freeze(monkeypatch, datetime(2025, 6, 10, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_holidays=True)
        sched._target_time = "23:00"

        def _boom():
            raise RuntimeError("cache exploded")

        monkeypatch.setattr("core.database.get_cache", _boom)

        sched._check_and_fire()
        cb.assert_called_once()
        sched.stop()

    def test_no_cached_holidays_fires(self, tmp_path, monkeypatch):
        _freeze(monkeypatch, datetime(2025, 6, 10, 23, 0, 0))
        sched, cb = _make_fired_scheduler(tmp_path, skip_holidays=True)
        sched._target_time = "23:00"

        fake_cache = MagicMock()
        fake_cache.has_holidays_for_year.return_value = False
        monkeypatch.setattr("core.database.get_cache", lambda: fake_cache)

        sched._check_and_fire()
        cb.assert_called_once()
        fake_cache.get_holidays.assert_not_called()
        sched.stop()
