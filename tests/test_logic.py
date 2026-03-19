#!/usr/bin/env python3
"""
tests/test_logic.py
---------------------------------------------------------------------------
Unit tests for core/logic.py — V2.5 Standard Date Resolution Engine.
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
#  BOTLogicEngine.resolve_rate — Standard Date Resolution (V2.5)
# =========================================================================

class TestResolveRate:
    """Tests for the standard date resolution algorithm (V2.5)."""

    def _make_rates(self, entries: dict) -> dict:
        """Helper: convert {date: float} to {date: Decimal}."""
        return {d: Decimal(str(v)) for d, v in entries.items()}

    def test_exact_weekday_returns_same_day(self):
        """Target is a normal Tuesday with data → returns Tuesday's rate."""
        engine = BOTLogicEngine(holidays=[])
        tuesday = date(2025, 1, 7)   # Tuesday
        usd = self._make_rates({tuesday: 33.5})
        eur = self._make_rates({tuesday: 36.2})
        trade_date, usd_rate, eur_rate = engine.resolve_rate(tuesday, usd, eur)
        assert trade_date == tuesday
        assert usd_rate == Decimal("33.5")
        assert eur_rate == Decimal("36.2")

    def test_exact_monday_returns_monday(self):
        """Target is Monday with data → returns Monday's rate (no rollback)."""
        engine = BOTLogicEngine(holidays=[])
        monday = date(2025, 1, 6)    # Monday
        usd = self._make_rates({monday: 33.4})
        eur = self._make_rates({monday: 36.1})
        trade_date, usd_rate, eur_rate = engine.resolve_rate(monday, usd, eur)
        assert trade_date == monday

    def test_exact_wednesday_returns_wednesday(self):
        """Target is Wednesday with data → returns Wednesday's rate."""
        engine = BOTLogicEngine(holidays=[])
        wednesday = date(2025, 1, 8)  # Wednesday
        usd = self._make_rates({wednesday: 33.6})
        eur = self._make_rates({wednesday: 36.3})
        trade_date, _, _ = engine.resolve_rate(wednesday, usd, eur)
        assert trade_date == wednesday

    def test_saturday_rolls_back_to_friday(self):
        """Target is Saturday → rolls back to Friday."""
        engine = BOTLogicEngine(holidays=[])
        saturday = date(2025, 1, 4)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(saturday, usd, eur)
        assert trade_date == friday

    def test_sunday_rolls_back_to_friday(self):
        """Target is Sunday → rolls back past Saturday to Friday."""
        engine = BOTLogicEngine(holidays=[])
        sunday = date(2025, 1, 5)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(sunday, usd, eur)
        assert trade_date == friday

    def test_holiday_on_weekday_rolls_back(self):
        """
        Target is Monday, but Monday is a BOT holiday.
        Rolls back to Friday.
        """
        engine = BOTLogicEngine(holidays=[date(2025, 1, 6)])  # Monday holiday
        monday = date(2025, 1, 6)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(monday, usd, eur)
        assert trade_date == friday

    def test_long_holiday_block(self):
        """Target during a multi-day holiday block stacks with weekend rollback."""
        # Wed-Fri holiday block
        holidays = [date(2025, 4, 16), date(2025, 4, 15), date(2025, 4, 14)]
        engine = BOTLogicEngine(holidays=holidays, max_rollback_days=10)
        # Target is Wed Apr 16 (holiday) → Tue Apr 15 (holiday) → Mon Apr 14 (holiday)
        # → Sun Apr 13 (weekend) → Sat Apr 12 (weekend) → Fri Apr 11 ✓
        target = date(2025, 4, 16)
        expected = date(2025, 4, 11)
        usd = self._make_rates({expected: 34.0})
        eur = self._make_rates({expected: 37.0})
        trade_date, _, _ = engine.resolve_rate(target, usd, eur)
        assert trade_date == expected

    def test_raises_when_exceeds_rollback_limit(self):
        """Exceeding max_rollback_days should raise RateNotFoundError."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=3)
        target = date(2025, 1, 7)  # Tuesday
        usd = self._make_rates({})
        eur = self._make_rates({})
        with pytest.raises(RateNotFoundError):
            engine.resolve_rate(target, usd, eur)

    def test_requires_both_usd_and_eur(self):
        """If USD exists on target but EUR doesn't, should keep rolling back."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=5)
        # Target is Tuesday 2025-01-07 — has USD only
        # Rolls back to Monday (has both)
        tuesday = date(2025, 1, 7)
        monday = date(2025, 1, 6)
        usd = self._make_rates({tuesday: 33.5, monday: 33.4})
        eur = self._make_rates({monday: 36.0})  # No EUR on Tuesday
        trade_date, usd_rate, eur_rate = engine.resolve_rate(tuesday, usd, eur)
        assert trade_date == monday
        assert eur_rate == Decimal("36.0")

    def test_raises_when_eur_missing_everywhere(self):
        """If EUR is never found within rollback, raises RateNotFoundError."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=2)
        tuesday = date(2025, 1, 7)
        usd = self._make_rates({tuesday: 33.5, date(2025, 1, 6): 33.4})
        eur = self._make_rates({})
        with pytest.raises(RateNotFoundError):
            engine.resolve_rate(tuesday, usd, eur)


# =========================================================================
#  BOTLogicEngine.resolve_rate_for_currency (V2.5)
# =========================================================================

class TestResolveRateForCurrency:
    """Tests for the currency-aware date resolver."""

    def _make_rates(self, entries: dict) -> dict:
        return {d: Decimal(str(v)) for d, v in entries.items()}

    def test_thb_returns_one(self):
        """THB should immediately return Decimal('1.0000') with no rollback."""
        engine = BOTLogicEngine(holidays=[])
        d = date(2025, 1, 7)
        trade_date, rate = engine.resolve_rate_for_currency(
            d, "THB", {}, {}
        )
        assert trade_date == d
        assert rate == Decimal("1.0000")

    def test_thb_case_insensitive(self):
        """THB matching should be case-insensitive."""
        engine = BOTLogicEngine(holidays=[])
        d = date(2025, 1, 7)
        trade_date, rate = engine.resolve_rate_for_currency(
            d, " thb ", {}, {}
        )
        assert rate == Decimal("1.0000")

    def test_usd_returns_exact_date_rate(self):
        """USD should resolve to the exact date's rate."""
        engine = BOTLogicEngine(holidays=[])
        tuesday = date(2025, 1, 7)
        usd = self._make_rates({tuesday: 33.5})
        eur = self._make_rates({tuesday: 36.2})
        trade_date, rate = engine.resolve_rate_for_currency(
            tuesday, "USD", usd, eur
        )
        assert trade_date == tuesday
        assert rate == Decimal("33.5")

    def test_eur_returns_exact_date_rate(self):
        """EUR should resolve to the exact date's rate."""
        engine = BOTLogicEngine(holidays=[])
        tuesday = date(2025, 1, 7)
        usd = self._make_rates({tuesday: 33.5})
        eur = self._make_rates({tuesday: 36.2})
        trade_date, rate = engine.resolve_rate_for_currency(
            tuesday, "EUR", usd, eur
        )
        assert trade_date == tuesday
        assert rate == Decimal("36.2")

    def test_unsupported_currency_returns_none(self):
        """Unknown currency should return None rate (skip silently)."""
        engine = BOTLogicEngine(holidays=[])
        d = date(2025, 1, 7)
        trade_date, rate = engine.resolve_rate_for_currency(
            d, "JPY", {}, {}
        )
        assert rate is None

    def test_empty_currency_returns_none(self):
        """Empty currency string should return None rate."""
        engine = BOTLogicEngine(holidays=[])
        d = date(2025, 1, 7)
        trade_date, rate = engine.resolve_rate_for_currency(
            d, "", {}, {}
        )
        assert rate is None


