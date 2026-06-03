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
from pathlib import Path

from core.constants import csv_safe, format_rate_value

logger = logging.getLogger(__name__)

# Lossless long-format header. csv_import auto-detects this on the way back in.
MULTI_HEADERS = ["Period", "Currency_ID", "Rate_Type", "Value"]


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

    # Path.parent is "." for a bare filename, matching the old `or "."`.
    Path(csv_path).parent.mkdir(parents=True, exist_ok=True)

    with Path(csv_path).open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(MULTI_HEADERS)

        for date_str, currency, rate_type, value in rows:
            if value is None:
                continue
            writer.writerow([
                csv_safe(date_str),
                csv_safe(currency),
                csv_safe(rate_type),
                format_rate_value(value),
            ])
            exported += 1

    logger.info(
        "CSV export complete: %d rows written to %s", exported, csv_path,
    )
    return exported
