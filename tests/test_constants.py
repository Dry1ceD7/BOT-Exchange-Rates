#!/usr/bin/env python3
"""
tests/test_constants.py
---------------------------------------------------------------------------
Unit tests for core/constants.py — date parsing (Buddhist-Era normalization,
plausible-year bounds, day-first policy) and the BOT business-date helper.
---------------------------------------------------------------------------
"""

from datetime import date, datetime, timedelta, timezone

from core.constants import (
    LEDGER_SUPPORTED_CURRENCIES,
    PER_100_UNIT_CURRENCIES,
    bot_today,
    parse_date,
)


class TestParseDateBuddhistEra:
    """B.E. years (~2400-2700) are normalized to Common Era."""

    def test_be_slash_format(self):
        # 2567 B.E. == 2024 CE; day-first → 31 Dec 2024.
        assert parse_date("31/12/2567") == date(2024, 12, 31)

    def test_be_iso_format(self):
        assert parse_date("2567-01-15") == date(2024, 1, 15)

    def test_be_dash_format(self):
        assert parse_date("15-01-2567") == date(2024, 1, 15)

    def test_be_lower_band_boundary(self):
        # 2400 B.E. == 1857 CE, which is below the plausible-year floor → None.
        assert parse_date("01/01/2400") is None

    def test_be_recent_year(self):
        # 2568 B.E. == 2025 CE.
        assert parse_date("01/06/2568") == date(2025, 6, 1)


class TestParseDateSanityBounds:
    """Implausible years are rejected rather than silently mis-targeted."""

    def test_year_9999_returns_none(self):
        assert parse_date("01/01/9999") is None

    def test_year_far_future_returns_none(self):
        # Above the B.E. band and far in the future → None.
        assert parse_date("2999-01-01") is None

    def test_year_too_old_returns_none(self):
        # 1969 is below the 1970 floor.
        assert parse_date("1969-12-31") is None


class TestParseDateNormalCe:
    """Plausible CE dates pass through unchanged."""

    def test_iso_ce(self):
        assert parse_date("2025-03-14") == date(2025, 3, 14)

    def test_slash_ce(self):
        assert parse_date("14/03/2025") == date(2025, 3, 14)

    def test_year_floor_1970_kept(self):
        assert parse_date("1970-01-01") == date(1970, 1, 1)

    def test_next_year_allowed(self):
        nxt = date.today().year + 1
        assert parse_date(f"{nxt}-01-01") == date(nxt, 1, 1)


class TestParseDateDayFirst:
    """DATE_FORMATS is day-first by deliberate Thai-locale policy."""

    def test_ambiguous_is_day_first(self):
        # "01/02/2025" is 1 February, NOT 2 January.
        assert parse_date("01/02/2025") == date(2025, 2, 1)


class TestParseDatePassThrough:
    """date/datetime inputs and junk tokens behave as before."""

    def test_datetime_passthrough(self):
        assert parse_date(datetime(2025, 5, 6, 12, 0)) == date(2025, 5, 6)

    def test_date_passthrough(self):
        assert parse_date(date(2025, 5, 6)) == date(2025, 5, 6)

    def test_empty_returns_none(self):
        assert parse_date("") is None

    def test_nan_returns_none(self):
        assert parse_date("nan") is None

    def test_garbage_returns_none(self):
        assert parse_date("not-a-date") is None


class TestLedgerCurrencyConstants:
    """Per-100-unit currencies must NOT be ledger-supported (F4).

    BOT quotes JPY per 100 yen; writing that figure into a ledger EX Rate
    column would overstate an "amount x rate" conversion 100x, so JPY is
    excluded until the department confirms its convention.
    """

    def test_jpy_not_ledger_supported(self):
        assert "JPY" not in LEDGER_SUPPORTED_CURRENCIES

    def test_per_100_unit_currencies_documented(self):
        assert PER_100_UNIT_CURRENCIES == ("JPY",)

    def test_per_100_set_disjoint_from_supported(self):
        # Re-including a per-100 currency requires handling the divide-by-100
        # convention first — the sets must never overlap silently.
        assert not set(PER_100_UNIT_CURRENCIES) & LEDGER_SUPPORTED_CURRENCIES


class TestBotToday:
    """bot_today() returns the Asia/Bangkok (UTC+7) calendar date."""

    def test_matches_bangkok_date(self):
        expected = datetime.now(timezone(timedelta(hours=7))).date()
        assert bot_today() == expected

    def test_returns_date_type(self):
        assert isinstance(bot_today(), date)
