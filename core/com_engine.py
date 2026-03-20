#!/usr/bin/env python3
"""
core/com_engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.9) — High-Speed Native Windows COM Engine
---------------------------------------------------------------------------
Primary data processing engine for Windows 11 using win32com.client.
Spawns an invisible Microsoft Excel instance to read/write ledger files,
guaranteeing 100% preservation of all native styles, fonts, and layouts.

PERFORMANCE ARCHITECTURE (v2.5.9):
  1. Silent Mode: Calculation=Manual, EnableEvents=False, ScreenUpdating=False
  2. Vectorized I/O: All reads/writes use bulk Range operations (zero cell loops)
  3. Instance Pooling: Optional shared Excel instance for batch processing

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
XL_FILE_FORMAT_XLSX = 51   # xlOpenXMLWorkbook
XL_UP = -4162              # xlUp
XL_CALC_MANUAL = -4135     # xlCalculationManual
XL_CALC_AUTOMATIC = -4105  # xlCalculationAutomatic


def _ensure_absolute(path: str) -> str:
    """Convert any path to a strict absolute Windows path for COM safety."""
    return os.path.abspath(path)


def _parse_cell_date(value) -> Optional[date]:
    """Parse a date from an Excel COM cell value (or from a bulk-read tuple)."""
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
            return datetime(value.year, value.month, value.day).date()
    except (AttributeError, TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        try:
            epoch = datetime(1899, 12, 30)
            return (epoch + timedelta(days=int(value))).date()
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
#  EXCEL COM CONTEXT MANAGER — Zombie-Safe + Silent Mode
# =========================================================================

class ExcelCOM:
    """
    Context manager for the Excel COM lifecycle.

    MANDATE 1 — Silent Mode Performance Flags:
      - ScreenUpdating  = False  (no UI redraws)
      - Calculation      = Manual (no recalc on every cell write)
      - EnableEvents     = False  (no VBA event triggers)

    All flags are restored in __exit__ before Quit(), even on crash.
    Guarantees excel.Quit() is ALWAYS called to prevent zombie processes.
    """

    def __init__(self):
        self.excel = None

    def __enter__(self):
        logger.info("Spawning invisible Microsoft Excel COM instance (Silent Mode)...")
        self.excel = win32com.client.DispatchEx("Excel.Application")
        self.excel.Visible = False
        self.excel.DisplayAlerts = False
        # ── MANDATE 1: Silent Mode Performance Flags ──────────────
        self.excel.ScreenUpdating = False
        self.excel.Calculation = XL_CALC_MANUAL
        self.excel.EnableEvents = False
        return self.excel

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.excel is not None:
            try:
                # ── Restore flags before quitting ─────────────────────
                self.excel.ScreenUpdating = True
                self.excel.Calculation = XL_CALC_AUTOMATIC
                self.excel.EnableEvents = True
            except Exception:
                pass  # Excel may already be dead
            try:
                self.excel.Quit()
                logger.info("Excel COM instance terminated cleanly.")
            except Exception as e:
                logger.warning("Failed to quit Excel COM: %s", e)
            finally:
                self.excel = None
        return False  # Do not suppress exceptions


# =========================================================================
#  EXRATE MASTER SHEET — Vectorized COM-Native Builder
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
    Build or refresh the ExRate master tab using VECTORIZED Excel COM.

    MANDATE 2 — Vectorized Write:
      1. Build the entire data grid as a Python 2D list in RAM
      2. Write ALL values in ONE Range.Value assignment
      3. Apply formatting via range-based operations (not per-cell)
    """
    SHEET_NAME = "ExRate"
    HEADERS = [
        "Date", "USD Buying TT Rate", "USD Selling Rate",
        "EUR Buying TT Rate", "EUR Selling Rate", "Holidays/Weekend"
    ]
    NUM_COLS = len(HEADERS)

    # Get or create the sheet
    sheet_names = [wb.Sheets(i).Name for i in range(1, wb.Sheets.Count + 1)]
    if SHEET_NAME in sheet_names:
        ws = wb.Sheets(SHEET_NAME)
    else:
        ws = wb.Sheets.Add(After=wb.Sheets(wb.Sheets.Count))
        ws.Name = SHEET_NAME

    # ── Write headers in one shot ─────────────────────────────────
    header_range = ws.Range(ws.Cells(1, 1), ws.Cells(1, NUM_COLS))
    header_range.Value = [HEADERS]  # 2D: [[h1, h2, ...]]
    header_range.Font.Name = "Calibri"
    header_range.Font.Size = 11
    header_range.Font.Bold = True
    header_range.Font.Color = 0xFFFFFF  # White
    header_range.Interior.Color = 0x5D361A  # Dark blue (BGR: 1A365D)
    header_range.HorizontalAlignment = -4108  # xlCenter
    header_range.VerticalAlignment = -4108  # xlCenter
    for edge in range(7, 13):
        try:
            header_range.Borders(edge).LineStyle = 1
            header_range.Borders(edge).Weight = 2
        except Exception:
            pass

    # Column widths (bulk)
    col_widths = [14, 18, 16, 18, 16, 40]
    for i, w in enumerate(col_widths, 1):
        ws.Columns(i).ColumnWidth = w

    # Clear existing data rows (keep header)
    last_row = ws.Cells(ws.Rows.Count, 1).End(XL_UP).Row
    if last_row > 1:
        clear_range = ws.Range(ws.Cells(2, 1), ws.Cells(last_row, NUM_COLS))
        clear_range.ClearContents()
        clear_range.ClearFormats()

    # ══════════════════════════════════════════════════════════════
    #  MANDATE 2: Build entire 2D data grid IN PYTHON RAM
    # ══════════════════════════════════════════════════════════════
    end_date = date.today()
    all_dates = []
    current = start_date
    while current <= end_date:
        all_dates.append(current)
        current += timedelta(days=1)
    all_dates.sort()

    num_rows = len(all_dates)
    if num_rows == 0:
        return

    # Build the 2D data array and track holiday/weekend rows
    data_grid = []
    holiday_rows = []  # (row_index, is_holiday, is_weekend)
    weekend_rows = []

    for row_offset, d in enumerate(all_dates):
        is_weekend = d.weekday() >= 5
        is_holiday = d in holidays_set

        ub = float(usd_buying[d]) if d in usd_buying and usd_buying[d] is not None else None
        us = float(usd_selling[d]) if d in usd_selling and usd_selling[d] is not None else None
        eb = float(eur_buying[d]) if d in eur_buying and eur_buying[d] is not None else None
        es = float(eur_selling[d]) if d in eur_selling and eur_selling[d] is not None else None

        # Holiday/Weekend label
        if is_weekend and is_holiday:
            label = f"weekend; {holidays_names.get(d, 'Holiday')}"
        elif is_weekend:
            label = "weekend"
        elif is_holiday:
            label = holidays_names.get(d, "Holiday")
        else:
            label = ""

        # COM expects datetime objects for Excel date serial conversion
        data_grid.append([
            datetime(d.year, d.month, d.day),
            ub, us, eb, es, label
        ])

        excel_row = row_offset + 2  # +2 because row 1 is header
        if is_holiday:
            holiday_rows.append(excel_row)
        elif is_weekend:
            weekend_rows.append(excel_row)

    # ══════════════════════════════════════════════════════════════
    #  SINGLE VECTORIZED WRITE — all values in one COM call
    # ══════════════════════════════════════════════════════════════
    data_range = ws.Range(
        ws.Cells(2, 1),
        ws.Cells(num_rows + 1, NUM_COLS)
    )
    data_range.Value = data_grid
    logger.info("ExRate: Vectorized write complete (%d rows in 1 COM call)", num_rows)

    # ══════════════════════════════════════════════════════════════
    #  RANGE-BASED FORMATTING (not per-cell)
    # ══════════════════════════════════════════════════════════════

    # Font for entire data range
    data_range.Font.Name = "Calibri"
    data_range.Font.Size = 10

    # Date column formatting
    date_col_range = ws.Range(ws.Cells(2, 1), ws.Cells(num_rows + 1, 1))
    date_col_range.NumberFormat = "DD MMM YYYY"
    date_col_range.HorizontalAlignment = -4108  # xlCenter

    # Rate columns number format (columns B-E)
    rate_range = ws.Range(ws.Cells(2, 2), ws.Cells(num_rows + 1, 5))
    rate_range.NumberFormat = "0.0000"

    # Borders on entire data range
    for edge in range(7, 13):
        try:
            data_range.Borders(edge).LineStyle = 1
            data_range.Borders(edge).Weight = 2
        except Exception:
            pass

    # Holiday row fills (targeted range operations)
    for row_idx in holiday_rows:
        try:
            ws.Range(
                ws.Cells(row_idx, 1), ws.Cells(row_idx, NUM_COLS)
            ).Interior.Color = 0xCDF3FF  # Light yellow (BGR: FFF3CD)
        except Exception:
            pass

    # Weekend row fills
    for row_idx in weekend_rows:
        try:
            ws.Range(
                ws.Cells(row_idx, 1), ws.Cells(row_idx, NUM_COLS)
            ).Interior.Color = 0xE8E8E8  # Light gray
        except Exception:
            pass

    logger.info("ExRate master sheet built via vectorized COM: %d rows", num_rows)


