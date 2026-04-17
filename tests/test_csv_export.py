#!/usr/bin/env python3
"""Tests for core/csv_export.py — Rate data CSV export."""

import csv
import os
from unittest.mock import MagicMock

from core.csv_export import _fmt, export_rates_csv

# ── Unit tests for _fmt ─────────────────────────────────────────────────

class TestFmt:
    """Tests for the _fmt formatting helper."""

    def test_none_returns_empty(self):
        assert _fmt(None) == ""

    def test_float_formats_four_decimals(self):
        assert _fmt(34.5) == "34.5000"

    def test_small_value(self):
        assert _fmt(0.1234) == "0.1234"

    def test_integer_formats_as_float(self):
        assert _fmt(1) == "1.0000"

    def test_string_number(self):
        assert _fmt("42.123") == "42.1230"


# ── Integration tests for export_rates_csv ───────────────────────────────

class TestExportRatesCsv:
    """Tests for the main export function."""

    def _make_mock_db(self, rows):
        """Create a mock CacheDB with specified get_all_rates() return."""
        db = MagicMock()
        db.get_all_rates.return_value = rows
        return db

    def test_export_empty_db(self, tmp_path):
        """Exporting from an empty DB should write headers only."""
        csv_path = str(tmp_path / "empty.csv")
        db = self._make_mock_db([])
        count = export_rates_csv(csv_path, db)
        assert count == 0
        assert os.path.exists(csv_path)
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 1  # Header only
        assert rows[0] == ["Period", "Currency_ID", "Buying Transfer", "Selling"]

    def test_export_usd_and_eur(self, tmp_path):
        """Both USD and EUR rows should be written per date."""
        csv_path = str(tmp_path / "rates.csv")
        db = self._make_mock_db([
            ("2025-01-02", 34.50, 35.00, 38.10, 39.20),
        ])
        count = export_rates_csv(csv_path, db)
        assert count == 2  # 1 USD + 1 EUR
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) == 3  # Header + 2 data rows
        assert rows[1] == ["2025-01-02", "USD", "34.5000", "35.0000"]
        assert rows[2] == ["2025-01-02", "EUR", "38.1000", "39.2000"]

    def test_export_usd_only_when_eur_null(self, tmp_path):
        """Only USD row should be written when EUR values are None."""
        csv_path = str(tmp_path / "usd_only.csv")
        db = self._make_mock_db([
            ("2025-01-02", 34.50, 35.00, None, None),
        ])
        count = export_rates_csv(csv_path, db)
        assert count == 1  # USD only

    def test_export_eur_only_when_usd_null(self, tmp_path):
        """Only EUR row should be written when USD values are None."""
        csv_path = str(tmp_path / "eur_only.csv")
        db = self._make_mock_db([
            ("2025-01-02", None, None, 38.10, 39.20),
        ])
        count = export_rates_csv(csv_path, db)
        assert count == 1  # EUR only

    def test_export_skips_all_null(self, tmp_path):
        """No rows should be written when all values are None."""
        csv_path = str(tmp_path / "all_null.csv")
        db = self._make_mock_db([
            ("2025-01-02", None, None, None, None),
        ])
        count = export_rates_csv(csv_path, db)
        assert count == 0

    def test_export_creates_parent_dirs(self, tmp_path):
        """Export should create parent directories if they don't exist."""
        csv_path = str(tmp_path / "subdir" / "deep" / "rates.csv")
        db = self._make_mock_db([])
        export_rates_csv(csv_path, db)
        assert os.path.exists(csv_path)

    def test_export_multiple_dates(self, tmp_path):
        """Multiple dates should produce the correct number of rows."""
        csv_path = str(tmp_path / "multi.csv")
        db = self._make_mock_db([
            ("2025-01-02", 34.50, 35.00, 38.10, 39.20),
            ("2025-01-03", 34.60, 35.10, 38.20, 39.30),
            ("2025-01-04", 34.70, None, None, None),
        ])
        count = export_rates_csv(csv_path, db)
        # 2 complete dates (USD+EUR each) + 1 date with USD only = 5
        assert count == 5
