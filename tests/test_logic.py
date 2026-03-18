#!/usr/bin/env python3
"""
tests/test_logic.py
---------------------------------------------------------------------------
Unit tests for core/logic.py — BOTLogicEngine and safe_to_decimal.
---------------------------------------------------------------------------
"""

import pytest
from datetime import date, timedelta
from decimal import Decimal
from core.logic import BOTLogicEngine, RateNotFoundError, safe_to_decimal


# =========================================================================
#  safe_to_decimal
# =========================================================================

class TestSafeToDecimal:
    """Tests for the safe_to_decimal utility."""

    def test_float_value(self):
        result = safe_to_decimal(33.1234)
        assert result == Decimal("33.1234")

    def test_string_value(self):
        result = safe_to_decimal("33.1234")
        assert result == Decimal("33.1234")

    def test_quantizes_to_4dp(self):
        result = safe_to_decimal(33.12345678)
        assert result == Decimal("33.1235")  # Rounded to 4dp

    def test_integer_value(self):
        result = safe_to_decimal(33)
        assert result == Decimal("33.0000")

    def test_none_returns_none(self):
        assert safe_to_decimal(None) is None

    def test_empty_string_returns_none(self):
        assert safe_to_decimal("") is None

    def test_invalid_string_returns_none(self):
        assert safe_to_decimal("not-a-number") is None

    def test_zero(self):
        result = safe_to_decimal(0)
        assert result == Decimal("0.0000")

    def test_negative_value(self):
        result = safe_to_decimal(-12.5)
        assert result == Decimal("-12.5000")


# =========================================================================
#  BOTLogicEngine.is_trading_day
# =========================================================================

class TestIsTradingDay:
    """Tests for the is_trading_day method."""

    def setup_method(self):
        # 2025-01-01 is a Wednesday and a Thai holiday (New Year's Day)
        self.holidays = [date(2025, 1, 1)]
        self.engine = BOTLogicEngine(holidays=self.holidays)

    def test_weekday_not_holiday_is_trading(self):
        # 2025-01-02 is Thursday, not a holiday
        assert self.engine.is_trading_day(date(2025, 1, 2)) is True

    def test_saturday_is_not_trading(self):
        # 2025-01-04 is Saturday
        assert self.engine.is_trading_day(date(2025, 1, 4)) is False

    def test_sunday_is_not_trading(self):
        # 2025-01-05 is Sunday
        assert self.engine.is_trading_day(date(2025, 1, 5)) is False

    def test_holiday_on_weekday_is_not_trading(self):
        # 2025-01-01 is Wednesday but is a holiday
        assert self.engine.is_trading_day(date(2025, 1, 1)) is False

    def test_friday_not_holiday_is_trading(self):
        # 2025-01-03 is Friday, not a holiday
        assert self.engine.is_trading_day(date(2025, 1, 3)) is True


# =========================================================================
#  BOTLogicEngine.resolve_rate — Core Backtrack Algorithm
# =========================================================================

class TestResolveRate:
    """Tests for the zero-guess backtrack algorithm."""

    def _make_rates(self, entries: dict) -> dict:
        """Helper: convert {date: float} to {date: Decimal}."""
        return {d: Decimal(str(v)) for d, v in entries.items()}

    def test_exact_match_on_trading_day(self):
        """Rate exists on the exact invoice date (a Monday)."""
        engine = BOTLogicEngine(holidays=[])
        d = date(2025, 1, 6)  # Monday
        usd = self._make_rates({d: 33.5})
        eur = self._make_rates({d: 36.2})
        trade_date, usd_rate, eur_rate = engine.resolve_rate(d, usd, eur)
        assert trade_date == d
        assert usd_rate == Decimal("33.5")
        assert eur_rate == Decimal("36.2")

    def test_backtrack_over_weekend(self):
        """Invoice on Saturday should roll back to Friday."""
        engine = BOTLogicEngine(holidays=[])
        saturday = date(2025, 1, 4)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, usd_rate, eur_rate = engine.resolve_rate(saturday, usd, eur)
        assert trade_date == friday

    def test_backtrack_over_holiday(self):
        """Invoice on Mon holiday → should roll back to previous Friday."""
        # 2025-01-06 is Monday, mark it as holiday
        engine = BOTLogicEngine(holidays=[date(2025, 1, 6)])
        monday = date(2025, 1, 6)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(monday, usd, eur)
        assert trade_date == friday

    def test_backtrack_over_long_holiday(self):
        """Invoice during a 3-day holiday + weekend → rolls back 5 days."""
        # Wed-Fri holiday + Sat-Sun weekend = 5 non-trading days
        holidays = [date(2025, 4, 16), date(2025, 4, 15), date(2025, 4, 14)]
        engine = BOTLogicEngine(holidays=holidays, max_rollback_days=7)
        # Invoice on Fri Apr 18 → no rates, Sat/Sun, then Wed-Fri holiday
        # Should find data on Mon Apr 11 (actually, let's test Sun Apr 13)
        invoice = date(2025, 4, 18)  # Friday (trading day but no data)
        target = date(2025, 4, 11)  # Friday before the holiday block
        usd = self._make_rates({target: 34.0})
        eur = self._make_rates({target: 37.0})
        trade_date, _, _ = engine.resolve_rate(invoice, usd, eur)
        assert trade_date == target

    def test_raises_when_exceeds_rollback_limit(self):
        """Exceeding max_rollback_days should raise RateNotFoundError."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=3)
        invoice = date(2025, 1, 6)  # Monday
        # No rate data at all
        usd = self._make_rates({})
        eur = self._make_rates({})
        with pytest.raises(RateNotFoundError):
            engine.resolve_rate(invoice, usd, eur)

    def test_requires_both_usd_and_eur(self):
        """If USD exists but EUR doesn't, should keep rolling back."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=3)
        d1 = date(2025, 1, 6)  # Monday — has USD only
        d2 = date(2025, 1, 3)  # Friday — has both
        usd = self._make_rates({d1: 33.5, d2: 33.4})
        eur = self._make_rates({d2: 36.0})  # No EUR on Monday
        trade_date, usd_rate, eur_rate = engine.resolve_rate(d1, usd, eur)
        assert trade_date == d2  # Rolled back to Friday
        assert eur_rate == Decimal("36.0")

    def test_raises_when_eur_missing_everywhere(self):
        """If EUR is never found within rollback, raises RateNotFoundError."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=2)
        d = date(2025, 1, 6)  # Monday
        usd = self._make_rates({d: 33.5, date(2025, 1, 3): 33.4})
        eur = self._make_rates({})  # No EUR anywhere
        with pytest.raises(RateNotFoundError):
            engine.resolve_rate(d, usd, eur)
