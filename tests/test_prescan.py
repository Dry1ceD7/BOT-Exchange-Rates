#!/usr/bin/env python3
"""
tests/test_prescan.py
---------------------------------------------------------------------------
Unit tests for core/prescan.py — Smart Date Pre-Scanner.
---------------------------------------------------------------------------
"""

from datetime import date, timedelta

import openpyxl
import pytest

from core.prescan import _parse_scan_date, prescan_oldest_date

# =========================================================================
#  HELPERS
# =========================================================================

class TestParseScanDate:
    """Tests for _parse_scan_date helper."""

    FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y%m%d"]

    def test_iso_string(self):
        assert _parse_scan_date("2025-03-10", self.FORMATS) == date(2025, 3, 10)

    def test_date_object(self):
        assert _parse_scan_date(date(2025, 3, 10), self.FORMATS) == date(2025, 3, 10)

    def test_none_returns_none(self):
        assert _parse_scan_date(None, self.FORMATS) is None

    def test_empty_string_returns_none(self):
        assert _parse_scan_date("", self.FORMATS) is None

    def test_nan_returns_none(self):
        assert _parse_scan_date("nan", self.FORMATS) is None

    def test_invalid_returns_none(self):
        assert _parse_scan_date("hello", self.FORMATS) is None


# =========================================================================
#  PRESCAN
# =========================================================================

class TestPrescanOldestDate:
    """Tests for prescan_oldest_date function."""

    @pytest.fixture
    def xlsx_with_dates(self, tmp_path):
        """Creates a .xlsx file with dates in a 'Date' column."""
        filepath = tmp_path / "test.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date", "Amount"])
        ws.append([date(2025, 3, 15), 100])
        ws.append([date(2025, 3, 10), 200])
        ws.append([date(2025, 3, 20), 300])
        wb.save(str(filepath))
        wb.close()
        return str(filepath)

    @pytest.fixture
    def xlsx_no_date_col(self, tmp_path):
        """Creates a .xlsx file without a 'Date' column."""
        filepath = tmp_path / "no_date.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Name", "Amount"])
        ws.append(["Alice", 100])
        wb.save(str(filepath))
        wb.close()
        return str(filepath)

    def test_finds_oldest_date(self, xlsx_with_dates):
        oldest, detected = prescan_oldest_date([xlsx_with_dates])
        assert detected is True
        assert oldest == date(2025, 3, 10)

    def test_empty_list_returns_fallback(self):
        oldest, detected = prescan_oldest_date([])
        assert detected is False
        assert isinstance(oldest, date)
        # Fallback should be ~30 days ago
        expected = date.today() - timedelta(days=30)
        assert oldest == expected

    def test_nonexistent_file_returns_fallback(self):
        oldest, detected = prescan_oldest_date(["/no/such/file.xlsx"])
        assert detected is False

    def test_file_without_date_column_returns_fallback(self, xlsx_no_date_col):
        oldest, detected = prescan_oldest_date([xlsx_no_date_col])
        assert detected is False

    def test_multiple_files_finds_global_oldest(self, tmp_path):
        """Test scanning multiple files picks the true oldest date."""
        f1 = tmp_path / "file1.xlsx"
        f2 = tmp_path / "file2.xlsx"

        wb1 = openpyxl.Workbook()
        ws1 = wb1.active
        ws1.append(["Date", "Amount"])
        ws1.append([date(2025, 4, 1), 100])
        wb1.save(str(f1))
        wb1.close()

        wb2 = openpyxl.Workbook()
        ws2 = wb2.active
        ws2.append(["Date", "Amount"])
        ws2.append([date(2025, 2, 15), 200])
        wb2.save(str(f2))
        wb2.close()

        oldest, detected = prescan_oldest_date([str(f1), str(f2)])
        assert detected is True
        assert oldest == date(2025, 2, 15)
