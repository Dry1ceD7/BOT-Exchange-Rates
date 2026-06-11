#!/usr/bin/env python3
"""
core/constants.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor — Configuration Constants
---------------------------------------------------------------------------
Centralized configuration values. All magic numbers are documented here
with their purpose and safe default values.

Override via environment variables where noted.
"""

import logging
import os
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)

# ── File Processing ──────────────────────────────────────────────────────
MAX_FILE_SIZE_MB: int = int(os.environ.get("BOT_MAX_FILE_MB", "15"))
"""Maximum allowed Excel file size in megabytes.

CLAUDE.md mandates a strict 15MB "Featherweight" limit. Override via the
BOT_MAX_FILE_MB environment variable when needed.
"""

SUPPORTED_EXCEL_EXTENSIONS: tuple = (".xlsx", ".xlsm")
"""File extensions accepted for processing."""

UNSUPPORTED_SPREADSHEET_EXTENSIONS: tuple = (".xls", ".xlsb", ".ods", ".csv")
"""Spreadsheet-looking extensions we explicitly recognise as UNSUPPORTED.

openpyxl cannot read legacy BIFF .xls (or .xlsb/.ods), and in-place
overwrite of those formats is impossible with this stack — so they are
rejected by design. They are listed here (single owner, shared by the GUI
drop resolver, headless input, and the scheduler) so every entry point can
NAME the files and tell the user the remedy (open in Excel, save as .xlsx)
instead of silently skipping them — a folder of legacy exports must never
read as an empty folder or an API failure."""

PREFORMAT_BUFFER_ROWS: int = 50
"""Number of rows below data to pre-format with DD/MM/YYYY."""


def collect_excel_files(
    path: str, *, dedup: bool = True, collect_rejected: bool = False,
) -> list[str] | tuple[list[str], list[str]]:
    """Return the sorted full-path Excel files at ``path``.

    Single owner of the directory-listing idiom (main.py headless input, the
    scheduler's watch paths, the GUI drop resolver). A single file yields a
    one-element list (or empty if it is not Excel); a directory is scanned
    non-recursively with the BARE names sorted before joining — for a single
    directory the constant prefix makes name order and full-path order
    identical, so this matches every prior call site (main.py sorted the
    joined paths; the scheduler/GUI sorted bare names). Dotfiles are skipped
    in the directory branch only — a directly-passed file is accepted as-is,
    exactly like the prior main.py single-file branch. Keeps os.listdir +
    os.path.join so the returned strings match the exact full-path form fed
    to the engine.

    Args:
        path: File or directory path (str — engine paths stay strings). A
            missing directory raises OSError from os.listdir, matching the
            prior inline listing.
        dedup: When True, drop ``os.path.normpath`` duplicates while keeping
            first-seen order. Callers that merge several listings (the
            scheduler's multiple watch paths, the GUI drop queue) pass
            ``dedup=False`` and run the same normpath dedup across the
            merged list themselves.
        collect_rejected: When True, return ``(files, rejected)`` where
            ``rejected`` lists present-but-unsupported spreadsheet files
            (``UNSUPPORTED_SPREADSHEET_EXTENSIONS``, e.g. legacy .xls) so the
            caller can tell the user WHY nothing was queued instead of
            reporting a misleading empty listing. ``rejected`` is never
            deduplicated (it is informational, per-call).
    """
    rejected: list[str] = []
    if Path(path).is_file():
        lowered = path.lower()
        files = [path] if lowered.endswith(SUPPORTED_EXCEL_EXTENSIONS) else []
        if not files and lowered.endswith(UNSUPPORTED_SPREADSHEET_EXTENSIONS):
            rejected.append(path)
    else:
        files = []
        for f in sorted(os.listdir(path)):  # noqa: PTH208
            if f.startswith("."):
                continue
            lowered = f.lower()
            if lowered.endswith(SUPPORTED_EXCEL_EXTENSIONS):
                files.append(os.path.join(path, f))  # noqa: PTH118
            elif lowered.endswith(UNSUPPORTED_SPREADSHEET_EXTENSIONS):
                rejected.append(os.path.join(path, f))  # noqa: PTH118
    if not dedup:
        return (files, rejected) if collect_rejected else files
    seen: set[str] = set()
    unique: list[str] = []
    for f in files:
        norm = os.path.normpath(f)
        if norm not in seen:
            seen.add(norm)
            unique.append(f)
    return (unique, rejected) if collect_rejected else unique

