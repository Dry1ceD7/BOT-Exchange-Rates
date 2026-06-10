#!/usr/bin/env python3
"""
core/logic.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.6.1) - Featherweight Architecture
---------------------------------------------------------------------------
Holiday-aware trading-day utilities plus Decimal helpers.

This module is the pure-logic layer: weekend/holiday detection, year-end
start-date computation, calendar fetch-window helpers, and exact Decimal
coercion. It holds NO Excel I/O and NO rate resolution — the approved
ledger contract is exact-match (rates are looked up for the exact ledger
date; there is no day-by-day rollback resolver here).
"""

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from core.constants import bot_today

# -------------------------------------------------------------------------
# BUSINESS DAY LOGIC
# -------------------------------------------------------------------------

class BOTLogicEngine:
    """Holiday container + trading-day predicate for BOT calendar checks.

    Downstream consumers (``core.engine``, ``core.exrate_updater``,
    ``core.rate_audit``) read ``logic_engine.holidays`` as the master
    holiday set; ``is_trading_day`` is the canonical weekend/holiday
    predicate.
    """

    def __init__(self, holidays: list[date], max_rollback_days: int = 10):
        """
        Args:
            holidays: A list of official BOT holiday dates.
            max_rollback_days: Retained for the constructor contract only
                               (core.engine still passes it); nothing in
                               this class consumes it since the dead
                               rollback resolvers were removed.
        """
        self.holidays = set(holidays) # Set for O(1) lookup
        self.max_rollback_days = max_rollback_days

    def is_trading_day(self, target_date: date) -> bool:
        """
        A trading day is Monday-Friday (weekday < 5) AND not a public holiday.
        """
        if target_date.weekday() >= 5: # 5 = Saturday, 6 = Sunday
            return False
        return target_date not in self.holidays

# -------------------------------------------------------------------------
# CALENDAR-WINDOW HELPERS (pure functions — extracted from engine)
# -------------------------------------------------------------------------

def weekdays_between(start: date, end: date) -> set[date]:
    """Return every calendar weekday (Mon-Fri) in the inclusive window.

    Calendar weekdays only — holidays are NOT excluded. This feeds the
    cache-miss computations in core.engine (which weekday dates lack cached
    rates); holiday gaps there are resolved later by the rollback logic,
    never by narrowing the fetch window.
    """
    out: set[date] = set()
    check = start
    while check <= end:
        if check.weekday() < 5:
            out.add(check)
        check += timedelta(days=1)
    return out


def default_fetch_window_start(target_year: int) -> date:
    """Return the default rate FETCH-WINDOW start: Dec 20 of the prior year.

    This is a fetch-window anchor, NOT a trading-day computation — Dec 20
    may itself fall on a weekend/holiday and that is fine: it only needs to
    open the API/cache window wide enough to cover the year-start rollback.
    The actual year-start trading day is ``compute_year_start_date``. The
    prescan's separate Dec-28 fallback
    (``core.prescan.prescan_oldest_date``) is a narrower "no dates detected"
    anchor, deliberately not this window.
    """
    return date(target_year - 1, 12, 20)


# -------------------------------------------------------------------------
# YEAR-END & HOLIDAY HELPERS (pure functions — extracted from engine)
# -------------------------------------------------------------------------

def compute_year_start_date(
    target_year: int,
    holidays: list[date],
) -> date:
    """
    Computes the last valid trading day of the PREVIOUS calendar year.
    Dec 31 is always office day-off. Start from Dec 30 and roll back.
    """
    holidays_set = set(holidays)
    prev_year = target_year - 1
    check_date = date(prev_year, 12, 30)
    # Roll back through December until a trading day is found. Do NOT
    # return a fixed fallback (Dec 20 may itself be a weekend/holiday).
    # Bound to December: a year-start outside Dec is meaningless, so
    # raise rather than silently returning a November date.
    while check_date.year == prev_year and check_date.month == 12:
        if check_date.weekday() < 5 and check_date not in holidays_set:
            return check_date
        check_date -= timedelta(days=1)
    raise ValueError(
        f"No trading day found in December {prev_year} "
        "(all weekends/holidays)."
    )


def build_holiday_lookup(
    cache,
    all_target_dates: set[date],
    computed_start: date,
    logic_engine,
) -> tuple[set[date], dict[date, str]]:
    """Build holiday sets and name mappings from cached holiday data.

    Parses substitution holiday names (e.g., "Substitution for
    Songkran Day (15th April 2025)") to map the original holiday
    date as well.

    Args:
        cache: A cache object exposing ``get_holidays(year)``.
        all_target_dates: Dates found in the ledger.
        computed_start: The computed year-start trading date.
        logic_engine: Engine whose ``holidays`` seeds the master set.

    Returns:
        Tuple of (master_holidays_set, holidays_names dict).
    """
    # Expected BOT format: "Substitution for Songkran Day (15th April 2025)"
    sub_pattern = re.compile(r"^Substitution for ([^(]+)\s*\((.*?)\)$")
    holidays_names: dict[date, str] = {}
    master_holidays_set = set(logic_engine.holidays)

    # Anchor "today" on the BOT business date (Asia/Bangkok), not naive local
    # time, so the holiday lookup covers the same year set the engine targets.
    for year in {
        d.year
        for d in (all_target_dates | {computed_start, bot_today()})
    }:
        cached_hols = cache.get_holidays(year)
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


# -------------------------------------------------------------------------
# UTILITIES
# -------------------------------------------------------------------------

def safe_to_decimal(value: object) -> Decimal | None:
    """Strictly converts a float/string payload to a highly precise Decimal."""
    if value is None or value == "":
        return None
    try:
        # Quantize to 4 decimal places as per standard Thai accounting format.
        # ROUND_HALF_EVEN (banker's rounding) is the pinned project standard —
        # explicit so behavior never drifts with the ambient decimal context —
        # pending any department mandate to change it.
        d = Decimal(str(value))
        return d.quantize(Decimal('0.0000'), rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, TypeError):
        return None
