#!/usr/bin/env python3
"""
core/rate_audit.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — ExRate Sheet Rate Auditor
---------------------------------------------------------------------------
Verifies the hard USD/EUR rate values already written into an existing
workbook's "ExRate" master sheet against the authoritative Bank of Thailand
values, and proposes / applies exact 4-decimal Decimal corrections.

This module is the PURE-LOGIC layer: the comparison and the cell rewrite. It
holds no network, no backup, and no file I/O orchestration — those live in the
caller (the GUI handler / a standalone runner), which fetches BOT rates, backs
the file up first, and saves. Keeping the comparison pure makes the financial
logic unit-testable without a workbook on disk or a live API.

CRITICAL INVARIANT — weekend / holiday rows are NEVER touched. BOT publishes
no rate on those days, and the ExRate sheet intentionally keeps their rate
cells blank (the v3.2.8 behavior restored in core/exrate_sheet.py). Filling
them would re-introduce the carry-forward bug. The scanner skips any row whose
Date is a Saturday/Sunday or a BOT holiday.
"""

import contextlib
import gc
import logging
import os
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter

from core.audit_logger import AuditLogger
from core.constants import MIN_DISK_SPACE_MB, bot_today
from core.constants import parse_date as _parse_date
from core.logic import safe_to_decimal
from core.workbook_io import atomic_save, ensure_disk_space

logger = logging.getLogger(__name__)

# ExRate fixed rate columns (mirror core/exrate_sheet.py):
#   A=Date, B=USD Buying TT, C=USD Selling, D=EUR Buying TT, E=EUR Selling.
# Each entry: (1-based column, header label, currency, BOT api field).
EXRATE_RATE_COLUMNS: tuple[tuple[int, str, str, str], ...] = (
    (2, "USD Buying TT Rate", "USD", "buying_transfer"),
    (3, "USD Selling Rate", "USD", "selling"),
    (4, "EUR Buying TT Rate", "EUR", "buying_transfer"),
    (5, "EUR Selling Rate", "EUR", "selling"),
)
DATA_START_ROW = 2


def rate_key(currency: str, rate_type: str) -> str:
    """Lookup key for the bot_rates map: ``"USD:buying_transfer"`` etc."""
    return f"{currency}:{rate_type}"


@dataclass(slots=True)
class RateChange:
    """A single proposed/applied correction to one ExRate rate cell."""

    row: int
    col: int
    cell: str  # e.g. "B5", for display
    rate_date: date
    column_label: str
    currency: str
    rate_type: str
    old_value: Decimal | None
    new_value: Decimal
    reason: str


@dataclass
class RateAuditReport:
    """Outcome of auditing one workbook's ExRate sheet against BOT."""

    file: str = ""
    sheet: str = "ExRate"
    scanned_rows: int = 0
    compared_cells: int = 0
    changes: list[RateChange] = field(default_factory=list)
    # Trading-day cells that held a value but BOT had no rate to compare
    # against (e.g. a date beyond the available BOT history). Left untouched.
    unverifiable: int = 0
    backup_path: str | None = None
    applied: bool = False

    @property
    def change_count(self) -> int:
        return len(self.changes)


