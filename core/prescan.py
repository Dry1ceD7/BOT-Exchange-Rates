#!/usr/bin/env python3
"""
core/prescan.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.5) - Smart Date Pre-Scanner
---------------------------------------------------------------------------
Separated from engine.py for SFFB compliance (<200 lines).
Pre-scans queued .xls/.xlsx files to detect the oldest date in the
source column. Supports both legacy xlrd (.xls) and openpyxl (.xlsx).
"""

import logging
import os
from datetime import date, datetime
from typing import List, Optional, Tuple

import openpyxl
import xlrd

logger = logging.getLogger(__name__)

DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
    "%d %b %Y", "%d %B %Y", "%Y%m%d",
]


def prescan_oldest_date(
    filepaths: List[str],
    target_col_name: str = "Date",
) -> Tuple[date, bool]:
    """
    Pre-scans queued .xls/.xlsx files to find the absolute
    oldest date in the source column.

    Returns:
        Tuple of (oldest_date, was_detected).
    """
    oldest: Optional[date] = None

    for fp in filepaths:
        if not os.path.exists(fp):
            continue

        is_legacy_xls = fp.lower().endswith(".xls") and not fp.lower().endswith(".xlsx")

        if is_legacy_xls:
            found = _scan_xls(fp, target_col_name)
        else:
            found = _scan_xlsx(fp, target_col_name)

        if found is not None:
            if oldest is None or found < oldest:
                oldest = found

    if oldest is not None:
        return oldest, True

    # Fallback: last week of previous year (not today - 30)
    prev_year = date.today().year - 1
    fallback = date(prev_year, 12, 28)
    return fallback, False


# ── Legacy .xls scanning (xlrd) ─────────────────────────────────────────


def _scan_xls(filepath: str, target_col_name: str) -> Optional[date]:
    """Scan a legacy .xls file using xlrd to find the oldest date."""
    oldest: Optional[date] = None
    try:
        wb = xlrd.open_workbook(filepath, formatting_info=False)
        for sheet_name in wb.sheet_names():
            ws = wb.sheet_by_name(sheet_name)
            target_col_idx = None

            # Search header rows (first 10 rows)
            for row_idx in range(min(10, ws.nrows)):
                for col_idx in range(ws.ncols):
                    val = ws.cell_value(row_idx, col_idx)
                    if isinstance(val, str) and val.strip() == target_col_name:
                        target_col_idx = col_idx
                        header_row = row_idx
                        break
                if target_col_idx is not None:
                    break

            if target_col_idx is None:
                continue

            # Scan data rows for dates
            for row_idx in range(header_row + 1, ws.nrows):
                cell_type = ws.cell_type(row_idx, target_col_idx)
                cell_val = ws.cell_value(row_idx, target_col_idx)

                parsed = None
                if cell_type == xlrd.XL_CELL_DATE and cell_val:
                    try:
                        dt = xlrd.xldate_as_datetime(cell_val, wb.datemode)
                        parsed = dt.date()
                    except Exception:
                        pass
                elif cell_type == xlrd.XL_CELL_TEXT:
                    parsed = _parse_scan_date(cell_val, DATE_FORMATS)

                if parsed is not None:
                    if oldest is None or parsed < oldest:
                        oldest = parsed
    except Exception as e:
        logger.debug("xlrd prescan failed for %s: %s", filepath, e)

    return oldest


# ── Modern .xlsx scanning (openpyxl) ────────────────────────────────────


def _scan_xlsx(filepath: str, target_col_name: str) -> Optional[date]:
    """Scan a .xlsx file using openpyxl to find the oldest date."""
    oldest: Optional[date] = None
    wb = None
    try:
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
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
                if parsed is not None:
                    if oldest is None or parsed < oldest:
                        oldest = parsed
    except (
        ValueError, TypeError, KeyError,
        openpyxl.utils.exceptions.InvalidFileException,
    ):
        pass
    finally:
        if wb is not None:
            wb.close()

    return oldest


# ── Shared date parsing ─────────────────────────────────────────────────


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
