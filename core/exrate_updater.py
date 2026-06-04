#!/usr/bin/env python3
"""
core/exrate_updater.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Workbook write pipeline + standalone updater
---------------------------------------------------------------------------
Two extracted units that previously lived inline in core/engine.py:

  - WorkbookWriter          → the process_ledger openpyxl write pipeline.
  - StandaloneExRateUpdater → the update_exrate_standalone method body.

LIVE-BINDING CONTRACT (mandatory): both classes store ONLY the live engine
object (``self._engine = engine``) and dereference every engine attribute
(backup, cache, api, _preload_api_data, _parse_date, _check_memory_guardrail,
_emit, target_cols) AT CALL TIME inside write()/run(). Construction must never
snapshot bound methods or attributes, so post-construction reassignment of
``engine.backup`` / ``engine._preload_api_data`` (done in tests) is honored.
"""

import contextlib
import gc
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl

from core.constants import BACKUP_MAX_AGE_DAYS, MIN_DISK_SPACE_MB, bot_today
from core.excel_io import (
    build_exrate_index,
    inject_xlookup_formulas,
    scan_sheet_headers,
    write_custom_exrate_data,
)
from core.exrate_sheet import update_master_exrate_sheet
from core.logic import (
    build_holiday_lookup,
    compute_year_start_date,
    safe_to_decimal,
)
from core.workbook_io import atomic_save as _atomic_save
from core.workbook_io import ensure_disk_space

logger = logging.getLogger(__name__)


class WorkbookWriter:
    """openpyxl write pipeline for process_ledger (extracted verbatim).

    Reads the live engine (target_cols, _parse_date, _emit) at call time.
    """

    def __init__(self, engine) -> None:
        self._engine = engine

    async def write(
        self,
        filepath: str,
        dry_run: bool,
        usd_buying,
        usd_selling,
        eur_buying,
        eur_selling,
        master_holidays_set,
        holidays_names,
        computed_start,
    ) -> str:
        try:
            wb = openpyxl.load_workbook(filepath)
        except (OSError, openpyxl.utils.exceptions.InvalidFileException):
            raise

        # try/finally guarantees the OS file handle is released and gc runs on
        # BOTH the success and error exits (the standalone paths do the same).
        # The previous except-and-reraise structure left the trailing
        # gc.collect() unreachable on the error path.
        try:
            # Scan monthly tabs for header/column mappings
            sheet_maps = scan_sheet_headers(wb, self._engine.target_cols)

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

            # Load user's rate type preference from settings
            from core.config_manager import SettingsManager
            _settings = SettingsManager()
            rate_type_setting = _settings.load().get(
                "rate_type", "buying_transfer"
            )

            inject_xlookup_formulas(
                wb, sheet_maps, exrate_last_row,
                parse_date_fn=self._engine._parse_date,
                emit_fn=self._engine._emit,
                dry_run=dry_run,
                rate_type=rate_type_setting,
            )

            # ── Save ─────────────────────────────────────────────────────
            if dry_run:
                self._engine._emit(
                    "[SIM] File NOT saved (dry run) "
                    f"— {Path(filepath).name}"
                )
            else:
                # ERR-03: Check disk space before saving
                ensure_disk_space(Path(filepath).parent, MIN_DISK_SPACE_MB)
                _atomic_save(wb, filepath)
                logger.info(
                    "Overwritten in-place: %s",
                    Path(filepath).name,
                )
        finally:
            # Release the file handle + reclaim memory on EVERY exit.
            try:
                wb.close()
            except OSError:
                logger.debug("Failed to close workbook (ledger write path)")
            del wb  # release file handle immediately
            gc.collect()

        self._engine._emit("File saved and memory cleaned", etype="success")
        return filepath


