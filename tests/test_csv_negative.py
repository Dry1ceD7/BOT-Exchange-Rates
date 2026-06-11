#!/usr/bin/env python3
"""
tests/test_csv_negative.py
---------------------------------------------------------------------------
Negative / edge-path regression coverage for core/csv_import.import_bot_csv.

Focus (complements tests/test_csv_import.py happy-path cases):
  - oversized CSV (> 15MB) rejected BEFORE the file is opened,
  - a non-empty file that yields 0 imported rows raises (silent mis-parse
    guard),
  - an invalid currency code is skipped (not counted), good rows still import,
  - Decimal EXACTNESS on a value like 35.1150 — asserted as `== Decimal`,
    `isinstance Decimal`, and explicitly NOT pytest.approx (no float taint),
  - a malformed numeric cell is logged + skipped, not counted as a rate.
---------------------------------------------------------------------------
"""

import logging
from datetime import date
from decimal import Decimal

import pytest

from core.csv_import import MAX_CSV_BYTES, import_bot_csv
from core.database import CacheDB


@pytest.fixture
def cache(tmp_path):
    db = CacheDB(db_path=str(tmp_path / "neg_cache.db"))
    yield db
    db.close()


def _write_csv(tmp_path, content: str, name: str = "rates.csv") -> str:
    path = str(tmp_path / name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# =========================================================================
#  SIZE GUARD
# =========================================================================

def test_oversized_csv_rejected_before_open(tmp_path, cache):
    """A CSV exceeding the featherweight 15MB limit must raise immediately."""
    path = str(tmp_path / "huge.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("Period,Currency_ID,Buying Transfer,Selling\n")
        # Pad past the byte limit. The guard checks os.path.getsize first.
        fh.write("z" * (MAX_CSV_BYTES + 1))

    with pytest.raises(ValueError, match="too large"):
        import_bot_csv(path, cache)


# =========================================================================
#  SILENT MIS-PARSE GUARD
# =========================================================================

def test_zero_imported_from_nonempty_raises(tmp_path, cache):
    """Valid headers + content rows that yield no rates must raise."""
    # Every data row carries content but an unparseable date -> 0 imported.
    content = (
        "Period,Currency_ID,Buying Transfer,Selling\n"
        "garbage-date,USD,34.5000,34.8000\n"
        "also-bad,EUR,37.0000,37.5000\n"
    )
    path = _write_csv(tmp_path, content)
    with pytest.raises(ValueError, match="No rates imported"):
        import_bot_csv(path, cache)


def test_empty_data_file_returns_zero_without_raising(tmp_path, cache):
    """Header-only file imports 0 rows and must NOT trip the guard."""
    content = "Period,Currency_ID,Buying Transfer,Selling\n"
    path = _write_csv(tmp_path, content)
    assert import_bot_csv(path, cache) == 0


# =========================================================================
#  INVALID CURRENCY
# =========================================================================

def test_invalid_currency_code_skipped(tmp_path, cache):
    """A non-3-letter currency is skipped; valid rows in the file still import."""
    content = (
        "Period,Currency_ID,Buying Transfer,Selling\n"
        "2025-02-03,US,34.5000,34.8000\n"      # 2 letters -> skipped
        "2025-02-03,USDD,34.5000,34.8000\n"    # 4 letters -> skipped
        "2025-02-03,12,34.5000,34.8000\n"      # digits -> skipped
        "2025-02-03,USD,34.6000,34.9000\n"     # valid -> imported
    )
    path = _write_csv(tmp_path, content)
    count = import_bot_csv(path, cache)
    assert count == 1
    # The skipped codes left nothing in the multi-rate table.
    assert cache.get_multi_rate(date(2025, 2, 3), "US", "buying_transfer") is None
    assert cache.get_multi_rate(date(2025, 2, 3), "USDD", "buying_transfer") is None
    # The valid row is present and exact.
    assert cache.get_multi_rate(
        date(2025, 2, 3), "USD", "buying_transfer",
    ) == Decimal("34.6000")


# =========================================================================
#  DECIMAL EXACTNESS (MATHEMATICAL TRUTH)
# =========================================================================

def test_decimal_exactness_35_1150(tmp_path, cache):
    """35.1150 must round-trip as an EXACT Decimal — no float contamination."""
    content = (
        "Period,Currency_ID,Buying Transfer,Selling\n"
        "2025-02-03,USD,35.1150,35.2250\n"
    )
    path = _write_csv(tmp_path, content)
    assert import_bot_csv(path, cache) == 1

    rate = cache.get_multi_rate(date(2025, 2, 3), "USD", "buying_transfer")
    # Exact-value contract: assert EXACT equality, never pytest.approx, which
    # would mask a float round-trip (35.1150 -> 35.11500000000000...).
    assert isinstance(rate, Decimal)
    assert not isinstance(rate, float)
    assert rate == Decimal("35.1150")
    # The literal trailing-zero digits are preserved verbatim. A float would
    # collapse 35.1150 to "35.115", so the exact text proves no float taint.
    assert str(rate) == "35.1150"
    assert str(rate) != str(35.1150)  # float repr drops the trailing zero

    selling = cache.get_multi_rate(date(2025, 2, 3), "USD", "selling")
    assert isinstance(selling, Decimal)
    assert selling == Decimal("35.2250")


# =========================================================================
#  MALFORMED NUMERIC CELL
# =========================================================================

def test_malformed_numeric_cell_skipped_not_counted(tmp_path, cache, caplog):
    """A non-numeric rate value is logged + skipped; the row is not counted
    unless another rate on the same row parses."""
    content = (
        "Period,Currency_ID,Buying Transfer,Selling\n"
        "2025-02-03,USD,not-a-number,also-bad\n"   # both unparseable -> skipped
    )
    path = _write_csv(tmp_path, content)

    # The whole file yields 0 rates -> silent-mis-parse guard raises.
    with (
        caplog.at_level(logging.DEBUG, logger="core.constants"),
        pytest.raises(ValueError, match="No rates imported"),
    ):
        import_bot_csv(path, cache)

    # Nothing was stored for that row.
    assert cache.get_multi_rate(
        date(2025, 2, 3), "USD", "buying_transfer",
    ) is None
    # The skip was observable in logs (parse_decimal_safe debug-logs).
    assert any(
        "non-numeric" in rec.getMessage().lower()
        for rec in caplog.records
    )


def test_partial_row_one_bad_one_good_counts_once(tmp_path, cache):
    """If one rate on a row is malformed but another parses, the row imports
    with only the valid rate stored."""
    content = (
        "Period,Currency_ID,Buying Transfer,Selling\n"
        "2025-02-03,USD,not-a-number,34.8000\n"   # buying bad, selling good
    )
    path = _write_csv(tmp_path, content)
    count = import_bot_csv(path, cache)
    assert count == 1

    # Bad cell -> not stored.
    assert cache.get_multi_rate(
        date(2025, 2, 3), "USD", "buying_transfer",
    ) is None
    # Good cell -> stored exactly.
    assert cache.get_multi_rate(
        date(2025, 2, 3), "USD", "selling",
    ) == Decimal("34.8000")


class TestNonPositiveRatesRejected:
    """A negative or zero exchange rate must never reach the cache.

    The anomaly guard is alert-only, so a stray minus sign that reached
    rates_multi would be written into the ExRate master sheet and multiply
    ledger amounts by a negative number.
    """

    def test_negative_rate_skipped(self):
        from core.csv_import import _parse_rate_4dp

        assert _parse_rate_4dp("-34.5678") is None

    def test_zero_rate_skipped(self):
        from core.csv_import import _parse_rate_4dp

        assert _parse_rate_4dp("0") is None

    def test_positive_rate_still_parses(self):
        from core.csv_import import _parse_rate_4dp

        assert _parse_rate_4dp("34.5678") == Decimal("34.5678")


class TestDuplicateHeadersRejected:
    """csv.DictReader keeps the LAST duplicate column — refuse ambiguity.

    An Excel-exported CSV with a repeated 'Value' header silently cached
    the wrong column's rate (the round-10 Excel-side duplicate-'Date' bug,
    CSV edition — but a CSV has no EX Rate anchor to resolve against, so
    the only safe answer is an explicit error).
    """

    def test_duplicate_value_header_raises(self, tmp_path, cache):
        fp = tmp_path / "dup.csv"
        fp.write_text(
            "Period,Currency_ID,Rate_Type,Value,Value\n"
            "2025-01-15,USD,buying_transfer,34.5678,99.9999\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicated header"):
            import_bot_csv(str(fp), cache)
