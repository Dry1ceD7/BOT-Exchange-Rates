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
