#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.0) - Cache-First + Singleton Architecture
---------------------------------------------------------------------------
V2.5.0 Changes:
  - Standard date resolution via resolve_rate_for_currency()
  - Source column: "Date"
  - THB rows → write 1 (no API lookup)
  - Unified "ExRate" master tab with "Merge, Don't Purge" resilience
  - Legacy "Exrate USD" / "Exrate EUR" tabs no longer updated
  - Smart year-end start date extraction (Dec 30 rollback)
"""

import os
import gc
import logging
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from datetime import date, datetime, timedelta
from typing import Dict, Tuple, Set, List, Callable, Optional
from decimal import Decimal

from core.api_client import BOTClient, BOTRateDetail
from core.logic import BOTLogicEngine, RateNotFoundError, safe_to_decimal
from core.backup_manager import BackupManager, BackupError
from core.database import CacheDB

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# EXCEPTIONS
# -------------------------------------------------------------------------

class FileSizeLimitError(Exception):
    pass

class MissingColumnError(Exception):
    pass

# -------------------------------------------------------------------------
# MODULE-LEVEL SINGLETONS (persist across batch clicks)
# -------------------------------------------------------------------------
_backup_singleton = None
_cache_singleton = None


def _get_backup() -> BackupManager:
    global _backup_singleton
    if _backup_singleton is None:
        _backup_singleton = BackupManager()
    return _backup_singleton


def _get_cache() -> CacheDB:
    global _cache_singleton
    if _cache_singleton is None:
        import atexit
        _cache_singleton = CacheDB()
        atexit.register(_cache_singleton.close)
    return _cache_singleton


# -------------------------------------------------------------------------
# CONSTANTS
# -------------------------------------------------------------------------
# Sheets that are reference/master and should NOT be processed as ledgers
SKIP_SHEET_NAMES = {"ExRate", "Exrate USD", "Exrate EUR"}


# -------------------------------------------------------------------------
# ENGINE
# -------------------------------------------------------------------------

class LedgerEngine:
    MAX_FILE_SIZE_MB = 15
    MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    def __init__(self, api_client: BOTClient):
        self.api = api_client
        self.backup = _get_backup()
        self.cache = _get_cache()
        self.target_cols = {
            "source_date": "Date",
            "out_date": "Date",
            "currency": "Cur",
            "out_rate": "EX Rate"
        }

    def _check_memory_guardrail(self, filepath: str):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Cannot find: {filepath}")
        file_size = os.path.getsize(filepath)
        if file_size > self.MAX_FILE_BYTES:
            raise FileSizeLimitError(f"File too large (> {self.MAX_FILE_SIZE_MB}MB).")

    def _parse_date(self, cell_value) -> date:
        if isinstance(cell_value, datetime): return cell_value.date()
        if isinstance(cell_value, date): return cell_value
        if isinstance(cell_value, str):
            val = cell_value.strip()
            if not val or val.lower() in ("nan", "null"):
                return None
            formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y%m%d"]
            for fmt in formats:
                try: return datetime.strptime(val, fmt).date()
                except ValueError: continue
        return None

    # ================================================================== #
    #  SMART DATE PRE-SCAN (V2.5 — reads from new column)
    # ================================================================== #
    @staticmethod
    def prescan_oldest_date(
        filepaths: List[str],
        target_col_name: str = "Date",
    ) -> Tuple[date, bool]:
        """
        Pre-scans queued .xlsx files in read-only mode to find the absolute
        oldest date in the source column. This eliminates manual date entry.

        V2.5: Reads from the "Date" column.

        Returns:
            Tuple of (oldest_date, was_detected).
        """
        oldest: Optional[date] = None
        date_formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%Y%m%d"]

        for fp in filepaths:
            if not os.path.exists(fp):
                continue
            wb = None
            try:
                wb = openpyxl.load_workbook(fp, read_only=True, data_only=True)
                for ws in wb.worksheets:
                    target_col_idx = None
                    header_row_idx = None
                    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
                        row_strs = [str(c).strip() if c is not None else "" for c in row]
                        if target_col_name in row_strs:
                            target_col_idx = row_strs.index(target_col_name) + 1
                            header_row_idx = row_idx
                            break

                    if target_col_idx is None or header_row_idx is None:
                        continue

                    for row in ws.iter_rows(
                        min_row=header_row_idx + 1,
                        min_col=target_col_idx, max_col=target_col_idx,
                        values_only=True
                    ):
                        cell_val = row[0]
                        parsed = None
                        if isinstance(cell_val, datetime):
                            parsed = cell_val.date()
                        elif isinstance(cell_val, date):
                            parsed = cell_val
                        elif isinstance(cell_val, str):
                            val = cell_val.strip()
                            if val and val.lower() not in ("nan", "null"):
                                for fmt in date_formats:
                                    try:
                                        parsed = datetime.strptime(val, fmt).date()
                                        break
                                    except ValueError:
                                        continue

                        if parsed is not None:
                            if oldest is None or parsed < oldest:
                                oldest = parsed
            except (ValueError, TypeError, KeyError, openpyxl.utils.exceptions.InvalidFileException):
                continue
            finally:
                if wb is not None:
                    wb.close()

        if oldest is not None:
            return oldest, True

        fallback = date.today() - timedelta(days=30)
        return fallback, False

    # ================================================================== #
    #  SMART YEAR-END START DATE (V2.5)
    # ================================================================== #
    @staticmethod
    def compute_year_start_date(
        target_year: int,
        holidays: List[date],
    ) -> date:
        """
        Computes the last valid trading day of the PREVIOUS calendar year.
        
        Rules:
          - Dec 31 is always an office day-off (company policy), skip it.
          - Start from Dec 30 of (target_year - 1).
          - Roll back if Dec 30 is a weekend or a BOT/company holiday.
          - Returns the first valid weekday non-holiday date found.
        
        Example for target_year=2026:
          - Start check from 2025-12-30
          - If Dec 30 is a holiday/weekend, check Dec 29, Dec 28, etc.
        """
        holidays_set = set(holidays)
        prev_year = target_year - 1
        
        # Dec 31 is always office day-off — start from Dec 30
        check_date = date(prev_year, 12, 30)
        
        # Roll back up to 10 days to handle edge cases
        for _ in range(10):
            if check_date.weekday() < 5 and check_date not in holidays_set:
                return check_date
            check_date -= timedelta(days=1)
        
        # Absolute fallback: Dec 20 of previous year
        return date(prev_year, 12, 20)

    # ================================================================== #
    #  CACHE-FIRST DATA LOADING (v2.5.1 — 4-Column Rates)
    # ================================================================== #
    async def _preload_api_data(
        self, dates: Set[date], start_date: str = "2025-01-01"
    ) -> Tuple:
        """
        Cache-First Architecture (v2.5.1):
        1. Check SQLite for holidays & rates (4 columns)
        2. Only call BOT API for MISSING data
        3. Store API results in SQLite immediately
        
        Returns:
            (logic_engine, usd_selling_rates, eur_selling_rates,
             usd_buying_rates, eur_buying_rates, usd_data, eur_data)
        """
        try:
            force_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            force_start = date(2025, 1, 1)

        today = date.today()
        all_d = set(dates) | {force_start, today}
        min_date, max_date = min(all_d), max(all_d)
        years = set(d.year for d in all_d)

        # ── HOLIDAYS: Cache-first ────────────────────────────────────────
        holidays_list = []
        years_to_fetch = []

        for year in years:
            if self.cache.has_holidays_for_year(year):
                cached_hols = self.cache.get_holidays(year)
                for h_date, h_name in cached_hols:
                    try:
                        holidays_list.append(datetime.strptime(h_date, "%Y-%m-%d").date())
                    except (ValueError, TypeError):
                        pass
            else:
                years_to_fetch.append(year)

        for year in years_to_fetch:
            hol_data = await self.api.get_holidays(year)
            hol_entries = []
            for h in hol_data:
                try:
                    hol_date = datetime.strptime(h.date, "%Y-%m-%d").date()
                    holidays_list.append(hol_date)
                    hol_entries.append((h.date, h.description))
                except (ValueError, TypeError):
                    pass
            self.cache.insert_holidays(hol_entries)

        logic_engine = BOTLogicEngine(holidays=holidays_list, max_rollback_days=10)

        # ── RATES: Cache-first (4 columns) ───────────────────────────────
        cached_rates = self.cache.get_rates_bulk(min_date, max_date)

        usd_buying_rates: Dict[date, Decimal] = {}
        usd_selling_rates: Dict[date, Decimal] = {}
        eur_buying_rates: Dict[date, Decimal] = {}
        eur_selling_rates: Dict[date, Decimal] = {}

        for d, rate_dict in cached_rates.items():
            if rate_dict["usd_buying"] is not None:
                usd_buying_rates[d] = rate_dict["usd_buying"]
            if rate_dict["usd_selling"] is not None:
                usd_selling_rates[d] = rate_dict["usd_selling"]
            if rate_dict["eur_buying"] is not None:
                eur_buying_rates[d] = rate_dict["eur_buying"]
            if rate_dict["eur_selling"] is not None:
                eur_selling_rates[d] = rate_dict["eur_selling"]

        all_needed_dates = set()
        check_date = min_date
        while check_date <= max_date:
            if check_date.weekday() < 5:
                all_needed_dates.add(check_date)
            check_date += timedelta(days=1)

        cached_dates = set(cached_rates.keys())
        missing_dates = all_needed_dates - cached_dates

        usd_data = []
        eur_data = []

        if missing_dates:
            usd_data = await self.api.get_exchange_rates(min_date, max_date, "USD")
            eur_data = await self.api.get_exchange_rates(min_date, max_date, "EUR")

            rate_cache_entries = {}

            for r in usd_data:
                d = datetime.strptime(r.period, "%Y-%m-%d").date()
                if r.buying_transfer is not None:
                    usd_buying_rates[d] = safe_to_decimal(r.buying_transfer)
                if r.selling is not None:
                    usd_selling_rates[d] = safe_to_decimal(r.selling)
                rate_cache_entries.setdefault(r.period, [None, None, None, None])
                rate_cache_entries[r.period][0] = r.buying_transfer
                rate_cache_entries[r.period][1] = r.selling

            for r in eur_data:
                d = datetime.strptime(r.period, "%Y-%m-%d").date()
                if r.buying_transfer is not None:
                    eur_buying_rates[d] = safe_to_decimal(r.buying_transfer)
                if r.selling is not None:
                    eur_selling_rates[d] = safe_to_decimal(r.selling)
                rate_cache_entries.setdefault(r.period, [None, None, None, None])
                rate_cache_entries[r.period][2] = r.buying_transfer
                rate_cache_entries[r.period][3] = r.selling

            bulk = [
                (d_str, vals[0], vals[1], vals[2], vals[3])
                for d_str, vals in rate_cache_entries.items()
            ]
            self.cache.insert_rates_bulk(bulk)

        return (logic_engine, usd_selling_rates, eur_selling_rates,
                usd_buying_rates, eur_buying_rates, usd_data, eur_data)

    # ================================================================== #
    #  UNIFIED "ExRate" MASTER SHEET — Merge & Backfill (V2.5.1)
    # ================================================================== #
    def _update_master_exrate_sheet(
        self, wb: openpyxl.Workbook,
        usd_buying_rates: Dict[date, Decimal],
        usd_selling_rates: Dict[date, Decimal],
        eur_buying_rates: Dict[date, Decimal],
        eur_selling_rates: Dict[date, Decimal],
        holidays_list: List[date],
        holidays_names: Dict[date, str],
        start_date: date,
    ):
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
        header_fill = PatternFill(start_color="1A365D", end_color="1A365D", fill_type="solid")
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
        existing_data: Dict[date, dict] = {}
        if ws.max_row and ws.max_row >= DATA_START_ROW:
            for row_idx in range(DATA_START_ROW, ws.max_row + 1):
                cell_val = ws.cell(row=row_idx, column=1).value
                row_date = None
                if isinstance(cell_val, datetime):
                    row_date = cell_val.date()
                elif isinstance(cell_val, date):
                    row_date = cell_val
                elif isinstance(cell_val, str):
                    try:
                        row_date = datetime.strptime(cell_val.strip(), "%Y-%m-%d").date()
                    except ValueError:
                        try:
                            row_date = datetime.strptime(cell_val.strip(), "%d %b %Y").date()
                        except ValueError:
                            continue
                
                if row_date:
                    existing_data[row_date] = {
                        "usd_buy": ws.cell(row=row_idx, column=2).value,
                        "usd_sell": ws.cell(row=row_idx, column=3).value,
                        "eur_buy": ws.cell(row=row_idx, column=4).value,
                        "eur_sell": ws.cell(row=row_idx, column=5).value,
                        "holidays_weekend": ws.cell(row=row_idx, column=6).value,
                    }
        
        # ── Build ALL calendar dates ─────────────────────────────────────
        holidays_set = set(holidays_list)
        today = date.today()
        end_date = today
        
        all_dates = set()
        current = start_date
        while current <= end_date:
            all_dates.add(current)
            current += timedelta(days=1)
        
        all_dates |= set(existing_data.keys())
        all_dates = {d for d in all_dates if d >= start_date}
        
        # ── Build the merged dataset ────────────────────────────────────
        merged: Dict[date, dict] = {}
        for d in sorted(all_dates):
            existing = existing_data.get(d, {})
            is_weekend = d.weekday() >= 5
            is_holiday = d in holidays_set
            
            # API data takes priority; MISSING API data preserves local
            ub = float(usd_buying_rates[d]) if d in usd_buying_rates and usd_buying_rates[d] is not None else None
            us = float(usd_selling_rates[d]) if d in usd_selling_rates and usd_selling_rates[d] is not None else None
            eb = float(eur_buying_rates[d]) if d in eur_buying_rates and eur_buying_rates[d] is not None else None
            es = float(eur_selling_rates[d]) if d in eur_selling_rates and eur_selling_rates[d] is not None else None
            
            merged_ub = ub if ub is not None else existing.get("usd_buy")
            merged_us = us if us is not None else existing.get("usd_sell")
            merged_eb = eb if eb is not None else existing.get("eur_buy")
            merged_es = es if es is not None else existing.get("eur_sell")
            
            # Build consolidated "Holidays/Weekend" label with semicolon
            holiday_label = ""
            if is_weekend and is_holiday:
                hol_name = holidays_names.get(d, "Holiday")
                holiday_label = f"Weekend; {hol_name}"
            elif is_weekend:
                holiday_label = "Weekend"
            elif is_holiday:
                holiday_label = holidays_names.get(d, "Holiday")
            
            merged[d] = {
                "usd_buy": merged_ub,
                "usd_sell": merged_us,
                "eur_buy": merged_eb,
                "eur_sell": merged_es,
                "holidays_weekend": holiday_label,
            }
        
        # ── Write data ───────────────────────────────────────────────────
        if ws.max_row and ws.max_row >= DATA_START_ROW:
            ws.delete_rows(DATA_START_ROW, ws.max_row - DATA_START_ROW + 1)
        
        data_font = Font(name="Calibri", size=10)
        date_align = Alignment(horizontal="center")
        num_align = Alignment(horizontal="right")
        holiday_fill = PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid")
        weekend_fill = PatternFill(start_color="E8E8E8", end_color="E8E8E8", fill_type="solid")
        
        current_row = DATA_START_ROW
        for d in sorted(merged.keys()):
            entry = merged[d]
            is_weekend = d.weekday() >= 5
            is_holiday = d in holidays_set
            
            # Date
            cell_date = ws.cell(row=current_row, column=1, value=d)
            cell_date.number_format = "DD MMM YYYY"
            cell_date.font = data_font
            cell_date.alignment = date_align
            cell_date.border = thin_border
            
            # USD Buying TT Rate
            cell_ub = ws.cell(row=current_row, column=2, value=entry["usd_buy"])
            if entry["usd_buy"] is not None:
                cell_ub.number_format = "0.0000"
            cell_ub.font = data_font
            cell_ub.alignment = num_align
            cell_ub.border = thin_border
            
            # USD Selling Rate
            cell_us = ws.cell(row=current_row, column=3, value=entry["usd_sell"])
            if entry["usd_sell"] is not None:
                cell_us.number_format = "0.0000"
            cell_us.font = data_font
            cell_us.alignment = num_align
            cell_us.border = thin_border
            
            # EUR Buying TT Rate
            cell_eb = ws.cell(row=current_row, column=4, value=entry["eur_buy"])
            if entry["eur_buy"] is not None:
                cell_eb.number_format = "0.0000"
            cell_eb.font = data_font
            cell_eb.alignment = num_align
            cell_eb.border = thin_border
            
            # EUR Selling Rate
            cell_es = ws.cell(row=current_row, column=5, value=entry["eur_sell"])
            if entry["eur_sell"] is not None:
                cell_es.number_format = "0.0000"
            cell_es.font = data_font
            cell_es.alignment = num_align
            cell_es.border = thin_border
            
            # Holidays/Weekend
            cell_hw = ws.cell(row=current_row, column=6, value=entry["holidays_weekend"])
            cell_hw.font = data_font
            cell_hw.border = thin_border
            
            # Row highlighting
            if is_holiday:
                for col in range(1, 7):
                    ws.cell(row=current_row, column=col).fill = holiday_fill
            elif is_weekend:
                for col in range(1, 7):
                    ws.cell(row=current_row, column=col).fill = weekend_fill
            
            current_row += 1

    # ================================================================== #
    #  PROCESS SINGLE LEDGER (V2.5)
    # ================================================================== #
    async def process_ledger(self, filepath: str, start_date: str = None) -> str:
        """
        Process a single ledger with V2.5 standard date resolution:
        1. Memory guardrail → 2. Backup → 3. Load → 4. Cache-first API
        5. Resolve per row, currency-aware → 6. Update ExRate master
        7. Save in-place → 8. Close + gc.collect()
        """
        self._check_memory_guardrail(filepath)
        self.backup.create_backup(filepath)

        wb = openpyxl.load_workbook(filepath)
        all_target_dates = set()
        sheet_maps = {}

        for sheet_name in wb.sheetnames:
            # Skip reference/master sheets
            if sheet_name in SKIP_SHEET_NAMES:
                continue

            ws = wb[sheet_name]
            header_row_idx = None
            col_indices = {}
            for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
                row_strs = [str(c).strip() if c is not None else "" for c in row]
                if self.target_cols["source_date"] in row_strs:
                    header_row_idx = row_idx
                    for col_idx, val in enumerate(row_strs):
                        if val == self.target_cols["source_date"]: col_indices["source"] = col_idx
                        elif val == self.target_cols["currency"]: col_indices["currency"] = col_idx
                        elif val == self.target_cols["out_rate"]: col_indices["out_rate"] = col_idx
                    break

            # MISSING COLUMN FAILSAFE: If the new date column is missing,
            # silently skip this sheet (do not crash, do not overwrite).
            if header_row_idx is None or "source" not in col_indices:
                logger.info(f"Sheet '{sheet_name}' missing source date column — skipped.")
                continue

            sheet_maps[sheet_name] = {"header_row": header_row_idx, "columns": col_indices}
            src_idx = col_indices["source"] + 1
            for row_idx in range(header_row_idx + 1, ws.max_row + 1):
                parsed_date = self._parse_date(ws.cell(row=row_idx, column=src_idx).value)
                if parsed_date: all_target_dates.add(parsed_date)

        # Even if no monthly sheets matched, we still fetch API data
        # for the master ExRate sheet update.
        
        # ── STRICT START DATE HIERARCHY ──────────────────────────────────
        # Monthly tabs ONLY provide the target year.
        # ExRate start date ALWAYS comes from compute_year_start_date().
        if all_target_dates:
            target_year = min(all_target_dates).year
        else:
            target_year = date.today().year
        
        # Preliminary fetch: we need holidays to compute year-end start
        preliminary_start = f"{target_year - 1}-12-20"
        if start_date is None:
            start_date = preliminary_start
        
        (logic_engine, usd_selling_rates, eur_selling_rates,
         usd_buying_rates, eur_buying_rates,
         usd_data, eur_data) = await self._preload_api_data(
            all_target_dates, start_date=start_date
        )
        
        # ── Compute STRICT start date from year-end logic ────────────────
        computed_start = self.compute_year_start_date(
            target_year, logic_engine.holidays
        )

        # ── STEP 1: Build ExRate master sheet FIRST ──────────────────────
        # The ExRate tab is the master database. It must be fully populated
        # before any monthly tab can read from it.
        holidays_names: Dict[date, str] = {}
        for year in set(d.year for d in (all_target_dates | {computed_start, date.today()})):
            cached_hols = self.cache.get_holidays(year)
            for h_date_str, h_name in cached_hols:
                try:
                    h_date_obj = datetime.strptime(h_date_str, "%Y-%m-%d").date()
                    holidays_names[h_date_obj] = h_name
                except (ValueError, TypeError):
                    pass

        self._update_master_exrate_sheet(
            wb,
            usd_buying_rates, usd_selling_rates,
            eur_buying_rates, eur_selling_rates,
            list(logic_engine.holidays),
            holidays_names,
            computed_start
        )

        # ── STEP 2: Build in-memory ExRate lookup index ──────────────────
        # This index is the VLOOKUP source for all monthly tabs.
        # Monthly tabs must NOT fetch from the API; they query this index.
        # Maps: date → {"usd_buying": float, "eur_buying": float, ...}
        exrate_index: Dict[date, dict] = {}
        if "ExRate" in wb.sheetnames:
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
                        "usd_buying": ws_exrate.cell(row=row_idx, column=2).value,
                        "usd_selling": ws_exrate.cell(row=row_idx, column=3).value,
                        "eur_buying": ws_exrate.cell(row=row_idx, column=4).value,
                        "eur_selling": ws_exrate.cell(row=row_idx, column=5).value,
                    }

        # ── STEP 3: Process monthly tabs via cross-tab VLOOKUP ───────────
        # Each row reads its date, resolves to the nearest trading day,
        # then looks up the Buying TT Rate from the ExRate index.
        # All values in the "EX Rate" column are styled with red font.
        exrate_font = Font(name="Calibri", size=10, color="C0392B")

        for sheet_name, mapping in sheet_maps.items():
            ws = wb[sheet_name]
            cols = mapping["columns"]
            src_idx = cols["source"] + 1
            cur_idx = cols.get("currency")
            out_rate_idx = cols.get("out_rate")

            for row_idx in range(mapping["header_row"] + 1, ws.max_row + 1):
                inv_date = self._parse_date(ws.cell(row=row_idx, column=src_idx).value)
                if inv_date:
                    # Read currency from the "Cur" column
                    ccy = ""
                    if cur_idx is not None:
                        raw_ccy = ws.cell(row=row_idx, column=cur_idx + 1).value
                        ccy = str(raw_ccy).strip().upper() if raw_ccy else ""

                    # THB bypass: always write 1
                    if ccy == "THB":
                        if out_rate_idx is not None:
                            cell = ws.cell(row=row_idx, column=out_rate_idx + 1)
                            cell.value = 1
                            cell.font = exrate_font
                        continue

                    # Resolve to nearest trading day (weekend/holiday rollback)
                    try:
                        trade_date, _ = logic_engine.resolve_rate(
                            inv_date, usd_selling_rates, eur_selling_rates
                        )
                    except RateNotFoundError:
                        if out_rate_idx is not None:
                            cell = ws.cell(row=row_idx, column=out_rate_idx + 1)
                            cell.value = None
                            cell.font = exrate_font
                        continue

                    # VLOOKUP: query ExRate index by resolved trade_date
                    exrate_row = exrate_index.get(trade_date, {})

                    # Currency matching → Buying TT Rate
                    rate = None
                    if ccy == "USD":
                        rate = exrate_row.get("usd_buying")
                    elif ccy == "EUR":
                        rate = exrate_row.get("eur_buying")

                    # Write the looked-up rate to the output column
                    if out_rate_idx is not None:
                        cell = ws.cell(row=row_idx, column=out_rate_idx + 1)
                        cell.value = float(rate) if rate is not None else None
                        cell.font = exrate_font

        wb.save(filepath)
        wb.close()
        gc.collect()

        return filepath

    # ================================================================== #
    #  BATCH PROCESSING
    # ================================================================== #
    async def process_batch(
        self,
        filepaths: List[str],
        start_date: str = None,
        progress_cb: Optional[Callable[[int, int, str, Optional[str]], None]] = None
    ) -> Tuple[int, int, List[str]]:
        """Batch processing with pre-edit backup, cache, and auto-cleanup."""
        self.backup.cleanup_old_backups(max_age_days=7)

        total = len(filepaths)
        success = 0
        errors = []

        for idx, fp in enumerate(filepaths):
            fname = os.path.basename(fp)
            try:
                await self.process_ledger(fp, start_date=start_date)
                success += 1
                if progress_cb:
                    progress_cb(idx + 1, total, fname, None)
            except BackupError as e:
                err_msg = f"{fname}: BACKUP FAILED — skipped ({e})"
                errors.append(err_msg)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))
            except Exception as e:
                err_msg = f"{fname}: {str(e)}"
                errors.append(err_msg)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))

        return success, total - success, errors
