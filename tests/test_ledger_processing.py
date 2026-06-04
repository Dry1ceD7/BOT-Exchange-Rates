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

from core.ledger_processing import prescan_target_dates

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
