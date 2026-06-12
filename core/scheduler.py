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
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from core.constants import (
    POLL_INTERVAL_SECONDS as _DEFAULT_POLL_INTERVAL,
)
from core.constants import (
    collect_excel_files,
)

logger = logging.getLogger(__name__)

# How many minutes past the configured slot a late poll may still trigger the
# day's run. The PC being asleep at the exact target minute, or the process
# being busy for >POLL_INTERVAL across the boundary, no longer silently skips
# the whole day: any poll within this window after the slot fires once.
# Override via BOT_SCHED_CATCHUP_MIN (capped at a sane 12h so a wildly large
# value can't make a long-overdue slot fire many hours later).
_CATCH_UP_WINDOW_MINUTES: int = max(
    1, min(720, int(os.environ.get("BOT_SCHED_CATCHUP_MIN", "120")))
)


class AutoScheduler:
    """
    Background scheduler that fires a callback at a configured time.

    The scheduler polls every POLL_INTERVAL_SECONDS to check whether the day's
    run is due. Times are interpreted in LOCAL machine time. Rather than
    requiring an exact ``HH:MM`` match (which silently skipped the whole day if
    the PC was asleep at that minute or busy across the boundary), the run
    fires on the first poll at-or-after the slot, within a catch-up window
    (default 120 min). Once fired it records the date and will not fire again
    until the next day. Optionally skips weekends and BOT holidays.

    Usage:
        scheduler = AutoScheduler()
        scheduler.start(
            time_str="23:00",
            watch_paths=["/path/to/ledgers"],
            callback=my_process_function,
            skip_weekends=True,
        )
        # ... later ...
        scheduler.stop()
    """

    POLL_INTERVAL_SECONDS = _DEFAULT_POLL_INTERVAL
    CATCH_UP_WINDOW_MINUTES = _CATCH_UP_WINDOW_MINUTES

    def __init__(self):
        self._timer: threading.Timer | None = None
        self._running = False
        self._target_time: str = "23:00"
        self._watch_paths: list[str] = []
        self._callback: Callable | None = None
        self._last_run_date: str | None = None
        self._skip_weekends: bool = False
        self._skip_holidays: bool = False
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Return True if the scheduler is actively polling."""
        return self._running

    def start(
        self,
        time_str: str,
        watch_paths: list[str],
        callback: Callable[[list[str]], None],
        skip_weekends: bool = False,
        skip_holidays: bool = False,
    ) -> None:
        """
        Start the scheduler.

        Args:
            time_str: Target time in "HH:MM" format (24h), LOCAL machine time.
            watch_paths: List of directory paths to scan for Excel files.
            callback: Function to call with the list of discovered files.
            skip_weekends: When True, Saturday/Sunday slots are marked done for
                the day without firing the callback (no batch on weekends).
            skip_holidays: When True, dates present in the BOT holiday cache are
                likewise skipped. Read-only access to the cached holidays via
                core.database.get_cache(); no network and no DB writes.
        """
        with self._lock:
            self._target_time = time_str
            self._watch_paths = list(watch_paths)
            self._callback = callback
            self._skip_weekends = bool(skip_weekends)
            self._skip_holidays = bool(skip_holidays)
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

    def _schedule_next(self) -> None:
        """Schedule the next poll check.

        Re-checks self._running and installs the new timer under the lock so a
        timer-thread call cannot race stop(): if stop() already flipped
        _running and cancelled the timer, we must not install a replacement.
        """
        with self._lock:
            if not self._running:
                return
            timer = threading.Timer(
                self.POLL_INTERVAL_SECONDS, self._check_and_fire,
            )
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _check_and_fire(self) -> None:
        """Check if the day's run is due, and if so, fire the callback.

        Runs on the Timer thread. Snapshot every shared field under the lock
        up front so start()/stop() mutations on another thread
        cannot cause torn reads or a double-fire. We operate on the locals,
        write _last_run_date back under the lock, and invoke the callback
        OUTSIDE the lock (it may be slow / re-enter the scheduler).

        Fire rule (vs. the old exact ``current_time == target_time`` equality
        that silently skipped the whole day on a sleepy/busy PC):
          fire once when  now >= today-at-target  AND  now < target + window
          AND we have not already run today.
        A poll a few minutes late still triggers; a poll arriving long after
        the catch-up window has closed does NOT (we don't want a 23:00 job
        suddenly firing at 06:00 the next morning).
        """
        with self._lock:
            if not self._running:
                return
            target_time = self._target_time
            callback = self._callback
            watch_paths = list(self._watch_paths)
            last_run_date = self._last_run_date
            skip_weekends = self._skip_weekends
            skip_holidays = self._skip_holidays

        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")

        # Prevent duplicate runs on the same day.
        if last_run_date == current_date:
            self._schedule_next()
            return

        if not self._is_run_due(now, target_time):
            self._schedule_next()
            return

        # The slot is due today. Mark it done up front so weekend/holiday skips
        # and a fired run alike consume the day exactly once (no retry storm
        # across the catch-up window, no double-fire race on the Timer thread).
        with self._lock:
            self._last_run_date = current_date

        if skip_weekends and now.weekday() >= 5:
            logger.info(
                "Scheduler: %s is a weekend — skipping today's run.",
                current_date,
            )
            self._schedule_next()
            return

        if skip_holidays and self._is_holiday(now):
            logger.info(
                "Scheduler: %s is a BOT holiday — skipping today's run.",
                current_date,
            )
            self._schedule_next()
            return

        logger.info("Scheduler firing for %s (slot %s)", current_date, target_time)

        try:
            # Scan watch paths for Excel files (using the snapshot).
            files = self._scan_watch_paths(watch_paths)

            # Invoke the callback OUTSIDE the lock — it may be slow or re-enter
            # the scheduler.
            if files and callback is not None:
                try:
                    callback(files)
                except Exception:  # noqa: BLE001 — scheduler must outlive any callback failure
                    # Broadened from (OSError, ValueError, RuntimeError): any
                    # other exception type (KeyError/AttributeError/TclError)
                    # previously propagated out of the Timer thread BEFORE
                    # _schedule_next ran — no next timer armed, scheduler
                    # silently dead until restart. logger.exception preserves
                    # the traceback that used to vanish with the thread.
                    logger.exception("Scheduler callback failed")
            elif not files:
                logger.info("Scheduler: no Excel files found in watched paths")
        finally:
            # The chain can NEVER break: the finally also covers an exception
            # escaping _scan_watch_paths itself, so the next poll is always
            # armed regardless of what this fire did.
            self._schedule_next()

    def _is_run_due(self, now: datetime, target_time: str) -> bool:
        """Return True if ``now`` is at-or-after today's slot, within the window.

        Parses ``target_time`` ("HH:MM"); a malformed value never fires (logged
        once per poll at debug). The window upper bound stops a long-overdue
        slot from firing hours later (e.g. machine woken the next morning).
        """
        try:
            hh, mm = target_time.split(":")
            slot = now.replace(
                hour=int(hh), minute=int(mm), second=0, microsecond=0,
            )
        except (ValueError, TypeError):
            logger.debug("Scheduler: bad target_time %r — not firing", target_time)
            return False
        if now < slot:
            return False
        return now < slot + timedelta(minutes=self.CATCH_UP_WINDOW_MINUTES)

    def _is_holiday(self, now: datetime) -> bool:
        """Read-only check of the BOT holiday cache for ``now``'s date.

        Uses core.database.get_cache() (the public, process-wide accessor) and
        only READS — no network, no DB writes. Any cache error is treated as
        "not a holiday" so a missing/empty cache never blocks a scheduled run.
        """
        try:
            from core.database import get_cache

            year = now.year
            cache = get_cache()
            if not cache.has_holidays_for_year(year):
                return False
            target = now.strftime("%Y-%m-%d")
            return any(
                row[0] == target for row in cache.get_holidays(year)
            )
        except Exception as exc:  # noqa: BLE001 — never let cache errors block
            logger.debug("Scheduler holiday check failed (ignored): %s", exc)
            return False

    def _scan_watch_paths(self, watch_paths: list[str]) -> list[str]:
        """
        Scan all configured watch paths for Excel files.
        Only looks in the specified directories (NOT recursive).

        Args:
            watch_paths: Snapshot of directories to scan. Passed in (rather
                than read from self) so the caller can snapshot it under the
                lock and avoid racing a concurrent start().
        """
        files = []
        rejected = []
        seen = set()

        for path in watch_paths:
            if not Path(path).is_dir():
                logger.debug("Watch path not found: %s", path)
                continue

            # Shared listing helper (core.constants.collect_excel_files):
            # bare names sorted then joined — the exact prior full-path form
            # the os.path.normpath dedup relies on. dedup=False because the
            # identity check must run ACROSS watch paths, here.
            found, path_rejected = collect_excel_files(
                path, dedup=False, collect_rejected=True,
            )
            rejected.extend(path_rejected)
            for full in found:
                norm = os.path.normpath(full)
                if norm not in seen:
                    seen.add(norm)
                    files.append(full)

        if rejected:
            # A watch folder fed by a legacy export (e.g. Crystal Reports
            # .xls) would otherwise be skipped silently FOREVER — make the
            # misconfiguration visible in the log/console each scan.
            logger.warning(
                "Scheduler: %d unsupported spreadsheet file(s) in watched "
                "paths will never be processed (only .xlsx/.xlsm are "
                "supported — open in Excel and save as .xlsx): %s",
                len(rejected),
                ", ".join(Path(r).name for r in rejected),
            )
        logger.info(
            "Scheduler scan: %d files found across %d paths",
            len(files), len(watch_paths),
        )
        return files
