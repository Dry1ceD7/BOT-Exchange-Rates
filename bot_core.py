#!/usr/bin/env python3
"""
================================================================================
  BOT Core Module — Shared Foundation for All BOT Exchange Rate Scripts
  
  This module centralizes all common logic so that bot_generator.py,
  bot_excel_report.py, and bot_acc_filler.py share a single source of truth.
  
  Provides:
    - .env file loading (API tokens)
    - Async API client with retries and exponential backoff
    - Holiday fetching (BOT API + hardcoded Thai fiscal calendar)
    - Exchange rate fetching (concurrent, chunked)
    - Date parsing utilities (Excel cell values → Python date objects)
    - Weekend/holiday rollback ("effective rate date" resolver)
================================================================================
"""

# ─── Standard library imports ────────────────────────────────
import sys
import os
import ssl
import asyncio
import logging
from datetime import date, timedelta, datetime
from typing import Dict, Any, Optional, Tuple, List

# ─── Auto-install aiohttp if not already available ───────────
# All dependencies are installed into a local _libs/ folder to
# avoid polluting the system Python environment.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIBS_DIR = os.path.join(SCRIPT_DIR, "_libs")

if not os.path.exists(_LIBS_DIR):
    os.makedirs(_LIBS_DIR)

if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

try:
    import aiohttp
except ImportError:
    print("  Installing required package 'aiohttp' locally...")
    import subprocess
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "--target", _LIBS_DIR, "aiohttp",
        "--break-system-packages", "--quiet"
    ])
    import importlib
    importlib.invalidate_caches()
    import aiohttp

try:
    import openpyxl  # noqa: F401 — needed by filler and report scripts
except ImportError:
    print("  Installing required package 'openpyxl' locally...")
    import subprocess
    subprocess.check_call([
        sys.executable, "-m", "pip", "install",
        "--target", _LIBS_DIR, "openpyxl",
        "--break-system-packages", "--quiet"
    ])
    import importlib
    importlib.invalidate_caches()
    import openpyxl  # noqa: F401


# ─── Logging ─────────────────────────────────────────────────
# Each script configures its own handler; this module provides
# a namespaced logger that child scripts inherit.
logger = logging.getLogger("bot_core")


# ─── Load API tokens from the .env file ─────────────────────
# Searches for .env in the script's directory first.
def load_env(script_dir: Optional[str] = None) -> None:
    """Read key=value pairs from a .env file into os.environ."""
    base = script_dir or SCRIPT_DIR
    env_path = os.path.join(base, ".env")

    if not os.path.exists(env_path):
        return

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key, val = parts
                    os.environ[key.strip()] = val.strip().strip("\"'")


def get_tokens() -> Tuple[str, str]:
    """Return (TOKEN_EXG, TOKEN_HOL) from the environment.
    Exits with an error message if either is missing."""
    load_env()
    token_exg = os.environ.get("BOT_TOKEN_EXG", "")
    token_hol = os.environ.get("BOT_TOKEN_HOL", "")
    if not token_exg or not token_hol:
        sys.exit("Error: Missing BOT API tokens in .env file.")
    return token_exg, token_hol


# ─── Configuration Constants ────────────────────────────────
GATEWAY = "https://gateway.api.bot.or.th"
EXG_PATH = "/Stat-ExchangeRate/v2/DAILY_AVG_EXG_RATE/"
HOL_PATH = "/financial-institutions-holidays/"
CHUNK_DAYS = 30  # BOT API max window per request

SSL_CTX = ssl.create_default_context()

# ─── Fixed Thai Holidays (Fallback) ─────────────────────────
# Used when the BOT API omits a holiday that falls on a weekend
# (only the substitution Monday is listed, not the actual date).
FIXED_HOLIDAYS: Dict[Tuple[int, int], str] = {
    (1, 1):   "New Year's Day",
    (4, 6):   "Chakri Memorial Day",
    (4, 13):  "Songkran Festival",
    (4, 14):  "Songkran Festival",
    (4, 15):  "Songkran Festival",
    (5, 1):   "National Labour Day",
    (6, 3):   "H.M. Queen Suthida's Birthday",
    (7, 28):  "H.M. King Vajiralongkorn's Birthday",
    (8, 12):  "H.M. Queen Sirikit's Birthday / Mother's Day",
    (10, 13): "King Bhumibol Memorial Day",
    (10, 23): "Chulalongkorn Memorial Day",
    (12, 5):  "King Bhumibol's Birthday / Father's Day",
    (12, 10): "Constitution Day",
    (12, 31): "New Year's Eve",
}


# ═══════════════════════════════════════════════════════════════
# ASYNC API CLIENT
# ═══════════════════════════════════════════════════════════════

