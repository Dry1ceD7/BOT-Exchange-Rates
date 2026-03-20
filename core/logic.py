#!/usr/bin/env python3
"""
core/logic.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.5.9) - Featherweight Architecture
---------------------------------------------------------------------------
The Standard Date Resolution Engine. Fetches the exchange rate for the
exact date provided. If the target date is a weekend or BOT holiday,
rolls back day-by-day until it finds the first valid historical trading
day. No Excel formulas are used; outputs are guaranteed Python Decimal
objects.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

# -------------------------------------------------------------------------
# EXCEPTIONS
# -------------------------------------------------------------------------

class RateNotFoundError(Exception):
    """Raised when a valid rate cannot be found within the strict rollback limit."""
    pass

class DateExtractionError(Exception):
    """Raised when the input date format is entirely unreadable."""
    pass

# -------------------------------------------------------------------------
# BUSINESS DAY & RATE RESOLUTION LOGIC
# -------------------------------------------------------------------------

class BOTLogicEngine:
    """Mathematical engine for resolving strict Bank of Thailand trading dates."""

    def __init__(self, holidays: List[date], max_rollback_days: int = 10):
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
        if target_date in self.holidays:
            return False
        return True

    def _get_rate_for_date(self, target_date: date, rates_data: Dict[date, Decimal]) -> Optional[Decimal]:
        """Safely extracts a Decimal rate for a specific date from the data dictionary."""
        return rates_data.get(target_date)

    def resolve_rate(
        self, target_date: date,
        usd_rates: Dict[date, Decimal],
        eur_rates: Dict[date, Decimal],
    ) -> Tuple[date, Optional[Decimal], Optional[Decimal]]:
        """
        Standard Date Resolution Engine (V2.5).

        Returns the rate for the EXACT date provided. If the target date is
        a weekend or BOT holiday, it rolls back day-by-day until it finds
        the first valid historical trading day with available data.

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
        usd_rates: Dict[date, Decimal], eur_rates: Dict[date, Decimal]
    ) -> Tuple[date, Optional[Decimal]]:
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
# UTILITIES
# -------------------------------------------------------------------------

def safe_to_decimal(value: Any) -> Optional[Decimal]:
    """Strictly converts a float/string payload to a highly precise Decimal."""
    if value is None or value == "":
        return None
    try:
        # Quantize to 4 decimal places as per standard Thai accounting format
        d = Decimal(str(value))
        return d.quantize(Decimal('0.0000'))
    except (InvalidOperation, TypeError):
        return None
