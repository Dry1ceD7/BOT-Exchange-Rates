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
import os
from datetime import datetime
from typing import Optional

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

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir is None:
            log_dir = os.path.join(get_project_root(), "data", "logs")
        os.makedirs(log_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._filepath = os.path.join(
            log_dir, f"Audit_Log_{timestamp}.csv"
        )
        self._file = open(self._filepath, "w", newline="", encoding="utf-8")
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
        self._writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            filename,
            sheet,
            row,
            cell_date,
            currency,
            original_value,
            new_value,
            rate_source,
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
        self._writer.writerow([])
        self._writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "=== BATCH SUMMARY ===",
            f"Files: {total_files}",
            f"Success: {success}",
            f"Failed: {failed}",
            f"Anomalies: {anomalies_detected}",
            f"Total Rows Modified: {self._row_count}",
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

        logger.info(
            "Audit log finalized: %s (%d entries)",
            self._filepath, self._row_count,
        )
        return self._filepath
