#!/usr/bin/env python3
"""
core/csv_export.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — CSV Export
---------------------------------------------------------------------------
Exports cached exchange rate data from the local SQLite cache to a
standard CSV file that can be re-imported losslessly.

This allows users to:
  - Back up their cached rate data
  - Share rate data between machines
  - Re-import data on a fresh installation

The export uses the multi-currency long format so that arbitrary currencies
and rate types round-trip exactly:

    Period, Currency_ID, Rate_Type, Value

core/csv_import.py auto-detects this format (and still reads the legacy
wide BOT format).
"""

import csv
import logging
import os
from decimal import Decimal

logger = logging.getLogger(__name__)

# Lossless long-format header. csv_import auto-detects this on the way back in.
MULTI_HEADERS = ["Period", "Currency_ID", "Rate_Type", "Value"]


def _csv_safe(value) -> str:
    """
    Neutralize CSV/formula injection for a non-numeric cell.

    Strips embedded CR/LF/TAB (which could split or shift fields) and prefixes
    a single quote to any value beginning with a spreadsheet formula trigger
    (=, +, -, @) so Excel/LibreOffice treat it as inert text.
    """
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return ("'" + s) if s and s[0] in ("=", "+", "-", "@") else s


def export_rates_csv(csv_path: str, cache_db) -> int:
    """
    Export all cached multi-currency rates from CacheDB to a CSV file.

    Output columns (long format): Period, Currency_ID, Rate_Type, Value.
    One row per (date, currency, rate_type), preserving exact Decimal values.

    Args:
        csv_path: Absolute path for the output CSV file.
        cache_db: A CacheDB instance.

    Returns:
        Number of rate rows exported.
    """
    exported = 0

    rows = cache_db.get_all_multi_rates()

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(MULTI_HEADERS)

        for date_str, currency, rate_type, value in rows:
            if value is None:
                continue
            writer.writerow([
                _csv_safe(date_str),
                _csv_safe(currency),
                _csv_safe(rate_type),
                _fmt(value),
            ])
            exported += 1

    logger.info(
        "CSV export complete: %d rows written to %s", exported, csv_path,
    )
    return exported


def _fmt(value) -> str:
    """Format a rate value for CSV output (4dp, numeric — never injected).

    Decimal inputs are quantized exactly (no float round-trip) so the
    written digits match the cached "Mathematical Truth" value.
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.4f}"
    return f"{float(value):.4f}"
