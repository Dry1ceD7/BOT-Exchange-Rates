#!/usr/bin/env python3
"""Tests for core/scheduler.py — Auto-Scheduler."""

import os
import time
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

        files = scheduler._scan_watch_paths()

        assert len(files) == 2
        names = [os.path.basename(f) for f in files]
        assert "ledger.xlsx" in names
        assert "data.xlsm" in names
        assert "readme.txt" not in names
        assert ".hidden.xlsx" not in names

    def test_scan_nonexistent_path(self):
        scheduler = AutoScheduler()
        scheduler._watch_paths = ["/nonexistent/path"]
        files = scheduler._scan_watch_paths()
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

    def test_callback_fires_at_time(self, tmp_path):
        """Test that callback fires when current time matches."""
        # Create a test file so scan finds something
        (tmp_path / "test.xlsx").write_text("test")

        callback = MagicMock()
        scheduler = AutoScheduler()
        scheduler._running = True
        scheduler._callback = callback
        scheduler._watch_paths = [str(tmp_path)]
        scheduler._last_run_date = None

        # Set target to current time so it fires immediately
        import time as _time
        scheduler._target_time = _time.strftime("%H:%M")

        # Call the check directly (without scheduling next)
        try:
            scheduler._check_and_fire()
        except Exception:
            pass  # Timer scheduling may fail in test context

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
