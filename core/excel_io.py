#!/usr/bin/env python3
"""
core/excel_io.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Excel I/O Operations
---------------------------------------------------------------------------
Extracted from engine.py (C-01 decomposition) to enforce Single
Responsibility and keep engine.py under the 200 LOC SFFB guideline.

Contains:
  - zero_touch_write: Write cell value without touching formatting
  - build_exrate_index: Build in-memory lookup from ExRate sheet
  - scan_sheet_headers: Scan monthly tabs for column mappings
  - inject_xlookup_formulas: Write XLOOKUP formulas into monthly tabs
  - write_custom_exrate_data: Write multi-currency ExRate data
"""

import logging
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from core.constants import PREFORMAT_BUFFER_ROWS, SKIP_SHEET_NAMES

logger = logging.getLogger(__name__)


def zero_touch_write(ws, row: int, col: int, value) -> None:
    """
    Write a value to a monthly-tab cell WITHOUT touching formatting.

    Zero-Touch Protocol: ONLY writes cell.value.
    NEVER reads, copies, or re-applies font/fill/border/alignment.

    In openpyxl, assigning cell.value does NOT alter the cell's
    existing styles. Touching style attributes (even via .copy())
    creates new style objects that can differ from the originals.

    Silently skips MergedCell instances (read-only).
    """
    cell = ws.cell(row=row, column=col)
    if isinstance(cell, MergedCell):
        return
    cell.value = value


def build_exrate_index(wb) -> Dict[date, dict]:
    """
    Build an in-memory ExRate lookup index from the ExRate sheet.

    Reads dates from column A and rate values from columns B-E.
    Returns a dict mapping date → {usd_buying, usd_selling,
    eur_buying, eur_selling}.
    """
    exrate_index: Dict[date, dict] = {}
    if "ExRate" not in wb.sheetnames:
        return exrate_index

    ws_exrate = wb["ExRate"]
    for row_idx in range(2, (ws_exrate.max_row or 1) + 1):
        cell_val = ws_exrate.cell(row=row_idx, column=1).value
        row_date = None
        if isinstance(cell_val, datetime):
            row_date = cell_val.date()
        elif isinstance(cell_val, date):
            row_date = cell_val
        if row_date:
            exrate_index[row_date] = {
                "usd_buying": ws_exrate.cell(
                    row=row_idx, column=2
                ).value,
                "usd_selling": ws_exrate.cell(
                    row=row_idx, column=3
                ).value,
                "eur_buying": ws_exrate.cell(
                    row=row_idx, column=4
                ).value,
                "eur_selling": ws_exrate.cell(
                    row=row_idx, column=5
                ).value,
            }

    return exrate_index


def scan_sheet_headers(
    wb,
    target_cols: Dict[str, str],
) -> Dict[str, dict]:
    """
    Scan monthly tabs for header rows and column indices.

    Returns a dict mapping sheet_name → {header_row, columns}.
    Skips sheets in SKIP_SHEET_NAMES and sheets without the
    source date column.
    """
    sheet_maps: Dict[str, dict] = {}

    for sheet_name in wb.sheetnames:
        if sheet_name in SKIP_SHEET_NAMES:
            continue
        ws = wb[sheet_name]
        header_row_idx = None
        col_indices_local: Dict[str, int] = {}
        for row_idx, row in enumerate(
            ws.iter_rows(min_row=1, max_row=10, values_only=True), 1
        ):
            row_strs = [
                str(c).strip() if c is not None else "" for c in row
            ]
            if target_cols["source_date"] in row_strs:
                header_row_idx = row_idx
                for ci, val in enumerate(row_strs):
                    if val == target_cols["source_date"]:
                        col_indices_local["source"] = ci
                    elif val == target_cols["currency"]:
                        col_indices_local["currency"] = ci
                    elif val == target_cols["out_rate"]:
                        col_indices_local["out_rate"] = ci
                break

        if header_row_idx is None or "source" not in col_indices_local:
            logger.info(
                "Sheet '%s' missing source date column — skipped.",
                sheet_name,
            )
            continue

        sheet_maps[sheet_name] = {
            "header_row": header_row_idx,
            "columns": col_indices_local,
        }

    return sheet_maps


