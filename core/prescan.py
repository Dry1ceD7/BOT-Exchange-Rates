#!/usr/bin/env python3
"""
core/prescan.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.1) - Smart Date Pre-Scanner
---------------------------------------------------------------------------
Separated from engine.py for SFFB compliance (<200 lines).
Pre-scans queued .xlsx files to detect the oldest date in the source column.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import openpyxl


def prescan_oldest_date(
    filepaths: List[str],
    target_col_name: str = "Date",
) -> Tuple[date, bool]:
    """
    Pre-scans queued .xlsx files in read-only mode to find the absolute
    oldest date in the source column. This eliminates manual date entry.

    Returns:
        Tuple of (oldest_date, was_detected).
    """
    import os

    oldest: Optional[date] = None
    date_formats = [
        "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
        "%d %b %Y", "%d %B %Y", "%Y%m%d",
    ]

    for fp in filepaths:
        if not os.path.exists(fp):
            continue
        wb = None
        try:
            wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
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
                    parsed = _parse_scan_date(row[0], date_formats)
                    if parsed is not None:
                        if oldest is None or parsed < oldest:
                            oldest = parsed
        except (
            ValueError, TypeError, KeyError,
            openpyxl.utils.exceptions.InvalidFileException,
        ):
            continue
        finally:
            if wb is not None:
                wb.close()

    if oldest is not None:
        return oldest, True

    fallback = date.today() - timedelta(days=30)
    return fallback, False


def _parse_scan_date(cell_val, formats: List[str]) -> Optional[date]:
    """Parse a date from a cell value using multiple format strings."""
    if isinstance(cell_val, datetime):
        return cell_val.date()
    if isinstance(cell_val, date):
        return cell_val
    if isinstance(cell_val, str):
        val = cell_val.strip()
        if val and val.lower() not in ("nan", "null"):
            for fmt in formats:
                try:
                    return datetime.strptime(val, fmt).date()
                except ValueError:
                    continue
    return None
