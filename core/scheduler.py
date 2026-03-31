#!/usr/bin/env python3
"""
core/scheduler.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Background Auto-Scheduler
---------------------------------------------------------------------------
Runs a lightweight background timer that triggers batch processing
at a user-configured time each day. Only scans user-specified folders
(never the whole PC). Designed to be started from the GUI and persist
as long as the application is running.
"""

import logging
import os
import threading
from datetime import datetime
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Supported Excel extensions (same as gui/app.py)
EXCEL_EXTENSIONS = (".xlsx", ".xlsm")


class AutoScheduler:
    """
    Background scheduler that fires a callback at a configured time.

    The scheduler polls every 30 seconds to check if the current time
    matches the target. Once fired, it waits until the next day to
    prevent duplicate runs.

    Usage:
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="23:00",
            watch_paths=["/path/to/ledgers"],
            callback=my_process_function,
        )
        # ... later ...
        scheduler.stop()
    """

    POLL_INTERVAL_SECONDS = 30

    def __init__(self):
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._target_time: str = "23:00"
        self._watch_paths: List[str] = []
        self._callback: Optional[Callable] = None
        self._last_run_date: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Return True if the scheduler is actively polling."""
        return self._running

    @property
    def target_time(self) -> str:
        """Return the configured target time (HH:MM)."""
        return self._target_time

    @property
    def watch_paths(self) -> List[str]:
        """Return the list of watched directories."""
        return list(self._watch_paths)

    @property
    def next_run_info(self) -> str:
        """Return a human-readable status string."""
        if not self._running:
            return "Scheduler is off"
        n_paths = len(self._watch_paths)
        return (
            f"Next run: {self._target_time} — "
            f"watching {n_paths} folder{'s' if n_paths != 1 else ''}"
        )

    def start(
        self,
        time_str: str,
        watch_paths: List[str],
        callback: Callable[[List[str]], None],
    ) -> None:
        """
        Start the scheduler.

        Args:
            time_str: Target time in "HH:MM" format (24h).
            watch_paths: List of directory paths to scan for Excel files.
            callback: Function to call with the list of discovered files.
        """
        with self._lock:
            self._target_time = time_str
            self._watch_paths = list(watch_paths)
            self._callback = callback
            self._running = True
            self._last_run_date = None

        logger.info(
            "Scheduler started: time=%s, paths=%s",
            time_str, watch_paths,
        )
        self._schedule_next()

    def stop(self) -> None:
        """Stop the scheduler and cancel any pending timer."""
        with self._lock:
            self._running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

        logger.info("Scheduler stopped")

    def update_config(
        self,
        time_str: Optional[str] = None,
        watch_paths: Optional[List[str]] = None,
    ) -> None:
        """Update scheduler configuration without restart."""
        with self._lock:
            if time_str is not None:
                self._target_time = time_str
            if watch_paths is not None:
                self._watch_paths = list(watch_paths)

        logger.info(
            "Scheduler config updated: time=%s, paths=%d",
            self._target_time, len(self._watch_paths),
        )

    def _schedule_next(self) -> None:
        """Schedule the next poll check."""
        if not self._running:
            return
        self._timer = threading.Timer(
            self.POLL_INTERVAL_SECONDS, self._check_and_fire,
        )
        self._timer.daemon = True
        self._timer.start()

    def _check_and_fire(self) -> None:
        """Check if it's time to run, and if so, fire the callback."""
        if not self._running:
            return

        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%Y-%m-%d")

        # Prevent duplicate runs on the same day
        if self._last_run_date == current_date:
            self._schedule_next()
            return

        if current_time == self._target_time:
            logger.info("Scheduler firing at %s", current_time)
            self._last_run_date = current_date

            # Scan watch paths for Excel files
            files = self._scan_watch_paths()

            if files and self._callback is not None:
                try:
                    self._callback(files)
                except Exception as e:
                    logger.error("Scheduler callback failed: %s", e)
            elif not files:
                logger.info(
                    "Scheduler: no Excel files found in watched paths"
                )

        self._schedule_next()

    def _scan_watch_paths(self) -> List[str]:
        """
        Scan all configured watch paths for Excel files.
        Only looks in the specified directories (NOT recursive).
        """
        files = []
        seen = set()

        for path in self._watch_paths:
            if not os.path.isdir(path):
                logger.debug("Watch path not found: %s", path)
                continue

            for fname in sorted(os.listdir(path)):
                if fname.startswith("."):
                    continue
                if fname.lower().endswith(EXCEL_EXTENSIONS):
                    full = os.path.join(path, fname)
                    norm = os.path.normpath(full)
                    if norm not in seen:
                        seen.add(norm)
                        files.append(full)

        logger.info(
            "Scheduler scan: %d files found across %d paths",
            len(files), len(self._watch_paths),
        )
        return files