async def bot_api_get_async(
    session: aiohttp.ClientSession,
    full_url: str,
    auth_token: str,
    retries: int = 3,
) -> Optional[Dict[str, Any]]:
    """Fetch JSON from the BOT API with exponential backoff retries.

    Args:
        session:    An open aiohttp ClientSession (for connection pooling).
        full_url:   The complete API endpoint URL with query params.
        auth_token: The Bearer/API token for the Authorization header.
        retries:    Number of attempts before giving up (default 3).

    Returns:
        Parsed JSON dict on success, None on failure.
    """
    headers = {"Authorization": auth_token, "accept": "application/json"}

    for attempt in range(1, retries + 1):
        try:
            async with session.get(
                full_url, headers=headers, ssl=SSL_CTX, timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    logger.warning("API returned %d for %s", response.status, full_url)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(
                "Connection error (%s) for %s. Retry %d/%d",
                type(e).__name__, full_url, attempt, retries,
            )

        if attempt < retries:
            await asyncio.sleep(2 ** attempt)  # 2s, 4s, 8s…

    logger.error("Failed to fetch %s after %d attempts.", full_url, retries)
    return None


# ═══════════════════════════════════════════════════════════════
# DATA FETCHING (HOLIDAYS + EXCHANGE RATES)
# ═══════════════════════════════════════════════════════════════

async def fetch_all_data(
    start_date: date,
    end_date: date,
    currencies: Tuple[str, ...] = ("USD", "EUR"),
    log_fn=print,
) -> Tuple[Dict[str, str], Dict[str, Dict[str, Dict[str, Optional[float]]]]]:
    """Fetch holidays and exchange rates concurrently.

    Returns:
        (holidays, rates) where:
          holidays = {"2025-01-01": "New Year's Day", …}
          rates    = {"2025-01-02": {"USD": {"buying": 34.5, "selling": 34.8}, …}, …}
    """
    token_exg, token_hol = get_tokens()
    holidays: Dict[str, str] = {}
    rates: Dict[str, Dict[str, Dict[str, Optional[float]]]] = {}

    async with aiohttp.ClientSession() as session:
        # ── Build holiday tasks (one per year) ────────────────
        holiday_tasks = []
        for year in range(start_date.year, end_date.year + 1):
            url = f"{GATEWAY}{HOL_PATH}?year={year}"
            holiday_tasks.append(bot_api_get_async(session, url, token_hol))

        # ── Build rate tasks (30-day chunks × currencies) ─────
        rate_tasks: List[Tuple[str, Any]] = []
        cs = start_date
        while cs <= end_date:
            ce = min(cs + timedelta(days=CHUNK_DAYS), end_date)
            sp, ep = cs.strftime("%Y-%m-%d"), ce.strftime("%Y-%m-%d")
            for ccy in currencies:
                url = f"{GATEWAY}{EXG_PATH}?start_period={sp}&end_period={ep}&currency={ccy}"
                rate_tasks.append((ccy, bot_api_get_async(session, url, token_exg)))
            cs = ce + timedelta(days=1)

        log_fn(
            f"  Fetching data ({len(holiday_tasks)} holiday years, "
            f"{len(rate_tasks)} rate chunks concurrently)..."
        )

        # ── Fire all requests at once ─────────────────────────
        holiday_results = await asyncio.gather(*holiday_tasks)
        rate_results = await asyncio.gather(*(t[1] for t in rate_tasks))

        # ── Parse holidays ────────────────────────────────────
        for data in holiday_results:
            if data:
                for h in data.get("result", {}).get("data", []):
                    dt = str(h.get("Date", "")).strip()[:10]
                    nm = str(h.get("HolidayDescription", "Holiday")).strip()
                    if dt:
                        holidays[dt] = nm

        # ── Parse rates ───────────────────────────────────────
        for (ccy, _), data in zip(rate_tasks, rate_results):
            if not data:
                continue
            try:
                details = data.get("result", {}).get("data", {}).get("data_detail", [])
            except (KeyError, AttributeError):
                continue
            if not isinstance(details, list):
                continue
            for row in details:
                dt = str(row.get("period", "")).strip()[:10]
                if not dt:
                    continue
                bt = str(row.get("buying_transfer", "")).strip()
                sl = str(row.get("selling", "")).strip()
                if dt not in rates:
                    rates[dt] = {}
                rates[dt][ccy] = {
                    "buying": float(bt) if bt else None,
                    "selling": float(sl) if sl else None,
                }

    log_fn(f"  Loaded {len(holidays)} holidays, {len(rates)} trading days.")
    return holidays, rates


# ═══════════════════════════════════════════════════════════════
# DATE UTILITIES
# ═══════════════════════════════════════════════════════════════

def parse_date_string(date_val: Any) -> Optional[date]:
    """Convert an Excel cell value (datetime, date, or string) to a Python date.

    Handles:
      - Python datetime / date objects (openpyxl reads dates this way)
      - Text strings like "04 Feb 2026", "4 February 2026", "2026-02-04"

    Returns None if the value cannot be parsed.
    """
    if date_val is None:
        return None
    if isinstance(date_val, datetime):
        return date_val.date()
    if isinstance(date_val, date):
        return date_val

    text = str(date_val).strip()
    if not text:
        return None

    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_bot_closed(check_date: date, holidays: Dict[str, str]) -> bool:
    """True if the Bank of Thailand is closed on this date.

    Closed = Saturday, Sunday, BOT API holiday, or fixed fiscal holiday.
    """
    if check_date.weekday() >= 5:
        return True
    if check_date.strftime("%Y-%m-%d") in holidays:
        return True
    if (check_date.month, check_date.day) in FIXED_HOLIDAYS:
        return True
    return False


def resolve_effective_rate_date(
    original_date: date, holidays: Dict[str, str]
) -> date:
    """Roll back from the given date to the most recent BOT open day.

    If the date is already a normal trading day, returns unmodified.
    Otherwise steps backward (max 10 days) until an open day is found.

    Example:
      Sunday Feb 8 → Sat Feb 7 (closed) → Fri Feb 6 (open!) → returns Feb 6
    """
    resolved = original_date
    for _ in range(10):
        if not is_bot_closed(resolved, holidays):
            return resolved
        resolved -= timedelta(days=1)
    return resolved


# ─── Changelog ───────────────────────────────────────────────
# 2026-03-12 | v1.0 — Initial extraction
#            | - Extracted shared logic from bot_generator.py,
#            |   bot_excel_report.py, and bot_acc_filler.py
#            | - Centralized .env loading, async API client,
#            |   holiday/rate fetching, and date utilities
