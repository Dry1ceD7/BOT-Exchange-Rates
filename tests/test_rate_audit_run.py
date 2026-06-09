#!/usr/bin/env python3
"""Integration tests for StandaloneRateAuditor.run — orchestration only.

A fake engine supplies canned BOT rates (no network); a real .xlsx on disk is
read, compared, and (optionally) rewritten. Asserts the end-to-end contract:
wrong trading-day cells are corrected, weekend rows are never touched, the file
is backed up before any write, and a dry run writes nothing.
"""
import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import openpyxl
import pytest

from core.constants import parse_date
from core.logic import safe_to_decimal
from core.rate_audit import StandaloneRateAuditor

HEADERS = [
    "Date", "USD Buying TT Rate", "USD Selling Rate",
    "EUR Buying TT Rate", "EUR Selling Rate", "Holidays/Weekend",
]
WED = date(2026, 5, 27)   # trading day
SAT = date(2026, 5, 23)   # weekend


def _make_xlsx(tmp_path, usd_buy_value):
    """ExRate sheet: one trading-day row (USD buy = usd_buy_value) + a blank
    weekend row."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ExRate"
    ws.append(HEADERS)
    ws.append([WED, usd_buy_value, Decimal("32.7790"),
               Decimal("37.0000"), Decimal("37.5000"), ""])
    ws.append([SAT, None, None, None, None, "Weekend"])
    fp = tmp_path / "ledger.xlsx"
    wb.save(str(fp))
    wb.close()
    return str(fp)


def _fake_engine():
    """Engine stub exposing exactly what StandaloneRateAuditor dereferences."""
    usd_buying = {WED: Decimal("32.4507")}
    usd_selling = {WED: Decimal("32.7790")}
    eur_buying = {WED: Decimal("37.0000")}
    eur_selling = {WED: Decimal("37.5000")}
    logic_engine = SimpleNamespace(holidays=set())

    async def _preload(_dates, _start):
        return (logic_engine, usd_selling, eur_selling,
                usd_buying, eur_buying, {}, {})

    backup = MagicMock()
    backup.create_backup.return_value = "/tmp/ledger.bak.xlsx"
    return SimpleNamespace(
        _check_memory_guardrail=lambda _fp: None,
        _parse_date=parse_date,
        _preload_api_data=_preload,
        backup=backup,
    )


def test_run_corrects_wrong_cell_and_backs_up(tmp_path):
    eng = _fake_engine()
    fp = _make_xlsx(tmp_path, Decimal("32.0000"))  # USD buy is WRONG
    report = asyncio.run(StandaloneRateAuditor(eng).run(fp, apply=True))

    assert report.change_count == 1
    ch = report.changes[0]
    assert ch.cell == "B2"
    assert ch.new_value == Decimal("32.4507")
    assert report.applied is True
    eng.backup.create_backup.assert_called_once()

    wb = openpyxl.load_workbook(fp)
    ws = wb["ExRate"]
    assert safe_to_decimal(ws.cell(row=2, column=2).value) == Decimal("32.4507")
    # Weekend row stays blank — never filled.
    assert ws.cell(row=3, column=2).value is None
    wb.close()


def test_run_no_changes_when_already_correct(tmp_path):
    eng = _fake_engine()
    fp = _make_xlsx(tmp_path, Decimal("32.4507"))  # already correct
    report = asyncio.run(StandaloneRateAuditor(eng).run(fp, apply=True))
    assert report.change_count == 0
    # Nothing differs → no write and therefore no backup (avoids clutter).
    eng.backup.create_backup.assert_not_called()


def test_dry_run_writes_nothing_and_skips_backup(tmp_path):
    eng = _fake_engine()
    fp = _make_xlsx(tmp_path, Decimal("32.0000"))
    report = asyncio.run(StandaloneRateAuditor(eng).run(fp, apply=False))

    assert report.change_count == 1
    assert report.applied is False
    eng.backup.create_backup.assert_not_called()
    wb = openpyxl.load_workbook(fp)
    ws = wb["ExRate"]
    assert safe_to_decimal(ws.cell(row=2, column=2).value) == Decimal("32.0000")
    wb.close()


def test_run_raises_without_exrate_sheet(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Sheet1"
    fp = tmp_path / "no_exrate.xlsx"
    wb.save(str(fp))
    wb.close()
    with pytest.raises(ValueError, match="No ExRate sheet"):
        asyncio.run(StandaloneRateAuditor(_fake_engine()).run(str(fp)))
