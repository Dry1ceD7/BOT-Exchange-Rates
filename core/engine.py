#!/usr/bin/env python3
"""
core/engine.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Cache-First Orchestrator
---------------------------------------------------------------------------
Slim orchestrator. Heavy logic extracted to:
  - core/excel_io.py -> Excel I/O operations (formulas, indexing, writing)
  - core/exrate_sheet.py -> Master ExRate sheet builder
  - core/prescan.py -> Smart date pre-scanner
"""

import asyncio
import json
import logging
import os
import threading
import traceback
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import openpyxl

from core.anomaly_guard import AnomalyGuard
from core.api_client import BOTAPIError, BOTClient
from core.audit_logger import AuditCollector, AuditLogger, cleanup_old_audit_logs
from core.backup_manager import BackupError, BackupManager
from core.config_manager import SettingsManager
from core.constants import (
    BACKUP_MAX_AGE_DAYS,
    DEFAULT_ANOMALY_THRESHOLD_PCT,
    MAX_FILE_SIZE_MB,
    bot_today,
    humanize_save_error,
    parse_date,
)
from core.database import CacheDB, get_cache
from core.exrate_updater import StandaloneExRateUpdater, WorkbookWriter
from core.ledger_processing import (
    classify_currencies,
    prescan_target_dates,
    prescan_target_dates_and_currencies,
    run_anomaly_check,
)
from core.logic import (
    BOTLogicEngine,
    build_holiday_lookup,
    compute_year_start_date,
    default_fetch_window_start,
    safe_to_decimal,
    weekdays_between,
)
from core.paths import get_project_root
from core.prescan import prescan_oldest_date
from core.workbook_io import atomic_write_text, is_standalone_exrate_workbook

logger = logging.getLogger(__name__)

# Schema version for the resume manifest. Bumped if the on-disk shape changes
# so a stale manifest from an incompatible build is ignored rather than
# misread. Only paths/dates/flags are ever persisted — NEVER rates or tokens.
BATCH_MANIFEST_VERSION = 1

# Backward-compat re-export: pure functions now live in core.logic but are
# still importable from core.engine (e.g. `from core.engine import
# compute_year_start_date`).
__all__ = [
    "BatchManifest",
    "FileSizeLimitError",
    "LedgerEngine",
    "build_holiday_lookup",
    "compute_year_start_date",
]

# -------------------------------------------------------------------------
# EXCEPTIONS
# -------------------------------------------------------------------------


class FileSizeLimitError(Exception):
    """Raised when the input workbook exceeds the configured size limit."""


# -------------------------------------------------------------------------
# CRASH-RECOVERY / RESUME MANIFEST
# -------------------------------------------------------------------------


class BatchManifest:
    """Featherweight crash-recovery manifest for an in-flight batch.

    Persists ``data/batch_state.json`` so an interrupted run (app crash, power
    loss, OS kill) can be resumed instead of forcing the operator to rebuild
    the whole selection. Written ONCE at batch start, then updated per completed
    file, and deleted on a clean finish AND on a user cancellation (a cancel is
    intentional, not a crash — there is nothing to recover).

    Privacy/featherweight contract: the manifest stores ONLY the file paths, the
    resolved start date, and the run flags (dry_run). It never holds exchange
    rates, holiday data, or API tokens — so a leftover file is harmless. Writes
    are atomic (temp + ``os.replace`` in the same dir, the round-7 save idiom)
    so a crash mid-write can never corrupt a previously-good manifest.
    """

    FILENAME = "batch_state.json"

    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = Path(get_project_root()) / "data" / self.FILENAME
        self.path = Path(path)

    # ── Write helpers ────────────────────────────────────────────────
    def begin(
        self,
        filepaths: list[str],
        start_date: str | None,
        dry_run: bool,
    ) -> None:
        """Write the initial manifest at batch start (every file pending)."""
        self._write({
            "version": BATCH_MANIFEST_VERSION,
            "start_date": start_date,
            "dry_run": bool(dry_run),
            "files": [{"path": fp, "done": False} for fp in filepaths],
        })

    def mark_done(self, filepath: str) -> None:
        """Flag ``filepath`` complete and re-persist (best-effort).

        A failure to update the manifest must NEVER abort the batch — at worst a
        resume re-processes an already-done file, which is safe (a fresh backup
        is taken and the same Decimal values are re-written). So persistence
        errors are swallowed with a debug log.
        """
        data = self._read_raw()
        if data is None:
            return
        for entry in data.get("files", []):
            if entry.get("path") == filepath:
                entry["done"] = True
                break
        try:
            self._write(data)
        except OSError as exc:
            logger.debug("batch manifest update failed (non-fatal): %s", exc)

    def clear(self) -> None:
        """Delete the manifest (clean completion OR intentional cancel)."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.debug("batch manifest delete failed (non-fatal): %s", exc)

    # ── Read helpers (used by GUI + CLI resume) ──────────────────────
    def _read_raw(self) -> dict | None:
        """Return the raw manifest dict, or None if absent/unreadable/stale."""
        try:
            with self.path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.debug("batch manifest read failed: %s", exc)
            return None
        if not isinstance(data, dict):
            return None
        if data.get("version") != BATCH_MANIFEST_VERSION:
            logger.debug("ignoring batch manifest with unknown version")
            return None
        return data

    def pending_files(self) -> list[str]:
        """Return paths not yet marked done (the work a resume would pick up).

        Only files that still EXIST on disk are returned — a path that was
        moved/deleted since the crash is skipped so a resume never chokes on a
        stale entry.
        """
        data = self._read_raw()
        if data is None:
            return []
        out: list[str] = []
        for entry in data.get("files", []):
            if entry.get("done"):
                continue
            fp = entry.get("path")
            if isinstance(fp, str) and Path(fp).is_file():
                out.append(fp)
        return out

    def start_date(self) -> str | None:
        """Return the persisted start date (None if absent/unreadable)."""
        data = self._read_raw()
        if data is None:
            return None
        sd = data.get("start_date")
        return sd if isinstance(sd, str) else None

    def has_pending(self) -> bool:
        """True when a resumable manifest with unfinished files exists."""
        return bool(self.pending_files())

    # ── Atomic write (round-7 temp + os.replace idiom) ───────────────
    def _write(self, data: dict) -> None:
        """Atomically (over)write the manifest JSON.

        Delegates to ``core.workbook_io.atomic_write_text`` (single owner of
        the temp + replace idiom): a crash mid-write leaves the previous good
        manifest untouched and the partial temp file is never left behind.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path, json.dumps(data, ensure_ascii=False, indent=2)
        )


