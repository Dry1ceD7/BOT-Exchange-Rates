#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v4.0) - Cache-First Orchestrator
---------------------------------------------------------------------------
Slim orchestrator. Heavy logic extracted to:
  - core/exrate_sheet.py → Master ExRate sheet builder
  - core/prescan.py → Smart date pre-scanner
"""

import asyncio
import gc
import logging
import os
import re
import time
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set, Tuple

import httpx
from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter

from core.api_client import CLIENT_TIMEOUT, BOTClient
from core.backup_manager import BackupError, BackupManager
from core.database import CacheDB
from core.exrate_sheet import update_master_exrate_sheet
from core.logic import BOTLogicEngine, safe_to_decimal
from core.prescan import prescan_oldest_date



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
                "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
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
        **_kwargs,
    ) -> str:
        """Process a single .xlsx ledger file end-to-end."""

        filepath = os.path.abspath(filepath)

        # ── Reject legacy .xls files ─────────────────────────────────
        if filepath.lower().endswith(".xls") and not filepath.lower().endswith(".xlsx"):
            raise ValueError(
                f"Legacy .xls files are no longer supported. "
                f"Please convert '{os.path.basename(filepath)}' to .xlsx first."
            )

        self._check_memory_guardrail(filepath)
        self._emit("Size check passed")
        self.backup.create_backup(filepath)
        self._emit("Backup created")

        # ── Standalone ExRate detection ────────────────────────────────
        try:
            import openpyxl as _opx
            _wb_check = _opx.load_workbook(filepath, read_only=True)
            has_exrate = "ExRate" in _wb_check.sheetnames
            has_month_tabs = False
            for sn in _wb_check.sheetnames:
                if sn in SKIP_SHEET_NAMES:
                    continue
                ws_check = _wb_check[sn]
                for row in ws_check.iter_rows(
                    min_row=1, max_row=5, values_only=True,
                ):
                    row_strs = [
                        str(c).strip() if c is not None else ""
                        for c in row
                    ]
                    if (
                        self.target_cols["source_date"] in row_strs
                        and self.target_cols["currency"] in row_strs
                    ):
                        has_month_tabs = True
                        break
                if has_month_tabs:
                    break
            _wb_check.close()

            if has_exrate and not has_month_tabs:
                self._emit("Standalone ExRate file detected — updating rates")
                return await self.update_exrate_standalone(filepath)
        except Exception:
            pass  # Fall through to normal pipeline

        # ── Pre-scan dates for API data loading ──────────────────────
        self._emit("Scanning dates from workbook")
        all_target_dates: Set[date] = set()

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
            raise
        finally:
            if wb_scan is not None:
                wb_scan.close()
                del wb_scan
                wb_scan = None
            gc.collect()

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
        #  openpyxl Engine — .xlsx only
        # ══════════════════════════════════════════════════════════════
        self._emit("Processing sheets with openpyxl engine")
        logger.info("Using openpyxl engine.")


        wb = openpyxl.load_workbook(filepath)

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

            # ── STEP 3: Cross-Tab XLOOKUP — Write formulas into monthly tabs ──
            #
            # Writes a clean IFS + XLOOKUP formula per row to the
            # "EX Rate" column. Supports 3 currencies:
            #   THB → 1 (hardcoded)
            #   USD → XLOOKUP against ExRate col B (Buying TT)
            #   EUR → XLOOKUP against ExRate col D (Buying TT)
            #
            # ALL existing values/formulas are OVERWRITTEN to ensure
            # consistency across the workbook.
            #
            # Date Normalization: text-string dates are converted to
            # proper date objects so XLOOKUP exact-match works.
            #
            # ExRate layout (data starts at row 2):
            #   A=Date, B=USD Buy, C=USD Sell, D=EUR Buy, E=EUR Sell

            # Determine ExRate data boundaries for bounded formula ranges
            exrate_last_row = 2  # minimum
            if "ExRate" in wb.sheetnames:
                ws_ex = wb["ExRate"]
                exrate_last_row = max(ws_ex.max_row or 2, 2)

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
                N = exrate_last_row  # last data row in ExRate

                written = 0
                for row_idx in range(
                    mapping["header_row"] + 1, ws.max_row + 1
                ):
                    src_cell = ws.cell(row=row_idx, column=src_idx)
                    if isinstance(src_cell, MergedCell):
                        continue
                    inv_date = self._parse_date(src_cell.value)
                    if not inv_date:
                        continue

                    # ── Date Normalization ─────────────────────────
                    if isinstance(src_cell.value, str):
                        src_cell.value = inv_date
                        src_cell.number_format = "DD/MM/YYYY"

                    # Cell references for this row
                    date_ref = f"{date_letter}{row_idx}"
                    cur_ref = f"{cur_letter}{row_idx}"

                    # Clean XLOOKUP-only formula (no VLOOKUP fallback)
                    formula = (
                        f"=_xlfn.IFS("
                        f"{cur_ref}=\"THB\",1,"
                        f"{cur_ref}=\"USD\","
                        f"_xlfn.XLOOKUP({date_ref},"
                        f"ExRate!$A$2:$A${N},"
                        f"ExRate!$B$2:$B${N},\"\",0),"
                        f"{cur_ref}=\"EUR\","
                        f"_xlfn.XLOOKUP({date_ref},"
                        f"ExRate!$A$2:$A${N},"
                        f"ExRate!$D$2:$D${N},\"\",0),"
                        f"TRUE,\"\")"
                    )
                    self._zero_touch_write(ws, row_idx, out_col, formula)
                    written += 1

                self._emit(
                    f"{sheet_name}: {written} EX Rate formulas written"
                )
                logger.info(
                    "Sheet '%s': %d EX Rate formulas written",
                    sheet_name, written,
                )

                # ── Pre-format Date column for manual entry ───────────
                # Apply "DD/MM/YYYY" to a small buffer zone below data
                max_preformat = ws.max_row + 50
                for r in range(mapping["header_row"] + 1, max_preformat + 1):
                    cell = ws.cell(row=r, column=src_idx)
                    if not isinstance(cell, MergedCell):
                        cell.number_format = "DD/MM/YYYY"

            # ── Save ─────────────────────────────────────────────────────
            wb.save(filepath)
            wb.close()
            del wb
            wb = None
            logger.info(
                "Overwritten in-place: %s",
                os.path.basename(filepath),
            )
        except Exception:
            if wb is not None:
                try:
                    wb.close()
                except Exception:
                    pass
                del wb
                wb = None
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
        """Batch processing with pre-edit backup, cache, and auto-cleanup."""
        self.backup.cleanup_old_backups(max_age_days=7)
        total = len(filepaths)
        success = 0
        errors: List[str] = []

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
                err_msg = f"{fname}: {e!s}"
                errors.append(err_msg)
                logger.error(
                    "File SKIPPED: %s\n%s",
                    fname, traceback.format_exc(),
                )
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))

        return success, total - success, errors

    # ================================================================== #
    #  STANDALONE EXRATE UPDATE
    # ================================================================== #

    # Mapping from rate_type API field → human-readable label suffix
    _RATE_LABELS = {
        "buying_transfer": "Buying TT",
        "buying_sight":    "Buying Sight",
        "selling":         "Selling",
        "mid_rate":        "Mid Rate",
    }

    async def update_exrate_standalone(
        self,
        filepath: str,
        progress_cb: Optional[Callable[[str], None]] = None,
        currencies: Optional[List[str]] = None,
        rate_types: Optional[Dict[str, str]] = None,
        date_range: Optional[Tuple[date, date]] = None,
    ) -> str:
        """
        Update a standalone ExRate .xlsx file with fresh exchange rates.

        Args:
            filepath: Path to the standalone ExRate .xlsx file.
            progress_cb: Optional status callback(message).
            currencies: List of currency codes (e.g. ["USD","EUR","GBP"]).
                         Defaults to ["USD","EUR"] if not provided.
            rate_types: Dict {label: api_field} of rate types to include.
                         Defaults to Buying TT + Selling if not provided.

        Returns:
            Path to the saved file.
        """
        import re

        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        # ── Defaults ──────────────────────────────────────────────────
        if not currencies:
            currencies = ["USD", "EUR"]
        if not rate_types:
            rate_types = {
                "Buying TT": "buying_transfer",
                "Selling": "selling",
            }

        # If standard USD+EUR with Buying TT + Selling, use the original
        # function for backward compatibility with ledger processing.
        is_standard = (
            set(currencies) == {"USD", "EUR"}
            and set(rate_types.values()) == {"buying_transfer", "selling"}
        )

        def _status(msg: str):
            if progress_cb:
                progress_cb(msg)
            self._emit(msg)

        _status("Opening ExRate file...")
        wb = openpyxl.load_workbook(filepath)

        if "ExRate" not in wb.sheetnames:
            wb.close()
            raise ValueError("No ExRate sheet found in the selected file.")

        # ── Standard path (backward compatible) ───────────────────────
        if is_standard:
            from core.exrate_sheet import update_master_exrate_sheet

            ws_ex = wb["ExRate"]
            existing_dates = set()
            for row_idx in range(2, (ws_ex.max_row or 1) + 1):
                cell_val = ws_ex.cell(row=row_idx, column=1).value
                parsed = self._parse_date(cell_val)
                if parsed:
                    existing_dates.add(parsed)

            if existing_dates:
                target_year = min(existing_dates).year
            else:
                target_year = date.today().year

            # Override with manual date_range if provided
            if date_range:
                dr_start, dr_end = date_range
                target_year = dr_start.year
                all_target_dates = {dr_start, dr_end, date.today()}
                start_date_str = f"{dr_start.year - 1}-12-20"
            else:
                start_date_str = f"{target_year - 1}-12-20"
                all_target_dates = existing_dates | {date.today()}

            _status("Fetching exchange rates from BOT API...")
            (
                logic_engine, usd_selling, eur_selling,
                usd_buying, eur_buying, _usd_data, _eur_data,
            ) = await self._preload_api_data(all_target_dates, start_date_str)

            computed_start = self.compute_year_start_date(
                target_year, logic_engine.holidays
            )

            sub_pattern = re.compile(r'^Substitution for (.*)\\((.*?)\\)$')
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
                            date_str_clean = re.sub(
                                r'(\d+)(st|nd|rd|th)', r'\1', date_str
                            )
                            date_str_clean = re.sub(
                                r'^[A-Za-z]+\s+', '', date_str_clean
                            )
                            try:
                                real_dt = datetime.strptime(
                                    date_str_clean, '%d %B %Y'
                                ).date()
                                holidays_names[real_dt] = real_name
                                master_holidays_set.add(real_dt)
                            except (ValueError, TypeError):
                                pass
                    except (ValueError, TypeError):
                        pass

            _status("Writing exchange rate data...")
            update_master_exrate_sheet(
                wb, usd_buying, usd_selling, eur_buying, eur_selling,
                sorted(master_holidays_set), holidays_names, computed_start,
            )
            wb.save(filepath)
            wb.close()
            _status(f"✓ ExRate updated: {os.path.basename(filepath)}")
            return filepath

        # ── Custom path (any currencies / rate types) ─────────────────
        _status(f"Fetching rates for {', '.join(currencies)}...")

        # Fetch from API for each currency
        if date_range:
            start_dt, end_dt = date_range
        else:
            target_year = date.today().year
            start_dt = date(target_year - 1, 12, 20)
            end_dt = date.today()

        # rate_data[currency][api_field][date] = value
        rate_data: Dict[str, Dict[str, Dict[date, float]]] = {}

        async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as http:
            from core.api_client import BOTClient

            client = BOTClient(http)

            for ccy in currencies:
                _status(f"Fetching {ccy} rates...")
                raw_results = await client.get_exchange_rates(
                    start_dt, end_dt, ccy,
                )
                rate_data[ccy] = {}
                for label, api_field in rate_types.items():
                    rate_data[ccy][api_field] = {}

                for rec in raw_results:
                    try:
                        rec_date = datetime.strptime(
                            rec.period, "%Y-%m-%d"
                        ).date()
                    except (ValueError, TypeError):
                        continue
                    for label, api_field in rate_types.items():
                        val = getattr(rec, api_field, None)
                        if val is not None:
                            rate_data[ccy][api_field][rec_date] = val

        # Fetch holidays
        _status("Fetching holidays...")
        all_target_dates = {date.today()}
        (
            logic_engine, _, _, _, _, _, _,
        ) = await self._preload_api_data(all_target_dates, str(start_dt))

        holidays_set = set(logic_engine.holidays)
        holidays_names_map: Dict[date, str] = {}
        for year in {start_dt.year, end_dt.year}:
            for h_str, h_name in self.cache.get_holidays(year):
                try:
                    holidays_names_map[
                        datetime.strptime(h_str, "%Y-%m-%d").date()
                    ] = h_name
                except (ValueError, TypeError):
                    pass

        # Build column headers: Date + (CCY RateType)... + Holidays
        headers = ["Date"]
        col_specs = []  # (currency, api_field) per data column
        for ccy in currencies:
            for label, api_field in rate_types.items():
                headers.append(f"{ccy} {label}")
                col_specs.append((ccy, api_field))
        headers.append("Holidays/Weekend")

        # Build date range
        all_dates = []
        d = start_dt
        while d <= end_dt:
            all_dates.append(d)
            d += timedelta(days=1)
        all_dates.sort()

        # ── Write to sheet ────────────────────────────────────────────
        ws = wb["ExRate"]
        _status("Writing custom ExRate data...")

        # Styles
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

        # Clear existing content
        for row_idx in range(1, max(ws.max_row or 1, 1) + 1):
            for col_idx in range(1, max(ws.max_column or 1, 1) + 1):
                ws.cell(row=row_idx, column=col_idx).value = None

        # Write headers
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Set column widths
        ws.column_dimensions["A"].width = 14
        for i in range(len(col_specs)):
            col_letter = openpyxl.utils.get_column_letter(i + 2)
            ws.column_dimensions[col_letter].width = 18
        last_col_letter = openpyxl.utils.get_column_letter(len(headers))
        ws.column_dimensions[last_col_letter].width = 40

        # Write data rows
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
                label = f"Weekend; {holidays_names_map.get(d, 'Holiday')}"
            elif is_weekend:
                label = "Weekend"
            elif is_holiday:
                label = holidays_names_map.get(d, "Holiday")
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

        wb.save(filepath)
        wb.close()
        _status(f"✓ ExRate created: {os.path.basename(filepath)}")
        return filepath
