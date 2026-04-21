#!/usr/bin/env python3
"""
tests/test_engine.py
---------------------------------------------------------------------------
Unit & integration tests for core/engine.py — LedgerEngine orchestrator.
Uses mocked API client and temporary files.
---------------------------------------------------------------------------
"""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openpyxl
import pytest

from core.constants import SKIP_SHEET_NAMES
from core.engine import (
    FileSizeLimitError,
    LedgerEngine,
)

# =========================================================================
#  FIXTURES
# =========================================================================

@pytest.fixture
def mock_api():
    """Creates a mock BOTClient."""
    api = AsyncMock()
    api.get_holidays = AsyncMock(return_value=[])
    api.get_exchange_rates = AsyncMock(return_value=[])
    return api


@pytest.fixture
def engine(mock_api):
    """Creates a LedgerEngine with mocked API."""
    return LedgerEngine(mock_api)


@pytest.fixture
def sample_xlsx(tmp_path):
    """Creates a minimal .xlsx file with a single ledger sheet."""
    filepath = tmp_path / "test_ledger.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jan"
    ws.append(["Date", "Cur", "EX Rate", "Amount"])
    ws.append([date(2025, 3, 10), "USD", None, 1000])
    ws.append([date(2025, 3, 11), "EUR", None, 2000])
    ws.append([date(2025, 3, 12), "THB", None, 3000])
    wb.save(str(filepath))
    wb.close()
    return str(filepath)


@pytest.fixture
def oversized_file(tmp_path):
    """Creates a file larger than MAX_FILE_SIZE_MB (50 MB)."""
    filepath = tmp_path / "huge.xlsx"
    filepath.write_bytes(b"x" * (51 * 1024 * 1024))  # 51 MB
    return str(filepath)


# =========================================================================
#  PARSE DATE
# =========================================================================

class TestParseDate:
    """Tests for _parse_date method."""

    def test_datetime_input(self, engine):
        dt = datetime(2025, 3, 10, 14, 30)
        assert engine._parse_date(dt) == date(2025, 3, 10)

    def test_date_input(self, engine):
        d = date(2025, 3, 10)
        assert engine._parse_date(d) == d

    def test_iso_string(self, engine):
        assert engine._parse_date("2025-03-10") == date(2025, 3, 10)

    def test_slash_format(self, engine):
        assert engine._parse_date("10/03/2025") == date(2025, 3, 10)

    def test_dash_dmy(self, engine):
        assert engine._parse_date("10-03-2025") == date(2025, 3, 10)

    def test_named_month(self, engine):
        assert engine._parse_date("10 Mar 2025") == date(2025, 3, 10)

    def test_full_month_name(self, engine):
        assert engine._parse_date("10 March 2025") == date(2025, 3, 10)

    def test_compact_format(self, engine):
        assert engine._parse_date("20250310") == date(2025, 3, 10)

    def test_none_returns_none(self, engine):
        assert engine._parse_date(None) is None

    def test_empty_string_returns_none(self, engine):
        assert engine._parse_date("") is None

    def test_nan_string_returns_none(self, engine):
        assert engine._parse_date("nan") is None

    def test_null_string_returns_none(self, engine):
        assert engine._parse_date("null") is None

    def test_invalid_string_returns_none(self, engine):
        assert engine._parse_date("not-a-date") is None

    def test_integer_returns_none(self, engine):
        assert engine._parse_date(12345) is None


# =========================================================================
#  MEMORY GUARDRAIL
# =========================================================================

class TestMemoryGuardrail:
    """Tests for _check_memory_guardrail method."""

    def test_existing_file_passes(self, engine, sample_xlsx):
        engine._check_memory_guardrail(sample_xlsx)  # Should not raise

    def test_missing_file_raises(self, engine):
        with pytest.raises(FileNotFoundError):
            engine._check_memory_guardrail("/nonexistent/path.xlsx")

    def test_oversized_file_raises(self, engine, oversized_file):
        with pytest.raises(FileSizeLimitError):
            engine._check_memory_guardrail(oversized_file)


