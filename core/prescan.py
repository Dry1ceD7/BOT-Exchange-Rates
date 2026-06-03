#!/usr/bin/env python3
"""
core/prescan.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.45) - Smart Date Pre-Scanner
---------------------------------------------------------------------------
Separated from engine.py for SFFB compliance (<200 lines).
Pre-scans queued .xlsx files to detect the oldest date in the
source column. Uses openpyxl for all modern Excel formats.
"""

import contextlib
import logging
from datetime import date
from pathlib import Path

import openpyxl

from core.constants import DATE_FORMATS
from core.constants import parse_date as _shared_parse_date

logger = logging.getLogger(__name__)

# Re-export for backward compatibility; canonical source is core.constants.
DATE_FORMATS = list(DATE_FORMATS)


def prescan_oldest_date(
    filepaths: list[str],
    target_col_name: str = "Date",
) -> tuple[date, bool]:
    """
    Pre-scans queued .xlsx files to find the absolute
    oldest date in the source column.

    Returns:
        Tuple of (oldest_date, was_detected).
    """
    oldest: date | None = None

    for fp in filepaths:
        if not Path(fp).exists():
            continue

        found = _scan_xlsx(fp, target_col_name)

        if found is not None and (oldest is None or found < oldest):
            oldest = found

    if oldest is not None:
        return oldest, True

    # Fallback: last week of previous year (not today - 30)
    prev_year = date.today().year - 1
    fallback = date(prev_year, 12, 28)
    return fallback, False


# ── Modern .xlsx scanning (openpyxl) ────────────────────────────────────


def _scan_xlsx(filepath: str, target_col_name: str) -> date | None:
    """Scan a .xlsx file using openpyxl to find the oldest date."""
    oldest: date | None = None
    wb = None
    try:
        with Path(filepath).open("rb") as f:
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            for ws in wb.worksheets:
                target_col_idx = None
                header_row_idx = None
                for row_idx, row in enumerate(
                    ws.iter_rows(min_row=1, max_row=10, values_only=True), 1
                ):
                    row_strs = [
                        str(c).strip() if c is not None else "" for c in row
                    ]
                    if target_col_name in row_strs:
                        target_col_idx = row_strs.index(target_col_name) + 1
                        header_row_idx = row_idx
                        break

                if target_col_idx is None or header_row_idx is None:
                    continue

                for row in ws.iter_rows(
                    min_row=header_row_idx + 1,
                    min_col=target_col_idx, max_col=target_col_idx,
                    values_only=True,
                ):
                    parsed = _parse_scan_date(row[0], DATE_FORMATS)
                    if parsed is not None and (oldest is None or parsed < oldest):
                        oldest = parsed
    except (
        ValueError, TypeError, KeyError,
        openpyxl.utils.exceptions.InvalidFileException,
    ):
        pass
    finally:
        if wb is not None:
            with contextlib.suppress(OSError):
                wb.close()

    return oldest


# ── Shared date parsing ─────────────────────────────────────────────────


def _parse_scan_date(cell_val, formats: list[str]) -> date | None:
    """Parse a date from a cell value (shared parser).

    The ``formats`` arg is retained for backward-compatible call sites; the
    canonical format list lives in core.constants.DATE_FORMATS.
    """
    return _shared_parse_date(cell_val)
