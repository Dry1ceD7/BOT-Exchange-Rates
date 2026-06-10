#!/usr/bin/env python3
"""
core/csv_import.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Offline CSV Fallback Importer
---------------------------------------------------------------------------
Imports exchange rate data from the Bank of Thailand's official
downloadable CSV format (wide) AND from this app's own lossless export
format (long: Period, Currency_ID, Rate_Type, Value) into the local
SQLite cache.

This allows the application to function during BOT API outages
or when internet connectivity is unavailable, and guarantees that an
export -> import -> export cycle is data-identical.
"""

import csv
import logging
import re
from decimal import Decimal
from pathlib import Path

from core.constants import parse_date, parse_decimal_safe, to_float
from core.logic import safe_to_decimal

logger = logging.getLogger(__name__)

# Featherweight constraint: reject oversized CSVs before opening them.
MAX_CSV_BYTES = 15 * 1024 * 1024  # 15 MB

# Flush accumulated multi-currency rows to SQLite in batches so a huge CSV
# never balloons memory on a 4GB legacy PC.
_FLUSH_EVERY = 5000

# Valid ISO-4217-style currency code: exactly three uppercase letters.
_CURRENCY_RE = re.compile(r"[A-Z]{3}")


def _parse_rate_4dp(raw) -> Decimal | None:
    """
    Parse a rate cell and quantize it to the project's 4dp invariant.

    Layer-1 hard gate: every Decimal that reaches the rates_multi cache
    (and from there the ExRate sheet) must be a finite, 4dp-quantized
    value constructed from the source string — safe_to_decimal applies the
    same quantize the engine and rate-audit paths use, so imported rates
    can never carry stray precision into Excel. Values that fail to parse
    or quantize are skipped (debug-logged), matching the importer's
    handling of other malformed fields.
    """
    dec = parse_decimal_safe(raw)
    if dec is None:
        return None
    if not dec.is_finite():
        logger.debug("Skipped non-finite rate value: %r", raw)
        return None
    quantized = safe_to_decimal(dec)
    if quantized is None:
        logger.debug("Skipped unquantizable rate value: %r", raw)
    return quantized


def import_bot_csv(csv_path: str, cache_db) -> int:
    """
    Parse a rate CSV and import all rates into CacheDB.

    Two formats are auto-detected:

    Long (this app's lossless export):
        Period, Currency_ID, Rate_Type, Value

    Wide (BOT download):
        Period, Currency, Buying Sight, Buying Transfer, Selling, Mid Rate

    Args:
        csv_path: Absolute path to the CSV file.
        cache_db: A CacheDB instance.

    Returns:
        Number of rate entries imported (rows that yielded >=1 stored rate).

    Raises:
        FileNotFoundError: If csv_path does not exist.
        ValueError: If the file is oversized, the format is unrecognizable,
            or a non-empty file imported zero rows (silent mis-parse guard).
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    size = csv_file.stat().st_size
    if size > MAX_CSV_BYTES:
        raise ValueError(
            f"CSV file too large: {size} bytes exceeds limit of {MAX_CSV_BYTES}."
        )

    imported = 0
    multi_entries = []

    def _flush():
        if multi_entries:
            cache_db.insert_multi_rates_bulk(multi_entries)
            multi_entries.clear()

    with csv_file.open(encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)

        # Restrict candidate delimiters so the sniffer can't pick something
        # exotic (e.g. a digit) out of financial data and mis-parse silently.
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(f, dialect=dialect)

        if reader.fieldnames is None:
            raise ValueError("CSV file has no header row.")

        field_map = {h.strip().lower(): h for h in reader.fieldnames}

        date_key = _find_column(field_map, ["period", "date", "วันที่"])
        currency_key = _find_column(
            field_map, ["currency_id", "currency", "สกุลเงิน"]
        )
        rate_type_key = _find_column(
            field_map, ["rate_type", "rate type"]
        )
        value_key = _find_column(field_map, ["value"])

        if date_key is None or currency_key is None:
            raise ValueError(
                "CSV must have 'Period'/'Date' and 'Currency'/'Currency_ID' "
                f"columns. Found columns: {list(reader.fieldnames)}"
            )

        is_long_format = rate_type_key is not None and value_key is not None

        if not is_long_format:
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

        data_rows = 0

        for row in reader:
            # Count any row that carries content (so an all-blank trailing
            # line doesn't trip the silent-mis-parse guard).
            if any((v or "").strip() for v in row.values()):
                data_rows += 1

            raw_date = (row.get(date_key) or "").strip()
            currency = (row.get(currency_key) or "").strip().upper()

            if not raw_date or not currency:
                continue

            if not _CURRENCY_RE.fullmatch(currency):
                logger.debug("Skipped invalid currency code: %r", currency)
                continue

            parsed_date = parse_date(raw_date)
            if parsed_date is None:
                logger.debug("Skipped unparseable date: %s", raw_date)
                continue

            date_str = parsed_date.strftime("%Y-%m-%d")

            # Collect (rate_type -> 4dp-quantized Decimal) for this row.
            rates: dict[str, Decimal] = {}

            if is_long_format:
                rate_type = (row.get(rate_type_key) or "").strip()
                dec = _parse_rate_4dp(row.get(value_key))
                if rate_type and dec is not None:
                    rates[rate_type] = dec
            else:
                for key, rate_type in [
                    (buying_tt_key, "buying_transfer"),
                    (selling_key, "selling"),
                    (buying_sight_key, "buying_sight"),
                    (mid_rate_key, "mid_rate"),
                ]:
                    if key:
                        dec = _parse_rate_4dp(row.get(key))
                        if dec is not None:
                            rates[rate_type] = dec

            if not rates:
                # Nothing captured for this row — do not count it as imported.
                continue

            for rate_type, value in rates.items():
                multi_entries.append((date_str, currency, rate_type, value))

            # Mirror into the legacy USD/EUR table for backward compatibility.
            # That table's columns are REAL; sqlite3 cannot bind Decimal, so
            # coerce to float here. The lossless source of truth stays in
            # rates_multi (stored as exact Decimal text above). insert_rate
            # upserts per-column, so these USD-only / EUR-only calls can never
            # null the sibling currency's columns for the same date.
            buy_tt = to_float(rates.get("buying_transfer"))
            sell = to_float(rates.get("selling"))
            if currency == "USD":
                cache_db.insert_rate(
                    parsed_date, usd_buying=buy_tt, usd_selling=sell,
                )
            elif currency == "EUR":
                cache_db.insert_rate(
                    parsed_date, eur_buying=buy_tt, eur_selling=sell,
                )

            imported += 1

            if len(multi_entries) >= _FLUSH_EVERY:
                _flush()

        _flush()

    if imported == 0 and data_rows > 0:
        logger.warning(
            "CSV import parsed 0 rows from a file with %d data row(s): %s "
            "(possible delimiter/format mismatch)", data_rows, csv_path,
        )
        raise ValueError(
            f"No rates imported from non-empty CSV: {csv_path}. "
            "Check the delimiter and column headers."
        )

    logger.info("CSV import complete: %d entries from %s", imported, csv_path)
    return imported


def _find_column(
    field_map: dict[str, str], candidates: list[str],
) -> str | None:
    """
    Find a column name from a list of candidates
    (case-insensitive match against normalized field map).
    """
    for candidate in candidates:
        norm = candidate.lower().strip()
        if norm in field_map:
            return field_map[norm]
    return None
