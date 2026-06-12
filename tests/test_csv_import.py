#!/usr/bin/env python3
"""Tests for core/csv_import.py — Offline CSV Import."""

from datetime import date
from decimal import Decimal

import pytest

from core.csv_import import MAX_CSV_BYTES, import_bot_csv


class TestCSVImport:
    """Test BOT CSV import functionality."""

    def _make_csv(self, tmp_path, content: str) -> str:
        """Create a temporary CSV file with given content."""
        csv_path = str(tmp_path / "test_rates.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(content)
        return csv_path

    def test_valid_csv_imports(self, tmp_path):
        """Test that a valid BOT CSV imports correctly."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,USD,34.5000,34.8000\n"
            "2025-01-02,EUR,37.2000,37.6000\n"
            "2025-01-03,USD,34.6000,34.9000\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)

        db_path = str(tmp_path / "test_cache.db")
        cache = CacheDB(db_path=db_path)

        count = import_bot_csv(csv_path, cache)
        assert count == 3

        # Verify rates were inserted into multi-currency table.
        # Must be an EXACT Decimal (no float/approx contamination).
        rate = cache.get_multi_rate(
            date(2025, 1, 2), "USD", "buying_transfer",
        )
        assert isinstance(rate, Decimal)
        assert rate == Decimal("34.5000")

        cache.close()

    def test_decimal_exact_preservation(self, tmp_path):
        """A 4dp value must survive import as an exact Decimal."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,USD,35.1150,35.2250\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        import_bot_csv(csv_path, cache)
        rate = cache.get_multi_rate(
            date(2025, 1, 2), "USD", "buying_transfer",
        )
        assert isinstance(rate, Decimal)
        assert rate == Decimal("35.1150")
        cache.close()

    def test_wide_format_quantizes_excess_precision_to_4dp(self, tmp_path):
        """F30: a >4dp wide-format value must land in the cache as the exact
        4dp-quantized Decimal (ROUND_HALF_EVEN), never the raw unquantized
        Decimal — otherwise stray precision reaches ExRate cells."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,USD,42.123456,34.800049\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        assert import_bot_csv(csv_path, cache) == 1

        buy = cache.get_multi_rate(date(2025, 1, 2), "USD", "buying_transfer")
        assert isinstance(buy, Decimal)
        assert str(buy) == "42.1235"          # rounded up at the 5th dp
        assert buy.as_tuple().exponent == -4  # stored at exactly 4dp

        sell = cache.get_multi_rate(date(2025, 1, 2), "USD", "selling")
        assert str(sell) == "34.8000"         # rounded down at the 5th dp
        assert sell.as_tuple().exponent == -4

        # The legacy USD/EUR mirror must carry the quantized value too.
        row = cache.get_rate(date(2025, 1, 2))
        assert row["usd_buying"] == Decimal("42.1235")
        assert row["usd_selling"] == Decimal("34.8000")
        cache.close()

    def test_long_format_quantizes_excess_precision_to_4dp(self, tmp_path):
        """F30: the long-format path applies the same 4dp quantize as wide."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Rate_Type,Value\n"
            "2025-01-02,GBP,mid_rate,42.123456\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        assert import_bot_csv(csv_path, cache) == 1
        rate = cache.get_multi_rate(date(2025, 1, 2), "GBP", "mid_rate")
        assert isinstance(rate, Decimal)
        assert str(rate) == "42.1235"
        assert rate.as_tuple().exponent == -4
        cache.close()

    def test_long_and_wide_paths_quantize_identically(self, tmp_path):
        """F30: the same over-precise digits yield the identical stored
        Decimal through either format — the two paths must never diverge."""
        from core.database import CacheDB

        wide = self._make_csv(
            tmp_path,
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,USD,42.123456,\n",
        )
        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        import_bot_csv(wide, cache)
        from_wide = cache.get_multi_rate(
            date(2025, 1, 2), "USD", "buying_transfer",
        )

        long_path = str(tmp_path / "long.csv")
        with open(long_path, "w", encoding="utf-8") as f:
            f.write(
                "Period,Currency_ID,Rate_Type,Value\n"
                "2025-01-03,USD,buying_transfer,42.123456\n"
            )
        import_bot_csv(long_path, cache)
        from_long = cache.get_multi_rate(
            date(2025, 1, 3), "USD", "buying_transfer",
        )

        assert from_wide == from_long == Decimal("42.1235")
        assert str(from_wide) == str(from_long)
        cache.close()

    def test_non_finite_wide_values_skipped(self, tmp_path):
        """F30: NaN/Infinity parse as Decimals but must never reach the
        cache; the row is skipped while a valid sibling row still imports."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,USD,NaN,Infinity\n"    # both non-finite -> skipped
            "2025-01-03,USD,34.6000,34.9000\n"  # valid
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        assert import_bot_csv(csv_path, cache) == 1
        assert cache.get_multi_rate(
            date(2025, 1, 2), "USD", "buying_transfer",
        ) is None
        assert cache.get_multi_rate(
            date(2025, 1, 3), "USD", "buying_transfer",
        ) == Decimal("34.6000")
        cache.close()

    def test_non_finite_long_value_skipped(self, tmp_path):
        """F30: the long-format path rejects non-finite values the same way."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Rate_Type,Value\n"
            "2025-01-02,GBP,mid_rate,NaN\n"
            "2025-01-03,GBP,mid_rate,44.1234\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        assert import_bot_csv(csv_path, cache) == 1
        assert cache.get_multi_rate(
            date(2025, 1, 2), "GBP", "mid_rate",
        ) is None
        assert cache.get_multi_rate(
            date(2025, 1, 3), "GBP", "mid_rate",
        ) == Decimal("44.1234")
        cache.close()

    def test_long_format_imports(self, tmp_path):
        """The app's own long export format must import losslessly."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Rate_Type,Value\n"
            "2025-01-02,GBP,mid_rate,44.1234\n"
            "2025-01-02,USD,buying_transfer,34.5000\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        count = import_bot_csv(csv_path, cache)
        assert count == 2
        assert cache.get_multi_rate(
            date(2025, 1, 2), "GBP", "mid_rate",
        ) == Decimal("44.1234")
        cache.close()

    def test_imported_non_usd_eur_rate_reachable_via_get_rates_multi(
        self, tmp_path,
    ):
        """CSV-imported GBP/JPY rates must be reachable by the cache API the
        ledger path reads (get_rates_multi), not stranded in rates_multi.

        This is the read-back guarantee behind the multi-currency ledger fix:
        the importer accepts any 3-letter code, and a cache-first extra-currency
        fetch keyed on the chosen rate type must find those exact rows.
        """
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,GBP,44.1234,45.6789\n"
            "2025-01-03,JPY,0.2300,0.2400\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        import_bot_csv(csv_path, cache)

        gbp_buy = cache.get_rates_multi(
            date(2025, 1, 1), date(2025, 1, 31), "GBP", "buying_transfer",
        )
        assert gbp_buy == {date(2025, 1, 2): Decimal("44.1234")}

        jpy_sell = cache.get_rates_multi(
            date(2025, 1, 1), date(2025, 1, 31), "JPY", "selling",
        )
        assert jpy_sell == {date(2025, 1, 3): Decimal("0.2400")}
        cache.close()

    def test_long_format_non_usd_eur_reachable_via_get_rates_multi(
        self, tmp_path,
    ):
        """The app's own long export of a GBP rate round-trips back through
        get_rates_multi for the matching rate type."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Rate_Type,Value\n"
            "2025-02-10,GBP,buying_transfer,44.5500\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        import_bot_csv(csv_path, cache)
        rates = cache.get_rates_multi(
            date(2025, 2, 1), date(2025, 2, 28), "GBP", "buying_transfer",
        )
        assert rates == {date(2025, 2, 10): Decimal("44.5500")}
        cache.close()

    def test_zero_imported_raises(self, tmp_path):
        """A non-empty file that parses no rows must raise, not pass silently."""
        from core.database import CacheDB

        # Valid headers but every data row has an unparseable date.
        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "not-a-date,USD,34.5,34.8\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        with pytest.raises(ValueError, match="No rates imported"):
            import_bot_csv(csv_path, cache)
        cache.close()

    def test_invalid_currency_skipped(self, tmp_path):
        """A bad currency code is skipped; a good one in the same file imports."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,US,34.5,34.8\n"        # too short -> skipped
            "2025-01-02,USD,34.6,34.9\n"       # valid
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        count = import_bot_csv(csv_path, cache)
        assert count == 1
        assert cache.get_multi_rate(
            date(2025, 1, 2), "US", "buying_transfer",
        ) is None
        cache.close()

    def test_oversized_csv_rejected(self, tmp_path):
        """A CSV over MAX_CSV_BYTES must be rejected before opening."""
        from core.database import CacheDB

        csv_path = str(tmp_path / "big.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Period,Currency_ID,Buying Transfer,Selling\n")
            f.write("x" * (MAX_CSV_BYTES + 1))
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        with pytest.raises(ValueError, match="too large"):
            import_bot_csv(csv_path, cache)
        cache.close()

    def test_file_not_found(self, tmp_path):
        """Test that FileNotFoundError is raised for missing file."""
        from core.database import CacheDB

        db_path = str(tmp_path / "test_cache.db")
        cache = CacheDB(db_path=db_path)

        with pytest.raises(FileNotFoundError):
            import_bot_csv("/nonexistent/file.csv", cache)

        cache.close()

    def test_invalid_format(self, tmp_path):
        """Test that ValueError is raised for unrecognizable format."""
        from core.database import CacheDB

        csv_content = "col_a,col_b,col_c\n1,2,3\n"
        csv_path = self._make_csv(tmp_path, csv_content)

        db_path = str(tmp_path / "test_cache.db")
        cache = CacheDB(db_path=db_path)

        with pytest.raises(ValueError, match="Period"):
            import_bot_csv(csv_path, cache)

        cache.close()

    def test_mixed_date_formats(self, tmp_path):
        """Test that various date formats are handled."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-03-01,USD,34.50,34.80\n"
            "01/03/2025,EUR,37.20,37.60\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)

        db_path = str(tmp_path / "test_cache.db")
        cache = CacheDB(db_path=db_path)

        count = import_bot_csv(csv_path, cache)
        assert count == 2

        cache.close()

    def test_empty_csv(self, tmp_path):
        """Test that empty CSV returns 0."""
        from core.database import CacheDB

        csv_content = "Period,Currency_ID,Buying Transfer,Selling\n"
        csv_path = self._make_csv(tmp_path, csv_content)

        db_path = str(tmp_path / "test_cache.db")
        cache = CacheDB(db_path=db_path)

        count = import_bot_csv(csv_path, cache)
        assert count == 0

        cache.close()

    def test_wide_csv_interleaved_currencies_keep_all_columns(self, tmp_path):
        """F1 regression: a wide CSV interleaving USD/EUR rows per date must
        leave ALL four legacy-table columns populated. The old INSERT OR
        REPLACE mirror wiped the first currency's columns on the second
        per-currency insert_rate call for the same date."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-06,USD,34.0512,34.3209\n"
            "2025-01-06,EUR,35.4023,36.1217\n"
            "2025-01-07,USD,34.1020,34.3718\n"
            "2025-01-07,EUR,35.4521,36.1722\n"
        )
        csv_path = self._make_csv(tmp_path, csv_content)
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        assert import_bot_csv(csv_path, cache) == 4

        row = cache.get_rate(date(2025, 1, 6))
        assert row["usd_buying"] == Decimal("34.0512")
        assert row["usd_selling"] == Decimal("34.3209")
        assert row["eur_buying"] == Decimal("35.4023")
        assert row["eur_selling"] == Decimal("36.1217")
        row = cache.get_rate(date(2025, 1, 7))
        assert row["usd_buying"] == Decimal("34.1020")
        assert row["eur_selling"] == Decimal("36.1722")
        cache.close()


# =========================================================================
#  END-TO-END CHAIN: wide CSV import → engine run → workbook (F1)
# =========================================================================

class TestWideCSVToWorkbookChain:
    """F1 regression chain demanded by the audit: a wide BOT CSV interleaving
    USD/EUR rows per date must survive import (no per-currency wipe) and feed
    a full ledger run.

    The BOT API mock returns NO rates, so every value that reaches the
    workbook's ExRate sheet must have come from the CSV import. Before the
    fix, the EUR mirror call nulled the USD columns (EUR was the last row per
    date), the engine never re-fetched the date (per-date cache hit), and the
    trading-day cells stayed blank.
    """

    _TODAY = date(2025, 1, 7)  # Tuesday — bounds the ExRate sheet span

    # (date_str, usd_buy, usd_sell, eur_buy, eur_sell) — exact 4dp strings.
    _ROWS = [
        ("2025-01-06", "34.0512", "34.3209", "35.4023", "36.1217"),
        ("2025-01-07", "34.1020", "34.3718", "35.4521", "36.1722"),
    ]

    def test_csv_rates_reach_workbook_for_both_currencies(
        self, tmp_path, tmp_cache, ledger_xlsx, monkeypatch,
    ):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        import openpyxl

        from core.engine import LedgerEngine

        # ── 1. Wide CSV, USD/EUR interleaved per date (the wipe pattern) ──
        lines = ["Period,Currency_ID,Buying Transfer,Selling"]
        for d_str, ub, us, eb, es in self._ROWS:
            lines.append(f"{d_str},USD,{ub},{us}")
            lines.append(f"{d_str},EUR,{eb},{es}")
        csv_path = str(tmp_path / "wide_rates.csv")
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        assert import_bot_csv(csv_path, tmp_cache) == 4

        # ── 2. Engine run: the API mock yields nothing — cache must serve ──
        monkeypatch.setattr("core.engine.bot_today", lambda: self._TODAY)
        monkeypatch.setattr("core.exrate_sheet.bot_today", lambda: self._TODAY)

        api = MagicMock()
        api.get_exchange_rates = AsyncMock(return_value=[])
        api.get_holidays = AsyncMock(return_value=[])
        engine = LedgerEngine(api, cache=tmp_cache, backup=MagicMock())

        path = ledger_xlsx({"Jan": [
            (date(2025, 1, 6), "USD"),
            (date(2025, 1, 7), "EUR"),
        ]})
        result = asyncio.run(
            engine.process_ledger(path, start_date="2025-01-06")
        )
        assert result == path

        # The CSV covered every weekday column-complete → zero rate calls
        # (also proves the per-column miss check does not over-fetch).
        api.get_exchange_rates.assert_not_called()

        # ── 3. Reload: exact 4dp values for BOTH currencies ──
        wb = openpyxl.load_workbook(path)
        try:
            ws = wb["ExRate"]
            by_date = {}
            for row in ws.iter_rows(min_row=2, values_only=True):
                d = row[0]
                d = d.date() if hasattr(d, "date") else d
                by_date[d] = row[1:5]
        finally:
            wb.close()

        for d_str, *expected in self._ROWS:
            cells = by_date[date.fromisoformat(d_str)]
            assert all(v is not None for v in cells), (d_str, cells)
            got = [Decimal(str(v)) for v in cells]
            assert got == [Decimal(e) for e in expected], (d_str, cells)


class TestImportAtomicity:
    """Round 11: the whole import is ONE transaction — a mid-file failure
    (e.g. an invalid UTF-8 byte after the first 5000-row flush) rolls back
    every batch instead of leaving a silently half-imported cache."""

    def test_mid_file_failure_rolls_back_all_batches(self, tmp_path):
        from datetime import timedelta

        from core.database import CacheDB

        # >5000 long-format USD rows (so both rates_multi AND the legacy
        # rates mirror receive writes), then a row with a raw invalid UTF-8
        # byte that blows up AFTER the first flush already ran.
        lines = ["Period,Currency_ID,Rate_Type,Value"]
        d = date(2020, 1, 1)
        for i in range(5500):
            lines.append(
                f"{(d + timedelta(days=i)).isoformat()},USD,"
                f"buying_transfer,34.5000"
            )
        body = ("\n".join(lines) + "\n").encode("utf-8")
        body += b"2026-01-01,USD,buying_transfer,3\xff4.5000\n"
        csv_path = tmp_path / "bad_tail.csv"
        csv_path.write_bytes(body)

        cache = CacheDB(db_path=str(tmp_path / "atomic.db"))
        try:
            # Round 11 contract change: the importer now retries the parse
            # under fallback encodings (utf-8-sig -> cp874; 0xff is invalid
            # in BOTH), then raises a clear ValueError naming the tried
            # encodings instead of leaking a raw UnicodeDecodeError. Each
            # failed attempt is its own rolled-back transaction, so the
            # atomicity guarantee below is unchanged.
            with pytest.raises(ValueError, match="encoding"):
                import_bot_csv(str(csv_path), cache)
            conn = cache._conn()
            # Pre-fix: 5000 rows in rates_multi and their legacy mirrors
            # were already committed. Post-fix: full rollback.
            assert conn.execute(
                "SELECT COUNT(*) FROM rates_multi"
            ).fetchone()[0] == 0
            assert conn.execute(
                "SELECT COUNT(*) FROM rates"
            ).fetchone()[0] == 0
        finally:
            cache.close()


# =========================================================================
#  ENCODING FALLBACK: cp874 (Thai-Windows ANSI) + UTF-16 (Excel "Unicode
#  Text") saves must import; undecodable bytes get a clear error. (Round 11)
# =========================================================================

# Thai BOT column headers as written by Thai-locale Excel exports.
_THAI_HEADER = "วันที่,สกุลเงิน,อัตราซื้อ (โอน),อัตราขาย"


class TestEncodingFallback:
    """The importer advertises Thai header support, but a hardcoded
    encoding='utf-8-sig' meant those headers could only ever match in UTF-8
    files. cp874 is the DEFAULT 'CSV (Comma delimited)' encoding on Thai
    Windows Excel; 'Unicode Text' saves are UTF-16 with a BOM."""

    def test_cp874_thai_headers_import(self, tmp_path):
        """A wide CSV with Thai headers saved as cp874 must import."""
        from core.database import CacheDB

        content = (
            f"{_THAI_HEADER}\r\n"
            "2025-01-02,USD,34.5000,34.8000\r\n"
            "2025-01-02,EUR,37.2000,37.6000\r\n"
        )
        csv_path = tmp_path / "thai_ansi.csv"
        csv_path.write_bytes(content.encode("cp874"))

        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        try:
            assert import_bot_csv(str(csv_path), cache) == 2
            assert cache.get_multi_rate(
                date(2025, 1, 2), "USD", "buying_transfer",
            ) == Decimal("34.5000")
            assert cache.get_multi_rate(
                date(2025, 1, 2), "EUR", "selling",
            ) == Decimal("37.6000")
            # Legacy mirror must be populated through the same path.
            row = cache.get_rate(date(2025, 1, 2))
            assert row["usd_buying"] == Decimal("34.5000")
            assert row["eur_selling"] == Decimal("37.6000")
        finally:
            cache.close()

    def test_utf16_unicode_text_import(self, tmp_path):
        """Excel's 'Unicode Text' save = UTF-16 with BOM, tab-delimited."""
        from core.database import CacheDB

        header = _THAI_HEADER.replace(",", "\t")
        content = (
            f"{header}\r\n"
            "2025-01-02\tUSD\t34.5000\t34.8000\r\n"
            "2025-01-03\tUSD\t34.6000\t34.9000\r\n"
        )
        csv_path = tmp_path / "unicode_text.csv"
        # Python's 'utf-16' encoder prepends the (little-endian) BOM,
        # matching what Excel writes.
        csv_path.write_bytes(content.encode("utf-16"))

        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        try:
            assert import_bot_csv(str(csv_path), cache) == 2
            assert cache.get_multi_rate(
                date(2025, 1, 3), "USD", "selling",
            ) == Decimal("34.9000")
        finally:
            cache.close()

    def test_utf16_big_endian_bom_import(self, tmp_path):
        """A big-endian UTF-16 BOM is honored too (BOM-detected codec)."""
        from core.database import CacheDB

        content = (
            "Period\tCurrency_ID\tBuying Transfer\tSelling\r\n"
            "2025-01-02\tUSD\t34.5000\t34.8000\r\n"
        )
        csv_path = tmp_path / "utf16be.csv"
        csv_path.write_bytes(b"\xfe\xff" + content.encode("utf-16-be"))

        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        try:
            assert import_bot_csv(str(csv_path), cache) == 1
        finally:
            cache.close()

    def test_undecodable_file_raises_clear_error(self, tmp_path):
        """Total decode failure must name the tried encodings, not leak a
        raw UnicodeDecodeError."""
        from core.database import CacheDB

        # 0xFF is invalid UTF-8 here AND undefined in cp874; no UTF-16 BOM.
        csv_path = tmp_path / "binary.csv"
        csv_path.write_bytes(b"Period,Currency\n\xff\x81\xff junk")

        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        try:
            with pytest.raises(ValueError, match="utf-8-sig") as excinfo:
                import_bot_csv(str(csv_path), cache)
            assert "cp874" in str(excinfo.value)
            assert "encoding" in str(excinfo.value).lower()
        finally:
            cache.close()

    def test_plain_utf8_still_imports(self, tmp_path):
        """The first-choice encoding path is unchanged for UTF-8 files."""
        from core.database import CacheDB

        csv_path = tmp_path / "utf8.csv"
        csv_path.write_bytes(
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-02,USD,34.5000,34.8000\n".encode("utf-8-sig")
        )
        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        try:
            assert import_bot_csv(str(csv_path), cache) == 1
        finally:
            cache.close()


# =========================================================================
#  Excel 'sep=<char>' first-line directive (Round 11)
# =========================================================================

class TestSepDirective:
    """Excel honors an optional 'sep=<char>' first line (written by
    LibreOffice / added for Excel compatibility). It must be treated as a
    delimiter declaration, never mistaken for the header row."""

    def _import(self, tmp_path, content: str) -> tuple:
        from core.database import CacheDB

        csv_path = tmp_path / "sep.csv"
        csv_path.write_text(content, encoding="utf-8")
        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        return csv_path, cache

    def test_long_format_with_sep_comma_directive(self, tmp_path):
        csv_path, cache = self._import(
            tmp_path,
            "sep=,\n"
            "Period,Currency_ID,Rate_Type,Value\n"
            "2025-01-02,USD,buying_transfer,34.5000\n",
        )
        try:
            assert import_bot_csv(str(csv_path), cache) == 1
            assert cache.get_multi_rate(
                date(2025, 1, 2), "USD", "buying_transfer",
            ) == Decimal("34.5000")
        finally:
            cache.close()

    def test_wide_semicolon_file_with_sep_directive(self, tmp_path):
        """'sep=;' declares the delimiter — parsing must use ';'."""
        csv_path, cache = self._import(
            tmp_path,
            "sep=;\n"
            "Period;Currency_ID;Buying Transfer;Selling\n"
            "2025-01-02;USD;34.5000;34.8000\n",
        )
        try:
            assert import_bot_csv(str(csv_path), cache) == 1
            assert cache.get_multi_rate(
                date(2025, 1, 2), "USD", "selling",
            ) == Decimal("34.8000")
        finally:
            cache.close()

    def test_sep_directive_case_insensitive_with_crlf_and_bom(self, tmp_path):
        """'SEP=;' + CRLF + UTF-8 BOM (the Excel-edited shape) parses."""
        from core.database import CacheDB

        content = (
            "SEP=;\r\n"
            "Period;Currency_ID;Buying Transfer;Selling\r\n"
            "2025-01-02;USD;34.5000;34.8000\r\n"
        )
        csv_path = tmp_path / "sep_bom.csv"
        csv_path.write_bytes(content.encode("utf-8-sig"))
        cache = CacheDB(db_path=str(tmp_path / "c.db"))
        try:
            assert import_bot_csv(str(csv_path), cache) == 1
        finally:
            cache.close()

    def test_file_with_only_sep_line_raises_no_header(self, tmp_path):
        csv_path, cache = self._import(tmp_path, "sep=,\n")
        try:
            with pytest.raises(ValueError, match="no header row"):
                import_bot_csv(str(csv_path), cache)
        finally:
            cache.close()


# =========================================================================
#  Legacy USD/EUR mirror is batched, never per-row (Round 11)
# =========================================================================

class TestLegacyMirrorBatched:
    def test_usd_eur_mirror_never_calls_per_row_insert_rate(self, tmp_path):
        """The legacy mirror must flow through insert_rates_bulk (one
        executemany per batch) — per-row insert_rate is one COMMIT (= one
        WAL fsync on the target HDD) per USD/EUR row, ~53x slower."""
        from core.database import CacheDB

        csv_content = (
            "Period,Currency_ID,Buying Transfer,Selling\n"
            "2025-01-06,USD,34.0512,34.3209\n"
            "2025-01-06,EUR,35.4023,36.1217\n"
        )
        csv_path = tmp_path / "mirror.csv"
        csv_path.write_text(csv_content, encoding="utf-8")
        cache = CacheDB(db_path=str(tmp_path / "c.db"))

        def _no_per_row(*args, **kwargs):
            raise AssertionError(
                "importer must not mirror USD/EUR via per-row insert_rate"
            )

        cache.insert_rate = _no_per_row
        try:
            assert import_bot_csv(str(csv_path), cache) == 2
            row = cache.get_rate(date(2025, 1, 6))
            assert row["usd_buying"] == Decimal("34.0512")
            assert row["usd_selling"] == Decimal("34.3209")
            assert row["eur_buying"] == Decimal("35.4023")
            assert row["eur_selling"] == Decimal("36.1217")
        finally:
            cache.close()
