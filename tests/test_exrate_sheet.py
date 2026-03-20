#!/usr/bin/env python3
"""
tests/test_exrate_sheet.py
---------------------------------------------------------------------------
Unit tests for core/exrate_sheet.py — Master ExRate sheet builder.
---------------------------------------------------------------------------
"""

from datetime import date
from decimal import Decimal

import openpyxl

from core.exrate_sheet import (
    _build_date_range,
    _merge_rate_data,
    _parse_cell_date,
    update_master_exrate_sheet,
)

# =========================================================================
#  HELPERS
# =========================================================================

class TestParseCellDate:
    """Tests for _parse_cell_date helper."""

    def test_date_object(self):
        assert _parse_cell_date(date(2025, 3, 10)) == date(2025, 3, 10)

    def test_iso_string(self):
        assert _parse_cell_date("2025-03-10") == date(2025, 3, 10)

    def test_none_returns_none(self):
        assert _parse_cell_date(None) is None

    def test_invalid_string_returns_none(self):
        assert _parse_cell_date("not-a-date") is None

    def test_integer_returns_none(self):
        assert _parse_cell_date(42) is None


class TestBuildDateRange:
    """Tests for _build_date_range helper."""

    def test_simple_range(self):
        start = date(2025, 3, 10)
        end = date(2025, 3, 12)
        result = _build_date_range(start, end, {})
        assert start in result
        assert end in result
        assert date(2025, 3, 11) in result
        assert len(result) == 3

    def test_includes_existing_data(self):
        start = date(2025, 3, 10)
        end = date(2025, 3, 10)
        existing = {date(2025, 3, 11): {"usd_buy": 33.0}}
        result = _build_date_range(start, end, existing)
        assert date(2025, 3, 11) in result

    def test_filters_before_start(self):
        start = date(2025, 3, 10)
        end = date(2025, 3, 10)
        existing = {date(2025, 3, 5): {"usd_buy": 33.0}}
        result = _build_date_range(start, end, existing)
        assert date(2025, 3, 5) not in result


class TestMergeRateData:
    """Tests for _merge_rate_data helper."""

    def test_api_rates_override_existing(self):
        all_dates = {date(2025, 3, 10)}
        existing = {date(2025, 3, 10): {"usd_buy": 30.0, "usd_sell": None, "eur_buy": None, "eur_sell": None}}
        usd_b = {date(2025, 3, 10): Decimal("33.5")}
        usd_s = {date(2025, 3, 10): Decimal("33.6")}
        eur_b = {date(2025, 3, 10): Decimal("37.0")}
        eur_s = {date(2025, 3, 10): Decimal("37.1")}

        merged = _merge_rate_data(
            all_dates, existing, set(), {},
            usd_b, usd_s, eur_b, eur_s,
        )
        entry = merged[date(2025, 3, 10)]
        assert entry["usd_buy"] == 33.5  # API overrides existing
        assert entry["eur_sell"] == 37.1

    def test_weekend_label(self):
        sat = date(2025, 3, 8)  # Saturday
        all_dates = {sat}
        merged = _merge_rate_data(
            all_dates, {}, set(), {},
            {}, {}, {}, {},
        )
        assert merged[sat]["holidays_weekend"] == "weekend"

    def test_holiday_label(self):
        d = date(2025, 3, 10)  # Monday
        holidays_set = {d}
        holidays_names = {d: "Test Holiday"}
        merged = _merge_rate_data(
            {d}, {}, holidays_set, holidays_names,
            {}, {}, {}, {},
        )
        assert merged[d]["holidays_weekend"] == "Test Holiday"

    def test_weekend_plus_holiday_label(self):
        sat = date(2025, 3, 8)
        holidays_set = {sat}
        holidays_names = {sat: "Weekend Holiday"}
        merged = _merge_rate_data(
            {sat}, {}, holidays_set, holidays_names,
            {}, {}, {}, {},
        )
        assert "Weekend" in merged[sat]["holidays_weekend"]
        assert "Weekend Holiday" in merged[sat]["holidays_weekend"]


# =========================================================================
#  FULL SHEET UPDATE
# =========================================================================

class TestUpdateMasterExrateSheet:
    """Integration tests for update_master_exrate_sheet."""

    def test_creates_exrate_sheet(self):
        wb = openpyxl.Workbook()
        update_master_exrate_sheet(
            wb,
            usd_buying_rates={date(2025, 3, 10): Decimal("33.5")},
            usd_selling_rates={date(2025, 3, 10): Decimal("33.6")},
            eur_buying_rates={date(2025, 3, 10): Decimal("37.0")},
            eur_selling_rates={date(2025, 3, 10): Decimal("37.1")},
            holidays_list=[],
            holidays_names={},
            start_date=date(2025, 3, 10),
        )
        assert "ExRate" in wb.sheetnames
        ws = wb["ExRate"]
        assert ws.cell(row=1, column=1).value == "Date"
        assert ws.cell(row=1, column=2).value == "USD Buying TT Rate"
        wb.close()

    def test_writes_rate_data(self):
        wb = openpyxl.Workbook()
        d = date(2025, 3, 10)
        update_master_exrate_sheet(
            wb,
            usd_buying_rates={d: Decimal("33.5")},
            usd_selling_rates={d: Decimal("33.6")},
            eur_buying_rates={d: Decimal("37.0")},
            eur_selling_rates={d: Decimal("37.1")},
            holidays_list=[],
            holidays_names={},
            start_date=d,
        )
        ws = wb["ExRate"]
        # Row 2 should have data (row 1 = header)
        assert ws.cell(row=2, column=2).value == 33.5
        assert ws.cell(row=2, column=3).value == 33.6
        wb.close()