# -------------------------------------------------------------------------
# MODULE-LEVEL SINGLETONS (persist across batch clicks)
# -------------------------------------------------------------------------
_backup_singleton = None
_singleton_lock = threading.Lock()


def _get_backup() -> BackupManager:
    global _backup_singleton
    if _backup_singleton is None:
        with _singleton_lock:
            if _backup_singleton is None:  # double-check after lock
                _backup_singleton = BackupManager()
    return _backup_singleton


def _get_cache() -> CacheDB:
    """Delegate to the canonical process-wide accessor (F36).

    ``core.database.get_cache()`` owns the single ``CacheDB`` for the whole
    process; the engine no longer keeps a second private singleton, so the
    engine and any GUI callers share one instance (one WAL connection pool,
    one atexit close — registered by ``CacheDB.__init__`` via weakref).
    """
    return get_cache()



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
        backup: BackupManager | None = None,
        cache: CacheDB | None = None,
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
        # ── Settings-snapshot contract (state-conflicts) ────────────────
        # ALL engine settings are snapshotted ONCE here, at engine
        # construction. A LedgerEngine is built fresh per batch (handlers.py,
        # main.py, exrate_dialog.py), so "engine construction" == "batch
        # start". This guarantees a mid-batch Settings "Save" can never change
        # behavior for the in-flight run: every file in the batch sees the same
        # rate_type AND the same anomaly threshold. Previously the threshold was
        # snapshotted here but rate_type was re-read per file inside
        # process_ledger, so the two settings behaved oppositely in one run.
        _settings = SettingsManager().load()
        threshold = _settings.get(
            "anomaly_threshold_pct", DEFAULT_ANOMALY_THRESHOLD_PCT
        )
        self._anomaly_guard = AnomalyGuard(threshold_pct=threshold)
        self._rate_type = _settings.get("rate_type", "buying_transfer")
        # Only Buying TT and Selling are fetched/stored for USD/EUR (the ExRate
        # master sheet has just those columns). Any other persisted value — e.g.
        # a legacy "buying_sight"/"mid_rate" from an older build — is normalized
        # to buying_transfer here so it can never silently resolve to the wrong
        # column downstream in inject_xlookup_formulas.
        if self._rate_type not in ("buying_transfer", "selling"):
            logger.warning(
                "Unsupported ledger rate_type %r; using 'buying_transfer'. "
                "Only 'buying_transfer' and 'selling' are available.",
                self._rate_type,
            )
            self._rate_type = "buying_transfer"
        self._last_anomaly_count = 0
        self._last_batch_anomaly_count = 0
        # Path to the audit CSV written by the most recent process_batch run
        # (None for dry runs / when an external caller owns the audit log).
        self.last_audit_path: str | None = None

    def _emit(self, msg: str, etype: str = "log") -> None:
        """Push event to EventBus if one is attached."""
        if self._bus is not None:
            self._bus.push({"type": etype, "msg": msg})

    @property
    def last_anomaly_count(self) -> int:
        """Return anomaly count from the most recent file run."""
        return self._last_anomaly_count

    @property
    def last_batch_anomaly_count(self) -> int:
        """Return anomaly count from the most recent batch run."""
        return self._last_batch_anomaly_count

    def _check_memory_guardrail(self, filepath: str):
        fp = Path(filepath)
        if not fp.exists():
            raise FileNotFoundError(f"Cannot find: {filepath}")
        file_size = fp.stat().st_size
        if file_size > self.MAX_FILE_BYTES:
            raise FileSizeLimitError(
                f"File too large (> {self.MAX_FILE_SIZE_MB}MB)."
            )

    @classmethod
    def preflight_file(cls, filepath: str) -> dict:
        """Cheap, side-effect-free pre-flight check for one selected file.

        Designed for the GUI to call at *selection* time (drop / browse) so an
        oversized, unsupported, missing, or locked file is flagged immediately
        instead of only failing mid-run after the API fetch + backup. Does NOT
        load the workbook, hit the network, or write anything — just stats the
        path, checks the extension, and (if the file exists) probes whether it
        is writable in place.

        Returns a dict::

            {
              "name": str,        # basename for display
              "ok": bool,         # True when the file is safe to process
              "exists": bool,
              "size_ok": bool,    # within MAX_FILE_SIZE_MB (False if missing)
              "size_mb": float,   # actual size in MB (0.0 if missing)
              "supported": bool,  # extension is .xlsx / .xlsm
              "writable": bool,   # in-place save would not hit a lock
              "reason": str | None,  # human message when not ok, else None
            }

        ``reason`` reuses the round-7 ``humanize_save_error`` wording for the
        locked-file case so selection-time and run-time messages match.
        """
        fp = Path(filepath)
        name = fp.name
        exists = fp.exists()
        supported = filepath.lower().endswith((".xlsx", ".xlsm"))

        size_mb = 0.0
        size_ok = False
        writable = False
        if exists and fp.is_file():
            size_bytes = fp.stat().st_size
            size_mb = round(size_bytes / (1024 * 1024), 2)
            size_ok = size_bytes <= cls.MAX_FILE_BYTES
            writable = cls._probe_writable(fp)

        reason: str | None = None
        if not exists:
            reason = f"{name}: File not found."
        elif not fp.is_file():
            reason = f"{name}: Not a file."
        elif not supported:
            reason = (
                f"{name}: Unsupported format. Only .xlsx and .xlsm files "
                "are supported."
            )
        elif not size_ok:
            reason = (
                f"{name}: File too large ({size_mb}MB > "
                f"{cls.MAX_FILE_SIZE_MB}MB limit)."
            )
        elif not writable:
            reason = (
                f"{name}: File is open in Excel or another program. "
                "Please close it and process again."
            )

        return {
            "name": name,
            "ok": reason is None,
            "exists": exists,
            "size_ok": size_ok,
            "size_mb": size_mb,
            "supported": supported,
            "writable": writable,
            "reason": reason,
        }

    @staticmethod
    def _probe_writable(fp: Path) -> bool:
        """Return False if the file cannot be opened for in-place writing.

        Opens with append-binary ("ab") — this acquires a write handle WITHOUT
        truncating any content, so it is safe to run before the real save. A
        file held open by Excel (Windows sharing violation / WinError 32) or
        without write permission (EACCES) raises and we report it as not
        writable. Any unexpected error is treated as "probe inconclusive" ->
        writable, so the real save path remains the authoritative guard.
        """
        try:
            with fp.open("ab"):
                pass
        except PermissionError:
            return False
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            return not (winerror == 32 or exc.errno in (13, 16))
        return True

    def _parse_date(self, cell_value) -> date | None:
        """Parse a date from a cell value (shared parser, full superset)."""
        return parse_date(cell_value)

    # ── Static delegates (kept for backward compat) ──────────────────
    @staticmethod
    def prescan_oldest_date(
        filepaths: list[str],
        target_col_name: str = "Date",
    ) -> tuple[date, bool]:
        """Delegate to core.prescan module."""
        return prescan_oldest_date(filepaths, target_col_name)

    @staticmethod
    def compute_year_start_date(
        target_year: int,
        holidays: list[date],
    ) -> date:
        """Backward-compat delegate to core.logic.compute_year_start_date."""
        return compute_year_start_date(target_year, holidays)


    # ================================================================== #
    #  CACHE-FIRST DATA LOADING (v2.6.1)
    # ================================================================== #
    async def _preload_api_data(
        self, dates: set[date], start_date: str,
        *, extend_to_today: bool = True,
    ) -> tuple:
        """
        Cache-First Architecture: SQLite -> API fallback -> cache store.
        Returns (logic_engine, usd_selling, eur_selling,
                 usd_buying, eur_buying, usd_data, eur_data).

        With ``extend_to_today=False`` the fetch window is bounded by the
        caller's dates alone (plus ``start_date``) instead of stretching to
        the current BOT business date — used by the rate audit so verifying
        an old archived workbook does not pull years of unrelated rates.
        """
        try:
            force_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            force_start = date(2025, 1, 1)

        all_d = set(dates) | {force_start}
        if extend_to_today:
            all_d.add(bot_today())
        min_date, max_date = min(all_d), max(all_d)
        years = {d.year for d in all_d}

        # ── HOLIDAYS: Cache-first ────────────────────────────────────
        holidays_list = []
        years_to_fetch = []
        for year in years:
            if self.cache.has_holidays_for_year(year):
                cached_hols = self.cache.get_holidays(year)
                for h_date, _h_name in cached_hols:
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
        usd_buying: dict[date, Decimal] = {}
        usd_selling: dict[date, Decimal] = {}
        eur_buying: dict[date, Decimal] = {}
        eur_selling: dict[date, Decimal] = {}

        for d, rate_dict in cached_rates.items():
            if rate_dict["usd_buying"] is not None:
                usd_buying[d] = rate_dict["usd_buying"]
            if rate_dict["usd_selling"] is not None:
                usd_selling[d] = rate_dict["usd_selling"]
            if rate_dict["eur_buying"] is not None:
                eur_buying[d] = rate_dict["eur_buying"]
            if rate_dict["eur_selling"] is not None:
                eur_selling[d] = rate_dict["eur_selling"]

        all_needed = weekdays_between(min_date, max_date)

        # Per-COLUMN cache miss: a cached row missing any of the four rate
        # columns (e.g. nulled by a partial write from an older version) must
        # count as missing so the API refetch self-heals it. Per-date
        # membership alone would never refetch such a date and its trading-day
        # cells stayed blank. The upsert in insert_rates_bulk fills only the
        # NULL columns, so the surviving currency's values are kept.
        # Weekday HOLIDAYS are excluded: BOT publishes nothing for them, so
        # counting them as misses made every file of every batch re-hit the
        # API forever (~15+ Thai weekday holidays per year window) and broke
        # the offline CSV path — the unavoidable holiday-date fetch raised on
        # machines with no network even though every needed rate was cached.
        _required_cols = (
            "usd_buying", "usd_selling", "eur_buying", "eur_selling",
        )
        holiday_set = set(holidays_list)
        missing_dates = {
            d for d in all_needed
            if d not in holiday_set and (
                d not in cached_rates or any(
                    cached_rates[d][col] is None for col in _required_cols
                )
            )
        }
        usd_data, eur_data = [], []
        if missing_dates:
            # ── Narrowed fetch range: only fetch the missing window ───
            fetch_start = min(missing_dates)
            fetch_end = max(missing_dates)
            self._emit(
                f"Cache miss: {len(missing_dates)} dates "
                f"({fetch_start.strftime('%Y-%m-%d')} to "
                f"{fetch_end.strftime('%Y-%m-%d')}). Calling API",
            )
            logger.info(
                "Cache miss: %d dates missing (%s -> %s). Fetching from API...",
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

            # Exactness gate: cache the quantized 4dp Decimal as a string
            # (the rates table has TEXT affinity) — NEVER the raw API value,
            # so a cache hit replays exactly what the writer was given.
            rate_cache: dict[str, list[str | None]] = {}
            for r in usd_data:
                # Same skip-on-unparseable guard as the holiday ingest above:
                # one malformed BOT period must not abort the whole preload.
                try:
                    d = datetime.strptime(r.period, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    logger.warning("Skipped unparseable USD rate period: %r", r.period)
                    continue
                buy = safe_to_decimal(r.buying_transfer)
                sell = safe_to_decimal(r.selling)
                if buy is not None:
                    usd_buying[d] = buy
                if sell is not None:
                    usd_selling[d] = sell
                rate_cache.setdefault(r.period, [None] * 4)
                rate_cache[r.period][0] = None if buy is None else str(buy)
                rate_cache[r.period][1] = None if sell is None else str(sell)
            for r in eur_data:
                try:
                    d = datetime.strptime(r.period, "%Y-%m-%d").date()
                except (ValueError, TypeError):
                    logger.warning("Skipped unparseable EUR rate period: %r", r.period)
                    continue
                buy = safe_to_decimal(r.buying_transfer)
                sell = safe_to_decimal(r.selling)
                if buy is not None:
                    eur_buying[d] = buy
                if sell is not None:
                    eur_selling[d] = sell
                rate_cache.setdefault(r.period, [None] * 4)
                rate_cache[r.period][2] = None if buy is None else str(buy)
                rate_cache[r.period][3] = None if sell is None else str(sell)
            bulk = [
                (d_str, v[0], v[1], v[2], v[3])
                for d_str, v in rate_cache.items()
            ]
            self.cache.insert_rates_bulk(bulk)
            self._emit(
                f"API fetch done: {len(usd_data)} USD + "
                f"{len(eur_data)} EUR records cached",
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
        usd_buying: dict[date, Decimal],
        usd_selling: dict[date, Decimal],
        eur_buying: dict[date, Decimal],
        eur_selling: dict[date, Decimal],
        extra_currency_rates: dict[str, dict[date, Decimal]] | None = None,
    ) -> tuple[int, set[tuple[str, date]]]:
        """Delegate to core.ledger_processing.run_anomaly_check (v3.1.0).

        Injects this engine's anomaly guard and emit callback. The extra
        (non-USD/EUR) ledger currencies join the check under the engine's
        snapshotted rate type (F42). Alert-only: the result never blocks,
        skips, or substitutes a write.

        Returns:
            ``(anomaly_count, anomalous)`` where ``anomalous`` is the set of
            flagged ``(currency, date)`` pairs, threaded into the audit
            trail so matching rows carry Anomaly_Flag (F25).
        """
        anomalous: set[tuple[str, date]] = set()
        count = run_anomaly_check(
            self._anomaly_guard,
            lambda msg, etype="log": self._emit(msg, etype),
            usd_buying, usd_selling, eur_buying, eur_selling,
            extra_currency_rates=extra_currency_rates,
            extra_rate_type=self._rate_type,
            anomalous_out=anomalous,
        )
        return count, anomalous

    # ================================================================== #
    #  PRIVATE HELPERS — Extracted from process_ledger for readability
    # ================================================================== #

    async def _detect_standalone_exrate(
        self, filepath: str,
    ) -> str | None:
        """Detect if the file is a standalone ExRate workbook (no month tabs).

        Returns the result of update_exrate_standalone() if standalone,
        or None if the file should be processed normally. Also validates
        that the file format is supported (.xlsx or .xlsm).

        The read-only probe is the shared
        ``core.workbook_io.is_standalone_exrate_workbook`` (single owner —
        main.py's headless labeller uses the same helper); probe failures
        return False inside it. The except below keeps the legacy contract
        that a failure of these types INSIDE update_exrate_standalone falls
        back to normal ledger processing rather than aborting the file.
        """
        try:
            if is_standalone_exrate_workbook(
                filepath,
                date_header=self.target_cols["source_date"],
                currency_header=self.target_cols["currency"],
            ):
                self._emit("Standalone ExRate file detected — updating rates")
                return await self.update_exrate_standalone(filepath)
        except (ValueError, TypeError, KeyError,
                openpyxl.utils.exceptions.InvalidFileException) as exc:
            logger.debug("Standalone detection probe failed: %s", exc)

        # Reject unsupported formats
        if not filepath.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError(
                f"Unsupported format: {Path(filepath).name}. "
                "Only .xlsx and .xlsm files are supported."
            )

        return None  # Not standalone — proceed with normal processing

    def _prescan_target_dates(self, filepath: str) -> set[date]:
        """Delegate to core.ledger_processing.prescan_target_dates.

        Injects this engine's column map, parser, and emit callback so the
        read-only date scan keeps identical behavior.
        """
        return prescan_target_dates(
            filepath,
            self.target_cols,
            parse_date_fn=self._parse_date,
            emit_fn=self._emit,
        )

    async def _fetch_extra_currency_rates(
        self,
        extra_currencies: list[str],
        api_field: str,
        start_dt: date,
        end_dt: date,
    ) -> dict[str, dict[date, Decimal]]:
        """Fetch the selected rate type for non-USD/EUR ledger currencies.

        Cache-first (mirrors the USD/EUR path in ``_preload_api_data``):
        1. Read ``rates_multi`` for each currency — covers CSV-imported and
           previously cached rates so the offline/air-gapped path works.
        2. Compute which weekday dates in the window are absent from the cache.
        3. Call the BOT API only for the missing window; API data wins for any
           date it returns (same precedence as the USD/EUR path — fresh API
           data supersedes stale cache).
        4. Store fresh API hits back into ``rates_multi`` for future runs.

        Returns ``{ccy: {date: Decimal}}`` quantized to 4dp (Mathematical
        Truth — never the raw API float). Featherweight: sequential per-
        currency fetch, no extra workbook loads.
        """
        out: dict[str, dict[date, Decimal]] = {}
        # Known holidays are non-publishing days — exclude them from the
        # miss set (same contract as the USD/EUR path) so cached extras
        # don't trigger a perpetual API refetch over holiday gaps.
        holiday_set: set[date] = set()
        for yr in range(start_dt.year, end_dt.year + 1):
            for date_str, _desc in self.cache.get_holidays(yr):
                try:
                    holiday_set.add(
                        datetime.strptime(date_str, "%Y-%m-%d").date()
                    )
                except (ValueError, TypeError):
                    continue
        for ccy in extra_currencies:
            # ── Step 1: seed from cache (rates_multi) ────────────────
            by_date: dict[date, Decimal] = self.cache.get_rates_multi(
                start_dt, end_dt, ccy, api_field
            )

            # ── Step 2: find weekday dates missing from cache ─────────
            all_weekdays: set[date] = weekdays_between(start_dt, end_dt)
            missing_dates = all_weekdays - set(by_date.keys()) - holiday_set

            # ── Step 3: API fetch for misses only ─────────────────────
            if missing_dates:
                fetch_start = min(missing_dates)
                fetch_end = max(missing_dates)
                self._emit(f"Fetching {ccy} rates ({fetch_start} to {fetch_end})")
                records = await self.api.get_exchange_rates(
                    fetch_start, fetch_end, ccy
                )
                bulk_entries: list[tuple] = []
                for rec in records:
                    try:
                        rec_date = datetime.strptime(
                            rec.period, "%Y-%m-%d"
                        ).date()
                    except (ValueError, TypeError):
                        continue
                    val = getattr(rec, api_field, None)
                    dec = safe_to_decimal(val)
                    if dec is not None:
                        # API wins — overwrite any cache value for this date.
                        by_date[rec_date] = dec
                        bulk_entries.append(
                            (rec.period, ccy, api_field, str(dec))
                        )
                if bulk_entries:
                    self.cache.insert_multi_rates_bulk(bulk_entries)
            else:
                self._emit(f"{ccy} rates served from cache", )

            out[ccy] = by_date
        return out

    def _build_holiday_lookup(
        self,
        all_target_dates: set[date],
        computed_start: date,
        logic_engine,
    ) -> tuple[set[date], dict[date, str]]:
        """Backward-compat delegate to core.logic.build_holiday_lookup.

        Injects this engine's cache so existing instance callers keep
        working unchanged.
        """
        return build_holiday_lookup(
            self.cache, all_target_dates, computed_start, logic_engine,
        )

    # ================================================================== #
    #  PROCESS SINGLE LEDGER
    # ================================================================== #
    async def process_ledger(
        self,
        filepath: str,
        start_date: str | None = None,
        dry_run: bool = False,
        audit: AuditCollector | None = None,
    ) -> str:
        """Process a single ledger file end-to-end.

        ``audit`` (an AuditCollector) receives one record per EX Rate cell that
        resolves to a rate, so the batch-level audit CSV captures every cell
        mutation. It is threaded into the WorkbookWriter and ignored on dry runs
        (no file is written, so there is nothing to audit).
        """

        # noqa: PTH100 — os.path.abspath normalizes WITHOUT resolving symlinks;
        # Path.resolve() would resolve symlinks and could change the in-place
        # save target string. Keep exact legacy behavior.
        filepath = os.path.abspath(filepath)  # noqa: PTH100
        self._last_anomaly_count = 0

        self._check_memory_guardrail(filepath)
        self._emit("Size check passed")

        # ── Standalone ExRate detection + format validation ─────────────
        # Detect BEFORE taking our own backup: the standalone path makes its
        # own pre-edit backup (StandaloneExRateUpdater.run), so backing up here
        # first would duplicate the identical pristine file. Delegate and skip
        # process_ledger's own backup when this is a standalone file.
        standalone_result = await self._detect_standalone_exrate(filepath)
        if standalone_result is not None:
            self._last_anomaly_count = 0
            return standalone_result

        if dry_run:
            self._emit("[SIM] Backup skipped (dry run)")
        else:
            # ── Pre-flight writability/lock check (fail fast) ───────────────
            # Probe that the in-place save target is writable BEFORE spending a
            # network round-trip + a backup copy. A file still open in Excel
            # raises a sharing violation here; we re-raise it as a PermissionError
            # so process_batch's OSError branch humanizes it via
            # humanize_save_error ("close it in Excel and process again") instead
            # of the user waiting through the whole API fetch only to hit the
            # same lock at the final save.
            if not self._probe_writable(Path(filepath)):
                raise PermissionError(
                    13,
                    "The process cannot access the file because it is being "
                    "used by another process",
                )
            self.backup.create_backup(filepath)
            self._emit("Backup created")

        # ── Use the batch-start rate-type snapshot ──────────────────────
        # rate_type is snapshotted ONCE in __init__ (batch start), exactly like
        # the anomaly threshold, so both settings honor the same contract: a
        # mid-batch Settings "Save" affects neither the rate basis nor the
        # anomaly threshold of the in-flight run. (Re-reading SettingsManager
        # here would have let a mid-batch save flip the rate basis for the
        # remaining files while the threshold stayed fixed — the two settings
        # behaving oppositely in one run.)
        rate_type = self._rate_type

        # ── Pre-scan dates + currencies for API data loading ──────────
        all_target_dates, ledger_currencies = (
            prescan_target_dates_and_currencies(
                filepath,
                self.target_cols,
                parse_date_fn=self._parse_date,
                emit_fn=self._emit,
            )
        )
        extra_currencies, unsupported_currencies = classify_currencies(
            ledger_currencies
        )

        # ── Date hierarchy ───────────────────────────────────────────
        target_year = (
            min(all_target_dates).year if all_target_dates
            else bot_today().year
        )
        if start_date is None:
            start_date = default_fetch_window_start(target_year).isoformat()

        self._emit("Loading exchange rates and holidays")
        (
            logic_engine, usd_selling, eur_selling,
            usd_buying, eur_buying, usd_data, eur_data,
        ) = await self._preload_api_data(all_target_dates, start_date)

        # ── Fetch any extra (non-USD/EUR) supported currencies ─────────
        # The master sheet gets one appended column per extra currency, filled
        # with the snapshotted rate type, so multi-currency ledger rows resolve
        # instead of silently leaving blank EX Rate cells.
        extra_currency_rates: dict[str, dict[date, Decimal]] = {}
        if extra_currencies:
            try:
                ec_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                ec_start = default_fetch_window_start(target_year)
            extra_currency_rates = await self._fetch_extra_currency_rates(
                extra_currencies, rate_type, ec_start, bot_today(),
            )

        # v3.1.0: Anomaly detection — check for suspicious rate jumps.
        # Extra (non-USD/EUR) currencies are included (F42); the flagged
        # (currency, date) set is threaded into the audit trail (F25).
        # Alert-only: every rate still writes unchanged.
        anomaly_count, anomalous_rates = self._run_anomaly_check(
            usd_buying, usd_selling, eur_buying, eur_selling,
            extra_currency_rates=extra_currency_rates,
        )
        self._last_anomaly_count = anomaly_count
        if anomaly_count:
            self._emit(
                f"WARNING: {anomaly_count} anomalous rate(s) detected — check audit log",
                etype="warning",
            )

        computed_start = compute_year_start_date(
            target_year, logic_engine.holidays
        )

        # ── Build holiday names lookup ───────────────────────────────
        master_holidays_set, holidays_names = build_holiday_lookup(
            self.cache, all_target_dates, computed_start, logic_engine
        )
        # ══════════════════════════════════════════════════════════════
        #  openpyxl ENGINE
        # ══════════════════════════════════════════════════════════════
        self._emit("Processing sheets with openpyxl engine")
        logger.info("Processing with openpyxl engine.")

        return await WorkbookWriter(self).write(
            filepath, dry_run,
            usd_buying, usd_selling, eur_buying, eur_selling,
            master_holidays_set, holidays_names, computed_start,
            rate_type=rate_type,
            extra_currency_rates=extra_currency_rates,
            unsupported_currencies=unsupported_currencies,
            anomalous_rates=anomalous_rates,
            audit=None if dry_run else audit,
        )

    # ================================================================== #
    #  BATCH PROCESSING
    # ================================================================== #
    async def process_batch(
        self,
        filepaths: list[str],
        start_date: str | None = None,
        progress_cb: Callable[[int, int, str, str | None], None] | None = None,
        dry_run: bool = False,
        stop_event: threading.Event | None = None,
        audit: AuditLogger | None = None,
        manifest: "BatchManifest | None" = None,
    ) -> tuple[int, int, list[str]]:
        """Batch processing with pre-edit backup, cache, audit trail, cleanup.

        ``stop_event`` (set by the GUI on shutdown) is checked BETWEEN files —
        a safe boundary after the previous file's wb.close()+gc — so a cancel
        never lands mid-save and risks truncating an in-place .xlsx. Remaining
        files are reported as unprocessed via errors + progress_cb.

        Crash-recovery / resume (``manifest``):
          * For a real (non-dry) run the engine writes a tiny JSON manifest
            (``data/batch_state.json``) at batch start listing the file paths,
            the resolved start date, and the dry-run flag — NO rates, NO tokens.
            Each file is flagged done as it completes, so an app crash / power
            loss leaves a manifest the GUI or ``--resume`` can pick up to finish
            only the unprocessed files.
          * The manifest is deleted on a CLEAN completion AND on a user
            cancellation (a cancel via ``stop_event`` is intentional, not a
            crash — nothing to recover).
          * Dry runs never write a manifest (no files are modified). Callers can
            inject their own ``BatchManifest`` (tests / a resume that wants a
            specific path); otherwise a default one is created for real runs.

        Audit trail (compliance):
          * If a caller passes its own ``audit`` AuditLogger, the engine records
            per-cell changes into it and leaves the summary/finalize to the
            caller (the CLI in main.py owns its log this way).
          * Otherwise, for a real (non-dry) run the engine creates, summarizes,
            finalizes, and prunes its OWN audit CSV so the GUI/scheduler paths
            get an identical auditor-facing trail without any extra wiring. The
            resulting path is exposed via ``self.last_audit_path``.
        Dry runs never write an audit log (no files are modified).
        """
        if not dry_run:
            self.backup.cleanup_old_backups(max_age_days=BACKUP_MAX_AGE_DAYS)
        total = len(filepaths)
        success = 0
        anomaly_total = 0
        errors: list[str] = []

        # ── Crash-recovery manifest ──────────────────────────────────────
        # Real runs only: write the resume manifest now (every file pending) so
        # an interruption before the first wb.close() is still recoverable. Dry
        # runs modify nothing, so they never write one. A manifest write failure
        # must never block processing — degrade to "no resume" rather than abort.
        if dry_run:
            manifest = None
        elif manifest is None:
            manifest = BatchManifest()
        if manifest is not None:
            try:
                manifest.begin(filepaths, start_date, dry_run)
            except OSError as exc:
                logger.debug("batch manifest begin failed (non-fatal): %s", exc)
                manifest = None

        # ── Audit-log lifecycle ──────────────────────────────────────────
        # Own the log only when the caller did not inject one AND this is a
        # real run; record cell changes into a thread-safe collector and drain
        # them into the CSV after each file's workbook is safely closed.
        self.last_audit_path = None
        owns_audit = audit is None and not dry_run
        if owns_audit:
            audit = AuditLogger()
        collector = AuditCollector() if audit is not None else None

        cancelled = False
        for idx, fp in enumerate(filepaths):
            fname = Path(fp).name
            # ── Cooperative cancellation (safe boundary: between files) ────
            if stop_event is not None and stop_event.is_set():
                # A cancel is INTENTIONAL, not a crash — drop the resume manifest
                # once, the first time we hit the cancel boundary, so the next
                # launch does not offer to resume a batch the user deliberately
                # stopped. Remaining files are still reported as unprocessed.
                if not cancelled and manifest is not None:
                    manifest.clear()
                    manifest = None
                cancelled = True
                err_msg = f"{fname}: cancelled — not processed"
                errors.append(err_msg)
                logger.warning("Batch cancelled before file: %s", fname)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, "cancelled")
                continue
            file_ok = False
            try:
                await self.process_ledger(
                    fp, start_date=start_date, dry_run=dry_run,
                    audit=collector,
                )
                file_ok = True
                success += 1
                anomaly_total += self.last_anomaly_count
                # File is safely written + closed — flag it done in the manifest
                # so a crash on a LATER file resumes from here, not the start.
                if manifest is not None:
                    manifest.mark_done(fp)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, None)
            except BackupError as e:
                err_msg = f"{fname}: BACKUP FAILED — skipped ({e})"
                errors.append(err_msg)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))
            except FileSizeLimitError as e:
                err_msg = f"{fname}: {e!s}"
                errors.append(err_msg)
                logger.error("File SKIPPED: %s", err_msg)
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))
            except (BOTAPIError, httpx.HTTPError) as e:
                # API/network failure on THIS file (401/503/timeout/conn drop).
                # None are OSError subclasses, so without this branch the whole
                # batch would abort at file N. Record + continue with N+1.
                err_msg = f"{fname}: {e!s}"
                errors.append(err_msg)
                logger.error(
                    "File SKIPPED (API/network): %s\n%s",
                    fname, traceback.format_exc(),
                )
                if progress_cb:
                    progress_cb(idx + 1, total, fname, str(e))
            except (OSError, ValueError, KeyError,
                    zipfile.BadZipFile,
                    openpyxl.utils.exceptions.InvalidFileException) as e:
                # A file open in Excel surfaces as a raw WinError 32 / EACCES
                # string a non-technical accountant cannot act on. Translate it
                # into a clear "close it in Excel and retry" message; any other
                # OS/value error keeps its original text. BadZipFile (non-zip
                # bytes wearing .xlsx — typically a renamed legacy .xls) gets
                # the save-as-.xlsx remedy from humanize_save_error and becomes
                # a per-file skip instead of aborting the whole batch.
                friendly = humanize_save_error(fname, e)
                err_msg = friendly if friendly is not None else f"{fname}: {e!s}"
                errors.append(err_msg)
                logger.error(
                    "File SKIPPED: %s\n%s",
                    fname, traceback.format_exc(),
                )
                if progress_cb:
                    progress_cb(idx + 1, total, fname, err_msg)
            finally:
                # Drain on EVERY terminal path, not just success. WorkbookWriter
                # adds per-cell AuditRecords (STEP 5) BEFORE the atomic save, so a
                # file that fails during/after save (disk-full, locked, etc.) has
                # already pushed records into the collector. Flush them into the
                # CSV only when the file was actually written; otherwise drain and
                # discard so phantom records can't leak into the next file's flush.
                if collector is not None:
                    drained = collector.drain()
                    if file_ok:
                        audit.log_records(drained)

        self._last_batch_anomaly_count = anomaly_total

        # ── Clear the resume manifest on a CLEAN completion ──────────────
        # The loop ran to its natural end (not cancelled mid-way), so there is
        # no crash to recover from — drop the manifest. Files that FAILED this
        # run are intentionally not resumed: a real run already reported them as
        # errors for the operator to fix and re-select, and silently re-running a
        # no-rate / oversized file every launch would be a worse experience than
        # a clean failure report. (Cancellation already cleared + nulled the
        # manifest inside the loop, so this only fires on a non-cancelled run.)
        if not cancelled and manifest is not None:
            manifest.clear()

        # ── Finalize the engine-owned audit log ─────────────────────────
        # A caller-supplied audit log is left open for the caller to summarize
        # and finalize (it may aggregate more than this batch). When the engine
        # owns the log it writes the summary, finalizes, prunes stale logs, and
        # publishes the path so the GUI can surface it.
        if owns_audit and audit is not None:
            try:
                audit.log_batch_summary(
                    total_files=total,
                    success=success,
                    failed=total - success,
                    anomalies_detected=anomaly_total,
                )
            except ValueError:
                logger.debug("Audit log already finalized before summary")
            self.last_audit_path = audit.finalize()
            cleanup_old_audit_logs()

        return success, total - success, errors

    # ================================================================== #
    #  STANDALONE EXRATE UPDATE
    # ================================================================== #

    async def update_exrate_standalone(
        self,
        filepath: str,
        progress_cb: Callable[[str], None] | None = None,
        currencies: list[str] | None = None,
        rate_types: dict[str, str] | None = None,
        date_range: tuple[date, date] | None = None,
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
        return await StandaloneExRateUpdater(self).run(
            filepath, progress_cb, currencies, rate_types, date_range,
        )