class StandaloneExRateUpdater:
    """Standalone ExRate update path (extracted verbatim).

    Reads the live engine (backup, cache, api, _preload_api_data, _parse_date,
    _check_memory_guardrail, _emit) at call time.
    """

    def __init__(self, engine) -> None:
        self._engine = engine

    async def run(
        self,
        filepath: str,
        progress_cb,
        currencies,
        rate_types,
        date_range,
    ) -> str:
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
            self._engine._emit(msg)

        # ── Fail-safe preconditions (mirror process_ledger) ───────────
        # Every in-place overwrite path must enforce the size guardrail and
        # create a pre-edit backup BEFORE load_workbook. If the file does not
        # yet exist this is a creation path — skip backup, but the loaded
        # save below would fail anyway, so a missing file is still an error.
        # noqa: PTH100 — keep os.path.abspath (no symlink resolution) so the
        # in-place save target string stays identical to the legacy behavior.
        filepath = os.path.abspath(filepath)  # noqa: PTH100
        self._engine._check_memory_guardrail(filepath)
        _status("Size check passed")
        if Path(filepath).exists():
            self._engine.backup.create_backup(filepath)
            _status("Backup created")
            # Prune stale backups on the standalone path too — the engine only
            # runs the 7-day cleanup inside process_batch, so without this the
            # standalone updater would never reclaim old backup disk space.
            self._engine.backup.cleanup_old_backups(
                max_age_days=BACKUP_MAX_AGE_DAYS
            )

        _status("Opening ExRate file...")
        wb = openpyxl.load_workbook(filepath)

        if "ExRate" not in wb.sheetnames:
            wb.close()
            del wb
            gc.collect()
            raise ValueError("No ExRate sheet found in the selected file.")

        # ── Standard path (backward compatible) ───────────────────────
        if is_standard:
            from core.exrate_sheet import update_master_exrate_sheet

            # try/finally guarantees the OS file handle is released and gc
            # runs even if API fetch / write / save raises (mirrors
            # process_ledger). Otherwise an exception would leak the handle.
            try:
                ws_ex = wb["ExRate"]
                existing_dates = set()
                for row_idx in range(2, (ws_ex.max_row or 1) + 1):
                    cell_val = ws_ex.cell(row=row_idx, column=1).value
                    parsed = self._engine._parse_date(cell_val)
                    if parsed:
                        existing_dates.add(parsed)

                target_year = min(existing_dates).year if existing_dates else bot_today().year

                # Override with manual date_range if provided
                if date_range:
                    dr_start, dr_end = date_range
                    target_year = dr_start.year
                    all_target_dates = {dr_start, dr_end, bot_today()}
                    start_date_str = f"{dr_start.year - 1}-12-20"
                else:
                    dr_start = dr_end = None
                    start_date_str = f"{target_year - 1}-12-20"
                    all_target_dates = existing_dates | {bot_today()}

                _status("Fetching exchange rates from BOT API...")
                (
                    logic_engine, usd_selling, eur_selling,
                    usd_buying, eur_buying, _usd_data, _eur_data,
                ) = await self._engine._preload_api_data(
                    all_target_dates, start_date_str
                )

                computed_start = compute_year_start_date(
                    target_year, logic_engine.holidays
                )

                master_holidays_set, holidays_names = build_holiday_lookup(
                    self._engine.cache, all_target_dates, computed_start, logic_engine,
                )

                _status("Writing exchange rate data...")
                # Manual range → honor the user's exact (dr_start, dr_end).
                # No range → prior-year-December computed_start, end defaults
                # to today() inside update_master_exrate_sheet.
                sheet_start = dr_start if dr_start is not None else computed_start
                update_master_exrate_sheet(
                    wb, usd_buying, usd_selling, eur_buying, eur_selling,
                    sorted(master_holidays_set), holidays_names,
                    sheet_start, end_date=dr_end,
                )
                # ERR-03: Check disk space before saving
                ensure_disk_space(Path(filepath).parent, MIN_DISK_SPACE_MB)
                _atomic_save(wb, filepath)
                _status(f"✓ ExRate updated: {Path(filepath).name}")
                return filepath
            finally:
                try:
                    wb.close()
                except OSError:
                    logger.debug("Failed to close workbook (standard path)")
                del wb
                gc.collect()

        # ── Custom path (any currencies / rate types) ─────────────────
        # try/finally guarantees handle release + gc on any error below.
        try:
            _status(f"Fetching rates for {', '.join(currencies)}...")

            # Fetch from API for each currency
            if date_range:
                start_dt, end_dt = date_range
            else:
                target_year = bot_today().year
                start_dt = date(target_year - 1, 12, 20)
                end_dt = bot_today()

            # rate_data[currency][api_field][date] = value
            rate_data: dict[str, dict[str, dict[date, float]]] = {}

            for ccy in currencies:
                _status(f"Fetching {ccy} rates...")
                raw_results = await self._engine.api.get_exchange_rates(
                    start_dt, end_dt, ccy,
                )
                rate_data[ccy] = {}
                for _label, api_field in rate_types.items():
                    rate_data[ccy][api_field] = {}

                for rec in raw_results:
                    try:
                        rec_date = datetime.strptime(
                            rec.period, "%Y-%m-%d"
                        ).date()
                    except (ValueError, TypeError):
                        continue
                    for _label, api_field in rate_types.items():
                        val = getattr(rec, api_field, None)
                        if val is not None:
                            # Mathematical Truth: quantize to 4dp Decimal, same
                            # discipline as the standard USD/EUR path — never
                            # write the raw API float.
                            rate_data[ccy][api_field][rec_date] = safe_to_decimal(val)

            # Fetch holidays
            _status("Fetching holidays...")
            all_target_dates = {bot_today()}
            (
                logic_engine, _, _, _, _, _, _,
            ) = await self._engine._preload_api_data(all_target_dates, str(start_dt))

            holidays_set = set(logic_engine.holidays)
            holidays_names_map: dict[date, str] = {}
            for year in {start_dt.year, end_dt.year}:
                for h_str, h_name in self._engine.cache.get_holidays(year):
                    with contextlib.suppress(ValueError, TypeError):
                        holidays_names_map[
                            datetime.strptime(h_str, "%Y-%m-%d").date()
                        ] = h_name

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

            # ── Write to sheet ────────────────────────────────────────
            ws = wb["ExRate"]
            _status("Writing custom ExRate data...")

            write_custom_exrate_data(
                ws, rate_data, col_specs, headers,
                all_dates, holidays_set, holidays_names_map,
            )

            # ERR-03: Check disk space before saving
            ensure_disk_space(Path(filepath).parent, MIN_DISK_SPACE_MB)
            _atomic_save(wb, filepath)
            _status(f"✓ ExRate created: {Path(filepath).name}")
            return filepath
        finally:
            try:
                wb.close()
            except OSError:
                logger.debug("Failed to close workbook (custom path)")
            del wb
            gc.collect()
