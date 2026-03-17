#!/usr/bin/env python3
"""
core/logic.py
---------------------------------------------------------------------------
BOT Exchange Rate Processor (v2.3.1) - Featherweight Architecture
---------------------------------------------------------------------------
The "Zero-Guess" decision engine. Handles exact date-matching, weekend,
and holiday backtracking to ensure absolute financial accuracy. No Excel
formulas are used; outputs are guaranteed Python Decimal objects.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Tuple, Optional

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
    
    def __init__(self, holidays: List[date], max_rollback_days: int = 5):
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

    def resolve_rate(self, invoice_date: date, usd_rates: Dict[date, Decimal], eur_rates: Dict[date, Decimal]) -> Tuple[date, Optional[Decimal], Optional[Decimal]]:
        """
        The Core Backtrack Loop.
        Finds the exact, valid BOT trading date and returns the hard-coded Decimals.
        
        Args:
            invoice_date: The date from the "วันที่ใบขน" column.
            usd_rates: Dictionary mapping dates to USD Decimal rates.
            eur_rates: Dictionary mapping dates to EUR Decimal rates.
            
        Returns:
            Tuple containing: (Confirmed Trading Date, USD Rate, EUR Rate)
            
        Raises:
            RateNotFoundError: If the backtrack limit is triggered.
        """
        current_date = invoice_date
        days_rolled_back = 0
        
        # Guardrail Loop
        while days_rolled_back <= self.max_rollback_days:
            if self.is_trading_day(current_date):
                # We found a valid trading day. Now, pull the exact numerical rates.
                usd_val = self._get_rate_for_date(current_date, usd_rates)
                eur_val = self._get_rate_for_date(current_date, eur_rates)
                
                # Double-check: Even if it's a trading day, did the BOT publish data?
                if usd_val is not None:
                    return current_date, usd_val, eur_val
                
                # If BOT data is mysteriously missing on a valid trading day, 
                # we must keep rolling back (e.g., BOT system failure that day).
                
            current_date -= timedelta(days=1)
            days_rolled_back += 1
            
        # If we exit the loop, we hit the safety limit
        raise RateNotFoundError(
            f"<ERROR: No Rate Found> Backtracked {self.max_rollback_days} days "
            f"from {invoice_date.strftime('%Y-%m-%d')} without hitting valid BOT data."
        )

# -------------------------------------------------------------------------
# UTILITIES
# -------------------------------------------------------------------------

def safe_to_decimal(value: any) -> Optional[Decimal]:
    """Strictly converts a float/string payload to a highly precise Decimal."""
    if value is None or value == "":
        return None
    try:
        # Quantize to 4 decimal places as per standard Thai accounting format
        d = Decimal(str(value)) 
        return d.quantize(Decimal('0.0000'))
    except (InvalidOperation, TypeError):
        return None