def inject_xlookup_formulas(
    wb,
    sheet_maps: Dict[str, dict],
    exrate_last_row: int,
    parse_date_fn: Callable,
    emit_fn: Optional[Callable[[str], None]] = None,
    dry_run: bool = False,
    buffer_rows: int = PREFORMAT_BUFFER_ROWS,
    rate_type: str = "buying_transfer",
    exrate_col_map: Optional[Dict[str, str]] = None,
) -> None:
    """
    Inject XLOOKUP formulas into monthly tabs.

    Writes a SINGLE IFS formula per row to the "EX Rate" column
    that dynamically checks the Cur column for the currency.

    This means:
      - Formula can be dragged down without breaking
      - Currency is checked inside the formula via IFS()
      - THB → 1, USD → ExRate col (varies by rate_type),
        EUR → ExRate col (varies by rate_type)
      - Additional currencies (GBP/JPY/CNY) via exrate_col_map

    CRITICAL: Date Normalization
    Monthly tab dates may be stored as TEXT STRINGS (e.g.,
    "10/03/2025") which lookups cannot match against the
    DATE SERIAL NUMBERS in ExRate. We normalize by writing
    the parsed date object back to the cell.

    Args:
        wb: openpyxl Workbook.
        sheet_maps: Dict from scan_sheet_headers().
        exrate_last_row: Last data row in ExRate sheet.
        parse_date_fn: Callable to parse cell values to date objects.
        emit_fn: Optional status callback.
        dry_run: If True, don't write; just report what would change.
        buffer_rows: Number of rows below data to pre-format.
        rate_type: API field name for the selected rate type
            ("buying_transfer", "selling", "buying_sight", "mid_rate").
            Determines which ExRate columns are referenced.
        exrate_col_map: Optional dict mapping currency code → ExRate
            column letter for additional currencies beyond USD/EUR.
    """
    N = exrate_last_row

    # ── Map rate_type → ExRate column letters for USD and EUR ─────
    # ExRate layout: A=Date, B=USD Buying, C=USD Selling,
    #                D=EUR Buying, E=EUR Selling, F=Holidays
    if rate_type == "selling":
        usd_col = "C"
        eur_col = "E"
    else:
        # "buying_transfer", "buying_sight", "mid_rate" → buying columns
        usd_col = "B"
        eur_col = "D"

    for sheet_name, mapping in sheet_maps.items():
        ws = wb[sheet_name]
        cols = mapping["columns"]
        src_idx = cols["source"] + 1
        cur_idx = cols.get("currency")
        out_rate_idx = cols.get("out_rate")
        if out_rate_idx is None or cur_idx is None:
            continue
        out_col = out_rate_idx + 1  # 1-indexed
        cur_col = cur_idx + 1       # 1-indexed

        # Column letters for cell references in formulas
        date_letter = get_column_letter(src_idx)
        cur_letter = get_column_letter(cur_col)

        skipped = 0
        written = 0
        overwritten = 0
        for row_idx in range(
            mapping["header_row"] + 1, ws.max_row + 1
        ):
            src_cell = ws.cell(row=row_idx, column=src_idx)
            out_cell = ws.cell(row=row_idx, column=out_col)

            # Skip merged cells — they are read-only
            if isinstance(src_cell, MergedCell):
                continue
            if isinstance(out_cell, MergedCell):
                continue

            # ── Date Normalization ─────────────────────────
            inv_date = parse_date_fn(src_cell.value)
            if inv_date:
                existing_fmt = src_cell.number_format or "General"
                src_cell.value = inv_date
                if existing_fmt in (
                    "General", "@", "0", "general",
                ):
                    src_cell.number_format = "dd mmm yyyy"
                else:
                    src_cell.number_format = existing_fmt

            # ── Build the expected XLOOKUP formula ─────────
            date_ref = f"{date_letter}{row_idx}"
            cur_ref = f"{cur_letter}{row_idx}"

            # Core IFS branches: THB, USD, EUR
            ifs_branches = (
                f"{cur_ref}=\"THB\",1,"
                f"{cur_ref}=\"USD\","
                f"IFERROR(_xlfn.XLOOKUP({date_ref},"
                f"ExRate!$A$2:$A${N},"
                f"ExRate!${usd_col}$2:${usd_col}${N},\"\",0),\"\"),"
                f"{cur_ref}=\"EUR\","
                f"IFERROR(_xlfn.XLOOKUP({date_ref},"
                f"ExRate!$A$2:$A${N},"
                f"ExRate!${eur_col}$2:${eur_col}${N},\"\",0),\"\")"
            )

            # Additional currency branches from exrate_col_map
            if exrate_col_map:
                for ccy, col_letter in exrate_col_map.items():
                    if ccy in ("USD", "EUR", "THB"):
                        continue  # already handled above
                    ifs_branches += (
                        f",{cur_ref}=\"{ccy}\","
                        f"IFERROR(_xlfn.XLOOKUP({date_ref},"
                        f"ExRate!$A$2:$A${N},"
                        f"ExRate!${col_letter}$2:${col_letter}${N},"
                        f"\"\",0),\"\")"
                    )

            formula = (
                f"=IF(OR({cur_ref}=\"\",{date_ref}=\"\"),\"\","
                f"_xlfn.IFS("
                f"{ifs_branches},"
                f"TRUE,\"\"))"
            )

            # ── Skip-if-identical: exact formula match ─────
            existing_val = out_cell.value
            if (
                isinstance(existing_val, str)
                and existing_val == formula
            ):
                skipped += 1
                continue

            # Track if we're replacing an old formula
            if existing_val is not None:
                if isinstance(existing_val, str) and existing_val.startswith("="):
                    overwritten += 1

            zero_touch_write(ws, row_idx, out_col, formula)
            written += 1

        if skipped or overwritten or written:
            logger.info(
                "Sheet '%s': %d identical (skipped), "
                "%d old formulas replaced, %d new written",
                sheet_name, skipped, overwritten,
                written - overwritten,
            )
            if dry_run and emit_fn:
                emit_fn(
                    f"[SIM] {sheet_name}: Would inject {written} formulas "
                    f"(replaced {overwritten}) and normalize {written} dates"
                )
            elif emit_fn:
                emit_fn(
                    f"{sheet_name}: {skipped} skipped, "
                    f"{overwritten} replaced, "
                    f"{written - overwritten} new"
                )

        # ── Pre-format Date column for manual entry ───────────
        max_preformat = ws.max_row + buffer_rows
        for r in range(mapping["header_row"] + 1, max_preformat + 1):
            cell = ws.cell(row=r, column=src_idx)
            if not isinstance(cell, MergedCell):
                cell.number_format = "DD/MM/YYYY"


