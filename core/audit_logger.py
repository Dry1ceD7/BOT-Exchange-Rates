#!/usr/bin/env python3
"""
core/audit_logger.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Audit Trail Generator
---------------------------------------------------------------------------
Creates a CSV audit log for each batch run, recording every cell
modification for external auditor review.

Output: data/logs/Audit_Log_YYYYMMDD_HHMMSS.csv
"""

import atexit
import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path

from core.constants import AUDIT_LOG_MAX_AGE_DAYS, csv_safe
from core.paths import get_project_root

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Generates a timestamped CSV audit trail for each processing batch.

    Each row records exactly what happened to a single cell:
    which file, which sheet, which row, what date, what currency,
    what the old value was, what the new value is, whether a
    holiday rollback was used, and whether an anomaly flag was raised.

    Supports context manager protocol:
        with AuditLogger() as audit:
            audit.log_row_change(filename="ledger.xlsx", ...)
    """

    HEADERS = [
        "Timestamp",
        "Filename",
        "Sheet",
        "Row",
        "Date",
        "Currency",
        "Original_Value",
        "New_Value",
        "Rate_Source",
        "Holiday_Rollback",
        "Anomaly_Flag",
    ]

    def __init__(self, log_dir: str | None = None):
        if log_dir is None:
            log_dir = str(Path(get_project_root()) / "data" / "logs")
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Keep _filepath as str: returned via the .filepath property and
        # finalize(); callers treat it as a string path.
        self._filepath = str(Path(log_dir) / f"Audit_Log_{timestamp}.csv")
        # Long-lived handle held for the object's lifetime; released via
        # __exit__/close()/atexit. A context manager here would close it
        # prematurely, so SIM115 does not apply.
        self._file = Path(self._filepath).open(  # noqa: SIM115
            "w", newline="", encoding="utf-8-sig"
        )
        self._writer = csv.writer(self._file)
        self._writer.writerow(self.HEADERS)
        self._row_count = 0
        self._closed = False

        # Safety net: guarantee file handle cleanup on interpreter exit
        atexit.register(self._atexit_cleanup)

        logger.info("Audit log opened: %s", self._filepath)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.finalize()
        return False  # do not suppress exceptions

    def _atexit_cleanup(self):
        """Ensure file is closed even if finalize() was never called."""
        if not self._closed:
            try:
                self._file.flush()
                self._file.close()
            except (OSError, ValueError):
                pass
            self._closed = True
        # Drop the atexit reference so a long-lived process that instantiates
        # one logger per batch does not accumulate stale callbacks.
        atexit.unregister(self._atexit_cleanup)

    @property
    def filepath(self) -> str:
        """Return the path to the current audit log file."""
        return self._filepath

    @property
    def row_count(self) -> int:
        """Return the number of rows written so far."""
        return self._row_count

    def log_row_change(
        self,
        filename: str,
        sheet: str,
        row: int,
        cell_date: str,
        currency: str,
        original_value: str,
        new_value: str,
        rate_source: str = "API",
        holiday_rollback: bool = False,
        anomaly_flag: bool = False,
    ) -> None:
        """
        Append a single row-change record to the audit log.

        Args:
            filename: Base name of the processed Excel file.
            sheet: Name of the worksheet.
            row: 1-indexed row number.
            cell_date: The date from the Date column (as string).
            currency: Currency code (e.g., "USD").
            original_value: What the cell contained before modification.
            new_value: What the cell now contains after modification.
            rate_source: "API" or "Cache" or "CSV Import".
            holiday_rollback: True if a holiday rollback was used.
            anomaly_flag: True if this rate was flagged by the guardian.
        """
        if self._closed:
            raise ValueError("Cannot log to a finalized audit log.")
        self._writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            csv_safe(filename),
            csv_safe(sheet),
            row,
            csv_safe(cell_date),
            csv_safe(currency),
            csv_safe(original_value),
            csv_safe(new_value),
            csv_safe(rate_source),
            "Yes" if holiday_rollback else "No",
            "ANOMALY" if anomaly_flag else "",
        ])
        self._row_count += 1

    def log_batch_summary(
        self,
        total_files: int,
        success: int,
        failed: int,
        anomalies_detected: int,
    ) -> None:
        """
        Write a summary row at the end of the batch.
        """
        if self._closed:
            raise ValueError("Cannot log to a finalized audit log.")
        self._writer.writerow([])
        self._writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            csv_safe("=== BATCH SUMMARY ==="),
            csv_safe(f"Files: {total_files}"),
            csv_safe(f"Success: {success}"),
            csv_safe(f"Failed: {failed}"),
            csv_safe(f"Anomalies: {anomalies_detected}"),
            csv_safe(f"Total Rows Modified: {self._row_count}"),
            "", "", "", "",
        ])

    def finalize(self) -> str:
        """
        Flush and close the audit log file.

        Returns:
            Path to the completed audit log.
        """
        if not self._closed:
            try:
                self._file.flush()
                self._file.close()
            except (OSError, ValueError) as e:
                logger.warning("Audit log close warning: %s", e)
            self._closed = True

        # The handle is now closed deterministically, so the interpreter-exit
        # safety net is no longer needed; unregister it so per-batch loggers
        # in a long-lived process do not pile up atexit callbacks.
        atexit.unregister(self._atexit_cleanup)

        logger.info(
            "Audit log finalized: %s (%d entries)",
            self._filepath, self._row_count,
        )
        return self._filepath


def cleanup_old_audit_logs(
    log_dir: str | None = None,
    max_age_days: int = AUDIT_LOG_MAX_AGE_DAYS,
) -> int:
    """Delete Audit_Log_*.csv files older than ``max_age_days``.

    The CLI writes one audit log per batch, so data/logs/ would grow forever
    without pruning. Age is derived from the YYYYMMDD_HHMMSS timestamp EMBEDDED
    in each filename — never st_mtime, which a copy/restore could reset to a
    misleading recent value.

    Returns:
        Number of files deleted. Unparseable names and unlink failures are
        skipped (logged at debug), never raised, so a stray file cannot abort
        the batch.
    """
    if log_dir is None:
        log_dir = str(Path(get_project_root()) / "data" / "logs")
    log_path = Path(log_dir)
    if not log_path.is_dir():
        return 0

    cutoff = datetime.now() - timedelta(days=max_age_days)
    deleted = 0
    for candidate in log_path.glob("Audit_Log_*.csv"):
        # Filename: "Audit_Log_YYYYMMDD_HHMMSS.csv" → strip prefix/suffix.
        stamp = candidate.name[len("Audit_Log_"):-len(".csv")]
        try:
            logged_at = datetime.strptime(stamp, "%Y%m%d_%H%M%S")
        except ValueError:
            logger.debug("Skipping audit log with unparseable name: %s", candidate.name)
            continue
        if logged_at < cutoff:
            try:
                candidate.unlink()
                deleted += 1
            except OSError as e:
                logger.debug("Could not delete old audit log %s: %s", candidate, e)
    if deleted:
        logger.info("Pruned %d audit log(s) older than %d days.", deleted, max_age_days)
    return deleted
