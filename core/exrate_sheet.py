#!/usr/bin/env python3
"""
core/exrate_sheet.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.7) - Master ExRate Sheet Builder
---------------------------------------------------------------------------
Separated from engine.py for SFFB compliance (<200 lines).
Builds and updates the unified "ExRate" master tab in Excel workbooks.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


def update_master_exrate_sheet(
    wb: openpyxl.Workbook,
    usd_buying_rates: Dict[date, Decimal],
    usd_selling_rates: Dict[date, Decimal],
    eur_buying_rates: Dict[date, Decimal],
    eur_selling_rates: Dict[date, Decimal],
    holidays_list: List[date],
    holidays_names: Dict[date, str],
    start_date: date,
) -> None:
    """
    Creates or updates a unified "ExRate" master tab.

    Columns: Date | USD Buying TT Rate | USD Selling Rate |
             EUR Buying TT Rate | EUR Selling Rate | Holidays/Weekend

    Holiday/Weekend Overlap Rule (semicolon separator):
      - Weekend only → "Weekend"
      - Holiday on weekday → "[Holiday Name]"
      - Holiday on weekend → "Weekend; [Holiday Name]"
    """
    SHEET_NAME = "ExRate"
    HEADER_ROW = 1
    DATA_START_ROW = 2
    HEADERS = [
        "Date", "USD Buying TT Rate", "USD Selling Rate",
        "EUR Buying TT Rate", "EUR Selling Rate", "Holidays/Weekend"
    ]

    # ── Get or create the sheet ──────────────────────────────────────
    if SHEET_NAME in wb.sheetnames:
        ws = wb[SHEET_NAME]
    else:
        ws = wb.create_sheet(SHEET_NAME)

    # Always write/refresh headers with enterprise styling
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(
        start_color="1A365D", end_color="1A365D", fill_type="solid"
    )
    header_align = Alignment(horizontal="center", vertical="center")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )
    for col_idx, header in enumerate(HEADERS, 1):
        cell = ws.cell(row=HEADER_ROW, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # Set column widths
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 16
    ws.column_dimensions["F"].width = 40

    # Clear legacy columns beyond our 6-column layout
    if ws.max_column and ws.max_column > 6:
        for row_idx in range(HEADER_ROW, (ws.max_row or 1) + 1):
            for col in range(7, ws.max_column + 1):
                ws.cell(row=row_idx, column=col).value = None

    # ── Read existing data from the sheet ────────────────────────────
    existing_data = _read_existing_data(ws, DATA_START_ROW)

    # ── Build ALL calendar dates ─────────────────────────────────────
    holidays_set = set(holidays_list)
    end_date = date.today()
    all_dates = _build_date_range(start_date, end_date, existing_data)

    # ── Build the merged dataset ────────────────────────────────────
    merged = _merge_rate_data(
        all_dates, existing_data, holidays_set, holidays_names,
        usd_buying_rates, usd_selling_rates,
        eur_buying_rates, eur_selling_rates,
    )

    # ── Write data ───────────────────────────────────────────────────
    if ws.max_row and ws.max_row >= DATA_START_ROW:
        ws.delete_rows(DATA_START_ROW, ws.max_row - DATA_START_ROW + 1)

    _write_merged_data(ws, merged, holidays_set, thin_border, DATA_START_ROW)


def _read_existing_data(ws, data_start_row: int) -> Dict[date, dict]:
    """Reads existing rate data from the ExRate sheet."""
    existing: Dict[date, dict] = {}
    if ws.max_row and ws.max_row >= data_start_row:
        for row_idx in range(data_start_row, ws.max_row + 1):
            cell_val = ws.cell(row=row_idx, column=1).value
            row_date = _parse_cell_date(cell_val)
            if row_date:
                existing[row_date] = {
                    "usd_buy": ws.cell(row=row_idx, column=2).value,
                    "usd_sell": ws.cell(row=row_idx, column=3).value,
                    "eur_buy": ws.cell(row=row_idx, column=4).value,
                    "eur_sell": ws.cell(row=row_idx, column=5).value,
                    "holidays_weekend": ws.cell(row=row_idx, column=6).value,
                }
    return existing


def _parse_cell_date(cell_val) -> date | None:
    """Parse a date from a cell value."""
    if isinstance(cell_val, datetime):
        return cell_val.date()
    if isinstance(cell_val, date):
        return cell_val
    if isinstance(cell_val, str):
        for fmt in ("%Y-%m-%d", "%d %b %Y"):
            try:
                return datetime.strptime(cell_val.strip(), fmt).date()
            except ValueError:
                continue
    return None


def _build_date_range(
    start: date, end: date, existing: Dict[date, dict]
) -> set:
    """Build the full set of calendar dates to populate."""
    all_dates = set()
    current = start
    while current <= end:
        all_dates.add(current)
        current += timedelta(days=1)
    all_dates |= set(existing.keys())
    return {d for d in all_dates if d >= start}


def _merge_rate_data(
    all_dates, existing_data, holidays_set, holidays_names,
    usd_buying_rates, usd_selling_rates,
    eur_buying_rates, eur_selling_rates,
) -> Dict[date, dict]:
    """Merge API rates with existing sheet data (API priority)."""
    merged: Dict[date, dict] = {}
    for d in sorted(all_dates):
        existing = existing_data.get(d, {})
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_set

        ub = float(usd_buying_rates[d]) if d in usd_buying_rates and usd_buying_rates[d] is not None else None
        us = float(usd_selling_rates[d]) if d in usd_selling_rates and usd_selling_rates[d] is not None else None
        eb = float(eur_buying_rates[d]) if d in eur_buying_rates and eur_buying_rates[d] is not None else None
        es = float(eur_selling_rates[d]) if d in eur_selling_rates and eur_selling_rates[d] is not None else None

        holiday_label = ""
        if is_weekend and is_holiday:
            holiday_label = f"weekend; {holidays_names.get(d, 'Holiday')}"
        elif is_weekend:
            holiday_label = "weekend"
        elif is_holiday:
            holiday_label = holidays_names.get(d, "Holiday")

        merged[d] = {
            "usd_buy": ub if ub is not None else existing.get("usd_buy"),
            "usd_sell": us if us is not None else existing.get("usd_sell"),
            "eur_buy": eb if eb is not None else existing.get("eur_buy"),
            "eur_sell": es if es is not None else existing.get("eur_sell"),
            "holidays_weekend": holiday_label,
        }
    return merged


def _write_merged_data(ws, merged, holidays_set, thin_border, start_row):
    """Write the merged rate data to the worksheet."""
    data_font = Font(name="Calibri", size=10)
    date_align = Alignment(horizontal="center")
    num_align = Alignment(horizontal="right")
    holiday_fill = PatternFill(
        start_color="FFF3CD", end_color="FFF3CD", fill_type="solid"
    )
    weekend_fill = PatternFill(
        start_color="E8E8E8", end_color="E8E8E8", fill_type="solid"
    )

    current_row = start_row
    for d in sorted(merged.keys()):
        entry = merged[d]
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_set

        cell_date = ws.cell(row=current_row, column=1, value=d)
        cell_date.number_format = "DD MMM YYYY"
        cell_date.font = data_font
        cell_date.alignment = date_align
        cell_date.border = thin_border

        for col, key, fmt in [
            (2, "usd_buy", "0.0000"), (3, "usd_sell", "0.0000"),
            (4, "eur_buy", "0.0000"), (5, "eur_sell", "0.0000"),
        ]:
            cell = ws.cell(row=current_row, column=col, value=entry[key])
            if entry[key] is not None:
                cell.number_format = fmt
            cell.font = data_font
            cell.alignment = num_align
            cell.border = thin_border

        cell_hw = ws.cell(
            row=current_row, column=6, value=entry["holidays_weekend"]
        )
        cell_hw.font = data_font
        cell_hw.border = thin_border

        if is_holiday:
            for col in range(1, 7):
                ws.cell(row=current_row, column=col).fill = holiday_fill
        elif is_weekend:
            for col in range(1, 7):
                ws.cell(row=current_row, column=col).fill = weekend_fill

        current_row += 1
