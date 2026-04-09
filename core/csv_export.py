#!/usr/bin/env python3
"""
core/csv_export.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — CSV Export
---------------------------------------------------------------------------
Exports cached exchange rate data from the local SQLite cache to a
standard CSV file that is compatible with the BOT CSV import format.

This allows users to:
  - Back up their cached rate data
  - Share rate data between machines
  - Re-import data on a fresh installation
"""

import csv
import logging
import os

logger = logging.getLogger(__name__)


def export_rates_csv(csv_path: str, cache_db) -> int:
    """
    Export all cached rates from CacheDB to a CSV file.

    The output CSV matches the BOT format with columns:
      Period, Currency_ID, Buying Transfer, Selling

    One row per currency per date (USD and EUR rows).

    Args:
        csv_path: Absolute path for the output CSV file.
        cache_db: A CacheDB instance.

    Returns:
        Number of rate rows exported.
    """
    exported = 0

    # Fetch all rows via public API (no private member access)
    rows = cache_db.get_all_rates()

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Period", "Currency_ID",
            "Buying Transfer", "Selling",
        ])

        for row in rows:
            date_str, usd_buy, usd_sell, eur_buy, eur_sell = row

            # Write USD row if any rate exists
            if usd_buy is not None or usd_sell is not None:
                writer.writerow([
                    date_str, "USD",
                    _fmt(usd_buy), _fmt(usd_sell),
                ])
                exported += 1

            # Write EUR row if any rate exists
            if eur_buy is not None or eur_sell is not None:
                writer.writerow([
                    date_str, "EUR",
                    _fmt(eur_buy), _fmt(eur_sell),
                ])
                exported += 1

    logger.info(
        "CSV export complete: %d rows written to %s", exported, csv_path,
    )
    return exported


def _fmt(value) -> str:
    """Format a rate value for CSV output."""
    if value is None:
        return ""
    return f"{float(value):.4f}"
