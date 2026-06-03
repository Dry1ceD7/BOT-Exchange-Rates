#!/usr/bin/env python3
"""
core/ledger_processing.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Ledger Processing Helpers
---------------------------------------------------------------------------
Near-pure helpers extracted from core/engine.py to keep the orchestrator
slim. These functions take all of their dependencies as explicit parameters
(no engine ``self`` state), so they are independently testable.

  - run_anomaly_check    → anomaly orchestration over loaded rate dicts
  - prescan_target_dates → read-only workbook scan for target dates
"""

import contextlib
import gc
import logging
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import openpyxl

from core.constants import SKIP_SHEET_NAMES, parse_date

logger = logging.getLogger(__name__)


def run_anomaly_check(
    anomaly_guard,
    emit_fn: Callable[[str, str], None],
    usd_buying: dict[date, Decimal],
    usd_selling: dict[date, Decimal],
    eur_buying: dict[date, Decimal],
    eur_selling: dict[date, Decimal],
) -> int:
    """Run anomaly detection across all loaded rates (v3.1.0).

    Args:
        anomaly_guard: An object exposing ``check_rates_bulk(rates_bundle)``
            that returns a list of anomaly records.
        emit_fn: Callback ``emit(msg, etype)`` for status events.
        usd_buying: USD buying-transfer rates keyed by date.
        usd_selling: USD selling rates keyed by date.
        eur_buying: EUR buying-transfer rates keyed by date.
        eur_selling: EUR selling rates keyed by date.

    Returns:
        The number of anomalies found.
    """
    rates_bundle = {
        "USD_buying_transfer": usd_buying,
        "USD_selling": usd_selling,
        "EUR_buying_transfer": eur_buying,
        "EUR_selling": eur_selling,
    }
    anomalies = anomaly_guard.check_rates_bulk(rates_bundle)
    for a in anomalies:
        emit_fn(
            f"⚠ ANOMALY: {a.currency} {a.rate_type} on "
            f"{a.check_date.strftime('%d %b %Y')}: "
            f"{a.pct_change:.2f}% change "
            f"({a.prev_value} → {a.new_value})",
            "warning",
        )
    if anomalies:
        logger.warning(
            "Anomaly guard: %d suspicious rate(s) detected",
            len(anomalies),
        )
    return len(anomalies)


def prescan_target_dates(
    filepath: str,
    target_cols: dict[str, str],
    parse_date_fn: Callable[[object], date | None] = parse_date,
    emit_fn: Callable[[str], None] | None = None,
) -> set[date]:
    """Scan a workbook in read-only mode to extract all target dates.

    Opens the workbook in read-only mode, scans all non-skipped sheets for
    the source date column, and returns a set of all parsed dates. The
    workbook is properly closed and garbage-collected after scanning.

    Args:
        filepath: Path to the .xlsx/.xlsm workbook.
        target_cols: Column-name mapping; ``target_cols["source_date"]`` is
            the header label of the date column.
        parse_date_fn: Cell-value → date parser (defaults to shared parser).
        emit_fn: Optional status callback ``emit(msg)``.

    Returns:
        Set of all parsed dates found in the source date column.
    """
    if emit_fn is not None:
        emit_fn("Scanning dates from workbook")
    all_target_dates: set[date] = set()
    source_label = target_cols["source_date"]

    wb_scan = None
    try:
        wb_scan = openpyxl.load_workbook(
            filepath, read_only=True, data_only=True,
        )
        for sheet_name in wb_scan.sheetnames:
            if sheet_name in SKIP_SHEET_NAMES:
                continue
            ws = wb_scan[sheet_name]
            header_row_idx = None
            col_indices: dict[str, int] = {}
            for row_idx, row in enumerate(
                ws.iter_rows(min_row=1, max_row=10, values_only=True), 1
            ):
                row_strs = [
                    str(c).strip() if c is not None else "" for c in row
                ]
                if source_label in row_strs:
                    header_row_idx = row_idx
                    for ci, val in enumerate(row_strs):
                        if val == source_label:
                            col_indices["source"] = ci
                    break

            if header_row_idx is None or "source" not in col_indices:
                continue

            src_idx = col_indices["source"] + 1
            for row_idx in range(header_row_idx + 1, (ws.max_row or 0) + 1):
                parsed_date = parse_date_fn(
                    ws.cell(row=row_idx, column=src_idx).value
                )
                if parsed_date:
                    all_target_dates.add(parsed_date)
    except (ValueError, TypeError, KeyError,
            openpyxl.utils.exceptions.InvalidFileException):
        raise
    finally:
        if wb_scan is not None:
            with contextlib.suppress(OSError):
                wb_scan.close()
            del wb_scan
            wb_scan = None
        gc.collect()

    return all_target_dates
