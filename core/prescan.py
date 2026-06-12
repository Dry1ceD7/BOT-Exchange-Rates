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

import logging
import zipfile
from datetime import date
from pathlib import Path

import openpyxl

from core.constants import bot_today
from core.constants import parse_date as _shared_parse_date
from core.ledger_processing import prescan_target_dates_and_currencies

logger = logging.getLogger(__name__)


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
    """Scan a .xlsx file to find the oldest date in the source column.

    Delegates the actual read-only workbook scan to
    ``core.ledger_processing.prescan_target_dates_and_currencies`` with
    ``use_cache=True``: the SAME scan process_ledger runs immediately
    afterwards, so memoizing it here means the Smart-Date pass and the
    ledger pipeline share ONE full workbook open per (unchanged) file
    instead of two. The header labels passed below ("Date"/"Cur"/"EX Rate"
    by default) match the engine's ``target_cols`` exactly so the memo key
    lines up. Skip-sheet filtering (ExRate etc.) and the duplicate-'Date'
    resolve-left-of-'EX Rate' anchor are owned by the delegate.
    """
    try:
        dates, _currencies = prescan_target_dates_and_currencies(
            filepath,
            {
                "source_date": target_col_name,
                "currency": "Cur",
                "out_rate": rate_col_name,
            },
            use_cache=True,
        )
    except (
        OSError, ValueError, TypeError, KeyError, SyntaxError,
        zipfile.BadZipFile,
        openpyxl.utils.exceptions.InvalidFileException,
    ) as exc:
        # Per-file skip semantics: one bad file must never kill the whole
        # headless/scheduled/GUI prescan — other queued files still scan.
        #   - OSError: locked/permission-denied .xlsx (open in Excel).
        #   - BadZipFile: non-zip bytes wearing .xlsx (renamed legacy .xls).
        #   - SyntaxError: covers xml.etree.ElementTree.ParseError AND lxml's
        #     XMLSyntaxError (both SyntaxError subclasses) raised on a
        #     truncated/garbled sheet XML inside a structurally valid zip.
        logger.debug("Prescan skipped %s: %s", filepath, exc)
        return None

    return min(dates) if dates else None


# ── Shared date parsing ─────────────────────────────────────────────────


def _parse_scan_date(cell_val) -> date | None:
    """Parse a date from a cell value.

    Thin delegate to the canonical parser, core.constants.parse_date
    (which owns DATE_FORMATS and the Buddhist-Era normalization).
    """
    return _shared_parse_date(cell_val)
