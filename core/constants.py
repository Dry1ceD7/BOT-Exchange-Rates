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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# ── File Processing ──────────────────────────────────────────────────────
MAX_FILE_SIZE_MB: int = int(os.environ.get("BOT_MAX_FILE_MB", "15"))
"""Maximum allowed Excel file size in megabytes.

CLAUDE.md mandates a strict 15MB "Featherweight" limit. Override via the
BOT_MAX_FILE_MB environment variable when needed.
"""

SUPPORTED_EXCEL_EXTENSIONS: tuple = (".xlsx", ".xlsm")
"""File extensions accepted for processing."""

PREFORMAT_BUFFER_ROWS: int = 50
"""Number of rows below data to pre-format with DD/MM/YYYY."""

SKIP_SHEET_NAMES: frozenset = frozenset({"ExRate", "Exrate USD", "Exrate EUR"})
"""Sheets that are reference/master and should NOT be processed as ledgers.
"Exrate USD" / "Exrate EUR" are pre-existing rate tabs in older workbooks;
they lack the standard Date/Cur/EX Rate header and must be skipped."""

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
        return f"{value:.4f}"
    return f"{float(value):.4f}"


def parse_decimal_safe(raw) -> Decimal | None:
    """
    Parse a rate cell into an exact Decimal, preserving the literal digits.

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
