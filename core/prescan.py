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
import zipfile
from datetime import date
from pathlib import Path

import openpyxl

from core.constants import DATE_FORMATS, bot_today, is_skip_sheet
from core.constants import parse_date as _shared_parse_date
from core.excel_io import find_header_row

logger = logging.getLogger(__name__)

# Re-export for backward compatibility; canonical source is core.constants.
DATE_FORMATS = list(DATE_FORMATS)


def prescan_oldest_date(
    filepaths: list[str],
    target_col_name: str = "Date",
    rate_col_name: str = "EX Rate",
) -> tuple[date, bool]:
    """
    Pre-scans queued .xlsx files to find the absolute
    oldest date in the source column.

    ``rate_col_name`` is the duplicate-resolution anchor: real ledgers
    carry two 'Date' columns (invoice + export-entry), and the source
    date must be the one the written formulas look up — the occurrence
    nearest left of 'EX Rate'.

    Returns:
        Tuple of (oldest_date, was_detected).
    """
    oldest: date | None = None

    for fp in filepaths:
        if not Path(fp).exists():
            continue

        found = _scan_xlsx(fp, target_col_name, rate_col_name)

        if found is not None and (oldest is None or found < oldest):
            oldest = found

    if oldest is not None:
        return oldest, True

    # Fallback: last week of previous year (not today - 30).
    # Anchor on the BOT business date (Asia/Bangkok), not naive local time, so
    # the year boundary matches the rates source near midnight.
    # NOTE: deliberately Dec 28 — a NARROW "no dates detected" anchor for the
    # Smart Date toggle, NOT the Dec-20 rate fetch-window start
    # (core.logic.default_fetch_window_start). The fetch window must open wide
    # enough to cover the year-start rollback; this fallback only needs to
    # land inside the prior year's last week, so the two stay separate.
    prev_year = bot_today().year - 1
    fallback = date(prev_year, 12, 28)
    return fallback, False


# ── Modern .xlsx scanning (openpyxl) ────────────────────────────────────


def _scan_xlsx(
    filepath: str, target_col_name: str, rate_col_name: str = "EX Rate",
) -> date | None:
    """Scan a .xlsx file using openpyxl to find the oldest date."""
    oldest: date | None = None
    wb = None
    try:
        with Path(filepath).open("rb") as f:
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
            for ws in wb.worksheets:
                # Skip the app's own master/reference sheets (ExRate etc.):
                # the ExRate sheet carries a "Date" column going back to the
                # year start, so scanning it would skew oldest-date detection
                # toward dates no ledger row actually needs.
                if is_skip_sheet(ws.title):
                    continue
                # Header location is owned by core.excel_io.find_header_row;
                # duplicates resolve to the first occurrence exactly like the
                # old row_strs.index() lookup, but the prescan stays silent
                # on collisions (warn_duplicates=False — the ledger paths
                # carry the operator warning).
                header_row_idx, cols = find_header_row(
                    ws,
                    (
                        ("source", target_col_name),
                        ("out_rate", rate_col_name),
                    ),
                    warn_duplicates=False,
                    resolve_left_of={"source": "out_rate"},
                )

                if header_row_idx is None or "source" not in cols:
                    continue
                target_col_idx = cols["source"] + 1

                for row in ws.iter_rows(
                    min_row=header_row_idx + 1,
                    min_col=target_col_idx, max_col=target_col_idx,
                    values_only=True,
                ):
                    parsed = _parse_scan_date(row[0], DATE_FORMATS)
                    if parsed is not None and (oldest is None or parsed < oldest):
                        oldest = parsed
    except (
        OSError, ValueError, TypeError, KeyError,
        zipfile.BadZipFile,
        openpyxl.utils.exceptions.InvalidFileException,
    ):
        # OSError covers a locked/permission-denied .xlsx (e.g. open in Excel
        # on the Windows target): skip that file rather than crash the whole
        # headless/scheduled prescan — other queued files still scan.
        # BadZipFile covers non-zip bytes wearing an .xlsx extension (a legacy
        # BIFF .xls renamed/mis-saved) — it is neither OSError nor
        # InvalidFileException, so without it one masquerading file killed the
        # entire prescan.
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
