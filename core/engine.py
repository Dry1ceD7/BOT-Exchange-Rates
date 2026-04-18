#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Cache-First Orchestrator
---------------------------------------------------------------------------
Slim orchestrator. Heavy logic extracted to:
  - core/excel_io.py → Excel I/O operations (formulas, indexing, writing)
  - core/exrate_sheet.py → Master ExRate sheet builder
  - core/prescan.py → Smart date pre-scanner
"""

import asyncio
import gc
import logging
import os
import re
import shutil
import threading
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Set, Tuple

import httpx
import openpyxl

from core.anomaly_guard import AnomalyGuard
from core.api_client import CLIENT_TIMEOUT, BOTClient
from core.backup_manager import BackupError, BackupManager
from core.config_manager import SettingsManager
from core.constants import (
    BACKUP_MAX_AGE_DAYS,
    DEFAULT_ANOMALY_THRESHOLD_PCT,
    MAX_FILE_SIZE_MB,
    MIN_DISK_SPACE_MB,
)
from core.database import CacheDB
from core.enterprise import (
    fetch_fallback_rates,
    load_holiday_overlays,
)
from core.excel_io import (
    build_exrate_index,
    inject_xlookup_formulas,
    scan_sheet_headers,
    write_custom_exrate_data,
    zero_touch_write,
)
from core.exrate_sheet import update_master_exrate_sheet
from core.logic import BOTLogicEngine, safe_to_decimal
from core.prescan import prescan_oldest_date

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# EXCEPTIONS
# -------------------------------------------------------------------------


class FileSizeLimitError(Exception):
    """Raised when the input workbook exceeds the configured size limit."""


# -------------------------------------------------------------------------
# MODULE-LEVEL SINGLETONS (persist across batch clicks)
# -------------------------------------------------------------------------
_backup_singleton = None
_cache_singleton = None
_singleton_lock = threading.Lock()


def _get_backup() -> BackupManager:
    global _backup_singleton
    if _backup_singleton is None:
        with _singleton_lock:
            if _backup_singleton is None:  # double-check after lock
                _backup_singleton = BackupManager()
    return _backup_singleton


def _get_cache() -> CacheDB:
    global _cache_singleton
    if _cache_singleton is None:
        with _singleton_lock:
            if _cache_singleton is None:  # double-check after lock
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
    MAX_FILE_SIZE_MB = MAX_FILE_SIZE_MB
    MAX_FILE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    def __init__(
        self,
        api_client: BOTClient,
        event_bus=None,
        backup: Optional[BackupManager] = None,
        cache: Optional[CacheDB] = None,
    ) -> None:
        """Initialize the processing engine.

        Args:
            api_client: Authenticated BOT API client for rate data.
            event_bus: Optional event bus for GUI status updates.
            backup: Optional BackupManager instance (defaults to singleton).
            cache: Optional CacheDB instance (defaults to singleton).
        """
        self.api = api_client
        self.backup = backup or _get_backup()
        self.cache = cache or _get_cache()
        self._bus = event_bus
        self.target_cols = {
            "source_date": "Date",
            "currency": "Cur",
            "out_rate": "EX Rate",
        }
        # v3.1.0: Load anomaly threshold from settings
        _settings = SettingsManager().load()
        threshold = _settings.get(
            "anomaly_threshold_pct", DEFAULT_ANOMALY_THRESHOLD_PCT
        )
        self._anomaly_guard = AnomalyGuard(threshold_pct=threshold)

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



    # ── Zero-Touch Write (delegates to excel_io) ──────────────────
    @staticmethod
    def _zero_touch_write(ws, row: int, col: int, value) -> None:
        """Write a value without touching formatting. Delegates to excel_io."""
        zero_touch_write(ws, row, col, value)

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
        settings = SettingsManager().load()

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

        # ── Optional holiday overlays (CSV/JSON/TXT) ────────────────
        overlay_path = str(settings.get("holiday_overlay_path", "")).strip()
        if overlay_path:
            overlay_rows = load_holiday_overlays(overlay_path)
            if overlay_rows:
                self.cache.insert_holidays(overlay_rows)
                for d_str, _name in overlay_rows:
                    try:
                        holidays_list.append(
                            datetime.strptime(d_str, "%Y-%m-%d").date()
                        )
                    except (ValueError, TypeError):
                        logger.debug("Skipped invalid overlay holiday: %s", d_str)
                self._emit(
                    f"Holiday overlays loaded: {len(overlay_rows)} entries",
                    etype="success",
                )

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
            try:
                usd_data, eur_data = await asyncio.gather(
                    self.api.get_exchange_rates(fetch_start, fetch_end, "USD"),
                    self.api.get_exchange_rates(fetch_start, fetch_end, "EUR"),
                )
            except Exception as api_error:
                if not settings.get("enable_fx_fallback", True):
                    raise
                self._emit(
                    "Primary BOT rates failed — using fallback FX source",
                    etype="warning",
                )
                logger.warning("BOT API fetch failed, fallback enabled: %s", api_error)
                fallback_base = str(
                    settings.get("fx_fallback_base_url", "https://api.frankfurter.app")
                ).strip() or "https://api.frankfurter.app"
                async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT) as fb_client:
                    usd_data, eur_data = await asyncio.gather(
                        fetch_fallback_rates(
                            fetch_start, fetch_end, "USD", fb_client, base_url=fallback_base
                        ),
                        fetch_fallback_rates(
                            fetch_start, fetch_end, "EUR", fb_client, base_url=fallback_base
                        ),
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

    def _run_anomaly_check(
        self,
        usd_buying: Dict[date, Decimal],
        usd_selling: Dict[date, Decimal],
        eur_buying: Dict[date, Decimal],
        eur_selling: Dict[date, Decimal],
    ) -> int:
        """
        v3.1.0: Run anomaly detection across all loaded rates.
        Returns the number of anomalies found.
        """
        rates_bundle = {
            "USD_buying_transfer": usd_buying,
            "USD_selling": usd_selling,
            "EUR_buying_transfer": eur_buying,
            "EUR_selling": eur_selling,
        }
        anomalies = self._anomaly_guard.check_rates_bulk(rates_bundle)
        for a in anomalies:
            self._emit(
                f"⚠ ANOMALY: {a.currency} {a.rate_type} on "
                f"{a.check_date.strftime('%d %b %Y')}: "
                f"{a.pct_change:.2f}% change "
                f"({a.prev_value} → {a.new_value})",
                etype="warning",
            )
        if anomalies:
            logger.warning(
                "Anomaly guard: %d suspicious rate(s) detected",
                len(anomalies),
            )
        return len(anomalies)

    # ================================================================== #
    #  PRIVATE HELPERS — Extracted from process_ledger for readability
    # ================================================================== #

    async def _detect_standalone_exrate(
        self, filepath: str,
    ) -> Optional[str]:
        """Detect if the file is a standalone ExRate workbook (no month tabs).

        Returns the result of update_exrate_standalone() if standalone,
        or None if the file should be processed normally. Also validates
        that the file format is supported (.xlsx or .xlsm).
        """
        if filepath.lower().endswith(".xlsx"):
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
            except (ValueError, TypeError, KeyError,
                    openpyxl.utils.exceptions.InvalidFileException) as exc:
                logger.debug("Standalone detection probe failed: %s", exc)

        # Reject unsupported formats
        if not filepath.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError(
                f"Unsupported format: {os.path.basename(filepath)}. "
                "Only .xlsx and .xlsm files are supported."
            )

        return None  # Not standalone — proceed with normal processing

    def _prescan_target_dates(self, filepath: str) -> Set[date]:
        """Scan the workbook in read-only mode to extract all target dates.

        Opens the workbook in read-only mode, scans all non-skipped sheets
        for the source date column, and returns a set of all parsed dates.
        The workbook is properly closed and garbage-collected after scanning.
        """
        self._emit("Scanning dates from workbook")
        all_target_dates: Set[date] = set()

        wb_scan = None
        try:
            wb_scan = openpyxl.load_workbook(
                filepath, read_only=True, data_only=True,
            )
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
        except (ValueError, TypeError, KeyError,
                openpyxl.utils.exceptions.InvalidFileException):
            raise
        finally:
            if wb_scan is not None:
                try:
                    wb_scan.close()
                except OSError:
                    pass
                del wb_scan
                wb_scan = None
            gc.collect()

        return all_target_dates

    def _build_holiday_lookup(
        self,
        all_target_dates: Set[date],
        computed_start: date,
        logic_engine,
    ) -> Tuple[Set[date], Dict[date, str]]:
        """Build holiday sets and name mappings from cached holiday data.

        Parses substitution holiday names (e.g., "Substitution for
        Songkran Day (15th April 2025)") to map the original holiday
        date as well.

        Returns:
            Tuple of (master_holidays_set, holidays_names dict).
        """
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

        return master_holidays_set, holidays_names

    # ================================================================== #
    #  PROCESS SINGLE LEDGER
    # ================================================================== #
    async def process_ledger(
        self,
        filepath: str,
        start_date: Optional[str] = None,
        dry_run: bool = False,
    ) -> str:
        """Process a single ledger file end-to-end."""

        filepath = os.path.abspath(filepath)

        self._check_memory_guardrail(filepath)
        self._emit("Size check passed")
        if dry_run:
            self._emit("[SIM] Backup skipped (dry run)")
        else:
            self.backup.create_backup(filepath)
            self._emit("Backup created")

        # ── Standalone ExRate detection + format validation ─────────────
        standalone_result = await self._detect_standalone_exrate(filepath)
        if standalone_result is not None:
            return standalone_result

        # ── Pre-scan dates for API data loading ──────────────────────
        all_target_dates = self._prescan_target_dates(filepath)

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

        # v3.1.0: Anomaly detection — check for suspicious rate jumps
        anomaly_count = self._run_anomaly_check(
            usd_buying, usd_selling, eur_buying, eur_selling,
        )
        if anomaly_count:
            self._emit(
                f"⚠ {anomaly_count} anomalous rate(s) detected — check audit log",
                etype="warning",
            )

        computed_start = self.compute_year_start_date(
            target_year, logic_engine.holidays
        )

        # ── Build holiday names lookup ───────────────────────────────
        master_holidays_set, holidays_names = self._build_holiday_lookup(
            all_target_dates, computed_start, logic_engine
        )
        # ══════════════════════════════════════════════════════════════
        #  openpyxl ENGINE
        # ══════════════════════════════════════════════════════════════
        self._emit("Processing sheets with openpyxl engine")
        logger.info("Processing with openpyxl engine.")

        try:
            wb = openpyxl.load_workbook(filepath)
        except (OSError, openpyxl.utils.exceptions.InvalidFileException):
            raise

        try:
            # Scan monthly tabs for header/column mappings
            sheet_maps = scan_sheet_headers(wb, self.target_cols)

            # ── STEP 1: Build ExRate master sheet ────────────────────────
            update_master_exrate_sheet(
                wb, usd_buying, usd_selling, eur_buying, eur_selling,
                list(master_holidays_set), holidays_names, computed_start,
            )

            # ── STEP 2: Build in-memory ExRate lookup index ──────────────
            build_exrate_index(wb)

            # ── STEP 3: Inject XLOOKUP formulas into monthly tabs ────────
            exrate_last_row = 2
            if "ExRate" in wb.sheetnames:
                ws_ex = wb["ExRate"]
                exrate_last_row = max(ws_ex.max_row or 2, 2)

            inject_xlookup_formulas(
                wb, sheet_maps, exrate_last_row,
                parse_date_fn=self._parse_date,
                emit_fn=self._emit,
                dry_run=dry_run,
            )

            # ── Save & Cleanup ───────────────────────────────────────────
            if dry_run:
                self._emit(
                    "[SIM] File NOT saved (dry run) "
                    f"— {os.path.basename(filepath)}"
                )
            else:
                # ERR-03: Check disk space before saving
                drive_stat = shutil.disk_usage(os.path.dirname(filepath))
                free_mb = drive_stat.free // (1024 * 1024)
                if free_mb < MIN_DISK_SPACE_MB:
                    raise OSError(
                        f"Insufficient disk space ({free_mb}MB free, "
                        f"need {MIN_DISK_SPACE_MB}MB). File NOT saved."
                    )
                wb.save(filepath)
                logger.info(
                    "Overwritten in-place: %s",
                    os.path.basename(filepath),
                )
            wb.close()
            del wb  # release file handle immediately
            wb = None
        except (OSError, ValueError, KeyError,
                openpyxl.utils.exceptions.InvalidFileException):
            # On ANY error, close the workbook to release the file lock
            if wb is not None:
                try:
                    wb.close()
                except OSError:
                    logger.debug("Failed to close workbook during error handling")
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
        dry_run: bool = False,
    ) -> Tuple[int, int, List[str]]:
        """Batch processing with pre-edit backup, cache, and auto-cleanup."""
        if not dry_run:
            self.backup.cleanup_old_backups(max_age_days=BACKUP_MAX_AGE_DAYS)
        total = len(filepaths)
        success = 0
        errors: List[str] = []

        for idx, fp in enumerate(filepaths):
            fname = os.path.basename(fp)
            try:
                await self.process_ledger(
                    fp, start_date=start_date, dry_run=dry_run,
                )
                success += 1
                if progress_cb:
                    progress_cb(idx + 1, total, fname, None)
            except BackupError as e:
                err_msg = f"{fname}: BACKUP FAILED — skipped ({e})"
                errors.append(err_msg)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))
            except (OSError, ValueError, KeyError,
                    openpyxl.utils.exceptions.InvalidFileException) as e:
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

        write_custom_exrate_data(
            ws, rate_data, col_specs, headers,
            all_dates, holidays_set, holidays_names_map,
        )

        wb.save(filepath)
        wb.close()
        _status(f"✓ ExRate created: {os.path.basename(filepath)}")
        return filepath