SKIP_SHEET_NAMES: frozenset = frozenset({"ExRate", "Exrate USD", "Exrate EUR"})
"""Sheets that are reference/master and should NOT be processed as ledgers.
"Exrate USD" / "Exrate EUR" are pre-existing rate tabs in older workbooks;
they lack the standard Date/Cur/EX Rate header and must be skipped."""

# THB is the company's home currency — its ledger rows take a literal 1.0, so
# it is "supported" without an API rate (the IFS formula handles it inline).
LEDGER_HOME_CURRENCY: str = "THB"
"""The home currency that resolves to a literal exchange rate of 1.0."""

# Currencies the ledger path can fetch from the BOT API and fill end-to-end.
# USD/EUR occupy the fixed master-sheet columns B-E; any OTHER code here gets a
# dynamically appended ExRate column + IFS branch. THB is handled separately as
# the home currency. A ledger row whose currency is none of these (and which the
# API returns no data for) is reported as unsupported rather than left silently
# blank — see core/ledger_processing.classify_currencies.
#
# JPY is deliberately EXCLUDED: BOT quotes it per 100 yen (~23.x THB/100JPY),
# so writing the published figure into a ledger EX Rate column would overstate
# any "amount x rate" conversion 100x. JPY rows take the unsupported path
# (blank cell + per-file warning) until the department confirms its convention.
LEDGER_SUPPORTED_CURRENCIES: frozenset = frozenset(
    {"USD", "EUR", "GBP", "CNY", "SGD", "HKD", "AUD", "CHF", "CAD"}
)
"""Currency codes the ledger multi-currency path can fetch + write."""

PER_100_UNIT_CURRENCIES: tuple = ("JPY",)
"""Currencies BOT publishes per 100 units of foreign currency, not per 1.

Documented so a future re-inclusion into LEDGER_SUPPORTED_CURRENCIES knows the
published rate must be divided by 100 (or the ledger convention confirmed)
before it is safe to multiply against per-unit amounts."""

BACKUP_MAX_AGE_DAYS: int = int(os.environ.get("BOT_BACKUP_AGE_DAYS", "7"))
"""Auto-cleanup backups older than this many days."""

AUDIT_LOG_MAX_AGE_DAYS: int = int(os.environ.get("BOT_AUDIT_LOG_AGE_DAYS", "30"))
"""Auto-cleanup Audit_Log_*.csv files older than this many days.

The CLI path writes one audit log per batch, so data/logs/ would otherwise
grow unbounded. Override via the BOT_AUDIT_LOG_AGE_DAYS environment variable."""

MIN_DISK_SPACE_MB: int = 100
"""Minimum free disk space (MB) required before saving a workbook."""

# ── Network ──────────────────────────────────────────────────────────────
MAX_429_RETRIES: int = int(os.environ.get("BOT_MAX_429_RETRIES", "10"))
"""Maximum retries for HTTP 429 rate limiting responses."""

API_TIMEOUT_SECONDS: float = 30.0
"""Default httpx timeout for API calls."""

API_CONNECT_TIMEOUT_SECONDS: float = 10.0
"""Default httpx connect timeout."""

API_RETRY_ATTEMPTS: int = 4
"""tenacity stop_after_attempt count for transient network/5xx errors."""

API_RETRY_BACKOFF_MULTIPLIER: float = 1.0
"""tenacity wait_exponential multiplier for retry backoff."""

API_RETRY_BACKOFF_MIN_SECONDS: float = 2.0
"""tenacity wait_exponential minimum backoff (seconds)."""

API_RETRY_BACKOFF_MAX_SECONDS: float = 20.0
"""tenacity wait_exponential maximum backoff (seconds)."""

RETRY_AFTER_MAX_SECONDS: int = 300
"""Upper clamp for a 429 Retry-After header value (seconds)."""

