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
  - tk_root: a withdrawn CTk root window for GUI widget tests; auto-skipped
    when no display is available (headless CI).
"""

import contextlib
import os
import sys
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


@pytest.fixture(scope="session")
def tk_root():
    """Withdrawn CTk root window for GUI widget tests (session-scoped).

    Session scope is required because CTk maintains a per-process singleton
    for the Tk interpreter; creating and destroying multiple CTk roots within
    one pytest process leads to segfaults on macOS/aarch64.  One root is
    created at the start of the session and destroyed at the end.

    Skips automatically when no graphical display is available so the suite
    stays green on headless CI (ubuntu-latest — no DISPLAY/WAYLAND_DISPLAY).
    On macOS the env-var check is unreliable (Aqua needs no DISPLAY), so we
    always attempt root creation there and convert a TclError (e.g. SSH
    session without a window server) into a clean skip.
    """
    # On headless CI (ubuntu-latest) neither DISPLAY nor WAYLAND_DISPLAY is
    # set, so we skip cleanly without attempting to create any Tk window.
    # Do NOT create a bare tkinter.Tk() probe: destroying a raw Tk interpreter
    # before a CTk root is created corrupts the Tcl runtime and causes
    # CTkScrollbar to segfault when update_idletasks() fires inside __init__.
    has_display = bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )
    on_macos = sys.platform == "darwin"
    if not has_display and not on_macos:
        pytest.skip("No display available (headless CI)")

    import tkinter

    import customtkinter as ctk

    try:
        root = ctk.CTk()
    except tkinter.TclError as exc:
        # macOS without a reachable window server (SSH, headless agent).
        pytest.skip(f"Display unavailable: {exc}")
    root.withdraw()  # keep window hidden during tests
    # Pump the Tk event loop once so CTk finishes all deferred initialisation.
    # CTkScrollbar calls update_idletasks() inside __init__, which segfaults if
    # mainloop() has never run.  after(1, quit) exits immediately after one cycle.
    root.after(1, root.quit)
    root.mainloop()
    yield root
    with contextlib.suppress(Exception):
        root.destroy()