def scan_exrate_corrections(
    ws,
    bot_rates: dict[str, dict[date, Decimal]],
    holidays_set: set[date] | None,
    *,
    data_start_row: int = DATA_START_ROW,
) -> RateAuditReport:
    """Compare each TRADING-DAY ExRate rate cell to the authoritative BOT value.

    Args:
        ws: The openpyxl "ExRate" worksheet.
        bot_rates: ``{f"{ccy}:{rate_type}": {date: Decimal}}`` — the BOT values
            keyed by currency + api field, as fetched by the caller.
        holidays_set: BOT holidays (any iterable of ``date``). Rows on these
            dates — and on weekends — are skipped entirely.
        data_start_row: First data row (row 1 is the header).

    Returns:
        A :class:`RateAuditReport` listing proposed corrections. Nothing is
        written; the caller decides whether to :func:`apply_corrections`.

    A cell is flagged when, on a trading day, the stored 4dp value differs from
    BOT's published value — including a blank trading-day cell that BOT *does*
    publish (recorded as a "missing rate filled" correction). A blank weekend/
    holiday cell is correct by design and is never filled.
    """
    report = RateAuditReport(sheet=ws.title)
    holidays = set(holidays_set or ())
    max_row = ws.max_row or 0

    for row in range(data_start_row, max_row + 1):
        rate_date = _parse_date(ws.cell(row=row, column=1).value)
        if rate_date is None:
            continue
        report.scanned_rows += 1

        # INVARIANT: weekend/holiday rows carry no BOT rate — never touch them.
        if rate_date.weekday() >= 5 or rate_date in holidays:
            continue

        for col, label, ccy, rate_type in EXRATE_RATE_COLUMNS:
            bot_val = bot_rates.get(rate_key(ccy, rate_type), {}).get(rate_date)
            old_val = safe_to_decimal(ws.cell(row=row, column=col).value)

            if bot_val is None:
                # No authoritative value to compare against — cannot verify.
                if old_val is not None:
                    report.unverifiable += 1
                continue

            bot_q = (
                bot_val if isinstance(bot_val, Decimal)
                else safe_to_decimal(bot_val)
            )
            if bot_q is None:
                continue

            report.compared_cells += 1
            if old_val == bot_q:
                continue  # already correct

            reason = (
                "missing rate filled from BOT"
                if old_val is None
                else f"value {old_val} != BOT {rate_type} {bot_q}"
            )
            report.changes.append(
                RateChange(
                    row=row,
                    col=col,
                    cell=f"{get_column_letter(col)}{row}",
                    rate_date=rate_date,
                    column_label=label,
                    currency=ccy,
                    rate_type=rate_type,
                    old_value=old_val,
                    new_value=bot_q,
                    reason=reason,
                )
            )
    return report


def apply_corrections(ws, report: RateAuditReport) -> RateAuditReport:
    """Write each proposed change's ``new_value`` into the sheet as a 4dp Decimal.

    Mutates ``ws`` in place and marks the report ``applied``. The caller owns
    backup-before-write and the workbook save/close/gc lifecycle.
    """
    for ch in report.changes:
        cell = ws.cell(row=ch.row, column=ch.col)
        cell.value = ch.new_value
        cell.number_format = "0.0000"
    report.applied = True
    return report


def write_audit_csv(report: RateAuditReport) -> str:
    """Write the report's changes to a timestamped CSV via AuditLogger.

    Returns the CSV path. Each change is one row (Rate Audit as the source).
    Always writes a file even with zero changes, so there is an auditable
    record that the verification ran.
    """
    audit = AuditLogger()
    try:
        fname = Path(report.file).name if report.file else "ExRate"
        for ch in report.changes:
            audit.log_row_change(
                filename=fname,
                sheet=report.sheet,
                row=ch.row,
                cell_date=ch.rate_date.strftime("%Y-%m-%d"),
                currency=f"{ch.currency} {ch.rate_type}",
                original_value="" if ch.old_value is None else str(ch.old_value),
                new_value=str(ch.new_value),
                rate_source="Rate Audit (BOT re-verify)",
            )
        return audit.finalize()
    finally:
        # finalize() is idempotent; guard against a mid-loop error leaking the
        # handle (atexit is the last-resort net, but close deterministically).
        with contextlib.suppress(Exception):
            audit.finalize()


