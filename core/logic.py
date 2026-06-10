#!/usr/bin/env python3
"""
core/logic.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.6.1) - Featherweight Architecture
---------------------------------------------------------------------------
Holiday-aware trading-day utilities plus Decimal helpers.

This module is the pure-logic layer: weekend/holiday detection, day-by-day
rollback to the nearest historical trading day, year-end start-date
computation, and exact Decimal coercion. It holds NO Excel I/O.

NOTE on Excel formulas: the live ledger write path injects XLOOKUP formulas
(the intended design per README); it does NOT consume ``resolve_rate``.
``resolve_rate`` / ``resolve_rate_for_currency`` are the standalone/utility
path that returns hard Decimal values for callers needing a resolved rate
in code (CSV export, anomaly checks, headless helpers). Do not treat them as
the live ledger engine.
"""

import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

# -------------------------------------------------------------------------
# EXCEPTIONS
# -------------------------------------------------------------------------

class RateNotFoundError(Exception):
    """Raised when a valid rate cannot be found within the strict rollback limit."""
    pass

# -------------------------------------------------------------------------
# BUSINESS DAY & RATE RESOLUTION LOGIC
# -------------------------------------------------------------------------

class BOTLogicEngine:
    """Mathematical engine for resolving strict Bank of Thailand trading dates."""

    def __init__(self, holidays: list[date], max_rollback_days: int = 10):
        """
        Args:
            holidays: A list of official BOT holiday dates.
            max_rollback_days: The safety guardrail. Maximum consecutive days
                               to backtrack before raising an alert.
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

    def _get_rate_for_date(self, target_date: date, rates_data: dict[date, Decimal]) -> Decimal | None:
        """Safely extracts a Decimal rate for a specific date from the data dictionary."""
        return rates_data.get(target_date)

    def resolve_rate(
        self, target_date: date,
        usd_rates: dict[date, Decimal],
        eur_rates: dict[date, Decimal],
    ) -> tuple[date, Decimal | None, Decimal | None]:
        """
        Standalone/utility date resolver (V2.5).

        Returns the rate for the EXACT date provided. If the target date is
        a weekend or BOT holiday, it rolls back day-by-day until it finds
        the first valid historical trading day with available data.

        This serves the standalone/utility path (CSV export, anomaly checks,
        headless helpers) and returns hard Decimal values. It is NOT the live
        ledger engine — the ledger write path injects XLOOKUP formulas and
        does not call this method.

        Examples:
            - Target is Tuesday (trading day)  → returns Tuesday's rate
            - Target is Saturday               → rolls back to Friday
            - Target is Monday (BOT holiday)   → rolls back to Friday

        Args:
            target_date: The date from the "Date" column in the ledger.
            usd_rates: Dictionary mapping dates to USD Decimal rates.
            eur_rates: Dictionary mapping dates to EUR Decimal rates.

        Returns:
            Tuple containing: (Confirmed Trading Date, USD Rate, EUR Rate)

        Raises:
            RateNotFoundError: If the backtrack limit is triggered or
                               a required rate is missing on a valid trading day.
        """
        # STANDARD RESOLUTION: Start from the exact target date
        current_date = target_date
        days_rolled_back = 0

        # Guardrail Loop
        while days_rolled_back <= self.max_rollback_days:
            if self.is_trading_day(current_date):
                # We found a valid trading day. Now, pull the exact numerical rates.
                usd_val = self._get_rate_for_date(current_date, usd_rates)
                eur_val = self._get_rate_for_date(current_date, eur_rates)

                # Both USD and EUR must be present for a valid trading day
                if usd_val is not None and eur_val is not None:
                    return current_date, usd_val, eur_val

                # If BOT data is mysteriously missing on a valid trading day,
                # we must keep rolling back (e.g., BOT system failure that day).

            current_date -= timedelta(days=1)
            days_rolled_back += 1

        # If we exit the loop, we hit the safety limit
        raise RateNotFoundError(
            f"<ERROR: No Rate Found> Backtracked {self.max_rollback_days} days "
            f"from {target_date.strftime('%Y-%m-%d')} without hitting valid BOT data."
        )

    def resolve_rate_for_currency(
        self, target_date: date, currency: str,
        usd_rates: dict[date, Decimal], eur_rates: dict[date, Decimal]
    ) -> tuple[date, Decimal | None]:
        """
        Currency-aware date resolver.

        - THB: Returns (target_date, Decimal("1.0000")) immediately.
        - USD: Returns (trade_date, usd_rate) via standard resolve.
        - EUR: Returns (trade_date, eur_rate) via standard resolve.
        - Other: Returns (target_date, None) — no rate available.
        """
        ccy = currency.strip().upper() if currency else ""

        if ccy == "THB":
            return target_date, Decimal("1.0000")

        if ccy in ("USD", "EUR"):
            trade_date, usd_rt, eur_rt = self.resolve_rate(
                target_date, usd_rates, eur_rates
            )
            if ccy == "USD":
                return trade_date, usd_rt
            else:
                return trade_date, eur_rt

        # Unsupported currency — skip silently
        return target_date, None

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

    for year in {
        d.year
        for d in (all_target_dates | {computed_start, date.today()})
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
