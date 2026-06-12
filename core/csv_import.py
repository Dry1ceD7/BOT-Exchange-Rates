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

import codecs
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

# Encodings attempted for a BOM-less CSV, in priority order. 'utf-8-sig'
# covers plain UTF-8 and Excel's "CSV UTF-8" save (BOM); 'cp874' is the
# Thai-Windows ANSI code page that Excel's default "CSV (Comma delimited)"
# save uses on Thai Windows — the importer advertises Thai header support,
# so those files must decode too. UTF-16 (Excel's "Unicode Text" save) is
# BOM-detected separately in _candidate_encodings: decoding BOM-less ASCII
# as UTF-16 would silently produce garbage, so it is never attempted blind.
_FALLBACK_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp874")


def _candidate_encodings(csv_file: Path) -> tuple[str, ...]:
    """Return the encodings to attempt for this file, in priority order.

    A UTF-16 BOM is definitive: 'utf-8-sig' would fail at byte 0 and
    'cp874' would mis-decode the BOM bytes into garbage characters, so the
    BOM-honoring codec is the only sensible candidate. Excel's "Unicode
    Text" save always writes the BOM, so BOM detection covers it.
    """
    try:
        with csv_file.open("rb") as fb:
            head = fb.read(2)
    except OSError:
        return _FALLBACK_ENCODINGS
    if head in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return ("utf-16",)
    return _FALLBACK_ENCODINGS


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
        return None
    # A non-positive exchange rate is impossible — a stray minus sign (or
    # zero) reaching the cache would multiply ledger amounts by a negative
    # number, and the anomaly guard is alert-only so nothing downstream
    # would block it.
    if quantized <= 0:
        logger.debug("Skipped non-positive rate value: %r", raw)
        return None
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
        ValueError: If the file is oversized, undecodable in every supported
            encoding (utf-8-sig / utf-16-with-BOM / cp874), the format is
            unrecognizable, or a non-empty file imported zero rows (silent
            mis-parse guard).
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
    data_rows = 0
    tried: list[str] = []
    for encoding in _candidate_encodings(csv_file):
        try:
            # cache_db.transaction(): each parse attempt is ONE atomic scope —
            # the per-batch executemany flushes (rates_multi AND the batched
            # USD/EUR legacy mirror) defer their commits until the file fully
            # parses, so a mid-file failure (e.g. an invalid byte after 5000
            # rows) rolls back instead of leaving a silently half-imported
            # cache. The 5000-row batching stays: it bounds Python list memory
            # (featherweight 4GB), only the COMMIT moved to the end. WAL +
            # connection-per-thread means this open write txn never blocks
            # other threads' readers, and the 15MB CSV cap keeps it short.
            with cache_db.transaction(), csv_file.open(encoding=encoding) as f:
                imported, data_rows = _import_stream(f, cache_db)
            break
        except UnicodeError:
            # Wrong encoding guess (UnicodeDecodeError mid-stream, or a
            # truncated UTF-16 tail). The transaction scope above already
            # rolled back anything this attempt flushed, so the next
            # candidate starts against a clean cache.
            tried.append(encoding)
            logger.debug("CSV decode as %s failed: %s", encoding, csv_path)
    else:
        raise ValueError(
            "CSV file is not readable text in any supported encoding "
            f"(tried: {', '.join(tried)}): {csv_path}. Re-save the file "
            "from Excel as 'CSV UTF-8 (Comma delimited)' and try again."
        )

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


def _import_stream(f, cache_db) -> tuple[int, int]:
    """Parse one decoded CSV text stream and stage every rate into cache_db.

    Returns ``(imported, data_rows)``. Raises UnicodeError when the caller's
    encoding guess was wrong (the caller retries with the next candidate)
    and ValueError for structural problems (no header / missing columns /
    duplicated headers). Must run inside a ``cache_db.transaction()`` scope.
    """
    imported = 0
    multi_entries: list[tuple] = []
    # Legacy USD/EUR mirror rows are batched too: per-row insert_rate is one
    # COMMIT (= one WAL fsync on the target HDD) per row, ~53x slower than
    # one executemany per batch. insert_rates_bulk runs the identical
    # per-column COALESCE upsert SQL through the same _rate_text
    # normalization, and executemany preserves row order, so last-write-wins
    # for duplicate dates and sibling-column preservation are unchanged.
    legacy_entries: list[tuple] = []

    def _flush():
        if multi_entries:
            cache_db.insert_multi_rates_bulk(multi_entries)
            multi_entries.clear()
        if legacy_entries:
            cache_db.insert_rates_bulk(legacy_entries)
            legacy_entries.clear()

    # Excel honors an optional 'sep=<char>' first-line directive (written
    # by LibreOffice / added manually for Excel compatibility). Treat it as
    # authoritative: use the declared delimiter and start parsing from the
    # second line, so it is never mistaken for the header row.
    first_line = f.readline()
    sep_match = re.fullmatch(
        r"sep=(.)", first_line.rstrip("\r\n"), flags=re.IGNORECASE
    )
    if sep_match:
        reader = csv.DictReader(f, delimiter=sep_match.group(1))
    else:
        f.seek(0)  # not a directive — rewind (the codec re-skips any BOM)
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

    # Reject ambiguous headers: csv.DictReader keeps the LAST duplicate
    # column, so an Excel-exported CSV with a repeated 'Value'/'Date'
    # header would silently cache the wrong column's rate (mirrors the
    # Excel-side duplicate-'Date' fix — but a CSV has no EX Rate anchor
    # to resolve against, so the only safe answer is an explicit error).
    normalized = [h.strip().lower() for h in reader.fieldnames]
    duplicates = sorted({h for h in normalized if normalized.count(h) > 1 and h})
    if duplicates:
        raise ValueError(
            "CSV has duplicated header column(s): "
            f"{', '.join(duplicates)} — remove the duplicate column(s) "
            "and re-export."
        )

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

        # Mirror into the legacy USD/EUR table for backward compatibility —
        # BATCHED into legacy_entries and drained by _flush via
        # insert_rates_bulk (per-row insert_rate would COMMIT once per row).
        # sqlite3 cannot bind Decimal, so coerce to float here; the lossless
        # source of truth stays in rates_multi (stored as exact Decimal text
        # above). The upsert is per-column (COALESCE), so these USD-only /
        # EUR-only tuples can never null the sibling currency's columns for
        # the same date.
        buy_tt = to_float(rates.get("buying_transfer"))
        sell = to_float(rates.get("selling"))
        if currency == "USD":
            legacy_entries.append((date_str, buy_tt, sell, None, None))
        elif currency == "EUR":
            legacy_entries.append((date_str, None, None, buy_tt, sell))

        imported += 1

        # legacy_entries grows at most one tuple per row while multi_entries
        # grows >=1 per row, so this single check bounds BOTH lists; the
        # final _flush() below drains the tails.
        if len(multi_entries) >= _FLUSH_EVERY:
            _flush()

    _flush()
    return imported, data_rows


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
