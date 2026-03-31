#!/usr/bin/env python3
"""Tests for core/csv_import.py — Offline CSV Import."""

from datetime import date

import pytest

from core.csv_import import import_bot_csv


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

        # Verify rates were inserted into multi-currency table
        rate = cache.get_multi_rate(
            date(2025, 1, 2), "USD", "buying_transfer",
        )
        assert rate is not None
        assert float(rate) == pytest.approx(34.5, abs=0.01)

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
