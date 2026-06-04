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

    def test_properties(self):
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="14:30",
            watch_paths=["/tmp/test"],
            callback=lambda files: None,
        )
        assert scheduler.target_time == "14:30"
        assert scheduler.watch_paths == ["/tmp/test"]
        assert "14:30" in scheduler.next_run_info
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

    def test_update_config(self):
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="08:00",
            watch_paths=["/a"],
            callback=lambda f: None,
        )
        scheduler.update_config(time_str="09:00", watch_paths=["/b", "/c"])
        assert scheduler.target_time == "09:00"
        assert len(scheduler.watch_paths) == 2
        scheduler.stop()

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
        lock, so a concurrent update_config() cannot torn-read mid-scan.
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
