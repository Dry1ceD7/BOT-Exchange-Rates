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

import core.exrate_sheet as exrate_sheet_mod
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

    def test_shared_superset_formats(self):
        """Now uses shared parser — full format superset, not just 2."""
        assert _parse_cell_date("10/03/2025") == date(2025, 3, 10)
        assert _parse_cell_date("10-03-2025") == date(2025, 3, 10)
        assert _parse_cell_date("20250310") == date(2025, 3, 10)
        assert _parse_cell_date("10 Mar 2025") == date(2025, 3, 10)


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
        # API overrides existing, value stays exact Decimal (no float cast).
        assert entry["usd_buy"] == Decimal("33.5")
        assert entry["eur_sell"] == Decimal("37.1")

    def test_decimal_precision_preserved_no_float(self):
        """Rate values must stay exact Decimal — never cast to float."""
        d = date(2025, 3, 10)
        usd_b = {d: Decimal("34.5650")}
        merged = _merge_rate_data(
            {d}, {}, set(), {},
            usd_b, {}, {}, {},
        )
        val = merged[d]["usd_buy"]
        assert isinstance(val, Decimal)
        # Exact value preserved (float would yield 34.564999...).
        assert val == Decimal("34.5650")
        assert str(val) == "34.5650"

    def test_decimal_written_to_sheet_exact(self):
        """End-to-end: Decimal survives into the written cell exactly."""
        wb = openpyxl.Workbook()
        d = date(2025, 3, 10)
        update_master_exrate_sheet(
            wb,
            usd_buying_rates={d: Decimal("34.5650")},
            usd_selling_rates={d: Decimal("34.7350")},
            eur_buying_rates={d: Decimal("37.1250")},
            eur_selling_rates={d: Decimal("37.4450")},
            holidays_list=[],
            holidays_names={},
            start_date=d,
        )
        ws = wb["ExRate"]
        cell = ws.cell(row=2, column=2).value
        assert isinstance(cell, Decimal)
        assert cell == Decimal("34.5650")
        wb.close()

    def test_weekend_label(self):
        sat = date(2025, 3, 8)  # Saturday
        all_dates = {sat}
        merged = _merge_rate_data(
            all_dates, {}, set(), {},
            {}, {}, {}, {},
        )
        assert merged[sat]["holidays_weekend"] == "Weekend"

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


class TestWeekendHolidayRatesBlank:
    """Weekend/holiday rows keep BLANK rate cells — no carry-forward.

    BOT publishes no rate on weekends/holidays, so those rows show only the
    Date + Holidays/Weekend label; no prior trading-day rate is fabricated for
    them. The ledger XLOOKUP is exact-match and intentionally yields "" for a
    weekend/holiday-dated transaction. This matches the v3.2.8 behavior.
    """

    def test_saturday_and_sunday_stay_blank(self):
        fri = date(2025, 3, 7)   # Friday — trading day with a rate
        sat = date(2025, 3, 8)   # Saturday — no rate from BOT
        sun = date(2025, 3, 9)   # Sunday — no rate either
        usd_b = {fri: Decimal("33.5000")}
        merged = _merge_rate_data(
            {fri, sat, sun}, {}, set(), {},
            usd_b, {}, {}, {},
        )
        # Friday keeps its own rate.
        assert merged[fri]["usd_buy"] == Decimal("33.5000")
        # Saturday + Sunday carry NO rate forward — cells stay blank.
        assert merged[sat]["usd_buy"] is None
        assert merged[sun]["usd_buy"] is None

    def test_holiday_stays_blank(self):
        fri = date(2025, 3, 7)   # Friday — trading day with a rate
        mon = date(2025, 3, 10)  # Monday — BOT holiday, no rate
        usd_b = {fri: Decimal("34.0000")}
        merged = _merge_rate_data(
            {fri, date(2025, 3, 8), date(2025, 3, 9), mon},
            {}, {mon}, {mon: "Test Holiday"},
            usd_b, {}, {}, {},
        )
        # Monday holiday has no own rate and none is carried forward.
        assert merged[mon]["usd_buy"] is None

    def test_weekend_with_own_api_rate_is_kept(self):
        # Defensive: API priority still holds. If BOT ever returns a rate for
        # a weekend date, that real value is used — only MISSING rates blank.
        sat = date(2025, 3, 8)   # Saturday
        usd_b = {sat: Decimal("33.9999")}
        merged = _merge_rate_data(
            {sat}, {}, set(), {},
            usd_b, {}, {}, {},
        )
        assert merged[sat]["usd_buy"] == Decimal("33.9999")


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
        # Row 2 should have data (row 1 = header). Cells hold exact Decimal.
        assert ws.cell(row=2, column=2).value == Decimal("33.5")
        assert ws.cell(row=2, column=3).value == Decimal("33.6")
        wb.close()

    def test_default_end_date_uses_bot_today(self, monkeypatch):
        """When end_date is None the range runs out to the BOT business date.

        The sweep replaced the bare date.today() default with bot_today()
        (Asia/Bangkok). Patching bot_today proves the default path now keys off
        the BOT calendar rather than the machine's local date.
        """
        fixed_today = date(2025, 3, 12)
        monkeypatch.setattr(
            exrate_sheet_mod, "bot_today", lambda: fixed_today
        )
        wb = openpyxl.Workbook()
        start = date(2025, 3, 10)
        update_master_exrate_sheet(
            wb,
            usd_buying_rates={start: Decimal("33.5")},
            usd_selling_rates={start: Decimal("33.6")},
            eur_buying_rates={start: Decimal("37.0")},
            eur_selling_rates={start: Decimal("37.1")},
            holidays_list=[],
            holidays_names={},
            start_date=start,
            # end_date omitted → defaults to bot_today() (patched).
        )
        ws = wb["ExRate"]
        written = {
            _parse_cell_date(ws.cell(row=r, column=1).value)
            for r in range(2, (ws.max_row or 1) + 1)
        }
        written.discard(None)
        # Range stops at the patched BOT today, inclusive.
        assert written == {start, date(2025, 3, 11), fixed_today}
        wb.close()

    def test_explicit_end_date_bounds_written_range(self):
        """A manual (start, end) writes exactly that range — end not today()."""
        wb = openpyxl.Workbook()
        start = date(2025, 3, 10)
        end = date(2025, 3, 12)
        update_master_exrate_sheet(
            wb,
            usd_buying_rates={start: Decimal("33.5")},
            usd_selling_rates={start: Decimal("33.6")},
            eur_buying_rates={start: Decimal("37.0")},
            eur_selling_rates={start: Decimal("37.1")},
            holidays_list=[],
            holidays_names={},
            start_date=start,
            end_date=end,
        )
        ws = wb["ExRate"]
        written = {
            _parse_cell_date(ws.cell(row=r, column=1).value)
            for r in range(2, (ws.max_row or 1) + 1)
        }
        written.discard(None)
        # Exactly the 3-day manual window — nothing up to today().
        assert written == {start, date(2025, 3, 11), end}
        wb.close()