class StandaloneRateAuditor:
    """Audit an existing workbook's ExRate sheet against BOT, optionally
    applying 4dp corrections (the file is backed up first).

    Live-binding contract (mirrors StandaloneExRateUpdater): stores ONLY the
    live engine and dereferences engine attributes (backup, cache,
    _preload_api_data, _parse_date, _check_memory_guardrail) AT CALL TIME, so a
    test that reassigns them post-construction is honored.
    """

    def __init__(self, engine) -> None:
        self._engine = engine

    async def run(
        self, filepath: str, *, apply: bool = True, status_cb=None,
    ) -> RateAuditReport:
        """Verify (and optionally correct) the ExRate sheet in ``filepath``.

        With ``apply=False`` this is a dry run: it returns the proposed changes
        without backing up or writing anything. With ``apply=True`` (default)
        it backs the file up, rewrites only the differing trading-day cells,
        and saves atomically. Weekend/holiday rows are never touched.
        """

        def _status(msg: str) -> None:
            if status_cb is not None:
                with contextlib.suppress(Exception):
                    status_cb(msg)

        # noqa: PTH100 — keep os.path.abspath (no symlink resolution) so the
        # in-place save target string matches the rest of the write pipeline.
        filepath = os.path.abspath(filepath)  # noqa: PTH100
        self._engine._check_memory_guardrail(filepath)

        # Pass 1: read the ExRate dates read-only, then release the handle
        # BEFORE the (slow) network fetch — never hold a file lock over I/O.
        _status("Reading ExRate sheet...")
        existing_dates = self._read_exrate_dates(filepath)
        if existing_dates is None:
            raise ValueError("No ExRate sheet found in the selected file.")
        if not existing_dates:
            return RateAuditReport(file=filepath)

        # Authoritative BOT values for the sheet's span (cache-first, same
        # path the standard ExRate build uses).
        _status("Fetching BOT rates for verification...")
        target_year = min(existing_dates).year
        start_str = f"{target_year - 1}-12-20"
        all_target_dates = existing_dates | {bot_today()}
        (
            logic_engine, usd_selling, eur_selling,
            usd_buying, eur_buying, _usd_data, _eur_data,
        ) = await self._engine._preload_api_data(all_target_dates, start_str)
        bot_rates = {
            rate_key("USD", "buying_transfer"): usd_buying,
            rate_key("USD", "selling"): usd_selling,
            rate_key("EUR", "buying_transfer"): eur_buying,
            rate_key("EUR", "selling"): eur_selling,
        }
        holidays_set = set(logic_engine.holidays)

        # Pass 2: load read-write, compare, optionally back up + apply + save.
        _status("Comparing against BOT...")
        wb = openpyxl.load_workbook(filepath)
        try:
            if "ExRate" not in wb.sheetnames:
                raise ValueError("No ExRate sheet found in the selected file.")
            ws = wb["ExRate"]
            report = scan_exrate_corrections(ws, bot_rates, holidays_set)
            report.file = filepath

            if report.changes and apply:
                # Back up the on-disk original BEFORE overwriting (enables
                # Revert via the existing restore_latest). atomic_save writes a
                # temp file + os.replace, so the original stays intact on disk
                # until the swap — the backup captures the pre-correction file.
                report.backup_path = self._engine.backup.create_backup(filepath)
                _status("Backup created")
                apply_corrections(ws, report)
                ensure_disk_space(Path(filepath).parent, MIN_DISK_SPACE_MB)
                atomic_save(wb, filepath)
                _status(f"✓ Applied {report.change_count} correction(s)")
            elif report.changes:
                _status(f"{report.change_count} difference(s) found (preview)")
            else:
                _status("All rates already match BOT")
            return report
        finally:
            with contextlib.suppress(OSError):
                wb.close()
            del wb
            gc.collect()

    def _read_exrate_dates(self, filepath: str) -> set[date] | None:
        """Return the set of dates in the ExRate sheet (read-only).

        Returns None when the workbook has no ExRate sheet (caller raises),
        an empty set when the sheet exists but has no parseable dates.
        """
        wb = None
        try:
            with Path(filepath).open("rb") as f:
                wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
                if "ExRate" not in wb.sheetnames:
                    return None
                ws = wb["ExRate"]
                dates: set[date] = set()
                for row in ws.iter_rows(
                    min_row=DATA_START_ROW, min_col=1, max_col=1,
                    values_only=True,
                ):
                    parsed = self._engine._parse_date(row[0])
                    if parsed:
                        dates.add(parsed)
                return dates
        finally:
            if wb is not None:
                with contextlib.suppress(OSError):
                    wb.close()