# =========================================================================
#  BUILD EXRATE INDEX — Vectorized Bulk Read
# =========================================================================

def _build_exrate_index_com(wb) -> Dict[date, dict]:
    """
    Build the in-memory ExRate lookup index using VECTORIZED bulk read.

    MANDATE 2: Reads the entire ExRate data range in ONE Range.Value call,
    then parses the returned tuple in pure Python (zero COM calls per-row).
    """
    exrate_index: Dict[date, dict] = {}
    sheet_names = [wb.Sheets(i).Name for i in range(1, wb.Sheets.Count + 1)]
    if "ExRate" not in sheet_names:
        return exrate_index

    ws = wb.Sheets("ExRate")
    last_row = ws.Cells(ws.Rows.Count, 1).End(XL_UP).Row
    if last_row < 2:
        return exrate_index

    # ── SINGLE BULK READ ──────────────────────────────────────────
    raw_data = ws.Range(
        ws.Cells(2, 1), ws.Cells(last_row, 5)
    ).Value  # Returns tuple of tuples

    if raw_data is None:
        return exrate_index

    # Handle single-row case (COM returns flat tuple, not nested)
    if not isinstance(raw_data[0], tuple):
        raw_data = (raw_data,)

    for row in raw_data:
        row_date = _parse_cell_date(row[0])
        if row_date:
            exrate_index[row_date] = {
                "usd_buying": row[1],
                "usd_selling": row[2],
                "eur_buying": row[3],
                "eur_selling": row[4],
            }

    logger.info("ExRate index built from bulk read: %d entries", len(exrate_index))
    return exrate_index


