#!/usr/bin/env python3
"""
tests/test_logic.py
---------------------------------------------------------------------------
Unit tests for core/logic.py — trading-day utilities & Decimal helpers.
---------------------------------------------------------------------------
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

import pytest

from core.logic import (
    BOTLogicEngine,
    build_holiday_lookup,
    compute_year_start_date,
    default_fetch_window_start,
    safe_to_decimal,
    weekdays_between,
)

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


class TestSafeToDecimalRoundingMode:
    """ROUND_HALF_EVEN is the pinned project standard for 4dp quantization.

    Boundary values are exact decimal ties at the 5th decimal place, chosen
    so HALF_EVEN and HALF_UP disagree — locking the mode, not just the math.
    """

    def test_tie_with_even_digit_rounds_down(self):
        # HALF_EVEN -> 34.5678 (8 is even); HALF_UP would give 34.5679.
        assert safe_to_decimal("34.56785") == Decimal("34.5678")

    def test_tie_with_even_digit_rounds_down_small(self):
        # HALF_EVEN -> 1.2344 (4 is even); HALF_UP would give 1.2345.
        assert safe_to_decimal("1.23445") == Decimal("1.2344")

    def test_tie_with_odd_digit_rounds_up(self):
        # HALF_EVEN -> 1.2344 (3 is odd, rounds to even 4) — same as HALF_UP.
        assert safe_to_decimal("1.23435") == Decimal("1.2344")

    def test_tie_at_zero_boundary(self):
        # HALF_EVEN -> 0.0000 (0 is even); HALF_UP would give 0.0001.
        assert safe_to_decimal("0.00005") == Decimal("0.0000")

    def test_mode_pinned_against_ambient_context(self):
        # Pre-pin, the quantize inherited the ambient decimal context; a
        # HALF_UP context would have flipped the tie to 34.5679. The
        # explicit rounding= argument must make the context irrelevant.
        with localcontext() as ctx:
            ctx.rounding = ROUND_HALF_UP
            assert safe_to_decimal("34.56785") == Decimal("34.5678")


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
#  core.logic.compute_year_start_date (V2.5)
# =========================================================================

class TestComputeYearStartDate:
    """Tests for the smart year-end start date extraction."""

    def test_normal_dec_30_weekday(self):
        """Dec 30 is a normal weekday → returns Dec 30."""
        # 2024-12-30 is a Monday
        result = compute_year_start_date(2025, holidays=[])
        assert result == date(2024, 12, 30)

    def test_dec_30_is_weekend(self):
        """Dec 30 falls on a weekend → rolls back."""
        # 2023-12-30 is a Saturday → should roll back to Fri Dec 29
        result = compute_year_start_date(2024, holidays=[])
        assert result == date(2023, 12, 29)

    def test_dec_30_is_holiday(self):
        """Dec 30 is a BOT holiday → rolls back."""
        # 2024-12-30 is Monday. Mark it as holiday → rolls to Fri Dec 27
        result = compute_year_start_date(
            2025, holidays=[date(2024, 12, 30)]
        )
        assert result == date(2024, 12, 27)

    def test_dec_31_always_skipped(self):
        """Dec 31 should never be returned (company office day-off)."""
        # Even with no holidays, Dec 31 shouldn't appear
        result = compute_year_start_date(2025, holidays=[])
        assert result.day != 31 or result.month != 12

    def test_no_trading_day_raises(self):
        """If every December weekday is a holiday, raise (no silent Dec 20).

        Migrated from tests/test_engine.py in round 11 when the dead
        LedgerEngine.compute_year_start_date static delegate was removed.
        """
        from datetime import timedelta
        prev_year = 2024
        all_dec = []
        d = date(prev_year, 12, 1)
        while d.year == prev_year:
            all_dec.append(d)
            d += timedelta(days=1)
        with pytest.raises(ValueError):
            compute_year_start_date(prev_year + 1, all_dec)


# =========================================================================
#  BOUNDARY TESTS — year boundary
# =========================================================================

class TestYearBoundaryNeverDec31:
    """compute_year_start_date must never return Dec 31."""

    def test_dec30_holiday_and_weekend_never_dec31(self):
        # 2022-12-31 is a Saturday and 2022-12-30 a Friday. Mark Dec 30 a
        # holiday so the only adjacent candidates are the weekend (31st) and
        # earlier trading days. Result must roll BACK, never forward to 31.
        result = compute_year_start_date(
            2023, holidays=[date(2022, 12, 30)],
        )
        assert not (result.month == 12 and result.day == 31)
        assert result == date(2022, 12, 29)  # Thursday before the holiday

    def test_dec30_weekend_rolls_back_not_to_31(self):
        # 2023-12-30 is a Saturday → must roll back to Fri 12/29, not 12/31.
        result = compute_year_start_date(2024, holidays=[])
        assert not (result.month == 12 and result.day == 31)
        assert result == date(2023, 12, 29)


# =========================================================================
#  core.logic.build_holiday_lookup
# =========================================================================

class TestBuildHolidayLookup:
    """Tests for the moved build_holiday_lookup pure function."""

    def test_parses_substitution_holiday(self):
        from types import SimpleNamespace

        substitution_entry = (
            "2025-04-16",
            "Substitution for Songkran Day (15th April 2025)",
        )

        class _Cache:
            def get_holidays(self, year):
                return [substitution_entry] if year == 2025 else []

        holidays_set, holidays_names = build_holiday_lookup(
            _Cache(),
            all_target_dates={date(2025, 4, 16)},
            computed_start=date(2024, 12, 30),
            logic_engine=SimpleNamespace(holidays=[]),
        )

        assert date(2025, 4, 16) in holidays_names
        assert date(2025, 4, 15) in holidays_set
        assert holidays_names[date(2025, 4, 15)] == "Songkran Day"


# =========================================================================
#  weekdays_between / default_fetch_window_start (calendar-window helpers)
# =========================================================================

class TestWeekdaysBetween:
    """Inclusive Mon-Fri calendar window — holidays are NOT excluded."""

    def test_week_span_excludes_weekend(self):
        # Mon 2025-01-06 .. Sun 2025-01-12 → exactly Mon-Fri.
        result = weekdays_between(date(2025, 1, 6), date(2025, 1, 12))
        assert result == {date(2025, 1, d) for d in range(6, 11)}

    def test_inclusive_bounds(self):
        # Single trading-day window: start == end (a Wednesday).
        assert weekdays_between(date(2025, 1, 8), date(2025, 1, 8)) == {
            date(2025, 1, 8)
        }

    def test_weekend_only_window_is_empty(self):
        # Sat 2025-01-11 .. Sun 2025-01-12 → no weekdays.
        assert weekdays_between(date(2025, 1, 11), date(2025, 1, 12)) == set()

    def test_empty_when_start_after_end(self):
        assert weekdays_between(date(2025, 1, 10), date(2025, 1, 6)) == set()


class TestDefaultFetchWindowStart:
    """Dec 20 of the prior year — a fetch-window anchor, never rolled."""

    def test_prior_year_dec_20(self):
        assert default_fetch_window_start(2025) == date(2024, 12, 20)

    def test_weekend_dec_20_is_returned_unchanged(self):
        # 2026-12-20 is a Sunday; the FETCH window deliberately keeps it —
        # only compute_year_start_date does trading-day rollback.
        assert default_fetch_window_start(2027) == date(2026, 12, 20)
        assert default_fetch_window_start(2027).weekday() == 6