# ── IPC ──────────────────────────────────────────────────────────────────
IPC_NONCE_LENGTH: int = 32
"""Length of hex nonce for IPC authentication."""

# ── Scheduler ────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS: int = int(os.environ.get("BOT_POLL_INTERVAL", "30"))
"""Background scheduler polling interval."""

# ── Anomaly Detection ────────────────────────────────────────────────────
DEFAULT_ANOMALY_THRESHOLD_PCT: float = 5.0
"""Default day-over-day rate change threshold for anomaly guardian."""

ANOMALY_MAX_DAY_GAP: int = 4
"""Max calendar-day gap between two rate observations before a day-over-day
comparison is skipped. Prevents long weekends/holiday closures from inflating
the percentage change and producing false anomalies."""

# ── Date Parsing ───────────────────────────────────────────────────────────
DATE_FORMATS: tuple = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
    "%d %b %Y", "%d %B %Y", "%Y%m%d",
)
"""Single source of truth for textual date formats accepted across the app
(prescan, exrate_sheet, engine). Superset of every format the individual
modules historically parsed.

Day-first by deliberate Thai-locale policy: a slash/dash date such as
"01/02/2025" is parsed as 1 February (day-month-year), NOT 2 January. Thai
ledgers are authored DD/MM/YYYY, so the day-first formats are listed before
any month-first interpretation could match."""

_NON_DATE_TOKENS = frozenset({"", "nan", "null"})

# Buddhist-Era ⇄ Common-Era offset. Thai ledgers routinely record years in
# B.E. (e.g. 2567 = 2024 CE); strptime parses them as literal CE, silently
# mis-targeting rate queries ~543 years out, so parse_date normalizes them.
_BE_CE_OFFSET = 543
_BE_YEAR_LOW = 2400
_BE_YEAR_HIGH = 2700


def _plausible_year(year: int) -> bool:
    """True if ``year`` is within the accepted Common-Era window.

    Lower bound is 1970 (epoch-ish; older accounting dates are not expected);
    upper bound is next year to tolerate forward-dated entries.
    """
    return 1970 <= year <= date.today().year + 1


def parse_date(cell_val) -> date | None:
    """Parse a date from a cell value using the shared DATE_FORMATS.

    Accepts datetime, date, or string inputs. Returns None for empty,
    "nan"/"null", non-string/non-date types, or unrecognized formats.

    Buddhist-Era normalization: this is the single choke point for every
    string-date caller, so a year landing in the B.E. band (~2400-2700) is
    converted to Common Era by subtracting 543 and re-validated. Years that
    are implausible after normalization (e.g. 9999) return None rather than
    silently mis-targeting a query.
    """
    if isinstance(cell_val, datetime):
        return cell_val.date()
    if isinstance(cell_val, date):
        return cell_val
    if isinstance(cell_val, str):
        val = cell_val.strip()
        if val.lower() in _NON_DATE_TOKENS:
            return None
        for fmt in DATE_FORMATS:
            try:
                parsed = datetime.strptime(val, fmt).date()
            except ValueError:
                continue
            return _normalize_year(parsed)
    return None


def _normalize_year(parsed: date) -> date | None:
    """Apply the plausible-year window with B.E.→CE fallback.

    Returns ``parsed`` unchanged for plausible CE years; converts B.E.-band
    years (subtract 543) and re-validates; returns None for anything that is
    still implausible.
    """
    if _plausible_year(parsed.year):
        return parsed
    if _BE_YEAR_LOW <= parsed.year <= _BE_YEAR_HIGH:
        try:
            converted = parsed.replace(year=parsed.year - _BE_CE_OFFSET)
        except ValueError:
            return None
        if _plausible_year(converted.year):
            return converted
    return None


# ── Locked-file / save-error humanization ────────────────────────────────
# Windows surfaces a sharing violation as WinError 32 (no portable errno);
# POSIX uses EACCES (13) / EBUSY (16). We match on both the errno and the
# raw text so a clear "close it in Excel" message replaces the cryptic OS
# string for a non-technical accountant.
_LOCKED_FILE_ERRNOS = frozenset({13, 16})  # EACCES, EBUSY
_LOCKED_FILE_MARKERS = (
    "winerror 32",
    "being used by another process",
    "permission denied",
    "errno 13",
    "errno 16",
    "resource busy",
)


