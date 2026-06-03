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
from datetime import date, datetime
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

MIN_DISK_SPACE_MB: int = 100
"""Minimum free disk space (MB) required before saving a workbook."""

# ── Network ──────────────────────────────────────────────────────────────
MAX_429_RETRIES: int = int(os.environ.get("BOT_MAX_429_RETRIES", "10"))
"""Maximum retries for HTTP 429 rate limiting responses."""

API_TIMEOUT_SECONDS: float = 30.0
"""Default httpx timeout for API calls."""

API_CONNECT_TIMEOUT_SECONDS: float = 10.0
"""Default httpx connect timeout."""

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
modules historically parsed."""

_NON_DATE_TOKENS = frozenset({"", "nan", "null"})


def parse_date(cell_val) -> date | None:
    """Parse a date from a cell value using the shared DATE_FORMATS.

    Accepts datetime, date, or string inputs. Returns None for empty,
    "nan"/"null", non-string/non-date types, or unrecognized formats.
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
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


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
