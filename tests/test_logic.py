#!/usr/bin/env python3
"""
tests/test_logic.py
---------------------------------------------------------------------------
Unit tests for core/logic.py — V2.5 T-1 Financial Accounting Engine.
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
#  BOTLogicEngine.resolve_rate — T-1 Retraction Algorithm (V2.5)
# =========================================================================

class TestResolveRate:
    """Tests for the T-1 retraction algorithm (V2.5)."""

    def _make_rates(self, entries: dict) -> dict:
        """Helper: convert {date: float} to {date: Decimal}."""
        return {d: Decimal(str(v)) for d, v in entries.items()}

    def test_t1_tuesday_returns_monday(self):
        """Invoice on Tuesday → T-1 → should return Monday's rate."""
        engine = BOTLogicEngine(holidays=[])
        tuesday = date(2025, 1, 7)   # Tuesday
        monday = date(2025, 1, 6)    # Monday (T-1)
        usd = self._make_rates({monday: 33.5})
        eur = self._make_rates({monday: 36.2})
        trade_date, usd_rate, eur_rate = engine.resolve_rate(tuesday, usd, eur)
        assert trade_date == monday
        assert usd_rate == Decimal("33.5")
        assert eur_rate == Decimal("36.2")

    def test_t1_monday_returns_friday(self):
        """Invoice on Monday → T-1 is Sunday → rolls back to Friday."""
        engine = BOTLogicEngine(holidays=[])
        monday = date(2025, 1, 6)    # Monday
        friday = date(2025, 1, 3)    # Previous Friday
        usd = self._make_rates({friday: 33.4})
        eur = self._make_rates({friday: 36.1})
        trade_date, usd_rate, eur_rate = engine.resolve_rate(monday, usd, eur)
        assert trade_date == friday

    def test_t1_wednesday_returns_tuesday(self):
        """Invoice on Wednesday → T-1 → should return Tuesday's rate."""
        engine = BOTLogicEngine(holidays=[])
        wednesday = date(2025, 1, 8)  # Wednesday
        tuesday = date(2025, 1, 7)    # Tuesday
        usd = self._make_rates({tuesday: 33.6})
        eur = self._make_rates({tuesday: 36.3})
        trade_date, _, _ = engine.resolve_rate(wednesday, usd, eur)
        assert trade_date == tuesday

    def test_t1_saturday_returns_friday(self):
        """Invoice on Saturday → T-1 is Friday → trading day → return Friday."""
        engine = BOTLogicEngine(holidays=[])
        saturday = date(2025, 1, 4)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(saturday, usd, eur)
        assert trade_date == friday

    def test_t1_sunday_returns_friday(self):
        """Invoice on Sunday → T-1 is Saturday → rolls back to Friday."""
        engine = BOTLogicEngine(holidays=[])
        sunday = date(2025, 1, 5)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(sunday, usd, eur)
        assert trade_date == friday

    def test_t1_tuesday_after_monday_holiday(self):
        """
        Invoice on Tuesday, but Monday is a BOT holiday.
        T-1 of Tuesday = Monday (holiday) → rolls back to Friday.
        """
        engine = BOTLogicEngine(holidays=[date(2025, 1, 6)])  # Monday holiday
        tuesday = date(2025, 1, 7)
        friday = date(2025, 1, 3)
        usd = self._make_rates({friday: 33.5})
        eur = self._make_rates({friday: 36.2})
        trade_date, _, _ = engine.resolve_rate(tuesday, usd, eur)
        assert trade_date == friday

    def test_t1_with_long_holiday_block(self):
        """Invoice during a holiday block: T-1 stacks with rollback."""
        # Wed-Fri holiday block
        holidays = [date(2025, 4, 16), date(2025, 4, 15), date(2025, 4, 14)]
        engine = BOTLogicEngine(holidays=holidays, max_rollback_days=10)
        # Invoice on Sat Apr 19 → T-1 is Fri Apr 18 (but Fri 18 has no data...
        # Actually Fri 18 is NOT in the holiday list, so we need data there.
        # Let's test: Invoice on Mon Apr 14 (holiday) — wait, resolve_rate
        # uses the invoice date as input, so T-1 of Mon Apr 14 = Sun Apr 13
        # → rolls back to... Fri Apr 11 (not holiday)
        # Actually let's do invoice on Thu Apr 17:
        # T-1 = Wed Apr 16 (holiday) → Tue Apr 15 (holiday) → Mon Apr 14 (holiday)
        # → Sun Apr 13 (weekend) → Sat Apr 12 (weekend) → Fri Apr 11 ✓
        invoice = date(2025, 4, 17)
        target = date(2025, 4, 11)
        usd = self._make_rates({target: 34.0})
        eur = self._make_rates({target: 37.0})
        trade_date, _, _ = engine.resolve_rate(invoice, usd, eur)
        assert trade_date == target

    def test_raises_when_exceeds_rollback_limit(self):
        """Exceeding max_rollback_days should raise RateNotFoundError."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=3)
        invoice = date(2025, 1, 7)  # Tuesday
        usd = self._make_rates({})
        eur = self._make_rates({})
        with pytest.raises(RateNotFoundError):
            engine.resolve_rate(invoice, usd, eur)

    def test_requires_both_usd_and_eur(self):
        """If USD exists on T-1 but EUR doesn't, should keep rolling back."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=5)
        # Invoice on Tuesday 2025-01-07 → T-1 is Monday 2025-01-06
        # Monday has USD only → rolls back to Friday
        tuesday = date(2025, 1, 7)
        monday = date(2025, 1, 6)
        friday = date(2025, 1, 3)
        usd = self._make_rates({monday: 33.5, friday: 33.4})
        eur = self._make_rates({friday: 36.0})  # No EUR on Monday
        trade_date, usd_rate, eur_rate = engine.resolve_rate(tuesday, usd, eur)
        assert trade_date == friday
        assert eur_rate == Decimal("36.0")

    def test_raises_when_eur_missing_everywhere(self):
        """If EUR is never found within rollback, raises RateNotFoundError."""
        engine = BOTLogicEngine(holidays=[], max_rollback_days=2)
        tuesday = date(2025, 1, 7)
        monday = date(2025, 1, 6)
        usd = self._make_rates({monday: 33.5, date(2025, 1, 3): 33.4})
        eur = self._make_rates({})
        with pytest.raises(RateNotFoundError):
            engine.resolve_rate(tuesday, usd, eur)


# =========================================================================
#  BOTLogicEngine.resolve_rate_for_currency (V2.5)
# =========================================================================

class TestResolveRateForCurrency:
    """Tests for the currency-aware T-1 resolver."""

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

    def test_usd_returns_t1_rate(self):
        """USD should route through T-1 resolve and return USD rate."""
        engine = BOTLogicEngine(holidays=[])
        tuesday = date(2025, 1, 7)
        monday = date(2025, 1, 6)
        usd = self._make_rates({monday: 33.5})
        eur = self._make_rates({monday: 36.2})
        trade_date, rate = engine.resolve_rate_for_currency(
            tuesday, "USD", usd, eur
        )
        assert trade_date == monday
        assert rate == Decimal("33.5")

    def test_eur_returns_t1_rate(self):
        """EUR should route through T-1 resolve and return EUR rate."""
        engine = BOTLogicEngine(holidays=[])
        tuesday = date(2025, 1, 7)
        monday = date(2025, 1, 6)
        usd = self._make_rates({monday: 33.5})
        eur = self._make_rates({monday: 36.2})
        trade_date, rate = engine.resolve_rate_for_currency(
            tuesday, "EUR", usd, eur
        )
        assert trade_date == monday
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