# =========================================================================
#  CROSS-TAB VLOOKUP (pure Python — no COM calls)
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
#  MAIN COM PIPELINE — process_ledger_com (Vectorized + Poolable)
# =========================================================================

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
    excel=None,
) -> str:
    """
    Process a single ledger file using Native Microsoft Excel COM.

    MANDATE 3 — Instance Pooling:
      If `excel` is provided, reuses the existing Excel.Application instance
      instead of creating a new one. This allows batch processing to boot
      Excel ONCE and process all files inside that single instance.

    MANDATE 2 — Vectorized I/O:
      All reads/writes use bulk Range operations. Zero cell-by-cell loops.

    Args:
        filepath: Path to the ledger file (will be made absolute)
        excel: Optional shared Excel.Application instance (for pooling)
        ... (rate/holiday data)

    Returns:
        The absolute path to the saved file.
    """
    filepath = _ensure_absolute(filepath)
    logger.info("COM Engine: Processing %s", os.path.basename(filepath))

    owns_excel = excel is None  # Did WE create the instance?
    wb = None
    ctx = None

    try:
        if owns_excel:
            ctx = ExcelCOM()
            excel = ctx.__enter__()

        # Open the workbook
        wb = excel.Workbooks.Open(filepath)

        # ── STEP 1: Build ExRate master sheet (vectorized) ────────
        _build_exrate_sheet_com(
            wb,
            usd_buying, usd_selling,
            eur_buying, eur_selling,
            holidays_set, holidays_names,
            computed_start,
        )

        # ── STEP 2: Build in-memory ExRate lookup index (bulk read)
        exrate_index = _build_exrate_index_com(wb)

        # ── STEP 3: Vectorized Cross-Tab VLOOKUP ─────────────────
        for sheet_idx in range(1, wb.Sheets.Count + 1):
            ws = wb.Sheets(sheet_idx)
            sheet_name = ws.Name
            if sheet_name in SKIP_SHEET_NAMES:
                continue

            # ── Header scan: bulk read first 10 rows × 20 cols ───
            scan_range = ws.Range(ws.Cells(1, 1), ws.Cells(10, 20))
            header_data = scan_range.Value  # tuple of tuples

            if header_data is None:
                continue

            header_row_idx = None
            col_indices: Dict[str, int] = {}
            for r_idx, row_tuple in enumerate(header_data):
                row_vals = [
                    str(v).strip() if v is not None else ""
                    for v in row_tuple
                ]
                if target_cols["source_date"] in row_vals:
                    header_row_idx = r_idx + 1  # 1-indexed
                    for ci, val in enumerate(row_vals):
                        if val == target_cols["source_date"]:
                            col_indices["source"] = ci + 1
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

            src_col = col_indices["source"]
            cur_col = col_indices.get("currency")
            rate_col = col_indices.get("out_rate")
            last_data_row = ws.Cells(ws.Rows.Count, src_col).End(XL_UP).Row

            if last_data_row <= header_row_idx:
                continue

            data_start = header_row_idx + 1
            num_data_rows = last_data_row - header_row_idx

            # ── VECTORIZED BULK READ: date + currency columns ────
            # Determine max column needed for the bulk read
            max_col = src_col
            if cur_col is not None:
                max_col = max(max_col, cur_col)
            if rate_col is not None:
                max_col = max(max_col, rate_col)

            bulk_data = ws.Range(
                ws.Cells(data_start, 1),
                ws.Cells(last_data_row, max_col)
            ).Value

            if bulk_data is None:
                continue

            # Handle single-row case
            if not isinstance(bulk_data[0], tuple):
                bulk_data = (bulk_data,)

            # ── COMPUTE RATES IN PYTHON RAM ──────────────────────
            rate_output = []  # list of (row_offset, rate_value)
            for row_offset, row_tuple in enumerate(bulk_data):
                # Parse date (0-indexed into tuple, but col is 1-indexed)
                inv_date = _parse_cell_date(row_tuple[src_col - 1])
                if not inv_date:
                    rate_output.append(None)  # placeholder
                    continue

                # Parse currency
                ccy = ""
                if cur_col is not None:
                    raw = row_tuple[cur_col - 1]
                    ccy = str(raw).strip().upper() if raw else ""

                if ccy == "THB":
                    rate_output.append(1)
                    continue

                # Cross-tab lookup (pure Python, no COM)
                rate = _vlookup_exrate(inv_date, ccy, exrate_index)
                rate_output.append(rate)

            # ── VECTORIZED BULK WRITE: rate column ───────────────
            if rate_col is not None and rate_output:
                # Build 2D array for single-column write
                write_data = [[v] for v in rate_output]
                write_range = ws.Range(
                    ws.Cells(data_start, rate_col),
                    ws.Cells(last_data_row, rate_col)
                )
                write_range.Value = write_data
                logger.info(
                    "Sheet '%s': Vectorized VLOOKUP write (%d rows in 1 COM call)",
                    sheet_name, num_data_rows,
                )

        # ── Save natively through Excel ───────────────────────────
        is_xls = filepath.lower().endswith(".xls") and not filepath.lower().endswith(".xlsx")
        if is_xls:
            final_path = os.path.splitext(filepath)[0] + ".xlsx"
            wb.SaveAs(_ensure_absolute(final_path), FileFormat=XL_FILE_FORMAT_XLSX)
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
        # ── Guaranteed workbook cleanup ───────────────────────────
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
        # ── Only quit Excel if WE created it ─────────────────────
        if owns_excel and ctx is not None:
            ctx.__exit__(None, None, None)

    return filepath
