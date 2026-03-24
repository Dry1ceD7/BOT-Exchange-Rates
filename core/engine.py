#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v3.0.8) - Cache-First Orchestrator
---------------------------------------------------------------------------
Slim orchestrator. Heavy logic extracted to:
  - core/exrate_sheet.py → Master ExRate sheet builder
  - core/xls_converter.py → Legacy .xls conversion
  - core/prescan.py → Smart date pre-scanner
"""

import asyncio
import gc
import logging
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set, Tuple

from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter

from core.api_client import BOTClient
from core.backup_manager import BackupError, BackupManager
from core.database import CacheDB
from core.exrate_sheet import update_master_exrate_sheet
from core.logic import BOTLogicEngine, safe_to_decimal
from core.prescan import prescan_oldest_date
from core.xls_converter import convert_xls_to_xlsx

# ── Detect if pywin32 is available (COM engine optional) ─────────────────
HAS_PYWIN32 = False
if sys.platform == "win32":
    try:
        import pywintypes  # noqa: F401
        import win32com  # noqa: F401
        HAS_PYWIN32 = True
    except ImportError:
        pass

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


# Sheets that are reference/master and should NOT be processed as ledgers.
# "Exrate USD" / "Exrate EUR" are pre-existing rate tabs in older workbooks;
# they lack the standard Date/Cur/EX Rate header and must be skipped.
SKIP_SHEET_NAMES = {"ExRate", "Exrate USD", "Exrate EUR"}


# -------------------------------------------------------------------------
# ENGINE
# -------------------------------------------------------------------------


class LedgerEngine:
    MAX_FILE_SIZE_MB = 15
    MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    def __init__(self, api_client: BOTClient, event_bus=None):
        self.api = api_client
        self.backup = _get_backup()
        self.cache = _get_cache()
        self._bus = event_bus
        self.target_cols = {
            "source_date": "Date",
            "currency": "Cur",
            "out_rate": "EX Rate",
        }

    def _emit(self, msg: str, etype: str = "log") -> None:
        """Push event to EventBus if one is attached."""
        if self._bus is not None:
            self._bus.push({"type": etype, "msg": msg})

    def _check_memory_guardrail(self, filepath: str):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Cannot find: {filepath}")
        file_size = os.path.getsize(filepath)
        if file_size > self.MAX_FILE_BYTES:
            raise FileSizeLimitError(
                f"File too large (> {self.MAX_FILE_SIZE_MB}MB)."
            )

    def _parse_date(self, cell_value) -> Optional[date]:
        if isinstance(cell_value, datetime):
            return cell_value.date()
        if isinstance(cell_value, date):
            return cell_value
        if isinstance(cell_value, str):
            val = cell_value.strip()
            if not val or val.lower() in ("nan", "null"):
                return None
            formats = [
                "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
                "%d %b %Y", "%d %B %Y", "%Y%m%d",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(val, fmt).date()
                except ValueError:
                    continue
        return None

    # ── Static delegates (kept for backward compat) ──────────────────
    @staticmethod
    def prescan_oldest_date(
        filepaths: List[str],
        target_col_name: str = "Date",
    ) -> Tuple[date, bool]:
        """Delegate to core.prescan module."""
        return prescan_oldest_date(filepaths, target_col_name)

    @staticmethod
    def compute_year_start_date(
        target_year: int,
        holidays: List[date],
    ) -> date:
        """
        Computes the last valid trading day of the PREVIOUS calendar year.
        Dec 31 is always office day-off. Start from Dec 30 and roll back.
        """
        holidays_set = set(holidays)
        prev_year = target_year - 1
        check_date = date(prev_year, 12, 30)
        for _ in range(10):
            if check_date.weekday() < 5 and check_date not in holidays_set:
                return check_date
            check_date -= timedelta(days=1)
        return date(prev_year, 12, 20)

    # ── Cross-tab VLOOKUP with rollback ───────────────────────────
    @staticmethod
    def _vlookup_exrate(
        target_date: date,
        currency: str,
        exrate_index: Dict[date, dict],
        max_rollback: int = 10,
    ) -> Optional[float]:
        """
        Cross-tab VLOOKUP: look up Buying TT Rate in the ExRate index.

        Walks backwards from target_date until a valid trading day
        with non-None rate data is found (skips weekends/holidays).

        Args:
            target_date: The date from the monthly tab's "Date" column.
            currency: The value from the "Cur" column ("USD" or "EUR").
            exrate_index: The in-memory index built from the ExRate tab.
            max_rollback: Safety guardrail for max backwards steps.

        Returns:
            The Buying TT rate as a float, or None if not resolvable.
        """
        rate_key = {
            "USD": "usd_buying",
            "EUR": "eur_buying",
        }.get(currency)
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

    # ── Zero-Touch Write (Global Formatting Protocol) ─────────────
    @staticmethod
    def _zero_touch_write(ws, row: int, col: int, value) -> None:
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
        # Value-only write. Formatting is NEVER touched.
        cell.value = value

    # ================================================================== #
    #  CACHE-FIRST DATA LOADING (v2.6.1)
    # ================================================================== #
    async def _preload_api_data(
        self, dates: Set[date], start_date: str
    ) -> Tuple:
        """
        Cache-First Architecture: SQLite → API fallback → cache store.
        Returns (logic_engine, usd_selling, eur_selling,
                 usd_buying, eur_buying, usd_data, eur_data).
        """
        try:
            force_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            force_start = date(2025, 1, 1)

        today = date.today()
        all_d = set(dates) | {force_start, today}
        min_date, max_date = min(all_d), max(all_d)
        years = {d.year for d in all_d}

        # ── HOLIDAYS: Cache-first ────────────────────────────────────
        holidays_list = []
        years_to_fetch = []
        for year in years:
            if self.cache.has_holidays_for_year(year):
                cached_hols = self.cache.get_holidays(year)
                for h_date, h_name in cached_hols:
                    try:
                        holidays_list.append(
                            datetime.strptime(h_date, "%Y-%m-%d").date()
                        )
                    except (ValueError, TypeError):
                        logger.debug("Skipped unparseable cached holiday: %s", h_date)
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
                    logger.debug("Skipped unparseable API holiday: %s", h.date)
            self.cache.insert_holidays(hol_entries)

        logic_engine = BOTLogicEngine(
            holidays=holidays_list, max_rollback_days=10
        )

        # ── RATES: Cache-first (4 columns) ───────────────────────────
        cached_rates = self.cache.get_rates_bulk(min_date, max_date)
        usd_buying: Dict[date, Decimal] = {}
        usd_selling: Dict[date, Decimal] = {}
        eur_buying: Dict[date, Decimal] = {}
        eur_selling: Dict[date, Decimal] = {}

        for d, rate_dict in cached_rates.items():
            if rate_dict["usd_buying"] is not None:
                usd_buying[d] = rate_dict["usd_buying"]
            if rate_dict["usd_selling"] is not None:
                usd_selling[d] = rate_dict["usd_selling"]
            if rate_dict["eur_buying"] is not None:
                eur_buying[d] = rate_dict["eur_buying"]
            if rate_dict["eur_selling"] is not None:
                eur_selling[d] = rate_dict["eur_selling"]

        all_needed = set()
        check = min_date
        while check <= max_date:
            if check.weekday() < 5:
                all_needed.add(check)
            check += timedelta(days=1)

        missing_dates = all_needed - set(cached_rates.keys())
        usd_data, eur_data = [], []
        if missing_dates:
            # ── Narrowed fetch range: only fetch the missing window ───
            fetch_start = min(missing_dates)
            fetch_end = max(missing_dates)
            self._emit(
                "Cache miss: %d dates (%s to %s). Calling API" % (
                    len(missing_dates),
                    fetch_start.strftime("%Y-%m-%d"),
                    fetch_end.strftime("%Y-%m-%d"),
                ),
            )
            logger.info(
                "Cache miss: %d dates missing (%s → %s). Fetching from API...",
                len(missing_dates),
                fetch_start.strftime("%Y-%m-%d"),
                fetch_end.strftime("%Y-%m-%d"),
            )

            # ── Concurrent USD + EUR fetch (different params, safe) ────
            # Each request has its own 429 handler + tenacity retries.
            usd_data, eur_data = await asyncio.gather(
                self.api.get_exchange_rates(fetch_start, fetch_end, "USD"),
                self.api.get_exchange_rates(fetch_start, fetch_end, "EUR"),
            )

            rate_cache = {}
            for r in usd_data:
                d = datetime.strptime(r.period, "%Y-%m-%d").date()
                if r.buying_transfer is not None:
                    usd_buying[d] = safe_to_decimal(r.buying_transfer)
                if r.selling is not None:
                    usd_selling[d] = safe_to_decimal(r.selling)
                rate_cache.setdefault(r.period, [None] * 4)
                rate_cache[r.period][0] = r.buying_transfer
                rate_cache[r.period][1] = r.selling
            for r in eur_data:
                d = datetime.strptime(r.period, "%Y-%m-%d").date()
                if r.buying_transfer is not None:
                    eur_buying[d] = safe_to_decimal(r.buying_transfer)
                if r.selling is not None:
                    eur_selling[d] = safe_to_decimal(r.selling)
                rate_cache.setdefault(r.period, [None] * 4)
                rate_cache[r.period][2] = r.buying_transfer
                rate_cache[r.period][3] = r.selling
            bulk = [
                (d_str, v[0], v[1], v[2], v[3])
                for d_str, v in rate_cache.items()
            ]
            self.cache.insert_rates_bulk(bulk)
            self._emit(
                "API fetch done: %d USD + %d EUR records cached" % (
                    len(usd_data), len(eur_data),
                ),
                etype="success",
            )
            logger.info(
                "API fetch complete: %d USD + %d EUR records cached.",
                len(usd_data), len(eur_data),
            )
        else:
            self._emit("All rates served from cache (0 API calls)", etype="success")
            logger.info("All rates served from cache (0 API calls).")

        return (
            logic_engine, usd_selling, eur_selling,
            usd_buying, eur_buying, usd_data, eur_data,
        )

    # ================================================================== #
    #  PROCESS SINGLE LEDGER
    # ================================================================== #
    async def process_ledger(
        self, filepath: str, start_date: Optional[str] = None,
        excel=None,
    ) -> str:
        """Process a single ledger file end-to-end.

        Args:
            excel: Optional shared Excel COM instance (for batch pooling).
        """

        # ── MANDATE 2: Absolute pathing for COM safety ───────────────
        filepath = os.path.abspath(filepath)

        self._check_memory_guardrail(filepath)
        self._emit("Size check passed")
        self.backup.create_backup(filepath)
        self._emit("Backup created")

        # ── .xls Auto-Conversion ─────────────────────────────────────
        original_path = filepath
        converted = False
        is_legacy_xls = filepath.lower().endswith(".xls") and not filepath.lower().endswith(".xlsx")

        if is_legacy_xls:
            converted = True
            self._emit("Converting legacy .xls to .xlsx")
            if not HAS_PYWIN32:
                # Without COM engine, convert .xls to .xlsx for openpyxl
                filepath = convert_xls_to_xlsx(filepath)

        # ── Pre-scan dates for API data loading ──────────────────────
        # We need to scan dates BEFORE choosing the engine path so that
        # the COM engine has all the rate/holiday data it needs.
        self._emit("Scanning dates from workbook")
        all_target_dates: Set[date] = set()

        if is_legacy_xls and HAS_PYWIN32:
            # On Windows, we didn't pre-convert, so openpyxl cannot read this.
            # Use xlrd purely for the read-only pre-scan to extract dates.
            wb_scan = None
            devnull_fh = None
            try:
                import xlrd
                devnull_fh = open(os.devnull, 'w')
                wb_scan = xlrd.open_workbook(filepath, logfile=devnull_fh)
                for sheet in wb_scan.sheets():
                    if sheet.name in SKIP_SHEET_NAMES:
                        continue

                    # Scan headers (first 10 rows)
                    header_row_idx = None
                    src_col_idx = None
                    for row_idx in range(min(10, sheet.nrows)):
                        row_vals = [str(c.value).strip() if c.value is not None else "" for c in sheet.row(row_idx)]
                        if self.target_cols["source_date"] in row_vals:
                            header_row_idx = row_idx
                            src_col_idx = row_vals.index(self.target_cols["source_date"])
                            break

                    if header_row_idx is not None and src_col_idx is not None:
                        for row_idx in range(header_row_idx + 1, sheet.nrows):
                            cell = sheet.cell(row_idx, src_col_idx)
                            if cell.ctype == xlrd.XL_CELL_DATE:
                                try:
                                    dt_tuple = xlrd.xldate_as_tuple(cell.value, wb_scan.datemode)
                                    parsed_date = date(dt_tuple[0], dt_tuple[1], dt_tuple[2])
                                    all_target_dates.add(parsed_date)
                                except Exception:
                                    pass
                            else:
                                parsed_date = self._parse_date(cell.value)
                                if parsed_date:
                                    all_target_dates.add(parsed_date)
            except Exception as e:
                logger.warning("xlrd pre-scan failed for %s: %s", os.path.basename(filepath), e)
            finally:
                if wb_scan is not None:
                    wb_scan.release_resources()
                    del wb_scan
                    wb_scan = None
                if devnull_fh is not None:
                    devnull_fh.close()
                # Release file handles before COM opens the file
                gc.collect()
                time.sleep(0.3)

        else:
            # Standard openpyxl pre-scan for .xlsx (and Mac/Linux converted files)
            wb_scan = None
            try:
                import openpyxl
                wb_scan = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
                for sheet_name in wb_scan.sheetnames:
                    if sheet_name in SKIP_SHEET_NAMES:
                        continue
                    ws = wb_scan[sheet_name]
                    header_row_idx = None
                    col_indices: Dict[str, int] = {}
                    for row_idx, row in enumerate(
                        ws.iter_rows(min_row=1, max_row=10, values_only=True), 1
                    ):
                        row_strs = [
                            str(c).strip() if c is not None else "" for c in row
                        ]
                        if self.target_cols["source_date"] in row_strs:
                            header_row_idx = row_idx
                            for ci, val in enumerate(row_strs):
                                if val == self.target_cols["source_date"]:
                                    col_indices["source"] = ci
                            break

                    if header_row_idx is None or "source" not in col_indices:
                        continue

                    src_idx = col_indices["source"] + 1
                    for row_idx in range(header_row_idx + 1, (ws.max_row or 0) + 1):
                        parsed_date = self._parse_date(
                            ws.cell(row=row_idx, column=src_idx).value
                        )
                        if parsed_date:
                            all_target_dates.add(parsed_date)
            except Exception:
                if converted and os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except OSError as cleanup_err:
                        logger.debug("Cleanup failed for temp file %s: %s", filepath, cleanup_err)
                raise
            finally:
                if wb_scan is not None:
                    wb_scan.close()
                    del wb_scan
                    wb_scan = None
                # Force Python to release file handles before COM opens it
                gc.collect()
                time.sleep(0.3)

        # ── Date hierarchy ───────────────────────────────────────────
        target_year = (
            min(all_target_dates).year if all_target_dates
            else date.today().year
        )
        start_date = f"{target_year - 1}-12-20"

        self._emit("Loading exchange rates and holidays")
        (
            logic_engine, usd_selling, eur_selling,
            usd_buying, eur_buying, usd_data, eur_data,
        ) = await self._preload_api_data(all_target_dates, start_date)

        computed_start = self.compute_year_start_date(
            target_year, logic_engine.holidays
        )

        # ── Build holiday names lookup ───────────────────────────────
        sub_pattern = re.compile(r'^Substitution for (.*)\((.*?)\)$')

        holidays_names: Dict[date, str] = {}
        master_holidays_set = set(logic_engine.holidays)

        for year in {
            d.year
            for d in (all_target_dates | {computed_start, date.today()})
        }:
            cached_hols = self.cache.get_holidays(year)
            for h_str, h_name in cached_hols:
                try:
                    h_obj = datetime.strptime(h_str, "%Y-%m-%d").date()
                    holidays_names[h_obj] = h_name

                    m = sub_pattern.search(h_name)
                    if m:
                        real_name = m.group(1).strip()
                        date_str = m.group(2).strip()
                        date_str_clean = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_str)
                        date_str_clean = re.sub(r'^[A-Za-z]+\s+', '', date_str_clean)
                        try:
                            real_dt = datetime.strptime(date_str_clean, '%d %B %Y').date()
                            holidays_names[real_dt] = real_name
                            master_holidays_set.add(real_dt)
                        except (ValueError, TypeError):
                            pass
                except (ValueError, TypeError):
                    logger.debug("Skipped unparseable holiday name: %s", h_str)

            # ── STATIC HOLIDAY OVERLAY (GAP FILLER) ──────────────────
            static_holidays = {
                (1, 1): "New Year's Day",
                (4, 6): "Chakri Memorial Day",
                (4, 13): "Songkran Festival",
                (4, 14): "Songkran Festival",
                (4, 15): "Songkran Festival",
                (5, 1): "National Labour Day",
                (6, 3): "H.M. Queen Suthida's Birthday",
                (7, 28): "H.M. King Maha Vajiralongkorn's Birthday",
                (8, 12): "H.M. Queen Sirikit The Queen Mother's Birthday / Mother's Day",
                (10, 13): "H.M. King Bhumibol Adulyadej The Great Memorial Day",
                (10, 23): "Chulalongkorn Day",
                (12, 5): "H.M. King Bhumibol Adulyadej The Great's Birthday / Father's Day",
                (12, 10): "Constitution Day",
                (12, 31): "New Year's Eve",
            }
            for (month, day), name in static_holidays.items():
                try:
                    fixed_dt = date(year, month, day)
                    if fixed_dt not in holidays_names:
                        holidays_names[fixed_dt] = name
                        master_holidays_set.add(fixed_dt)
                except ValueError:
                    pass

        # ══════════════════════════════════════════════════════════════
        #  OS-AWARE DISPATCHER: COM (Windows+pywin32) vs openpyxl
        # ══════════════════════════════════════════════════════════════
        if HAS_PYWIN32:
            # ── PRIMARY PATH: Native Microsoft Excel COM Engine ───────
            try:
                from core.com_engine import process_ledger_com
                self._emit("Dispatching to Native COM Engine (Windows)")
                logger.info("Dispatching to Native COM Engine (Windows).")

                result = process_ledger_com(
                    filepath=filepath,
                    usd_buying=usd_buying,
                    usd_selling=usd_selling,
                    eur_buying=eur_buying,
                    eur_selling=eur_selling,
                    holidays_set=master_holidays_set,
                    holidays_names=holidays_names,
                    computed_start=computed_start,
                    target_cols=self.target_cols,
                    excel=excel,
                )

                # Handle .xls → .xlsx output path
                if converted:
                    final_path = os.path.splitext(original_path)[0] + ".xlsx"
                    # COM engine SaveAs may have already saved to the final path.
                    # Use normcase for case-insensitive comparison on Windows.
                    if os.path.normcase(os.path.abspath(result)) != os.path.normcase(os.path.abspath(final_path)):
                        import shutil
                        shutil.move(result, final_path)
                    result = final_path
                    logger.info(
                        "Saved processed output as: %s (original .xls preserved)",
                        os.path.basename(final_path),
                    )

                gc.collect()
                self._emit("File saved and memory cleaned", etype="success")
                return result
            except (ImportError, RuntimeError) as com_err:
                # COM engine import failed — fall through to openpyxl
                logger.warning(
                    "COM Engine unavailable, falling back to openpyxl: %s",
                    com_err,
                )
                self._emit("COM unavailable — using openpyxl engine")
                # If .xls wasn't converted yet, convert now for openpyxl
                if is_legacy_xls and not filepath.lower().endswith(".xlsx"):
                    filepath = convert_xls_to_xlsx(filepath)

        # ══════════════════════════════════════════════════════════════
        #  FALLBACK PATH: openpyxl (macOS / Linux)
        # ══════════════════════════════════════════════════════════════
        self._emit("Processing sheets with openpyxl engine")
        logger.info("Using openpyxl engine (non-Windows fallback).")

        try:
            wb = openpyxl.load_workbook(filepath)
        except Exception:
            if converted and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError as cleanup_err:
                    logger.debug("Cleanup failed for temp file %s: %s", filepath, cleanup_err)
            raise

        try:
            # Re-scan sheets for the openpyxl path (uses full workbook, not read-only)
            sheet_maps = {}
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
                    if self.target_cols["source_date"] in row_strs:
                        header_row_idx = row_idx
                        for ci, val in enumerate(row_strs):
                            if val == self.target_cols["source_date"]:
                                col_indices_local["source"] = ci
                            elif val == self.target_cols["currency"]:
                                col_indices_local["currency"] = ci
                            elif val == self.target_cols["out_rate"]:
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

            # ── STEP 1: Build ExRate master sheet ────────────────────────
            update_master_exrate_sheet(
                wb, usd_buying, usd_selling, eur_buying, eur_selling,
                list(master_holidays_set), holidays_names, computed_start,
            )

            # ── STEP 2: Build in-memory ExRate lookup index ──────────────
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

            # ── STEP 3: Cross-Tab VLOOKUP — Write formulas into monthly tabs ──
            for sheet_name, mapping in sheet_maps.items():
                ws = wb[sheet_name]
                cols = mapping["columns"]
                src_idx = cols["source"] + 1
                cur_idx = cols.get("currency")
                out_rate_idx = cols.get("out_rate")
                if out_rate_idx is None:
                    continue
                out_col = out_rate_idx + 1  # 1-indexed

                for row_idx in range(
                    mapping["header_row"] + 1, ws.max_row + 1
                ):
                    src_cell = ws.cell(row=row_idx, column=src_idx)
                    if isinstance(src_cell, MergedCell):
                        continue
                    inv_date = self._parse_date(src_cell.value)
                    if not inv_date:
                        continue

                    # Determine currency for this row
                    ccy = ""
                    if cur_idx is not None:
                        cur_cell = ws.cell(
                            row=row_idx, column=cur_idx + 1
                        )
                        if not isinstance(cur_cell, MergedCell):
                            raw = cur_cell.value
                            ccy = str(raw).strip().upper() if raw else ""

                    if ccy == "THB":
                        self._zero_touch_write(ws, row_idx, out_col, 1)
                        continue

                    # Build dual lookup formula:
                    #   Primary:  XLOOKUP (Excel 365 / 2021+)
                    #   Fallback: VLOOKUP (all Excel versions)
                    # ExRate layout: A=Date, B=USD Buy, C=USD Sell,
                    #                D=EUR Buy, E=EUR Sell
                    # We use Buying TT Rate: USD=col B (vlookup 2),
                    #                        EUR=col D (vlookup 4)
                    col_map = {"USD": ("$B", 2), "EUR": ("$D", 4)}
                    col_info = col_map.get(ccy)
                    if col_info is None:
                        continue
                    xl_col, vl_col = col_info

                    # Cell reference for the date column in this sheet
                    date_col_letter = get_column_letter(src_idx)
                    date_ref = f"{date_col_letter}{row_idx}"

                    # XLOOKUP first → VLOOKUP fallback
                    formula = (
                        f"=IFERROR(_xlfn.XLOOKUP({date_ref},"
                        f"ExRate!$A:$A,ExRate!{xl_col}:{xl_col},\"\"),"
                        f"IFERROR(VLOOKUP({date_ref},"
                        f"ExRate!$A:$E,{vl_col},FALSE),\"\"))"
                    )
                    self._zero_touch_write(ws, row_idx, out_col, formula)

            # ── Save & Cleanup ───────────────────────────────────────────
            if converted:
                final_path = os.path.splitext(original_path)[0] + ".xlsx"
                wb.save(final_path)
                wb.close()
                del wb  # release file handle immediately
                wb = None
                try:
                    os.remove(filepath)
                except OSError as e:
                    logger.debug("Cleanup of temp file failed: %s", e)
                filepath = final_path
                logger.info(
                    "Saved processed output as: %s (original .xls preserved)",
                    os.path.basename(final_path),
                )
            else:
                wb.save(filepath)
                wb.close()
                del wb  # release file handle immediately
                wb = None
                logger.info(
                    "Overwritten in-place: %s",
                    os.path.basename(filepath),
                )
        except Exception:
            # On ANY error, close the workbook to release the file lock
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass
                del wb
                wb = None
            if converted and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            raise
        gc.collect()
        self._emit("File saved and memory cleaned", etype="success")
        return filepath

    # ================================================================== #
    #  BATCH PROCESSING
    # ================================================================== #
    async def process_batch(
        self,
        filepaths: List[str],
        start_date: Optional[str] = None,
        progress_cb: Optional[
            Callable[[int, int, str, Optional[str]], None]
        ] = None,
    ) -> Tuple[int, int, List[str]]:
        """
        Batch processing with pre-edit backup, cache, and auto-cleanup.

        MANDATE 3 — Instance Pooling:
          On Windows, boots a SINGLE Excel.Application instance for the
          entire batch queue. All files are processed inside that one
          instance. excel.Quit() fires only when the full batch finishes.
        """
        self.backup.cleanup_old_backups(max_age_days=7)
        total = len(filepaths)
        success = 0
        errors: List[str] = []

        # ── MANDATE 3: Pool a single Excel instance for the batch ─────
        excel_instance = None
        excel_ctx = None
        if HAS_PYWIN32:
            try:
                from core.com_engine import ExcelCOM
                excel_ctx = ExcelCOM()
                excel_instance = excel_ctx.__enter__()
                logger.info(
                    "Batch: Pooled single Excel COM instance for %d files.",
                    total,
                )
            except Exception as e:
                logger.warning("Failed to pool Excel COM: %s", e)
                excel_instance = None
                excel_ctx = None

        try:
            for idx, fp in enumerate(filepaths):
                fname = os.path.basename(fp)
                try:
                    await self.process_ledger(
                        fp, start_date=start_date, excel=excel_instance,
                    )
                    success += 1
                    if progress_cb:
                        progress_cb(idx + 1, total, fname, None)
                except BackupError as e:
                    err_msg = f"{fname}: BACKUP FAILED — skipped ({e})"
                    errors.append(err_msg)
                    if progress_cb:
                        progress_cb(idx + 1, total, fname, str(e))
                except Exception as e:
                    err_msg = f"{fname}: {e!s}"
                    errors.append(err_msg)
                    logger.error(
                        "File SKIPPED: %s\n%s",
                        fname, traceback.format_exc(),
                    )
                    if progress_cb:
                        progress_cb(idx + 1, total, fname, str(e))
        finally:
            # ── Quit the pooled Excel instance ────────────────────────
            if excel_ctx is not None:
                excel_ctx.__exit__(None, None, None)
                logger.info("Batch: Pooled Excel COM instance terminated.")

        return success, total - success, errors