# =========================================================================
#  COMPUTE YEAR START DATE
# =========================================================================

class TestComputeYearStartDate:
    """Tests for compute_year_start_date static method."""

    def test_normal_weekday(self):
        # 2024-12-30 is Monday
        result = LedgerEngine.compute_year_start_date(2025, [])
        assert result == date(2024, 12, 30)

    def test_with_holiday_on_dec30(self):
        holidays = [date(2024, 12, 30)]
        result = LedgerEngine.compute_year_start_date(2025, holidays)
        # Rolls back to 2024-12-27 (Friday)
        assert result == date(2024, 12, 27)

    def test_dec30_on_weekend(self):
        # 2023-12-30 is Saturday → should roll back to Friday 12/29
        result = LedgerEngine.compute_year_start_date(2024, [])
        assert result == date(2023, 12, 29)


# =========================================================================
#  PRESCAN DELEGATE
# =========================================================================

class TestPrescanDelegate:
    """Tests for the static prescan_oldest_date delegate."""

    def test_empty_list_returns_fallback(self):
        d, detected = LedgerEngine.prescan_oldest_date([])
        assert detected is False
        assert isinstance(d, date)

    def test_with_xlsx_file(self, sample_xlsx):
        d, detected = LedgerEngine.prescan_oldest_date([sample_xlsx])
        assert detected is True
        assert d == date(2025, 3, 10)

    def test_nonexistent_file_gracefully_skipped(self):
        d, detected = LedgerEngine.prescan_oldest_date(["/no/such/file.xlsx"])
        assert detected is False


# =========================================================================
#  SKIP SHEET NAMES
# =========================================================================

class TestSkipSheetNames:
    """Tests for the SKIP_SHEET_NAMES constant."""

    def test_contains_exrate(self):
        assert "ExRate" in SKIP_SHEET_NAMES

    def test_legacy_tabs_skipped(self):
        """Legacy Exrate tabs must be skipped (non-standard headers)."""
        assert "Exrate USD" in SKIP_SHEET_NAMES
        assert "Exrate EUR" in SKIP_SHEET_NAMES

    def test_normal_month_not_skipped(self):
        assert "January" not in SKIP_SHEET_NAMES
        assert "Jan" not in SKIP_SHEET_NAMES


class TestBatchAndHolidayLookup:
    """Tests for batch-level error handling and holiday substitution parsing."""

    def test_process_batch_skips_oversized_file(self, engine, oversized_file):
        success, failed, errors = asyncio.run(engine.process_batch([oversized_file]))
        assert success == 0
        assert failed == 1
        assert len(errors) == 1
        assert "File too large" in errors[0]

    def test_build_holiday_lookup_parses_substitution_holiday(self, engine, monkeypatch):
        substitution_entry = (
            "2025-04-16",
            "Substitution for Songkran Day (15th April 2025)",
        )
        monkeypatch.setattr(
            engine.cache,
            "get_holidays",
            lambda year: [substitution_entry] if year == 2025 else [],
        )

        holidays_set, holidays_names = engine._build_holiday_lookup(
            all_target_dates={date(2025, 4, 16)},
            computed_start=date(2024, 12, 30),
            logic_engine=SimpleNamespace(holidays=[]),
        )

        assert date(2025, 4, 16) in holidays_names
        assert date(2025, 4, 15) in holidays_set
        assert holidays_names[date(2025, 4, 15)] == "Songkran Day"

    def test_process_batch_tracks_anomaly_totals(self, engine, monkeypatch):
        async def _fake_process_ledger(*args, **kwargs):
            engine._last_anomaly_count = 2
            return "fake.xlsx"

        monkeypatch.setattr(engine, "process_ledger", _fake_process_ledger)
        success, failed, errors = asyncio.run(
            engine.process_batch(["a.xlsx", "b.xlsx"], dry_run=True)
        )
        assert (success, failed, errors) == (2, 0, [])
        assert engine.last_batch_anomaly_count == 4
