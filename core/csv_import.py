#!/usr/bin/env python3
"""
core/csv_import.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Offline CSV Fallback Importer
---------------------------------------------------------------------------
Imports exchange rate data from the Bank of Thailand's official
downloadable CSV format into the local SQLite cache.

This allows the application to function during BOT API outages
or when internet connectivity is unavailable.
"""

import csv
import logging
import os
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)


def import_bot_csv(csv_path: str, cache_db) -> int:
    """
    Parse a BOT-format CSV and import all rates into CacheDB.

    The BOT CSV typically has columns like:
      Period, Currency, Buying Sight, Buying Transfer, Selling, Mid Rate

    Args:
        csv_path: Absolute path to the downloaded CSV file.
        cache_db: A CacheDB instance.

    Returns:
        Number of rate entries imported.

    Raises:
        FileNotFoundError: If csv_path does not exist.
        ValueError: If the CSV format is unrecognizable.
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    imported = 0

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        # Try to detect the CSV dialect
        sample = f.read(4096)
        f.seek(0)

        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)

        if reader.fieldnames is None:
            raise ValueError("CSV file has no header row.")

        # Normalize headers (case-insensitive, strip whitespace)
        field_map = {
            h.strip().lower(): h for h in reader.fieldnames
        }

        # Identify the key columns (BOT format flexibility)
        date_key = _find_column(field_map, [
            "period", "date", "วันที่",
        ])
        currency_key = _find_column(field_map, [
            "currency_id", "currency", "สกุลเงิน",
        ])
        buying_tt_key = _find_column(field_map, [
            "buying transfer", "buying_transfer", "buying tt",
            "อัตราซื้อ (โอน)",
        ])
        selling_key = _find_column(field_map, [
            "selling", "selling rate", "อัตราขาย",
        ])
        buying_sight_key = _find_column(field_map, [
            "buying sight", "buying_sight", "อัตราซื้อ (ตั๋วเงิน)",
        ])
        mid_rate_key = _find_column(field_map, [
            "mid rate", "mid_rate", "อัตรากลาง",
        ])

        if date_key is None or currency_key is None:
            raise ValueError(
                "CSV must have 'Period'/'Date' and 'Currency'/'Currency_ID' columns. "
                f"Found columns: {list(reader.fieldnames)}"
            )

        # Accumulate rates for bulk insert
        multi_entries = []

        for row in reader:
            raw_date = row.get(date_key, "").strip()
            currency = row.get(currency_key, "").strip().upper()

            if not raw_date or not currency:
                continue

            # Parse date (BOT uses YYYY-MM-DD typically)
            parsed_date = _parse_csv_date(raw_date)
            if parsed_date is None:
                logger.debug("Skipped unparseable date: %s", raw_date)
                continue

            date_str = parsed_date.strftime("%Y-%m-%d")

            # Extract rate values
            rates = {}
            for key, rate_type in [
                (buying_tt_key, "buying_transfer"),
                (selling_key, "selling"),
                (buying_sight_key, "buying_sight"),
                (mid_rate_key, "mid_rate"),
            ]:
                if key:
                    raw_val = row.get(key, "").strip()
                    if raw_val:
                        try:
                            rates[rate_type] = float(raw_val)
                        except ValueError:
                            pass

            # Insert into multi-currency cache
            for rate_type, value in rates.items():
                multi_entries.append(
                    (date_str, currency, rate_type, value)
                )

            # Also insert into legacy USD/EUR table for backward compat
            if currency == "USD":
                cache_db.insert_rate(
                    parsed_date,
                    usd_buying=rates.get("buying_transfer"),
                    usd_selling=rates.get("selling"),
                )
            elif currency == "EUR":
                cache_db.insert_rate(
                    parsed_date,
                    eur_buying=rates.get("buying_transfer"),
                    eur_selling=rates.get("selling"),
                )

            imported += 1

        # Bulk insert into multi-currency table
        if multi_entries:
            cache_db.insert_multi_rates_bulk(multi_entries)

    logger.info("CSV import complete: %d entries from %s", imported, csv_path)
    return imported


def _find_column(
    field_map: dict[str, str], candidates: list[str],
) -> Optional[str]:
    """
    Find a column name from a list of candidates
    (case-insensitive match against normalized field map).
    """
    for candidate in candidates:
        norm = candidate.lower().strip()
        if norm in field_map:
            return field_map[norm]
    return None


def _parse_csv_date(raw: str) -> Optional[date]:
    """Parse a date string from BOT CSV format."""
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y",
        "%Y%m%d", "%d %b %Y", "%d %B %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None