# =========================================================================
#  EXTRA-CURRENCY COLUMNS (multi-currency ledger master sheet)
# =========================================================================

class TestExtraCurrencyColumns:
    """update_master_exrate_sheet appends a column per extra currency and
    returns the {ccy: column_letter} map for the ledger formula."""

    def test_extra_columns_appended_after_eur_before_holidays(self):
        wb = openpyxl.Workbook()
        d = date(2025, 3, 10)  # Monday
        col_map = update_master_exrate_sheet(
            wb,
            usd_buying_rates={d: Decimal("33.5")},
            usd_selling_rates={d: Decimal("33.6")},
            eur_buying_rates={d: Decimal("37.0")},
            eur_selling_rates={d: Decimal("37.1")},
            holidays_list=[],
            holidays_names={},
            start_date=d,
            end_date=d,
            extra_currency_rates={
                "GBP": {d: Decimal("42.1234")},
                "JPY": {d: Decimal("0.2155")},
            },
        )
        ws = wb["ExRate"]
        headers = [
            ws.cell(row=1, column=c).value
            for c in range(1, (ws.max_column or 1) + 1)
        ]
        # Date, USD x2, EUR x2, then extra cols (dict order), then Holidays.
        assert headers == [
            "Date", "USD Buying TT Rate", "USD Selling Rate",
            "EUR Buying TT Rate", "EUR Selling Rate",
            "GBP Rate", "JPY Rate", "Holidays/Weekend",
        ]
        # Column map points the ledger formula at F (GBP) and G (JPY).
        assert col_map == {"GBP": "F", "JPY": "G"}
        # USD/EUR are NOT in the map (fixed B-E columns).
        assert "USD" not in col_map and "EUR" not in col_map
        wb.close()

    def test_extra_currency_value_exact_decimal(self):
        wb = openpyxl.Workbook()
        d = date(2025, 3, 10)
        update_master_exrate_sheet(
            wb,
            usd_buying_rates={}, usd_selling_rates={},
            eur_buying_rates={}, eur_selling_rates={},
            holidays_list=[], holidays_names={},
            start_date=d, end_date=d,
            extra_currency_rates={"GBP": {d: Decimal("42.1234")}},
        )
        ws = wb["ExRate"]
        cell = ws.cell(row=2, column=6)  # F = GBP
        assert cell.value == Decimal("42.1234")
        assert cell.number_format == "0.0000"
        wb.close()

    def test_extra_currency_weekend_stays_blank(self):
        """A GBP weekend row keeps a blank rate — no carry-forward."""
        fri = date(2025, 3, 7)   # Friday
        sat = date(2025, 3, 8)   # Saturday
        merged = _merge_rate_data(
            {fri, sat}, {}, set(), {},
            {}, {}, {}, {},
            {"GBP": {fri: Decimal("42.5000")}},
        )
        assert merged[fri]["extra:GBP"] == Decimal("42.5000")
        assert merged[sat]["extra:GBP"] is None

    def test_no_extra_currencies_keeps_six_column_layout(self):
        """Backward compat: omitting extra rates keeps the legacy 6 columns and
        returns an empty map."""
        wb = openpyxl.Workbook()
        d = date(2025, 3, 10)
        col_map = update_master_exrate_sheet(
            wb,
            usd_buying_rates={d: Decimal("33.5")},
            usd_selling_rates={d: Decimal("33.6")},
            eur_buying_rates={d: Decimal("37.0")},
            eur_selling_rates={d: Decimal("37.1")},
            holidays_list=[], holidays_names={},
            start_date=d, end_date=d,
        )
        ws = wb["ExRate"]
        assert ws.max_column == 6
        assert ws.cell(row=1, column=6).value == "Holidays/Weekend"
        assert col_map == {}
        wb.close()