# =========================================================================
#  LedgerEngine.compute_year_start_date (V2.5)
# =========================================================================

class TestComputeYearStartDate:
    """Tests for the smart year-end start date extraction."""

    def test_normal_dec_30_weekday(self):
        """Dec 30 is a normal weekday → returns Dec 30."""
        from core.engine import LedgerEngine
        # 2024-12-30 is a Monday
        result = LedgerEngine.compute_year_start_date(2025, holidays=[])
        assert result == date(2024, 12, 30)

    def test_dec_30_is_weekend(self):
        """Dec 30 falls on a weekend → rolls back."""
        from core.engine import LedgerEngine
        # 2023-12-30 is a Saturday → should roll back to Fri Dec 29
        result = LedgerEngine.compute_year_start_date(2024, holidays=[])
        assert result == date(2023, 12, 29)

    def test_dec_30_is_holiday(self):
        """Dec 30 is a BOT holiday → rolls back."""
        from core.engine import LedgerEngine
        # 2024-12-30 is Monday. Mark it as holiday → rolls to Fri Dec 27
        result = LedgerEngine.compute_year_start_date(
            2025, holidays=[date(2024, 12, 30)]
        )
        assert result == date(2024, 12, 27)

    def test_dec_31_always_skipped(self):
        """Dec 31 should never be returned (company office day-off)."""
        from core.engine import LedgerEngine
        # Even with no holidays, Dec 31 shouldn't appear
        result = LedgerEngine.compute_year_start_date(2025, holidays=[])
        assert result.day != 31 or result.month != 12