def write_custom_exrate_data(
    ws,
    rate_data: Dict[str, Dict[str, Dict[date, float]]],
    col_specs: List[Tuple[str, str]],
    headers: List[str],
    all_dates: List[date],
    holidays_set: Set[date],
    holidays_names: Dict[date, str],
) -> None:
    """
    Write multi-currency ExRate data with styling to a worksheet.

    Used by the custom ExRate path in update_exrate_standalone
    for non-standard currency/rate-type combinations.

    Args:
        ws: Target worksheet.
        rate_data: Nested dict: rate_data[ccy][api_field][date] = value.
        col_specs: List of (currency, api_field) per data column.
        headers: Column header labels.
        all_dates: Sorted list of dates to write.
        holidays_set: Set of holiday dates.
        holidays_names: Map of date → holiday name.
    """
    # ── Styles ────────────────────────────────────────────────────
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(
        start_color="1A365D", end_color="1A365D", fill_type="solid"
    )
    header_align = Alignment(horizontal="center", vertical="center")
    data_font = Font(name="Calibri", size=10)
    date_align = Alignment(horizontal="center")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )
    holiday_fill = PatternFill(
        start_color="FFF3CD", end_color="FFF3CD", fill_type="solid",
    )
    weekend_fill = PatternFill(
        start_color="E8E8E8", end_color="E8E8E8", fill_type="solid",
    )

    # ── Clear existing content ────────────────────────────────────
    for row_idx in range(1, max(ws.max_row or 1, 1) + 1):
        for col_idx in range(1, max(ws.max_column or 1, 1) + 1):
            ws.cell(row=row_idx, column=col_idx).value = None

    # ── Write headers ─────────────────────────────────────────────
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # ── Column widths ─────────────────────────────────────────────
    ws.column_dimensions["A"].width = 14
    for i in range(len(col_specs)):
        col_letter = get_column_letter(i + 2)
        ws.column_dimensions[col_letter].width = 18
    last_col_letter = get_column_letter(len(headers))
    ws.column_dimensions[last_col_letter].width = 40

    # ── Data rows ─────────────────────────────────────────────────
    for row_offset, d in enumerate(all_dates):
        row_idx = row_offset + 2
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_set

        # Date
        cell_date = ws.cell(row=row_idx, column=1, value=d)
        cell_date.number_format = "DD/MM/YYYY"
        cell_date.font = data_font
        cell_date.alignment = date_align
        cell_date.border = thin_border

        # Rate columns
        for col_offset, (ccy, api_field) in enumerate(col_specs):
            val = rate_data.get(ccy, {}).get(api_field, {}).get(d)
            cell = ws.cell(row=row_idx, column=col_offset + 2, value=val)
            cell.number_format = "0.0000"
            cell.font = data_font
            cell.border = thin_border

        # Holiday/Weekend label
        if is_weekend and is_holiday:
            label = f"Weekend; {holidays_names.get(d, 'Holiday')}"
        elif is_weekend:
            label = "Weekend"
        elif is_holiday:
            label = holidays_names.get(d, "Holiday")
        else:
            label = ""

        cell_label = ws.cell(
            row=row_idx, column=len(headers), value=label,
        )
        cell_label.font = data_font
        cell_label.border = thin_border

        # Row fill
        if is_holiday:
            for ci in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=ci).fill = holiday_fill
        elif is_weekend:
            for ci in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=ci).fill = weekend_fill
