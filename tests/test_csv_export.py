#!/usr/bin/env python3
"""Tests for core/csv_export.py — Rate data CSV export (lossless long format)."""

import csv
import os
from decimal import Decimal
from unittest.mock import MagicMock

from core.constants import csv_safe, format_rate_value
from core.csv_export import export_rates_csv

# ── Unit tests for format_rate_value ─────────────────────────────────────

class TestFmt:
    """Tests for the format_rate_value formatting helper."""

    def test_none_returns_empty(self):
        assert format_rate_value(None) == ""

    def test_float_formats_four_decimals(self):
        assert format_rate_value(34.5) == "34.5000"

    def test_small_value(self):
        assert format_rate_value(0.1234) == "0.1234"

    def test_integer_formats_as_float(self):
        assert format_rate_value(1) == "1.0000"

    def test_string_number(self):
        assert format_rate_value("42.123") == "42.1230"

    def test_decimal_exact_no_float_roundtrip(self):
        # Decimal path must not detour through float.
        assert format_rate_value(Decimal("35.1150")) == "35.1150"
        assert format_rate_value(Decimal("35.115")) == "35.1150"


# ── Unit tests for csv_safe ──────────────────────────────────────────────

class TestCsvSafe:
    """Tests for the formula-injection sanitizer."""

    def test_none_is_empty(self):
        assert csv_safe(None) == ""

    def test_plain_text_unchanged(self):
        assert csv_safe("USD") == "USD"

    def test_leading_equals_neutralized(self):
        assert csv_safe("=SUM(A1:A9)") == "'=SUM(A1:A9)"

    def test_leading_plus_minus_at_neutralized(self):
        assert csv_safe("+1") == "'+1"
        assert csv_safe("-cmd") == "'-cmd"
        assert csv_safe("@x") == "'@x"

    def test_strips_newlines_and_tabs(self):
        assert csv_safe("a\r\nb\tc") == "a  b c"


# ── Integration tests for export_rates_csv ───────────────────────────────

class TestExportRatesCsv:
    """Tests for the main export function (long format)."""

    def _make_mock_db(self, rows):
        """Create a mock CacheDB with specified get_all_multi_rates() return."""
        db = MagicMock()
        db.get_all_multi_rates.return_value = rows
        return db

    def test_export_empty_db(self, tmp_path):
        """Exporting from an empty DB should write headers only."""
        csv_path = str(tmp_path / "empty.csv")
        db = self._make_mock_db([])
        count = export_rates_csv(csv_path, db)
        assert count == 0
        assert os.path.exists(csv_path)
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 1  # Header only
        assert rows[0] == ["Period", "Currency_ID", "Rate_Type", "Value"]

    def test_export_writes_rows(self, tmp_path):
        csv_path = str(tmp_path / "rates.csv")
        db = self._make_mock_db([
            ("2025-01-02", "USD", "buying_transfer", Decimal("34.5000")),
            ("2025-01-02", "USD", "selling", Decimal("35.0000")),
            ("2025-01-02", "GBP", "mid_rate", Decimal("44.1234")),
        ])
        count = export_rates_csv(csv_path, db)
        assert count == 3
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert rows[1] == ["2025-01-02", "USD", "buying_transfer", "34.5000"]
        assert rows[3] == ["2025-01-02", "GBP", "mid_rate", "44.1234"]

    def test_export_skips_none_value(self, tmp_path):
        csv_path = str(tmp_path / "skip.csv")
        db = self._make_mock_db([
            ("2025-01-02", "USD", "buying_transfer", None),
            ("2025-01-02", "USD", "selling", Decimal("35.0000")),
        ])
        count = export_rates_csv(csv_path, db)
        assert count == 1

    def test_export_neutralizes_formula_injection(self, tmp_path):
        """A malicious currency literal must be quoted, not emitted raw."""
        csv_path = str(tmp_path / "evil.csv")
        db = self._make_mock_db([
            ("2025-01-02", "=cmd|'/c calc'!A1", "selling", Decimal("1.0000")),
        ])
        export_rates_csv(csv_path, db)
        with open(csv_path, encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        assert rows[1][1] == "'=cmd|'/c calc'!A1"

    def test_export_creates_parent_dirs(self, tmp_path):
        csv_path = str(tmp_path / "subdir" / "deep" / "rates.csv")
        db = self._make_mock_db([])
        export_rates_csv(csv_path, db)
        assert os.path.exists(csv_path)


class TestExportCorruptCachedValue:
    """Round 11: a corrupt cached value (decimal.InvalidOperation territory)
    must not wedge the export — the DB read boundary maps it to None and the
    export skips it with the row count reflecting only good rows."""

    def test_export_completes_and_skips_corrupt_row(self, tmp_path):
        from core.database import CacheDB

        cache = CacheDB(db_path=str(tmp_path / "corrupt.db"))
        try:
            cache.insert_multi_rates_bulk([
                ("2026-01-06", "GBP", "selling", Decimal("44.5000")),
            ])
            # Bypass the validating insert to plant legacy junk.
            conn = cache._conn()
            conn.execute(
                "INSERT INTO rates_multi (date, currency, rate_type, value) "
                "VALUES ('2026-01-05', 'GBP', 'selling', 'N/A')"
            )
            conn.commit()

            csv_path = str(tmp_path / "out.csv")
            count = export_rates_csv(csv_path, cache)  # must not raise
            assert count == 1
            with open(csv_path, encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
            assert rows[1] == ["2026-01-06", "GBP", "selling", "44.5000"]
            assert all("N/A" not in c for r in rows for c in r)
        finally:
            cache.close()
