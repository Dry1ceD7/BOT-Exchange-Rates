#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.4) - Cache-First Orchestrator
---------------------------------------------------------------------------
Slim orchestrator. Heavy logic extracted to:
  - core/exrate_sheet.py → Master ExRate sheet builder
  - core/xls_converter.py → Legacy .xls conversion
  - core/prescan.py → Smart date pre-scanner
"""

import gc
import logging
import os
import shutil
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set, Tuple

import openpyxl
from openpyxl.styles import Font

from core.api_client import BOTClient
from core.backup_manager import BackupError, BackupManager
from core.database import CacheDB
from core.exrate_sheet import update_master_exrate_sheet
from core.logic import BOTLogicEngine, RateNotFoundError, safe_to_decimal
from core.prescan import prescan_oldest_date
from core.xls_converter import convert_xls_to_xlsx

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
            "out_rate": "EX Rate",
        }

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

    # ================================================================== #
    #  CACHE-FIRST DATA LOADING (v2.5.4)
    # ================================================================== #
    async def _preload_api_data(
        self, dates: Set[date], start_date: str = "2025-01-01"
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
            usd_data = await self.api.get_exchange_rates(
                min_date, max_date, "USD"
            )
            eur_data = await self.api.get_exchange_rates(
                min_date, max_date, "EUR"
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

        return (
            logic_engine, usd_selling, eur_selling,
            usd_buying, eur_buying, usd_data, eur_data,
        )

    # ================================================================== #
    #  PROCESS SINGLE LEDGER
    # ================================================================== #
    async def process_ledger(
        self, filepath: str, start_date: Optional[str] = None
    ) -> str:
        """Process a single ledger file end-to-end."""
        self._check_memory_guardrail(filepath)
        self.backup.create_backup(filepath)

        # ── .xls Auto-Conversion ─────────────────────────────────────
        original_path = filepath
        converted = False
        if filepath.lower().endswith(".xls") and not filepath.lower().endswith(
            ".xlsx"
        ):
            filepath = convert_xls_to_xlsx(filepath)
            converted = True

        try:
            wb = openpyxl.load_workbook(filepath)
        except Exception:
            if converted and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except OSError as cleanup_err:
                    logger.debug("Cleanup failed for temp file %s: %s", filepath, cleanup_err)
            raise

        all_target_dates: Set[date] = set()
        sheet_maps = {}
        for sheet_name in wb.sheetnames:
            if sheet_name in SKIP_SHEET_NAMES:
                continue
            ws = wb[sheet_name]
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
                        elif val == self.target_cols["currency"]:
                            col_indices["currency"] = ci
                        elif val == self.target_cols["out_rate"]:
                            col_indices["out_rate"] = ci
                    break

            if header_row_idx is None or "source" not in col_indices:
                logger.info(
                    f"Sheet '{sheet_name}' missing source date column — "
                    f"skipped."
                )
                continue

            sheet_maps[sheet_name] = {
                "header_row": header_row_idx,
                "columns": col_indices,
            }
            src_idx = col_indices["source"] + 1
            for row_idx in range(header_row_idx + 1, ws.max_row + 1):
                parsed_date = self._parse_date(
                    ws.cell(row=row_idx, column=src_idx).value
                )
                if parsed_date:
                    all_target_dates.add(parsed_date)

        # ── Date hierarchy ───────────────────────────────────────────
        target_year = (
            min(all_target_dates).year if all_target_dates
            else date.today().year
        )
        preliminary_start = f"{target_year - 1}-12-20"
        if start_date is None:
            start_date = preliminary_start

        (
            logic_engine, usd_selling, eur_selling,
            usd_buying, eur_buying, usd_data, eur_data,
        ) = await self._preload_api_data(all_target_dates, start_date)

        computed_start = self.compute_year_start_date(
            target_year, logic_engine.holidays
        )

        # ── Build holiday names lookup ───────────────────────────────
        holidays_names: Dict[date, str] = {}
        for year in {
            d.year
            for d in (all_target_dates | {computed_start, date.today()})
        }:
            cached_hols = self.cache.get_holidays(year)
            for h_str, h_name in cached_hols:
                try:
                    h_obj = datetime.strptime(h_str, "%Y-%m-%d").date()
                    holidays_names[h_obj] = h_name
                except (ValueError, TypeError):
                    logger.debug("Skipped unparseable holiday name: %s", h_str)

        # ── STEP 1: Build ExRate master sheet ────────────────────────
        update_master_exrate_sheet(
            wb, usd_buying, usd_selling, eur_buying, eur_selling,
            list(logic_engine.holidays), holidays_names, computed_start,
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

        # ── STEP 3: Process monthly tabs via cross-tab VLOOKUP ───────
        exrate_font = Font(name="Calibri", size=10, color="C0392B")
        for sheet_name, mapping in sheet_maps.items():
            ws = wb[sheet_name]
            cols = mapping["columns"]
            src_idx = cols["source"] + 1
            cur_idx = cols.get("currency")
            out_rate_idx = cols.get("out_rate")
            for row_idx in range(mapping["header_row"] + 1, ws.max_row + 1):
                inv_date = self._parse_date(
                    ws.cell(row=row_idx, column=src_idx).value
                )
                if not inv_date:
                    continue
                ccy = ""
                if cur_idx is not None:
                    raw = ws.cell(row=row_idx, column=cur_idx + 1).value
                    ccy = str(raw).strip().upper() if raw else ""
                if ccy == "THB":
                    if out_rate_idx is not None:
                        cell = ws.cell(
                            row=row_idx, column=out_rate_idx + 1
                        )
                        cell.value = 1
                        cell.font = exrate_font
                    continue
                try:
                    trade_date, _ = logic_engine.resolve_rate(
                        inv_date, usd_buying, eur_buying
                    )
                except RateNotFoundError:
                    if out_rate_idx is not None:
                        cell = ws.cell(
                            row=row_idx, column=out_rate_idx + 1
                        )
                        cell.value = None
                        cell.font = exrate_font
                    continue
                exrate_row = exrate_index.get(trade_date, {})
                rate = None
                if ccy == "USD":
                    rate = exrate_row.get("usd_buying")
                elif ccy == "EUR":
                    rate = exrate_row.get("eur_buying")
                if out_rate_idx is not None:
                    cell = ws.cell(row=row_idx, column=out_rate_idx + 1)
                    cell.value = float(rate) if rate is not None else None
                    cell.font = exrate_font

        # ── Save & Cleanup ───────────────────────────────────────────
        wb.save(filepath)
        wb.close()
        if converted:
            xlsx_output = os.path.splitext(original_path)[0] + ".xlsx"
            shutil.copy2(filepath, xlsx_output)
            try:
                os.remove(filepath)
            except OSError as e:
                logger.debug("Cleanup of temp conversion file failed: %s", e)
            filepath = xlsx_output
            logger.info(
                f"Saved processed output as: {os.path.basename(xlsx_output)}"
            )
        gc.collect()
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
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))
        return success, total - success, errors
