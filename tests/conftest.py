#!/usr/bin/env python3
"""
tests/conftest.py
---------------------------------------------------------------------------
Shared pytest fixtures for the BOT Exchange Rate Processor test suite.
---------------------------------------------------------------------------
Provides:
  - ledger_xlsx: factory building a real workbook with Date/Cur/EX Rate
    columns across one or more monthly tabs.
  - tmp_cache: a CacheDB backed by a temporary on-disk SQLite file.
  - mock_api: an AsyncMock BOTClient with a helper to seed rate records.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import openpyxl
import pytest

from core.database import CacheDB


@pytest.fixture
def ledger_xlsx(tmp_path):
    """Factory: build a real ledger workbook with monthly tabs.

    Usage:
        path = ledger_xlsx({
            "Jan": [(date(2025, 1, 7), "USD"), ("10/03/2025", "EUR")],
        })

    Each tab gets a header row (Date / Cur / EX Rate / Amount) followed by
    one data row per (date, currency) tuple. The date value is written
    verbatim, so callers may pass a real date object OR a string to exercise
    the date-normalization path. The EX Rate cell is left empty.
    """
    counter = {"n": 0}

    def _build(tabs, filename=None):
        counter["n"] += 1
        name = filename or f"ledger_{counter['n']}.xlsx"
        filepath = tmp_path / name
        wb = openpyxl.Workbook()
        # Remove the default sheet; we add named tabs explicitly.
        wb.remove(wb.active)
        for tab_name, rows in tabs.items():
            ws = wb.create_sheet(tab_name)
            ws.append(["Date", "Cur", "EX Rate", "Amount"])
            for raw_date, ccy in rows:
                ws.append([raw_date, ccy, None, 1000])
        wb.save(str(filepath))
        wb.close()
        return str(filepath)

    return _build


@pytest.fixture
def tmp_cache(tmp_path):
    """A CacheDB backed by a temp SQLite file (public constructor)."""
    db_path = str(tmp_path / "cache_test.db")
    cache = CacheDB(db_path=db_path)
    yield cache
    cache.close()


@pytest.fixture
def mock_api():
    """An AsyncMock BOTClient with helpers to seed rate/holiday records.

    Attach `seed_rates(records)` to populate get_exchange_rates returns and
    `seed_holidays(records)` for get_holidays. Records are SimpleNamespaces
    matching the BOTRateDetail / BOTHolidayDetail attribute shape.
    """
    api = AsyncMock()
    api.get_holidays = AsyncMock(return_value=[])
    api.get_exchange_rates = AsyncMock(return_value=[])

    def make_rate(period, buying_transfer=None, selling=None,
                  buying_sight=None, mid_rate=None, currency="USD"):
        return SimpleNamespace(
            period=period,
            currency=currency,
            buying_transfer=buying_transfer,
            buying_sight=buying_sight,
            selling=selling,
            mid_rate=mid_rate,
        )

    def make_holiday(d, description):
        return SimpleNamespace(date=d, description=description)

    api.make_rate = make_rate
    api.make_holiday = make_holiday
    return api


@pytest.fixture
def thai_holidays_2025():
    """A small, well-known set of 2025 Thai holiday dates."""
    return [date(2025, 1, 1)]
