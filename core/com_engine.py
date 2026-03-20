#!/usr/bin/env python3
"""
core/com_engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.8) - Native Windows COM Engine
---------------------------------------------------------------------------
Primary data processing engine for Windows 11 using win32com.client.
Spawns an invisible Microsoft Excel instance to read/write ledger files,
guaranteeing 100% preservation of all native styles, fonts, and layouts.

CRITICAL: This module MUST ONLY be imported on sys.platform == "win32".
---------------------------------------------------------------------------
"""

import logging
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Guard: never import on non-Windows
if sys.platform != "win32":
    raise ImportError("com_engine.py is strictly Windows-only (win32com)")

try:
    import pywintypes
    import win32com.client  # type: ignore
except ImportError:
    raise RuntimeError(
        "FATAL: Missing pywin32 dependency.\n"
        "Please run: pip install pywin32"
    )

# Excel constants
XL_FILE_FORMAT_XLSX = 51  # xlOpenXMLWorkbook
XL_UP = -4162  # xlUp


def _ensure_absolute(path: str) -> str:
    """Convert any path to a strict absolute Windows path for COM safety."""
    return os.path.abspath(path)


def _parse_cell_date(value) -> Optional[date]:
    """Parse a date from an Excel COM cell value."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    # COM often returns pywintypes.TimeType
    try:
        import pywintypes as _pt
        if isinstance(value, _pt.TimeType):
            # Convert to Python datetime
            return datetime(
                value.year, value.month, value.day
            ).date()
    except (AttributeError, TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        # Excel serial date number
        try:
            from datetime import timedelta as _td
            # Excel epoch: 1899-12-30
            epoch = datetime(1899, 12, 30)
            return (epoch + _td(days=int(value))).date()
        except (ValueError, OverflowError):
            return None
    if isinstance(value, str):
        val = value.strip()
        if not val or val.lower() in ("nan", "null"):
            return None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
                    "%d %b %Y", "%d %B %Y", "%Y%m%d"):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


# =========================================================================
#  EXCEL COM CONTEXT MANAGER — Zombie-Safe
# =========================================================================

class ExcelCOM:
    """
    Context manager for the Excel COM lifecycle.

    Guarantees that excel.Quit() is ALWAYS called, even if the
    processing code crashes midway. This prevents zombie EXCEL.EXE
    processes from lingering in the Windows Task Manager.
    """

    def __init__(self):
        self.excel = None

    def __enter__(self):
        logger.info("Spawning invisible Microsoft Excel COM instance...")
        self.excel = win32com.client.DispatchEx("Excel.Application")
        self.excel.Visible = False
        self.excel.DisplayAlerts = False
        self.excel.ScreenUpdating = False
        return self.excel

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.excel is not None:
            try:
                self.excel.Quit()
                logger.info("Excel COM instance terminated cleanly.")
            except Exception as e:
                logger.warning("Failed to quit Excel COM: %s", e)
            finally:
                self.excel = None
        return False  # Do not suppress exceptions


# =========================================================================
#  EXRATE MASTER SHEET — COM-Native Builder
# =========================================================================

def _build_exrate_sheet_com(
    wb,
    usd_buying: Dict[date, Decimal],
    usd_selling: Dict[date, Decimal],
    eur_buying: Dict[date, Decimal],
    eur_selling: Dict[date, Decimal],
    holidays_set: Set[date],
    holidays_names: Dict[date, str],
    start_date: date,
) -> None:
    """
    Build or refresh the ExRate master tab using native Excel COM.

    Uses Excel's native formatting engine so fonts/colors/borders
    are applied by Microsoft Excel itself — zero fidelity loss.
    """
    SHEET_NAME = "ExRate"
    HEADERS = [
        "Date", "USD Buying TT Rate", "USD Selling Rate",
        "EUR Buying TT Rate", "EUR Selling Rate", "Holidays/Weekend"
    ]

    # Get or create the sheet
    sheet_names = [wb.Sheets(i).Name for i in range(1, wb.Sheets.Count + 1)]
    if SHEET_NAME in sheet_names:
        ws = wb.Sheets(SHEET_NAME)
    else:
        ws = wb.Sheets.Add(After=wb.Sheets(wb.Sheets.Count))
        ws.Name = SHEET_NAME

    # Write headers (Row 1)
    for col_idx, header in enumerate(HEADERS, 1):
        cell = ws.Cells(1, col_idx)
        cell.Value = header
        cell.Font.Name = "Calibri"
        cell.Font.Size = 11
        cell.Font.Bold = True
        cell.Font.Color = 0xFFFFFF  # White
        cell.Interior.Color = 0x5D361A  # Dark blue (BGR: 1A365D)
        cell.HorizontalAlignment = -4108  # xlCenter
        cell.VerticalAlignment = -4108  # xlCenter

    # Set borders on header row
    header_range = ws.Range(ws.Cells(1, 1), ws.Cells(1, 6))
    for edge in range(7, 13):  # xlEdgeLeft through xlInsideVertical
        try:
            header_range.Borders(edge).LineStyle = 1  # xlContinuous
            header_range.Borders(edge).Weight = 2  # xlThin
        except Exception:
            pass

    # Column widths
    ws.Columns("A").ColumnWidth = 14
    ws.Columns("B").ColumnWidth = 18
    ws.Columns("C").ColumnWidth = 16
    ws.Columns("D").ColumnWidth = 18
    ws.Columns("E").ColumnWidth = 16
    ws.Columns("F").ColumnWidth = 40

    # Clear existing data rows (keep header)
    last_row = ws.Cells(ws.Rows.Count, 1).End(XL_UP).Row
    if last_row > 1:
        ws.Range(
            ws.Cells(2, 1), ws.Cells(last_row, 6)
        ).ClearContents()
        ws.Range(
            ws.Cells(2, 1), ws.Cells(last_row, 6)
        ).ClearFormats()

    # Build date range
    end_date = date.today()
    all_dates = set()
    current = start_date
    while current <= end_date:
        all_dates.add(current)
        current += timedelta(days=1)

    # Write data rows
    current_row = 2
    for d in sorted(all_dates):
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_set

        # Date
        cell_date = ws.Cells(current_row, 1)
        cell_date.Value = datetime(d.year, d.month, d.day)
        cell_date.NumberFormat = "DD MMM YYYY"
        cell_date.Font.Name = "Calibri"
        cell_date.Font.Size = 10
        cell_date.HorizontalAlignment = -4108  # xlCenter

        # USD Buying
        ub = float(usd_buying[d]) if d in usd_buying and usd_buying[d] is not None else None
        ws.Cells(current_row, 2).Value = ub
        if ub is not None:
            ws.Cells(current_row, 2).NumberFormat = "0.0000"

        # USD Selling
        us = float(usd_selling[d]) if d in usd_selling and usd_selling[d] is not None else None
        ws.Cells(current_row, 3).Value = us
        if us is not None:
            ws.Cells(current_row, 3).NumberFormat = "0.0000"

        # EUR Buying
        eb = float(eur_buying[d]) if d in eur_buying and eur_buying[d] is not None else None
        ws.Cells(current_row, 4).Value = eb
        if eb is not None:
            ws.Cells(current_row, 4).NumberFormat = "0.0000"

        # EUR Selling
        es = float(eur_selling[d]) if d in eur_selling and eur_selling[d] is not None else None
        ws.Cells(current_row, 5).Value = es
        if es is not None:
            ws.Cells(current_row, 5).NumberFormat = "0.0000"

        # Holiday/Weekend label
        holiday_label = ""
        if is_weekend and is_holiday:
            holiday_label = f"weekend; {holidays_names.get(d, 'Holiday')}"
        elif is_weekend:
            holiday_label = "weekend"
        elif is_holiday:
            holiday_label = holidays_names.get(d, "Holiday")
        ws.Cells(current_row, 6).Value = holiday_label

        # Font for data columns
        for col in range(1, 7):
            ws.Cells(current_row, col).Font.Name = "Calibri"
            ws.Cells(current_row, col).Font.Size = 10

        # Row-level styling for holidays/weekends
        row_range = ws.Range(ws.Cells(current_row, 1), ws.Cells(current_row, 6))
        if is_holiday:
            row_range.Interior.Color = 0xCDF3FF  # Light yellow (BGR: FFF3CD)
        elif is_weekend:
            row_range.Interior.Color = 0xE8E8E8  # Light gray

        # Borders
        for edge in range(7, 13):
            try:
                row_range.Borders(edge).LineStyle = 1
                row_range.Borders(edge).Weight = 2
            except Exception:
                pass

        current_row += 1

    logger.info("ExRate master sheet built via COM: %d rows", current_row - 2)


# =========================================================================
#  BUILD EXRATE INDEX (from COM worksheet)
# =========================================================================

def _build_exrate_index_com(wb) -> Dict[date, dict]:
    """Build the in-memory ExRate lookup index from the COM workbook."""
    exrate_index: Dict[date, dict] = {}
    sheet_names = [wb.Sheets(i).Name for i in range(1, wb.Sheets.Count + 1)]
    if "ExRate" not in sheet_names:
        return exrate_index

    ws = wb.Sheets("ExRate")
    last_row = ws.Cells(ws.Rows.Count, 1).End(XL_UP).Row
    if last_row < 2:
        return exrate_index

    for row_idx in range(2, last_row + 1):
        cell_val = ws.Cells(row_idx, 1).Value
        row_date = _parse_cell_date(cell_val)
        if row_date:
            exrate_index[row_date] = {
                "usd_buying": ws.Cells(row_idx, 2).Value,
                "usd_selling": ws.Cells(row_idx, 3).Value,
                "eur_buying": ws.Cells(row_idx, 4).Value,
                "eur_selling": ws.Cells(row_idx, 5).Value,
            }
    return exrate_index


# =========================================================================
#  CROSS-TAB VLOOKUP
# =========================================================================

def _vlookup_exrate(
    target_date: date,
    currency: str,
    exrate_index: Dict[date, dict],
    max_rollback: int = 10,
) -> Optional[float]:
    """Cross-tab VLOOKUP: find the Buying TT Rate walking backwards."""
    rate_key = {"USD": "usd_buying", "EUR": "eur_buying"}.get(currency)
    if rate_key is None:
        return None
    current = target_date
    for _ in range(max_rollback + 1):
        entry = exrate_index.get(current)
        if entry is not None:
            val = entry.get(rate_key)
            if val is not None:
                return float(val) if not isinstance(val, float) else val
        current -= timedelta(days=1)
    return None


# =========================================================================
#  MAIN COM PIPELINE — process_ledger_com
# =========================================================================

# Sheets that are reference/master and should NOT be processed as ledgers
SKIP_SHEET_NAMES = {"ExRate"}


def process_ledger_com(
    filepath: str,
    usd_buying: Dict[date, Decimal],
    usd_selling: Dict[date, Decimal],
    eur_buying: Dict[date, Decimal],
    eur_selling: Dict[date, Decimal],
    holidays_set: Set[date],
    holidays_names: Dict[date, str],
    computed_start: date,
    target_cols: Dict[str, str],
) -> str:
    """
    Process a single ledger file using Native Microsoft Excel COM.

    This function opens the file in an invisible Excel instance,
    writes the ExRate master sheet, performs cross-tab VLOOKUP on
    monthly tabs, and saves using Excel's native engine.

    Memory management:
      - ExcelCOM context manager guarantees excel.Quit() in finally
      - Each workbook is explicitly closed before Excel quits
      - No zombie EXCEL.EXE processes can survive

    Args:
        filepath: Path to the .xlsx ledger file (will be made absolute)
        usd_buying/usd_selling/eur_buying/eur_selling: Rate dicts
        holidays_set: Set of holiday dates
        holidays_names: Dict mapping dates to holiday names
        computed_start: Start date for ExRate sheet
        target_cols: Column name mapping (source_date, currency, out_rate)

    Returns:
        The absolute path to the saved file.
    """
    # ── MANDATE 2: Absolute pathing for COM safety ────────────────────
    filepath = _ensure_absolute(filepath)
    logger.info("COM Engine: Processing %s", os.path.basename(filepath))

    wb = None
    with ExcelCOM() as excel:
        try:
            # Open the workbook
            wb = excel.Workbooks.Open(filepath)

            # ── STEP 1: Build ExRate master sheet ─────────────────────
            _build_exrate_sheet_com(
                wb,
                usd_buying, usd_selling,
                eur_buying, eur_selling,
                holidays_set, holidays_names,
                computed_start,
            )

            # ── STEP 2: Build in-memory ExRate lookup index ──────────
            exrate_index = _build_exrate_index_com(wb)

            # ── STEP 3: Cross-Tab VLOOKUP on monthly tabs ────────────
            for sheet_idx in range(1, wb.Sheets.Count + 1):
                ws = wb.Sheets(sheet_idx)
                sheet_name = ws.Name
                if sheet_name in SKIP_SHEET_NAMES:
                    continue

                # Scan headers (first 10 rows)
                header_row_idx = None
                col_indices: Dict[str, int] = {}
                for row_idx in range(1, 11):
                    row_vals = []
                    # Read up to 20 columns for header scan
                    for col_idx in range(1, 21):
                        val = ws.Cells(row_idx, col_idx).Value
                        row_vals.append(
                            str(val).strip() if val is not None else ""
                        )

                    if target_cols["source_date"] in row_vals:
                        header_row_idx = row_idx
                        for ci, val in enumerate(row_vals):
                            if val == target_cols["source_date"]:
                                col_indices["source"] = ci + 1  # 1-indexed
                            elif val == target_cols["currency"]:
                                col_indices["currency"] = ci + 1
                            elif val == target_cols["out_rate"]:
                                col_indices["out_rate"] = ci + 1
                        break

                if header_row_idx is None or "source" not in col_indices:
                    logger.info(
                        "Sheet '%s' missing source date column — skipped.",
                        sheet_name,
                    )
                    continue

                # Process data rows
                src_col = col_indices["source"]
                cur_col = col_indices.get("currency")
                rate_col = col_indices.get("out_rate")
                last_data_row = ws.Cells(
                    ws.Rows.Count, src_col
                ).End(XL_UP).Row

                for row_idx in range(header_row_idx + 1, last_data_row + 1):
                    inv_date = _parse_cell_date(
                        ws.Cells(row_idx, src_col).Value
                    )
                    if not inv_date:
                        continue

                    # Read currency
                    ccy = ""
                    if cur_col is not None:
                        raw = ws.Cells(row_idx, cur_col).Value
                        ccy = str(raw).strip().upper() if raw else ""

                    if ccy == "THB":
                        if rate_col is not None:
                            ws.Cells(row_idx, rate_col).Value = 1
                        continue

                    # Cross-tab lookup
                    rate = _vlookup_exrate(inv_date, ccy, exrate_index)
                    if rate_col is not None:
                        ws.Cells(row_idx, rate_col).Value = rate

            # ── Save natively through Excel ───────────────────────────
            # If the original file is a legacy .xls, save it out as a modern .xlsx
            is_xls = filepath.lower().endswith(".xls") and not filepath.lower().endswith(".xlsx")
            if is_xls:
                # FileFormat = 51 strictly enforces .xlsx
                final_path = os.path.splitext(filepath)[0] + ".xlsx"
                wb.SaveAs(_ensure_absolute(final_path), FileFormat=51)
                filepath = final_path
                logger.info(
                    "COM Engine: Processed and converted to %s via native Excel.",
                    os.path.basename(filepath),
                )
            else:
                wb.Save()
                logger.info(
                    "COM Engine: Saved %s via native Excel.",
                    os.path.basename(filepath),
                )

        except pywintypes.com_error as ce:
            logger.error("Excel COM error during processing: %s", ce)
            raise RuntimeError(
                f"Microsoft Excel COM error: {ce}\n"
                "Ensure Microsoft Excel is installed on this Windows machine."
            ) from ce
        finally:
            # ── MANDATE 3: Guaranteed cleanup — no zombies ────────────
            if wb is not None:
                try:
                    wb.Close(SaveChanges=False)
                except Exception:
                    pass

    return filepath
