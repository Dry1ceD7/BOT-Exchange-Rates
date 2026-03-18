#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.3.2) - Cache-First + Singleton Architecture
---------------------------------------------------------------------------
V2.3.2 Fixes:
  - BOTRateDetail import moved to top level
  - Cache uses today's rate if already stored (BOT publishes once/day)
  - CacheDB & BackupManager are module-level singletons
"""

import os
import gc
import openpyxl
from datetime import date, datetime, timedelta
from typing import Dict, Tuple, Set, List, Callable, Optional
from decimal import Decimal

from core.api_client import BOTClient, BOTRateDetail
from core.logic import BOTLogicEngine, RateNotFoundError, safe_to_decimal
from core.backup_manager import BackupManager, BackupError
from core.database import CacheDB

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
            "source_date": "วันที่ใบขน",
            "out_date": "วันที่ดึง Exchange rate date",
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
    #  CACHE-FIRST DATA LOADING (v2.3.2 — fixed cache heuristic)
    # ================================================================== #
    async def _preload_api_data(
        self, dates: Set[date], start_date: str = "2025-01-01"
    ) -> Tuple[BOTLogicEngine, Dict[date, Decimal], Dict[date, Decimal], List, List]:
        """
        Cache-First Architecture (v2.3.2):
        1. Check SQLite for holidays & rates
        2. Only call BOT API for MISSING data
        3. Store API results in SQLite immediately
        
        v2.3.2 Fix: Today's rate IS cached. BOT publishes once per day,
        so a cached rate for today is valid until midnight.
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

        logic_engine = BOTLogicEngine(holidays=holidays_list, max_rollback_days=5)

        # ── RATES: Cache-first (v2.3.2 FIX) ─────────────────────────────
        cached_rates = self.cache.get_rates_bulk(min_date, max_date)

        usd_rates: Dict[date, Decimal] = {}
        eur_rates: Dict[date, Decimal] = {}

        for d, (usd, eur) in cached_rates.items():
            if usd is not None:
                usd_rates[d] = usd
            if eur is not None:
                eur_rates[d] = eur

        # v2.3.2 FIX: Determine which dates are actually MISSING from cache.
        # BOT publishes rates once per day — cached today's rate is valid.
        # Only fetch from API if there are gaps in the date range.
        all_needed_dates = set()
        check_date = min_date
        while check_date <= max_date:
            # Only consider weekdays (Mon-Fri) — weekends never have rates
            if check_date.weekday() < 5:
                all_needed_dates.add(check_date)
            check_date += timedelta(days=1)

        cached_dates = set(cached_rates.keys())
        missing_dates = all_needed_dates - cached_dates

        usd_data = []
        eur_data = []

        if missing_dates:
            # Fetch the FULL range from API (BOT API doesn't support
            # individual date queries efficiently)
            usd_data = await self.api.get_exchange_rates(min_date, max_date, "USD")
            eur_data = await self.api.get_exchange_rates(min_date, max_date, "EUR")

            rate_cache_entries = {}

            for r in usd_data:
                if r.selling is not None:
                    d = datetime.strptime(r.period, "%Y-%m-%d").date()
                    usd_rates[d] = safe_to_decimal(r.selling)
                    rate_cache_entries.setdefault(r.period, [None, None])
                    rate_cache_entries[r.period][0] = r.selling

            for r in eur_data:
                if r.selling is not None:
                    d = datetime.strptime(r.period, "%Y-%m-%d").date()
                    eur_rates[d] = safe_to_decimal(r.selling)
                    rate_cache_entries.setdefault(r.period, [None, None])
                    rate_cache_entries[r.period][1] = r.selling

            bulk = [(d_str, vals[0], vals[1]) for d_str, vals in rate_cache_entries.items()]
            self.cache.insert_rates_bulk(bulk)
        else:
            # FULL CACHE HIT — build BOTRateDetail objects for reference sheets
            for d, (usd, eur) in cached_rates.items():
                d_str = d.strftime("%Y-%m-%d")
                if usd is not None:
                    usd_data.append(BOTRateDetail(
                        period=d_str, currency_id="USD",
                        buying_transfer=None, selling=float(usd)
                    ))
                if eur is not None:
                    eur_data.append(BOTRateDetail(
                        period=d_str, currency_id="EUR",
                        buying_transfer=None, selling=float(eur)
                    ))

        return logic_engine, usd_rates, eur_rates, usd_data, eur_data

    async def _update_reference_sheets(
        self, wb: openpyxl.Workbook, usd_data: List, eur_data: List,
        start_date: str = "2025-01-01"
    ):
        try:
            min_cutoff = datetime.strptime(start_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            min_cutoff = date(2025, 1, 1)

        for sheet_name in wb.sheetnames:
            if not sheet_name.startswith("Exrate "): continue
            ws = wb[sheet_name]
            currency = "USD" if "USD" in sheet_name else "EUR"
            raw_data = usd_data if currency == "USD" else eur_data

            date_col_idx = None
            start_row = 1
            for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
                row_strs = [str(c).strip() if c is not None else "" for c in row]
                if "Date" in row_strs or "Date " in row_strs:
                    date_col_idx = row_strs.index("Date") + 1 if "Date" in row_strs else row_strs.index("Date ") + 1
                    start_row = row_idx + 2
                    break

            if not date_col_idx: continue
            if ws.max_row >= start_row:
                ws.delete_rows(start_row, ws.max_row - start_row + 1)

            sorted_data = sorted(raw_data, key=lambda x: x.period)
            current_row = start_row
            for item in sorted_data:
                d_obj = datetime.strptime(item.period, "%Y-%m-%d").date()
                if d_obj >= min_cutoff:
                    ws.cell(row=current_row, column=date_col_idx).value = d_obj.strftime("%d %b %Y")
                    if item.buying_transfer is not None:
                        ws.cell(row=current_row, column=date_col_idx + 2).value = float(item.buying_transfer)
                    if item.selling is not None:
                        ws.cell(row=current_row, column=date_col_idx + 3).value = float(item.selling)
                    current_row += 1

    async def process_ledger(self, filepath: str, start_date: str = "2025-01-01") -> str:
        """
        Process a single ledger with:
        1. Memory guardrail → 2. Backup → 3. Load → 4. Cache-first API
        5. Write → 6. Save in-place → 7. Close + gc.collect()
        """
        self._check_memory_guardrail(filepath)
        self.backup.create_backup(filepath)

        wb = openpyxl.load_workbook(filepath)
        all_target_dates = set()
        sheet_maps = {}

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            header_row_idx = None
            col_indices = {}
            for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=10, values_only=True), 1):
                row_strs = [str(c).strip() if c is not None else "" for c in row]
                if self.target_cols["source_date"] in row_strs:
                    header_row_idx = row_idx
                    for col_idx, val in enumerate(row_strs):
                        if val == self.target_cols["source_date"]: col_indices["source"] = col_idx
                        elif val == self.target_cols["out_date"]: col_indices["out_date"] = col_idx
                        elif val == self.target_cols["currency"]: col_indices["currency"] = col_idx
                        elif val == self.target_cols["out_rate"]: col_indices["out_rate"] = col_idx
                    break

            if header_row_idx and "source" in col_indices:
                sheet_maps[sheet_name] = {"header_row": header_row_idx, "columns": col_indices}
                src_idx = col_indices["source"] + 1
                for row_idx in range(header_row_idx + 1, ws.max_row + 1):
                    parsed_date = self._parse_date(ws.cell(row=row_idx, column=src_idx).value)
                    if parsed_date: all_target_dates.add(parsed_date)

        if not sheet_maps:
            wb.close()
            gc.collect()
            raise MissingColumnError(f"Headers not found in: {os.path.basename(filepath)}")
        if not all_target_dates:
            wb.close()
            gc.collect()
            return filepath

        logic_engine, usd_rates, eur_rates, usd_data, eur_data = await self._preload_api_data(
            all_target_dates, start_date=start_date
        )

        for sheet_name, mapping in sheet_maps.items():
            ws = wb[sheet_name]
            cols = mapping["columns"]
            src_idx = cols["source"] + 1
            out_date_idx = cols.get("out_date")
            cur_idx = cols.get("currency")
            out_rate_idx = cols.get("out_rate")

            for row_idx in range(mapping["header_row"] + 1, ws.max_row + 1):
                inv_date = self._parse_date(ws.cell(row=row_idx, column=src_idx).value)
                if inv_date:
                    try:
                        trade_date, usd_rt, eur_rt = logic_engine.resolve_rate(inv_date, usd_rates, eur_rates)
                        if out_date_idx is not None:
                            ws.cell(row=row_idx, column=out_date_idx + 1).value = trade_date.strftime("%d %b %Y")
                        if cur_idx is not None and out_rate_idx is not None:
                            ccy = str(ws.cell(row=row_idx, column=cur_idx + 1).value).strip().upper()
                            if ccy == "USD" and usd_rt:
                                ws.cell(row=row_idx, column=out_rate_idx + 1).value = float(usd_rt)
                            elif ccy == "EUR" and eur_rt:
                                ws.cell(row=row_idx, column=out_rate_idx + 1).value = float(eur_rt)
                    except RateNotFoundError:
                        if out_date_idx is not None:
                            ws.cell(row=row_idx, column=out_date_idx + 1).value = "<ERROR: No Rate>"
                        if out_rate_idx is not None:
                            ws.cell(row=row_idx, column=out_rate_idx + 1).value = "<ERROR>"

        await self._update_reference_sheets(wb, usd_data, eur_data, start_date=start_date)

        wb.save(filepath)
        wb.close()
        gc.collect()

        return filepath

    async def process_batch(
        self,
        filepaths: List[str],
        start_date: str = "2025-01-01",
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