def humanize_save_error(filename: str, exc: BaseException) -> str | None:
    """Translate a locked-file OSError into an actionable accountant message.

    Returns a clear "close the file in Excel and process again" message when
    ``exc`` looks like a file-locked / sharing-violation error (the file is
    open in Excel or another program), or None when the error is something
    else and the caller should keep its original message.

    Centralized here so the engine, standalone, and scheduler paths reuse one
    translation instead of leaking raw WinError/errno strings to the user.
    """
    # A non-zip file wearing an .xlsx extension (typically a legacy BIFF .xls
    # renamed or re-saved with the wrong extension) raises zipfile.BadZipFile
    # — neither OSError nor InvalidFileException. Translate it to the actual
    # remedy instead of leaking "File is not a zip file" to an accountant.
    if isinstance(exc, zipfile.BadZipFile):
        return (
            f"{filename}: Not a valid .xlsx workbook — it may be a legacy "
            ".xls file renamed or saved with the wrong extension. Open it "
            "in Excel and save as .xlsx, then process again."
        )
    if not isinstance(exc, OSError):
        return None
    is_locked = isinstance(exc, PermissionError)
    errno = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    if errno in _LOCKED_FILE_ERRNOS or winerror == 32:
        is_locked = True
    if not is_locked:
        text = str(exc).lower()
        if any(marker in text for marker in _LOCKED_FILE_MARKERS):
            is_locked = True
    if not is_locked:
        return None
    return (
        f"{filename}: File is open in Excel or another program. "
        "Please close it and process again."
    )


def bot_today() -> date:
    """Return today's date in the Bank of Thailand timezone (Asia/Bangkok).

    BOT publishes rates on the local trading calendar (UTC+7). A machine in
    an earlier timezone can still be on "yesterday" at Bangkok midnight, so a
    naive ``date.today()`` would lag the BOT business date by up to a day and
    target the wrong trading day near the day boundary. Using the fixed
    UTC+7 offset (Thailand observes no DST) keeps date targeting aligned with
    the rates source.
    """
    return datetime.now(timezone(timedelta(hours=7))).date()


# ── CSV / Decimal Helpers ────────────────────────────────────────────────


def csv_safe(value) -> str:
    """
    Neutralize CSV/formula injection for a non-numeric cell.

    Strips embedded CR/LF/TAB (which could split or shift fields) and prefixes
    a single quote to any value beginning with a spreadsheet formula trigger
    (=, +, -, @) so Excel/LibreOffice treat it as inert text.
    """
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return ("'" + s) if s and s[0] in ("=", "+", "-", "@") else s


def format_rate_value(value) -> str:
    """Format a rate value for CSV output (4dp, numeric — never injected).

    Decimal inputs are quantized exactly (no float round-trip) so the
    written digits match the cached "Mathematical Truth" value.
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        # Quantize with ROUND_HALF_EVEN (banker's rounding) — the pinned
        # project standard, explicit so the result never drifts with the
        # ambient decimal context — pending any department mandate.
        return f"{value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_EVEN)}"
    # Float path: Python's fixed-point float formatting is round-half-even
    # on the binary value (exact decimal ties are vanishingly rare here).
    return f"{float(value):.4f}"


def parse_decimal_safe(raw) -> Decimal | None:
    """
    Parse a rate cell into an exact Decimal, preserving the literal digits.

    Deliberately performs NO quantize — no rounding mode applies here; the
    source digits pass through untouched. Any later 4dp quantize must pin
    rounding=ROUND_HALF_EVEN (the project standard).

    Returns None (and debug-logs) for empty/unparseable values instead of
    silently swallowing them, so mis-formatted data is observable in logs.
    """
    s = "" if raw is None else str(raw).strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        logger.debug("Skipped non-numeric rate value: %r", s)
        return None


def to_float(value: Decimal | None) -> float | None:
    """Coerce an optional Decimal to float for the legacy REAL-column table."""
    return None if value is None else float(value)
