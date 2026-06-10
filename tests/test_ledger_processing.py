#!/usr/bin/env python3
"""
tests/test_ledger_processing.py
---------------------------------------------------------------------------
Unit tests for core/ledger_processing.py — near-pure ledger helpers.
Focus: prescan_target_dates duplicate-header determinism (first-wins).
---------------------------------------------------------------------------
"""

from datetime import date

import openpyxl

from core.ledger_processing import (
    classify_currencies,
    prescan_target_dates,
    prescan_target_dates_and_currencies,
)

TARGET_COLS = {"source_date": "Date", "currency": "Cur", "out_rate": "EX Rate"}


def _write_workbook(tmp_path, rows, header):
    """Build a one-tab workbook with the given header + data rows."""
    filepath = tmp_path / "ledger.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jan"
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(str(filepath))
    wb.close()
    return str(filepath)


class TestPrescanTargetDates:

    def test_scans_source_date_column(self, tmp_path):
        path = _write_workbook(
            tmp_path,
            rows=[
                [date(2025, 1, 7), "USD", None],
                [date(2025, 1, 8), "EUR", None],
            ],
            header=["Date", "Cur", "EX Rate"],
        )
        dates = prescan_target_dates(path, TARGET_COLS)
        assert dates == {date(2025, 1, 7), date(2025, 1, 8)}

    def test_duplicate_date_header_uses_first_column(self, tmp_path, caplog):
        """Two 'Date' columns → scan only the FIRST, deterministically.

        Column A holds the real dates; the duplicate column D holds DIFFERENT
        dates. First-wins means only column A's dates are returned, and the
        collision is logged.
        """
        path = _write_workbook(
            tmp_path,
            rows=[
                # A=real date, B=Cur, C=EX Rate, D=duplicate "Date" column.
                [date(2025, 1, 7), "USD", None, date(2030, 12, 31)],
                [date(2025, 1, 8), "EUR", None, date(2031, 12, 31)],
            ],
            header=["Date", "Cur", "EX Rate", "Date"],
        )
        with caplog.at_level("WARNING"):
            dates = prescan_target_dates(path, TARGET_COLS)
        # Only the first "Date" column (A) is scanned — never the duplicate (D).
        assert dates == {date(2025, 1, 7), date(2025, 1, 8)}
        assert date(2030, 12, 31) not in dates
        assert date(2031, 12, 31) not in dates
        # The collision is logged.
        assert any(
            "duplicate" in r.message.lower() and "Date" in r.message
            for r in caplog.records
        )


class TestPrescanCurrencies:
    """Currency collection powering the multi-currency ledger path."""

    def test_collects_distinct_currency_codes(self, tmp_path):
        path = _write_workbook(
            tmp_path,
            rows=[
                [date(2025, 1, 7), "USD", None],
                [date(2025, 1, 8), "gbp", None],   # lower-case → normalized
                [date(2025, 1, 9), " EUR ", None],  # whitespace → trimmed
                [date(2025, 1, 10), "GBP", None],   # dup → collapsed
            ],
            header=["Date", "Cur", "EX Rate"],
        )
        dates, currencies = prescan_target_dates_and_currencies(
            path, TARGET_COLS,
        )
        assert dates == {
            date(2025, 1, 7), date(2025, 1, 8),
            date(2025, 1, 9), date(2025, 1, 10),
        }
        assert currencies == {"USD", "GBP", "EUR"}

    def test_dates_only_wrapper_matches(self, tmp_path):
        """prescan_target_dates returns the same dates as the combined scan."""
        path = _write_workbook(
            tmp_path,
            rows=[[date(2025, 1, 7), "USD", None]],
            header=["Date", "Cur", "EX Rate"],
        )
        dates_only = prescan_target_dates(path, TARGET_COLS)
        dates_combined, _ = prescan_target_dates_and_currencies(
            path, TARGET_COLS,
        )
        assert dates_only == dates_combined == {date(2025, 1, 7)}

    def test_skip_sheet_currencies_ignored(self, tmp_path):
        """Currencies on a SKIP sheet (ExRate) must not be collected."""
        filepath = tmp_path / "with_exrate.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Jan"
        ws.append(["Date", "Cur", "EX Rate"])
        ws.append([date(2025, 6, 2), "GBP", None])
        ws_ex = wb.create_sheet("ExRate")
        ws_ex.append(["Date", "Cur"])
        ws_ex.append([date(1999, 1, 1), "XXX"])
        wb.save(str(filepath))
        wb.close()
        _dates, currencies = prescan_target_dates_and_currencies(
            str(filepath), TARGET_COLS,
        )
        assert currencies == {"GBP"}
        assert "XXX" not in currencies


class TestClassifyCurrencies:
    """classify_currencies splits scanned codes into extra vs unsupported."""

    def test_usd_eur_thb_are_dropped(self):
        extra, unsupported = classify_currencies({"USD", "EUR", "THB"})
        # All handled by the core IFS branches — nothing to fetch / warn about.
        assert extra == []
        assert unsupported == []

    def test_supported_extra_currencies_sorted(self):
        extra, unsupported = classify_currencies({"SGD", "GBP", "USD"})
        # Sorted for deterministic ExRate column ordering.
        assert extra == ["GBP", "SGD"]
        assert unsupported == []

    def test_jpy_routed_to_unsupported_path(self):
        # JPY is quoted by BOT per 100 yen, so it is deliberately excluded
        # from LEDGER_SUPPORTED_CURRENCIES and must take the unsupported path.
        extra, unsupported = classify_currencies({"JPY", "GBP"})
        assert extra == ["GBP"]
        assert unsupported == ["JPY"]

    def test_unsupported_currency_flagged(self):
        extra, unsupported = classify_currencies({"GBP", "XYZ", "ABC"})
        assert extra == ["GBP"]
        assert unsupported == ["ABC", "XYZ"]
